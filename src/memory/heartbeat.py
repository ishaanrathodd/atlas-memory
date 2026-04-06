from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from memory.models import (
    Commitment,
    CommitmentStatus,
    HeartbeatDispatch,
    HeartbeatDispatchStatus,
    HeartbeatOpportunity,
    HeartbeatOpportunityKind,
    HeartbeatOpportunityStatus,
    Episode,
    EpisodeRole,
    PresenceState,
)

_TOKEN_RE = re.compile(r"[a-z0-9']+")
_ACK_MARKERS = {
    "ok",
    "okay",
    "kk",
    "k",
    "got",
    "gotit",
    "thanks",
    "thankyou",
    "thx",
    "nice",
    "cool",
    "yep",
    "yeah",
    "done",
    "perfect",
    "great",
    "lol",
}
_TENSION_EMOTIONS = {"anger", "fear", "sadness", "disgust"}
_WARM_EMOTIONS = {"joy", "trust"}
_PLAYFUL_EMOTIONS = {"surprise", "anticipation", "joy"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clamp_unit(value: float, *, floor: float = 0.0, ceiling: float = 1.0) -> float:
    return max(floor, min(ceiling, float(value)))


def _hour_distance(left: int, right: int) -> int:
    diff = abs(int(left) - int(right)) % 24
    return min(diff, 24 - diff)


def build_rhythm_profile(
    activity_timestamps: list[datetime],
    *,
    now: datetime | None = None,
    timezone_info: Any = timezone.utc,
) -> dict[str, Any]:
    if not activity_timestamps:
        return {
            "sample_count": 0,
            "top_hours": [],
            "current_hour": (now or _utcnow()).astimezone(timezone_info).hour,
            "is_likely_active_hour": False,
            "is_quiet_hour": False,
        }

    reference_time = now or _utcnow()
    hour_counts: dict[int, float] = {}
    for timestamp in activity_timestamps:
        localized = timestamp.astimezone(timezone_info)
        age_days = max((reference_time - timestamp.astimezone(reference_time.tzinfo or timezone.utc)).total_seconds() / 86400.0, 0.0)
        recency_weight = max(0.35, 1.0 - (0.06 * age_days))
        hour_counts[localized.hour] = hour_counts.get(localized.hour, 0.0) + recency_weight

    ranked_hours = sorted(hour_counts.items(), key=lambda item: (-item[1], item[0]))
    top_hours = [hour for hour, _score in ranked_hours[:5]]
    current_hour = reference_time.astimezone(timezone_info).hour
    nearest_distance = min((_hour_distance(current_hour, hour) for hour in top_hours), default=12)
    sample_count = len(activity_timestamps)

    return {
        "sample_count": sample_count,
        "top_hours": top_hours,
        "current_hour": current_hour,
        "is_likely_active_hour": bool(sample_count >= 4 and nearest_distance <= 2),
        "is_quiet_hour": bool(sample_count >= 6 and nearest_distance >= 5),
        "nearest_active_hour_distance": nearest_distance,
    }


def build_response_profile(
    dispatches: list[HeartbeatDispatch],
    episodes: list[Episode],
    *,
    now: datetime | None = None,
    quick_reply_window: timedelta = timedelta(minutes=20),
    late_reply_window: timedelta = timedelta(hours=12),
    momentum_window: timedelta = timedelta(minutes=90),
) -> dict[str, Any]:
    reference_time = now or _utcnow()
    relevant_dispatches = [
        item
        for item in dispatches
        if item.dispatch_status == HeartbeatDispatchStatus.SENT
    ]
    if not relevant_dispatches:
        return {
            "sample_count": 0,
            "quick_reply_rate": 0.0,
            "late_reply_rate": 0.0,
            "no_reply_rate": 0.0,
            "momentum_reopen_rate": 0.0,
            "acknowledgment_only_rate": 0.0,
            "kind_profiles": {},
        }

    user_episodes_by_session: dict[str, list[Episode]] = {}
    for episode in episodes:
        if str(getattr(episode.role, "value", episode.role)) != EpisodeRole.USER.value:
            continue
        session_key = str(episode.session_id or "").strip()
        if not session_key:
            continue
        user_episodes_by_session.setdefault(session_key, []).append(episode)

    for items in user_episodes_by_session.values():
        items.sort(key=lambda item: item.message_timestamp)

    def _classify_quality(dispatch: HeartbeatDispatch, first_reply: Episode | None) -> str:
        if first_reply is None:
            return "no_reply"
        session_key = str(dispatch.session_id or "").strip()
        replies = user_episodes_by_session.get(session_key, [])
        reply_window_end = dispatch.attempted_at + momentum_window
        window_replies = [
            episode
            for episode in replies
            if dispatch.attempted_at < episode.message_timestamp <= reply_window_end
        ]
        content = str(first_reply.content or "").strip().lower()
        tokens = _TOKEN_RE.findall(content)
        short_ack = len(tokens) <= 6 and (set(tokens) & _ACK_MARKERS)
        has_question = "?" in content
        longish_reply = len(tokens) >= 12 or content.count(".") >= 1

        if len(window_replies) >= 2 or has_question or longish_reply:
            return "momentum_reopen"
        if short_ack:
            return "acknowledgment_only"
        return "momentum_reopen"

    def _classify(dispatch: HeartbeatDispatch) -> tuple[str, float | None]:
        session_key = str(dispatch.session_id or "").strip()
        if not session_key:
            return ("no_reply", None)
        replies = user_episodes_by_session.get(session_key, [])
        first_reply = next(
            (episode for episode in replies if episode.message_timestamp > dispatch.attempted_at),
            None,
        )
        if first_reply is None:
            if dispatch.attempted_at <= reference_time - late_reply_window:
                return ("no_reply", None)
            return ("pending", None)
        latency_seconds = max((first_reply.message_timestamp - dispatch.attempted_at).total_seconds(), 0.0)
        if latency_seconds <= quick_reply_window.total_seconds():
            return ("quick_reply", latency_seconds)
        if latency_seconds <= late_reply_window.total_seconds():
            return ("late_reply", latency_seconds)
        return ("no_reply", latency_seconds)

    totals = {"quick_reply": 0, "late_reply": 0, "no_reply": 0}
    quality_totals = {"momentum_reopen": 0, "acknowledgment_only": 0}
    latencies: list[float] = []
    kind_profiles: dict[str, dict[str, Any]] = {}

    for dispatch in relevant_dispatches:
        outcome, latency_seconds = _classify(dispatch)
        if outcome == "pending":
            continue
        totals[outcome] += 1
        if latency_seconds is not None:
            latencies.append(latency_seconds)

        kind_key = str(getattr(dispatch.opportunity_kind, "value", dispatch.opportunity_kind) or "unknown")
        profile = kind_profiles.setdefault(
            kind_key,
            {
                "sample_count": 0,
                "quick_reply_rate": 0.0,
                "late_reply_rate": 0.0,
                "no_reply_rate": 0.0,
                "momentum_reopen_rate": 0.0,
                "acknowledgment_only_rate": 0.0,
                "_totals": {"quick_reply": 0, "late_reply": 0, "no_reply": 0},
                "_quality_totals": {"momentum_reopen": 0, "acknowledgment_only": 0},
            },
        )
        profile["sample_count"] += 1
        profile["_totals"][outcome] += 1
        session_key = str(dispatch.session_id or "").strip()
        replies = user_episodes_by_session.get(session_key, [])
        first_reply = next(
            (episode for episode in replies if episode.message_timestamp > dispatch.attempted_at),
            None,
        )
        quality = _classify_quality(dispatch, first_reply)
        if quality in quality_totals:
            quality_totals[quality] += 1
            profile["_quality_totals"][quality] += 1

    sample_count = sum(totals.values())
    if sample_count == 0:
        return {
            "sample_count": 0,
            "quick_reply_rate": 0.0,
            "late_reply_rate": 0.0,
            "no_reply_rate": 0.0,
            "momentum_reopen_rate": 0.0,
            "acknowledgment_only_rate": 0.0,
            "kind_profiles": {},
        }

    for kind_key, profile in kind_profiles.items():
        kind_total = max(int(profile["sample_count"]), 1)
        profile["quick_reply_rate"] = profile["_totals"]["quick_reply"] / kind_total
        profile["late_reply_rate"] = profile["_totals"]["late_reply"] / kind_total
        profile["no_reply_rate"] = profile["_totals"]["no_reply"] / kind_total
        profile["momentum_reopen_rate"] = profile["_quality_totals"]["momentum_reopen"] / kind_total
        profile["acknowledgment_only_rate"] = profile["_quality_totals"]["acknowledgment_only"] / kind_total
        profile.pop("_totals", None)
        profile.pop("_quality_totals", None)

    return {
        "sample_count": sample_count,
        "quick_reply_rate": totals["quick_reply"] / sample_count,
        "late_reply_rate": totals["late_reply"] / sample_count,
        "no_reply_rate": totals["no_reply"] / sample_count,
        "momentum_reopen_rate": quality_totals["momentum_reopen"] / sample_count,
        "acknowledgment_only_rate": quality_totals["acknowledgment_only"] / sample_count,
        "avg_reply_latency_seconds": (sum(latencies) / len(latencies)) if latencies else None,
        "kind_profiles": kind_profiles,
    }


def _episode_emotion_scores(episode: Episode) -> dict[str, float]:
    if episode.emotions:
        return {str(key): float(value) for key, value in episode.emotions.items()}
    if episode.dominant_emotion:
        return {str(episode.dominant_emotion): float(episode.emotional_intensity or 0.0)}
    return {}


def build_thread_emotion_profile(
    episodes: list[Episode],
    *,
    handoff_tone: str | None = None,
    presence: PresenceState | None = None,
) -> dict[str, Any]:
    if not episodes and not handoff_tone and presence is None:
        return {
            "sample_count": 0,
            "tone_label": "neutral",
            "tension_score": 0.0,
            "warmth_score": 0.0,
            "playfulness_score": 0.0,
            "unresolved_score": 0.0,
            "closure_score": 0.0,
            "is_emotionally_unresolved": False,
        }

    recent = episodes[-8:]
    user_emotions: dict[str, float] = {}
    assistant_emotions: dict[str, float] = {}
    intensities: list[float] = []
    for episode in recent:
        scores = _episode_emotion_scores(episode)
        bucket = user_emotions if str(getattr(episode.role, "value", episode.role)) == EpisodeRole.USER.value else assistant_emotions
        for emotion, score in scores.items():
            bucket[emotion] = bucket.get(emotion, 0.0) + float(score)
        if float(episode.emotional_intensity or 0.0) > 0.0:
            intensities.append(float(episode.emotional_intensity))

    combined: dict[str, float] = {}
    for source in (user_emotions, assistant_emotions):
        for emotion, score in source.items():
            combined[emotion] = combined.get(emotion, 0.0) + score

    total_emotion_mass = max(sum(combined.values()), 1e-6)
    tension_score = sum(combined.get(emotion, 0.0) for emotion in _TENSION_EMOTIONS) / total_emotion_mass
    warmth_score = sum(combined.get(emotion, 0.0) for emotion in _WARM_EMOTIONS) / total_emotion_mass
    playfulness_score = sum(combined.get(emotion, 0.0) for emotion in _PLAYFUL_EMOTIONS) / total_emotion_mass
    avg_intensity = (sum(intensities) / len(intensities)) if intensities else 0.0

    last_episode = recent[-1] if recent else None
    assistant_open_question = bool(
        last_episode is not None
        and str(getattr(last_episode.role, "value", last_episode.role)) == EpisodeRole.ASSISTANT.value
        and "?" in str(last_episode.content or "")
    )
    last_user = next(
        (episode for episode in reversed(recent) if str(getattr(episode.role, "value", episode.role)) == EpisodeRole.USER.value),
        None,
    )
    last_user_question = bool(last_user is not None and "?" in str(last_user.content or ""))

    handoff_lower = str(handoff_tone or "").lower()
    handoff_tense = any(token in handoff_lower for token in ("anger", "fear", "sad", "frustr", "worr", "tense", "stress"))
    handoff_settled = any(token in handoff_lower for token in ("relief", "joy", "trust", "calm", "warm", "settled"))

    unresolved_score = (
        0.55 * tension_score
        + (0.2 if assistant_open_question else 0.0)
        + (0.08 if last_user_question else 0.0)
        + (0.08 if handoff_tense else 0.0)
        + 0.12 * float(getattr(presence, "tension_score", 0.0) or 0.0)
    )
    closure_score = (
        0.45 * warmth_score
        + 0.16 * max(0.0, 1.0 - tension_score)
        + (0.08 if handoff_settled else 0.0)
        + 0.1 * float(getattr(presence, "warmth_score", 0.0) or 0.0)
    )
    unresolved_score = _clamp_unit(unresolved_score)
    closure_score = _clamp_unit(closure_score)

    tone_label = "neutral"
    if unresolved_score >= 0.55 and tension_score >= max(warmth_score, playfulness_score):
        tone_label = "tense"
    elif playfulness_score >= 0.45 and unresolved_score < 0.55:
        tone_label = "playful"
    elif closure_score >= 0.6 and unresolved_score < 0.45:
        tone_label = "settled"
    elif warmth_score >= 0.42 and unresolved_score < 0.5:
        tone_label = "warm"
    elif unresolved_score >= 0.45:
        tone_label = "unresolved"

    dominant_user_emotion = max(user_emotions, key=user_emotions.get) if user_emotions else None
    dominant_assistant_emotion = max(assistant_emotions, key=assistant_emotions.get) if assistant_emotions else None

    return {
        "sample_count": len(recent),
        "tone_label": tone_label,
        "dominant_user_emotion": dominant_user_emotion,
        "dominant_assistant_emotion": dominant_assistant_emotion,
        "avg_intensity": avg_intensity,
        "tension_score": tension_score,
        "warmth_score": warmth_score,
        "playfulness_score": playfulness_score,
        "unresolved_score": unresolved_score,
        "closure_score": closure_score,
        "assistant_open_question": assistant_open_question,
        "last_user_question": last_user_question,
        "handoff_tone": handoff_tone,
        "is_emotionally_unresolved": bool(unresolved_score >= 0.5),
    }


def build_conversation_dropoff_opportunity(
    state: PresenceState | None,
    *,
    now: datetime | None = None,
    min_delay: timedelta = timedelta(minutes=2),
    max_delay: timedelta = timedelta(minutes=15),
) -> HeartbeatOpportunity | None:
    if state is None or not state.user_disappeared_mid_thread or state.active_session_id is None:
        return None
    if state.last_agent_message_at is None:
        return None

    scored_at = now or _utcnow()
    recent_proactive = int(state.recent_proactive_count_24h or 0)
    priority = 0.5
    priority += 0.28 * float(state.conversation_energy or 0.0)
    priority += 0.12 * float(state.warmth_score or 0.0)
    priority -= 0.18 * float(state.tension_score or 0.0)
    priority -= 0.08 * min(recent_proactive, 3)

    annoyance_risk = 0.12
    annoyance_risk += 0.18 * min(recent_proactive, 3)
    annoyance_risk += 0.25 * float(state.tension_score or 0.0)

    summary = (state.current_thread_summary or "").strip()
    if not summary:
        summary = "The user disappeared in the middle of an active conversation."

    return HeartbeatOpportunity(
        agent_namespace=state.agent_namespace,
        opportunity_key=f"dropoff:{state.active_session_id}",
        kind=HeartbeatOpportunityKind.CONVERSATION_DROPOFF,
        status=HeartbeatOpportunityStatus.PENDING,
        session_id=state.active_session_id,
        reason_summary=summary,
        earliest_send_at=state.last_agent_message_at + min_delay,
        latest_useful_at=state.last_agent_message_at + max_delay,
        priority_score=_clamp_unit(priority, floor=0.0, ceiling=1.5),
        annoyance_risk=_clamp_unit(annoyance_risk),
        desired_pressure=_clamp_unit(0.35 + (0.1 * float(state.conversation_energy or 0.0))),
        warmth_target=_clamp_unit(max(float(state.warmth_score or 0.6), 0.62)),
        requires_authored_llm_message=True,
        requires_main_agent_reasoning=False,
        source_refs=[f"session:{state.active_session_id}"],
        cancel_conditions=["user_message", "session_reset", "session_end"],
        created_at=scored_at,
        updated_at=scored_at,
        last_scored_at=scored_at,
    )


def build_promise_followup_opportunity(
    commitment: Commitment,
    *,
    now: datetime | None = None,
    min_age: timedelta = timedelta(hours=18),
    min_delay_after_observation: timedelta = timedelta(hours=6),
    useful_window: timedelta = timedelta(days=7),
) -> HeartbeatOpportunity | None:
    if commitment.status != CommitmentStatus.OPEN:
        return None

    reference_time = now or _utcnow()
    first_committed_at = commitment.first_committed_at
    if (reference_time - first_committed_at) < min_age:
        return None

    earliest_send_at = max(first_committed_at + min_age, commitment.last_observed_at + min_delay_after_observation)
    latest_useful_at = commitment.last_observed_at + useful_window
    summary = str(commitment.statement or "").strip()
    if not summary:
        return None

    source_refs = [f"commitment:{commitment.commitment_key}"]
    source_refs.extend(f"session:{session_id}" for session_id in commitment.source_session_ids)

    return HeartbeatOpportunity(
        agent_namespace=commitment.agent_namespace,
        opportunity_key=f"followup:{commitment.commitment_key}",
        kind=HeartbeatOpportunityKind.PROMISE_FOLLOWUP,
        status=HeartbeatOpportunityStatus.PENDING,
        session_id=commitment.source_session_ids[0] if commitment.source_session_ids else None,
        reason_summary=summary,
        earliest_send_at=earliest_send_at,
        latest_useful_at=latest_useful_at,
        priority_score=0.45 + (0.45 * float(commitment.priority_score or 0.0)) + (0.15 * float(commitment.confidence or 0.0)),
        annoyance_risk=_clamp_unit(0.12 + (0.22 * max(0.0, 1.0 - float(commitment.priority_score or 0.0)))),
        desired_pressure=_clamp_unit(0.28 + (0.3 * float(commitment.priority_score or 0.0))),
        warmth_target=0.72,
        requires_authored_llm_message=True,
        requires_main_agent_reasoning=False,
        source_refs=source_refs,
        cancel_conditions=["user_message", "commitment_completed", "session_reset", "session_end"],
        created_at=reference_time,
        updated_at=reference_time,
        last_scored_at=reference_time,
    )


def build_background_task_completion_opportunity(
    *,
    agent_namespace: str | None,
    session_id: str | None,
    reason_summary: str,
    now: datetime | None = None,
    priority_score: float = 0.8,
    annoyance_risk: float = 0.08,
    desired_pressure: float = 0.42,
    warmth_target: float = 0.76,
    source_refs: list[str] | None = None,
) -> HeartbeatOpportunity:
    reference_time = now or _utcnow()
    normalized_summary = str(reason_summary or "").strip()
    if not normalized_summary:
        raise ValueError("reason_summary is required for background task completion opportunities.")
    normalized_session_id = str(session_id or "").strip()
    if normalized_session_id:
        key_suffix = normalized_session_id
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", normalized_summary.lower()).strip("-")[:32] or "background"
        digest = hashlib.sha1(normalized_summary.encode("utf-8")).hexdigest()[:12]
        key_suffix = f"{slug}:{digest}"

    refs = list(source_refs or [])
    if normalized_session_id:
        refs.append(f"session:{normalized_session_id}")

    return HeartbeatOpportunity(
        agent_namespace=agent_namespace,
        opportunity_key=f"completion:{key_suffix}",
        kind=HeartbeatOpportunityKind.BACKGROUND_TASK_COMPLETION,
        status=HeartbeatOpportunityStatus.PENDING,
        session_id=normalized_session_id or None,
        reason_summary=normalized_summary,
        earliest_send_at=reference_time,
        latest_useful_at=reference_time + timedelta(days=3),
        priority_score=max(0.0, float(priority_score)),
        annoyance_risk=_clamp_unit(annoyance_risk),
        desired_pressure=_clamp_unit(desired_pressure),
        warmth_target=_clamp_unit(warmth_target),
        requires_authored_llm_message=True,
        requires_main_agent_reasoning=False,
        source_refs=refs,
        cancel_conditions=["user_message", "session_reset", "session_end"],
        created_at=reference_time,
        updated_at=reference_time,
        last_scored_at=reference_time,
    )


def is_opportunity_due(opportunity: HeartbeatOpportunity, *, now: datetime | None = None) -> bool:
    reference_time = now or _utcnow()
    if opportunity.status != HeartbeatOpportunityStatus.PENDING:
        return False
    if opportunity.earliest_send_at > reference_time:
        return False
    if opportunity.latest_useful_at is not None and opportunity.latest_useful_at < reference_time:
        return False
    return True


def score_opportunity(
    opportunity: HeartbeatOpportunity,
    *,
    state: PresenceState | None = None,
    now: datetime | None = None,
) -> float:
    reference_time = now or _utcnow()
    score = float(opportunity.priority_score or 0.0)
    score -= 0.6 * float(opportunity.annoyance_risk or 0.0)

    if opportunity.latest_useful_at is not None and reference_time > opportunity.latest_useful_at:
        score -= 0.5
    if opportunity.earliest_send_at <= reference_time:
        score += 0.08

    if state is not None:
        score += 0.22 * float(state.conversation_energy or 0.0)
        score += 0.08 * float(state.warmth_score or 0.0)
        score -= 0.2 * float(state.tension_score or 0.0)
        score -= 0.1 * min(int(state.recent_proactive_count_24h or 0), 3)

    return score


def _recent_dispatch_count(
    dispatches: list[HeartbeatDispatch],
    *,
    opportunity_key: str | None = None,
    opportunity_kind: HeartbeatOpportunityKind | None = None,
    session_id: str | None = None,
    since: datetime,
) -> int:
    count = 0
    normalized_key = str(opportunity_key or "").strip()
    normalized_session_id = str(session_id or "").strip()
    for dispatch in dispatches:
        if dispatch.attempted_at < since:
            continue
        if normalized_key and dispatch.opportunity_key == normalized_key:
            count += 1
            continue
        if opportunity_kind is not None and dispatch.opportunity_kind == opportunity_kind:
            count += 1
            continue
        if normalized_session_id and str(dispatch.session_id or "") == normalized_session_id:
            count += 1
            continue
    return count


def selection_score_opportunity(
    opportunity: HeartbeatOpportunity,
    *,
    state: PresenceState | None = None,
    recent_dispatches: list[HeartbeatDispatch] | None = None,
    rhythm_profile: dict[str, Any] | None = None,
    response_profile: dict[str, Any] | None = None,
    thread_emotion_profile: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> float:
    reference_time = now or _utcnow()
    dispatches = list(recent_dispatches or [])
    score = score_opportunity(opportunity, state=state, now=reference_time)

    same_opportunity_recent = _recent_dispatch_count(
        dispatches,
        opportunity_key=opportunity.opportunity_key,
        since=reference_time - timedelta(hours=6),
    )
    same_kind_recent = _recent_dispatch_count(
        dispatches,
        opportunity_kind=opportunity.kind,
        since=reference_time - timedelta(minutes=45),
    )
    same_session_recent = _recent_dispatch_count(
        dispatches,
        session_id=str(opportunity.session_id or "") or None,
        since=reference_time - timedelta(minutes=15),
    )

    if same_opportunity_recent:
        score -= 0.9 * same_opportunity_recent
    if same_kind_recent:
        score -= 0.22 * same_kind_recent
    if same_session_recent:
        score -= 0.18 * same_session_recent

    if opportunity.kind == HeartbeatOpportunityKind.BACKGROUND_TASK_COMPLETION:
        score += 0.14
    elif opportunity.kind == HeartbeatOpportunityKind.PROMISE_FOLLOWUP:
        score += 0.05
    elif opportunity.kind == HeartbeatOpportunityKind.CONVERSATION_DROPOFF:
        score += 0.06
        if opportunity.latest_useful_at is not None:
            total_window = max(
                (opportunity.latest_useful_at - opportunity.earliest_send_at).total_seconds(),
                1.0,
            )
            elapsed = max((reference_time - opportunity.earliest_send_at).total_seconds(), 0.0)
            freshness = min(elapsed / total_window, 1.0)
            score += 0.08 * max(0.0, 1.0 - freshness)

    if state is not None and int(state.recent_proactive_count_24h or 0) >= 2:
        score -= 0.08 * float(opportunity.desired_pressure or 0.0)

    profile = dict(rhythm_profile or {})
    if int(profile.get("sample_count") or 0) >= 4:
        if bool(profile.get("is_likely_active_hour")):
            score += 0.1 if opportunity.kind == HeartbeatOpportunityKind.CONVERSATION_DROPOFF else 0.06
        if bool(profile.get("is_quiet_hour")):
            quiet_penalty = 0.0
            if opportunity.kind == HeartbeatOpportunityKind.PROMISE_FOLLOWUP:
                quiet_penalty = 0.18
            elif opportunity.kind == HeartbeatOpportunityKind.CONVERSATION_DROPOFF:
                quiet_penalty = 0.14
            elif opportunity.kind == HeartbeatOpportunityKind.BACKGROUND_TASK_COMPLETION:
                quiet_penalty = 0.08
            quiet_penalty += 0.08 * float(opportunity.desired_pressure or 0.0)
            score -= quiet_penalty

    response = dict(response_profile or {})
    kind_profiles = response.get("kind_profiles") if isinstance(response.get("kind_profiles"), dict) else {}
    kind_key = str(getattr(opportunity.kind, "value", opportunity.kind))
    kind_profile = kind_profiles.get(kind_key) if isinstance(kind_profiles, dict) else None
    if isinstance(kind_profile, dict) and int(kind_profile.get("sample_count") or 0) >= 2:
        quick_reply_rate = float(kind_profile.get("quick_reply_rate") or 0.0)
        late_reply_rate = float(kind_profile.get("late_reply_rate") or 0.0)
        no_reply_rate = float(kind_profile.get("no_reply_rate") or 0.0)
        momentum_reopen_rate = float(kind_profile.get("momentum_reopen_rate") or 0.0)
        acknowledgment_only_rate = float(kind_profile.get("acknowledgment_only_rate") or 0.0)
        score += 0.22 * quick_reply_rate
        score += 0.06 * late_reply_rate
        score += 0.18 * momentum_reopen_rate
        score -= 0.28 * no_reply_rate
        if opportunity.kind in {
            HeartbeatOpportunityKind.CONVERSATION_DROPOFF,
            HeartbeatOpportunityKind.PROMISE_FOLLOWUP,
        }:
            score -= 0.14 * acknowledgment_only_rate
        else:
            score -= 0.05 * acknowledgment_only_rate
        if no_reply_rate >= 0.6 and opportunity.kind in {
            HeartbeatOpportunityKind.CONVERSATION_DROPOFF,
            HeartbeatOpportunityKind.PROMISE_FOLLOWUP,
        }:
            score -= 0.08

    thread_profile = dict(thread_emotion_profile or {})
    unresolved_score = float(thread_profile.get("unresolved_score") or 0.0)
    closure_score = float(thread_profile.get("closure_score") or 0.0)
    tension_score = float(thread_profile.get("tension_score") or 0.0)
    playfulness_score = float(thread_profile.get("playfulness_score") or 0.0)
    tone_label = str(thread_profile.get("tone_label") or "").strip().lower()

    if unresolved_score >= 0.5:
        if opportunity.kind == HeartbeatOpportunityKind.BACKGROUND_TASK_COMPLETION:
            score += 0.12
        elif opportunity.kind == HeartbeatOpportunityKind.CONVERSATION_DROPOFF:
            score += 0.08
        elif opportunity.kind == HeartbeatOpportunityKind.PROMISE_FOLLOWUP:
            score -= 0.04
        score -= 0.12 * tension_score * float(opportunity.desired_pressure or 0.0)
    if closure_score >= 0.6 and opportunity.kind in {
        HeartbeatOpportunityKind.CONVERSATION_DROPOFF,
        HeartbeatOpportunityKind.PROMISE_FOLLOWUP,
    }:
        score -= 0.12
    if tone_label == "playful" and opportunity.kind == HeartbeatOpportunityKind.CONVERSATION_DROPOFF:
        score += 0.05 + (0.04 * playfulness_score)

    return score


def rank_due_opportunities(
    opportunities: list[HeartbeatOpportunity],
    *,
    state: PresenceState | None = None,
    recent_dispatches: list[HeartbeatDispatch] | None = None,
    rhythm_profile: dict[str, Any] | None = None,
    response_profile: dict[str, Any] | None = None,
    thread_emotion_profiles: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    reference_time = now or _utcnow()
    ranked: list[dict[str, object]] = []
    for opportunity in opportunities:
        if not is_opportunity_due(opportunity, now=reference_time):
            continue
        thread_profile = {}
        if opportunity.session_id is not None and isinstance(thread_emotion_profiles, dict):
            thread_profile = dict(thread_emotion_profiles.get(str(opportunity.session_id), {}) or {})
        base_score = score_opportunity(opportunity, state=state, now=reference_time)
        selection_score = selection_score_opportunity(
            opportunity,
            state=state,
            recent_dispatches=recent_dispatches,
            rhythm_profile=rhythm_profile,
            response_profile=response_profile,
            thread_emotion_profile=thread_profile,
            now=reference_time,
        )
        ranked.append(
            {
                **opportunity.model_dump(mode="json"),
                "base_send_score": base_score,
                "send_score": selection_score,
                "rhythm_profile": dict(rhythm_profile or {}),
                "response_profile": dict(response_profile or {}),
                "thread_emotion_profile": thread_profile,
            }
        )
    ranked.sort(
        key=lambda item: (
            float(item.get("send_score") or 0.0),
            float(item.get("base_send_score") or 0.0),
            float(item.get("priority_score") or 0.0),
        ),
        reverse=True,
    )
    return ranked
