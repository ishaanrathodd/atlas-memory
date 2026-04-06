from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from memory.heartbeat import (
    build_response_profile,
    build_rhythm_profile,
    build_thread_emotion_profile,
    build_background_task_completion_opportunity,
    build_conversation_dropoff_opportunity,
    build_promise_followup_opportunity,
    is_opportunity_due,
    rank_due_opportunities,
    score_opportunity,
    selection_score_opportunity,
)
from memory.models import Commitment, CommitmentKind, CommitmentStatus, Episode, EpisodeRole, HeartbeatDispatch, PresenceState


def test_build_conversation_dropoff_opportunity_uses_presence_state() -> None:
    now = datetime.now(timezone.utc)
    session_id = uuid4()
    state = PresenceState(
        agent_namespace="main",
        active_session_id=session_id,
        last_agent_message_at=now - timedelta(minutes=6),
        current_thread_summary="The user vanished after I asked whether I should keep debugging.",
        conversation_energy=0.74,
        warmth_score=0.68,
        tension_score=0.15,
        user_disappeared_mid_thread=True,
    )

    opportunity = build_conversation_dropoff_opportunity(
        state,
        now=now,
        min_delay=timedelta(minutes=2),
        max_delay=timedelta(minutes=20),
    )

    assert opportunity is not None
    assert opportunity.opportunity_key == f"dropoff:{session_id}"
    assert opportunity.reason_summary == state.current_thread_summary
    assert opportunity.requires_authored_llm_message is True


def test_is_opportunity_due_respects_send_window() -> None:
    now = datetime.now(timezone.utc)
    state = PresenceState(
        agent_namespace="main",
        active_session_id=uuid4(),
        last_agent_message_at=now - timedelta(minutes=10),
        user_disappeared_mid_thread=True,
    )
    opportunity = build_conversation_dropoff_opportunity(
        state,
        now=now,
        min_delay=timedelta(minutes=2),
        max_delay=timedelta(minutes=20),
    )

    assert opportunity is not None
    assert is_opportunity_due(opportunity, now=now) is True
    assert is_opportunity_due(opportunity, now=now - timedelta(minutes=9)) is False


def test_score_opportunity_penalizes_annoyance_risk() -> None:
    now = datetime.now(timezone.utc)
    state = PresenceState(
        agent_namespace="main",
        active_session_id=uuid4(),
        last_agent_message_at=now - timedelta(minutes=8),
        conversation_energy=0.9,
        warmth_score=0.8,
        tension_score=0.05,
        user_disappeared_mid_thread=True,
    )
    opportunity = build_conversation_dropoff_opportunity(state, now=now)
    assert opportunity is not None

    high_risk = opportunity.model_copy(update={"annoyance_risk": 0.9})
    low_score = score_opportunity(high_risk, state=state, now=now)
    high_score = score_opportunity(opportunity, state=state, now=now)

    assert high_score > low_score


def test_build_promise_followup_opportunity_from_open_commitment() -> None:
    now = datetime.now(timezone.utc)
    commitment = Commitment(
        agent_namespace="main",
        kind=CommitmentKind.FOLLOW_UP,
        statement="Check whether the migration cleanup ever got finished.",
        commitment_key="followup:migration-cleanup",
        status=CommitmentStatus.OPEN,
        confidence=0.88,
        priority_score=0.83,
        first_committed_at=now - timedelta(days=2),
        last_observed_at=now - timedelta(days=1),
        source_session_ids=[uuid4()],
    )

    opportunity = build_promise_followup_opportunity(commitment, now=now)

    assert opportunity is not None
    assert opportunity.kind.value == "promise_followup"
    assert opportunity.opportunity_key == "followup:followup:migration-cleanup"
    assert opportunity.reason_summary == commitment.statement


def test_build_background_task_completion_opportunity_is_immediately_due() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    opportunity = build_background_task_completion_opportunity(
        agent_namespace="main",
        session_id=session_id,
        reason_summary="Finished tracing the warm-start regression and isolated the real write path.",
        now=now,
        source_refs=["job:bg-17"],
    )

    assert opportunity.kind.value == "background_task_completion"
    assert opportunity.opportunity_key == f"completion:{session_id}"
    assert is_opportunity_due(opportunity, now=now) is True


def test_selection_score_penalizes_recent_same_opportunity_dispatch() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    opportunity = build_background_task_completion_opportunity(
        agent_namespace="main",
        session_id=session_id,
        reason_summary="Finished tracing the issue in the background.",
        now=now,
    )
    recent_dispatch = HeartbeatDispatch(
        agent_namespace="main",
        opportunity_key=opportunity.opportunity_key,
        opportunity_kind=opportunity.kind,
        session_id=session_id,
        dispatch_status="sent",
        attempted_at=now - timedelta(minutes=8),
    )

    cooled_score = selection_score_opportunity(
        opportunity,
        recent_dispatches=[recent_dispatch],
        now=now,
    )
    fresh_score = selection_score_opportunity(
        opportunity,
        recent_dispatches=[],
        now=now,
    )

    assert fresh_score > cooled_score


def test_rank_due_opportunities_prefers_completion_over_repetitive_followup() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    completion = build_background_task_completion_opportunity(
        agent_namespace="main",
        session_id=session_id,
        reason_summary="Finished the background task and have concrete findings.",
        now=now,
        priority_score=0.72,
    )
    commitment = Commitment(
        agent_namespace="main",
        kind=CommitmentKind.FOLLOW_UP,
        statement="Check whether the refactor ever got finished.",
        commitment_key="followup:refactor",
        status=CommitmentStatus.OPEN,
        confidence=0.86,
        priority_score=0.9,
        first_committed_at=now - timedelta(days=2),
        last_observed_at=now - timedelta(days=1),
        source_session_ids=[uuid4()],
    )
    followup = build_promise_followup_opportunity(commitment, now=now)
    assert followup is not None

    recent_followup_dispatch = HeartbeatDispatch(
        agent_namespace="main",
        opportunity_key="followup:older-open-loop",
        opportunity_kind=followup.kind,
        session_id=str(followup.session_id or ""),
        dispatch_status="sent",
        attempted_at=now - timedelta(minutes=10),
    )

    ranked = rank_due_opportunities(
        [followup, completion],
        recent_dispatches=[recent_followup_dispatch],
        now=now + timedelta(minutes=1),
    )

    assert ranked[0]["kind"] == "background_task_completion"
    assert ranked[0]["send_score"] >= ranked[1]["send_score"]


def test_build_rhythm_profile_marks_likely_active_hour() -> None:
    now = datetime(2026, 4, 6, 14, 30, tzinfo=timezone.utc)
    activity = [
        datetime(2026, 4, 1, 14, 5, tzinfo=timezone.utc),
        datetime(2026, 4, 2, 15, 20, tzinfo=timezone.utc),
        datetime(2026, 4, 3, 14, 45, tzinfo=timezone.utc),
        datetime(2026, 4, 4, 13, 55, tzinfo=timezone.utc),
        datetime(2026, 4, 5, 14, 10, tzinfo=timezone.utc),
        datetime(2026, 4, 6, 15, 0, tzinfo=timezone.utc),
    ]

    profile = build_rhythm_profile(activity, now=now)

    assert profile["sample_count"] == 6
    assert profile["is_likely_active_hour"] is True
    assert 14 in profile["top_hours"] or 15 in profile["top_hours"]


def test_selection_score_penalizes_quiet_hour_for_soft_followup() -> None:
    now = datetime(2026, 4, 6, 3, 30, tzinfo=timezone.utc)
    commitment = Commitment(
        agent_namespace="main",
        kind=CommitmentKind.FOLLOW_UP,
        statement="Check whether the refactor ever got finished.",
        commitment_key="followup:quiet-hours",
        status=CommitmentStatus.OPEN,
        confidence=0.82,
        priority_score=0.88,
        first_committed_at=now - timedelta(days=2),
        last_observed_at=now - timedelta(days=1),
        source_session_ids=[uuid4()],
    )
    followup = build_promise_followup_opportunity(commitment, now=now)
    assert followup is not None

    quiet_profile = {
        "sample_count": 8,
        "top_hours": [14, 15, 16],
        "current_hour": 3,
        "is_likely_active_hour": False,
        "is_quiet_hour": True,
        "nearest_active_hour_distance": 11,
    }
    active_profile = {
        "sample_count": 8,
        "top_hours": [2, 3, 4],
        "current_hour": 3,
        "is_likely_active_hour": True,
        "is_quiet_hour": False,
        "nearest_active_hour_distance": 0,
    }

    quiet_score = selection_score_opportunity(
        followup,
        rhythm_profile=quiet_profile,
        now=now + timedelta(minutes=1),
    )
    active_score = selection_score_opportunity(
        followup,
        rhythm_profile=active_profile,
        now=now + timedelta(minutes=1),
    )

    assert active_score > quiet_score


def test_build_response_profile_measures_quick_and_missing_replies() -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    session_quick = str(uuid4())
    session_silent = str(uuid4())
    dispatches = [
        HeartbeatDispatch(
            agent_namespace="main",
            opportunity_key=f"dropoff:{session_quick}",
            opportunity_kind="conversation_dropoff",
            session_id=session_quick,
            dispatch_status="sent",
            attempted_at=now - timedelta(minutes=40),
        ),
        HeartbeatDispatch(
            agent_namespace="main",
            opportunity_key=f"followup:{session_silent}",
            opportunity_kind="promise_followup",
            session_id=session_silent,
            dispatch_status="sent",
            attempted_at=now - timedelta(hours=13),
        ),
    ]
    episodes = [
        Episode(
            session_id=session_quick,
            role=EpisodeRole.USER,
            content="i'm back, keep going",
            content_hash="hash-1",
            message_timestamp=now - timedelta(minutes=30),
        ),
    ]

    profile = build_response_profile(dispatches, episodes, now=now)

    assert profile["sample_count"] == 2
    assert profile["quick_reply_rate"] == 0.5
    assert profile["no_reply_rate"] == 0.5
    assert profile["kind_profiles"]["conversation_dropoff"]["quick_reply_rate"] == 1.0
    assert profile["kind_profiles"]["conversation_dropoff"]["momentum_reopen_rate"] == 1.0


def test_build_response_profile_distinguishes_acknowledgment_from_momentum() -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    session_ack = str(uuid4())
    session_momentum = str(uuid4())
    dispatches = [
        HeartbeatDispatch(
            agent_namespace="main",
            opportunity_key=f"dropoff:{session_ack}",
            opportunity_kind="conversation_dropoff",
            session_id=session_ack,
            dispatch_status="sent",
            attempted_at=now - timedelta(minutes=50),
        ),
        HeartbeatDispatch(
            agent_namespace="main",
            opportunity_key=f"dropoff:{session_momentum}",
            opportunity_kind="conversation_dropoff",
            session_id=session_momentum,
            dispatch_status="sent",
            attempted_at=now - timedelta(minutes=80),
        ),
    ]
    episodes = [
        Episode(
            session_id=session_ack,
            role=EpisodeRole.USER,
            content="ok thanks",
            content_hash="ack-hash",
            message_timestamp=now - timedelta(minutes=45),
        ),
        Episode(
            session_id=session_momentum,
            role=EpisodeRole.USER,
            content="wait, what exactly did you find in the dispatch path?",
            content_hash="momentum-hash-1",
            message_timestamp=now - timedelta(minutes=70),
        ),
        Episode(
            session_id=session_momentum,
            role=EpisodeRole.USER,
            content="also can you check whether it affects the restart flow too?",
            content_hash="momentum-hash-2",
            message_timestamp=now - timedelta(minutes=65),
        ),
    ]

    profile = build_response_profile(dispatches, episodes, now=now)

    assert profile["sample_count"] == 2
    assert profile["momentum_reopen_rate"] == 0.5
    assert profile["acknowledgment_only_rate"] == 0.5
    assert profile["kind_profiles"]["conversation_dropoff"]["momentum_reopen_rate"] == 0.5
    assert profile["kind_profiles"]["conversation_dropoff"]["acknowledgment_only_rate"] == 0.5


def test_selection_score_uses_response_profile_feedback() -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    session_id = str(uuid4())
    completion = build_background_task_completion_opportunity(
        agent_namespace="main",
        session_id=session_id,
        reason_summary="Finished the background task.",
        now=now,
    )
    weak_response_profile = {
        "sample_count": 4,
        "kind_profiles": {
            "background_task_completion": {
                "sample_count": 3,
                "quick_reply_rate": 0.0,
                "late_reply_rate": 0.0,
                "no_reply_rate": 1.0,
            }
        },
    }
    strong_response_profile = {
        "sample_count": 4,
        "kind_profiles": {
            "background_task_completion": {
                "sample_count": 3,
                "quick_reply_rate": 1.0,
                "late_reply_rate": 0.0,
                "no_reply_rate": 0.0,
            }
        },
    }

    weak_score = selection_score_opportunity(
        completion,
        response_profile=weak_response_profile,
        now=now,
    )
    strong_score = selection_score_opportunity(
        completion,
        response_profile=strong_response_profile,
        now=now,
    )

    assert strong_score > weak_score


def test_selection_score_prefers_momentum_reopening_over_acknowledgment_only() -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    session_id = str(uuid4())
    opportunity = build_conversation_dropoff_opportunity(
        PresenceState(
            agent_namespace="main",
            active_session_id=session_id,
            last_agent_message_at=now - timedelta(minutes=5),
            user_disappeared_mid_thread=True,
            conversation_energy=0.8,
            warmth_score=0.7,
            tension_score=0.1,
        ),
        now=now,
    )
    assert opportunity is not None

    acknowledgment_profile = {
        "sample_count": 4,
        "kind_profiles": {
            "conversation_dropoff": {
                "sample_count": 3,
                "quick_reply_rate": 0.7,
                "late_reply_rate": 0.0,
                "no_reply_rate": 0.0,
                "momentum_reopen_rate": 0.0,
                "acknowledgment_only_rate": 1.0,
            }
        },
    }
    momentum_profile = {
        "sample_count": 4,
        "kind_profiles": {
            "conversation_dropoff": {
                "sample_count": 3,
                "quick_reply_rate": 0.7,
                "late_reply_rate": 0.0,
                "no_reply_rate": 0.0,
                "momentum_reopen_rate": 1.0,
                "acknowledgment_only_rate": 0.0,
            }
        },
    }

    acknowledgment_score = selection_score_opportunity(
        opportunity,
        response_profile=acknowledgment_profile,
        now=now,
    )
    momentum_score = selection_score_opportunity(
        opportunity,
        response_profile=momentum_profile,
        now=now,
    )

    assert momentum_score > acknowledgment_score


def test_build_thread_emotion_profile_detects_tense_unresolved_thread() -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    session_id = str(uuid4())
    episodes = [
        Episode(
            session_id=session_id,
            role=EpisodeRole.USER,
            content="this is stressing me out and i'm worried we broke the restart path",
            content_hash="emotion-1",
            emotions={"fear": 0.6, "anger": 0.2},
            dominant_emotion="fear",
            emotional_intensity=0.6,
            message_timestamp=now - timedelta(minutes=8),
        ),
        Episode(
            session_id=session_id,
            role=EpisodeRole.ASSISTANT,
            content="i see it. want me to trace the dispatch path end to end?",
            content_hash="emotion-2",
            emotions={"trust": 0.3, "anticipation": 0.3},
            dominant_emotion="anticipation",
            emotional_intensity=0.4,
            message_timestamp=now - timedelta(minutes=5),
        ),
    ]

    profile = build_thread_emotion_profile(episodes, handoff_tone="fear, frustration")

    assert profile["tone_label"] in {"tense", "unresolved"}
    assert profile["is_emotionally_unresolved"] is True
    assert profile["unresolved_score"] >= 0.5


def test_selection_score_uses_thread_emotion_profile() -> None:
    now = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    session_id = str(uuid4())
    completion = build_background_task_completion_opportunity(
        agent_namespace="main",
        session_id=session_id,
        reason_summary="Finished tracing the background issue.",
        now=now,
    )
    calm_profile = {
        "tone_label": "settled",
        "unresolved_score": 0.1,
        "closure_score": 0.8,
        "tension_score": 0.1,
        "playfulness_score": 0.0,
    }
    tense_profile = {
        "tone_label": "tense",
        "unresolved_score": 0.8,
        "closure_score": 0.2,
        "tension_score": 0.7,
        "playfulness_score": 0.0,
    }

    calm_score = selection_score_opportunity(
        completion,
        thread_emotion_profile=calm_profile,
        now=now,
    )
    tense_score = selection_score_opportunity(
        completion,
        thread_emotion_profile=tense_profile,
        now=now,
    )

    assert tense_score > calm_score
