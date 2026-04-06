from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from memory.client import MemoryClient
from memory.embedding import MockEmbeddingProvider, OpenAIEmbeddingProvider, truncate_embedding
from memory.emotions import EmotionAnalyzer
from memory.models import (
    ActiveState,
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    Commitment,
    Correction,
    DecisionOutcome,
    Directive,
    Episode,
    EpisodeRole,
    Fact,
    FactHistory,
    HeartbeatDispatch,
    HeartbeatDispatchStatus,
    HeartbeatOpportunity,
    HeartbeatOpportunityStatus,
    Pattern,
    PresenceState,
    Session,
    SessionHandoff,
    TimelineEvent,
)
from memory.heartbeat import build_conversation_dropoff_key
from memory.models import Reflection


class InMemoryTransport:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.episodes: list[Episode] = []
        self.facts: dict[str, Fact] = {}
        self.history: list[FactHistory] = []
        self.touched_facts: list[str] = []
        self.active_state: list[object] = []
        self.directives: list[Directive] = []
        self.timeline_events: list[TimelineEvent] = []
        self.decision_outcomes: list[DecisionOutcome] = []
        self.patterns: list[Pattern] = []
        self.reflections: list[Reflection] = []
        self.commitments: list[Commitment] = []
        self.corrections: list[Correction] = []
        self.session_handoffs: list[SessionHandoff] = []
        self.presence_state: PresenceState | None = None
        self.background_jobs: list[BackgroundJob] = []
        self.heartbeat_opportunities: list[HeartbeatOpportunity] = []
        self.heartbeat_dispatches: list[HeartbeatDispatch] = []

    async def insert_session(self, session: Session) -> Session:
        if session.id is None:
            session = session.model_copy(update={"id": uuid4()})
        self.sessions[str(session.id)] = session
        return session

    async def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    async def get_session_by_legacy_id(self, legacy_session_id: str) -> Session | None:
        for session in self.sessions.values():
            if session.legacy_session_id == legacy_session_id:
                return session
        return None

    async def list_sessions(
        self,
        limit: int = 20,
        platform: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Session]:
        _ = agent_namespace
        sessions = list(self.sessions.values())
        if platform is not None:
            sessions = [session for session in sessions if session.platform.value == platform]
        sessions.sort(key=lambda session: session.started_at, reverse=True)
        return sessions[:limit]

    async def list_episodes_for_session(self, session_id: str, limit: int | None = None) -> list[Episode]:
        episodes = [episode for episode in self.episodes if str(episode.session_id) == session_id]
        episodes.sort(key=lambda episode: episode.message_timestamp)
        if limit is not None:
            episodes = episodes[:limit]
        return episodes

    async def update_session(self, session_id: str, updates: dict) -> Session:
        session = self.sessions[session_id]
        normalized_updates = dict(updates)
        if "model_config" in normalized_updates and "session_model_config" not in normalized_updates:
            normalized_updates["session_model_config"] = normalized_updates.pop("model_config")
        updated = session.model_copy(update=normalized_updates)
        self.sessions[session_id] = updated
        return updated

    async def delete_session(self, session_id: str) -> bool:
        self.sessions.pop(session_id, None)
        self.episodes = [episode for episode in self.episodes if str(episode.session_id) != session_id]
        return True

    async def insert_episode(self, episode: Episode) -> Episode:
        if episode.id is None:
            episode = episode.model_copy(update={"id": uuid4()})
        self.episodes.append(episode)
        return episode

    async def insert_fact(self, fact: Fact) -> Fact:
        if fact.id is None:
            fact = fact.model_copy(update={"id": uuid4()})
        self.facts[str(fact.id)] = fact
        return fact

    async def get_fact(self, fact_id: str) -> Fact | None:
        return self.facts.get(fact_id)

    async def update_fact(self, fact_id: str, updates: dict) -> Fact:
        fact = self.facts[fact_id]
        updated = fact.model_copy(update=updates)
        self.facts[fact_id] = updated
        return updated

    async def deactivate_fact(self, fact_id: str, replaced_by: str | None = None) -> None:
        fact = self.facts[fact_id]
        self.facts[fact_id] = fact.model_copy(update={"is_active": False, "replaced_by": replaced_by})

    async def touch_fact(self, fact_id: str) -> None:
        self.touched_facts.append(fact_id)

    async def search_episodes(
        self,
        query: str,
        limit: int = 20,
        platform: str | None = None,
        days_back: int = 30,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        _ = (query, platform, days_back, agent_namespace)
        return self.episodes[:limit]

    async def list_recent_episodes(
        self,
        limit: int = 5,
        platform: str | None = None,
        exclude_session_id: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        _ = agent_namespace
        episodes = list(self.episodes)
        if platform is not None:
            episodes = [episode for episode in episodes if episode.platform.value == platform]
        if exclude_session_id is not None:
            episodes = [episode for episode in episodes if str(episode.session_id) != exclude_session_id]
        episodes.sort(key=lambda episode: episode.message_timestamp, reverse=True)
        return episodes[:limit]

    async def search_facts(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        agent_namespace: str | None = None,
    ) -> list[Fact]:
        _ = agent_namespace
        facts = [fact for fact in self.facts.values() if fact.is_active]
        if category:
            facts = [fact for fact in facts if fact.category.value == category]
        if tags:
            facts = [fact for fact in facts if all(tag in fact.tags for tag in tags)]
        return facts[:limit]

    async def insert_fact_history(self, history: FactHistory) -> FactHistory:
        if history.id is None:
            history = history.model_copy(update={"id": uuid4()})
        self.history.append(history)
        return history

    async def upsert_active_state(self, state):
        self.active_state = [existing for existing in self.active_state if getattr(existing, "state_key", None) != state.state_key]
        self.active_state.append(state)
        return state

    async def list_active_state(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        _ = (agent_namespace, statuses)
        return list(self.active_state)[:limit]

    async def upsert_directive(self, directive: Directive) -> Directive:
        self.directives = [existing for existing in self.directives if existing.directive_key != directive.directive_key]
        self.directives.append(directive)
        return directive

    async def list_directives(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        _ = (agent_namespace, statuses)
        return list(self.directives)[:limit]

    async def upsert_timeline_event(self, event: TimelineEvent) -> TimelineEvent:
        self.timeline_events = [existing for existing in self.timeline_events if existing.event_key != event.event_key]
        self.timeline_events.append(event)
        return event

    async def list_timeline_events(self, limit: int = 10, agent_namespace: str | None = None):
        _ = agent_namespace
        events = sorted(self.timeline_events, key=lambda event: event.event_time, reverse=True)
        return events[:limit]

    async def upsert_decision_outcome(self, outcome: DecisionOutcome) -> DecisionOutcome:
        self.decision_outcomes = [existing for existing in self.decision_outcomes if existing.outcome_key != outcome.outcome_key]
        self.decision_outcomes.append(outcome)
        return outcome

    async def list_decision_outcomes(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        _ = (agent_namespace, statuses)
        outcomes = sorted(self.decision_outcomes, key=lambda outcome: outcome.event_time, reverse=True)
        return outcomes[:limit]

    async def upsert_pattern(self, pattern: Pattern) -> Pattern:
        self.patterns = [existing for existing in self.patterns if existing.pattern_key != pattern.pattern_key]
        self.patterns.append(pattern)
        return pattern

    async def list_patterns(self, limit: int = 10, agent_namespace: str | None = None, pattern_types: list[str] | None = None):
        _ = (agent_namespace, pattern_types)
        patterns = sorted(self.patterns, key=lambda pattern: (pattern.impact_score, pattern.last_observed_at), reverse=True)
        return patterns[:limit]

    async def upsert_reflection(self, reflection: Reflection) -> Reflection:
        self.reflections = [existing for existing in self.reflections if existing.reflection_key != reflection.reflection_key]
        self.reflections.append(reflection)
        return reflection

    async def list_reflections(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        _ = (agent_namespace, statuses)
        reflections = sorted(self.reflections, key=lambda reflection: (reflection.confidence, reflection.last_observed_at), reverse=True)
        return reflections[:limit]

    async def delete_reflection(self, reflection_key: str, *, agent_namespace: str | None = None) -> bool:
        _ = agent_namespace
        before = len(self.reflections)
        self.reflections = [existing for existing in self.reflections if existing.reflection_key != reflection_key]
        return len(self.reflections) < before

    async def upsert_commitment(self, commitment: Commitment) -> Commitment:
        self.commitments = [existing for existing in self.commitments if existing.commitment_key != commitment.commitment_key]
        self.commitments.append(commitment)
        return commitment

    async def list_commitments(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        _ = (agent_namespace, statuses)
        commitments = sorted(self.commitments, key=lambda item: (item.priority_score, item.last_observed_at), reverse=True)
        return commitments[:limit]

    async def upsert_correction(self, correction: Correction) -> Correction:
        self.corrections = [existing for existing in self.corrections if existing.correction_key != correction.correction_key]
        self.corrections.append(correction)
        return correction

    async def list_corrections(self, limit: int = 10, agent_namespace: str | None = None, active_only: bool = True):
        _ = agent_namespace
        corrections = list(self.corrections)
        if active_only:
            corrections = [item for item in corrections if item.active]
        corrections.sort(key=lambda item: item.last_observed_at, reverse=True)
        return corrections[:limit]

    async def upsert_session_handoff(self, handoff: SessionHandoff) -> SessionHandoff:
        self.session_handoffs = [
            existing
            for existing in self.session_handoffs
            if existing.handoff_key != handoff.handoff_key
        ]
        self.session_handoffs.append(handoff)
        return handoff

    async def list_session_handoffs(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        exclude_session_id: str | None = None,
    ) -> list[SessionHandoff]:
        _ = agent_namespace
        handoffs = sorted(self.session_handoffs, key=lambda item: item.last_observed_at, reverse=True)
        if exclude_session_id is not None:
            handoffs = [item for item in handoffs if str(item.session_id) != str(exclude_session_id)]
        return handoffs[:limit]

    async def health_check(self) -> bool:
        return True

    async def upsert_presence_state(self, state: PresenceState) -> PresenceState:
        self.presence_state = state
        return state

    async def get_presence_state(self, agent_namespace: str | None = None) -> PresenceState | None:
        _ = agent_namespace
        return self.presence_state

    async def upsert_background_job(self, job: BackgroundJob) -> BackgroundJob:
        if job.id is None:
            job = job.model_copy(update={"id": uuid4()})
        self.background_jobs = [existing for existing in self.background_jobs if existing.job_key != job.job_key]
        self.background_jobs.append(job)
        self.background_jobs.sort(key=lambda item: item.updated_at or item.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return job

    async def list_background_jobs(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        session_id: str | None = None,
        job_key: str | None = None,
    ) -> list[BackgroundJob]:
        _ = agent_namespace
        items = list(self.background_jobs)
        if statuses:
            allowed = {str(item) for item in statuses}
            items = [item for item in items if item.status.value in allowed]
        if session_id is not None:
            items = [item for item in items if str(item.session_id or "") == str(session_id)]
        if job_key is not None:
            items = [item for item in items if item.job_key == job_key]
        items.sort(key=lambda item: item.updated_at or item.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items[:limit]

    async def transition_background_job(
        self,
        job_key: str,
        *,
        status: str,
        agent_namespace: str | None = None,
        progress_note: str | None = None,
        completion_summary: str | None = None,
        result_refs: list[str] | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> BackgroundJob | None:
        _ = agent_namespace
        changed = None
        items: list[BackgroundJob] = []
        for item in self.background_jobs:
            if item.job_key == job_key:
                patch: dict[str, object] = {
                    "status": BackgroundJobStatus(status),
                    "updated_at": updated_at or datetime.now(timezone.utc),
                }
                if progress_note is not None:
                    patch["progress_note"] = progress_note
                    patch["last_progress_at"] = updated_at or datetime.now(timezone.utc)
                if completion_summary is not None:
                    patch["completion_summary"] = completion_summary
                if result_refs is not None:
                    patch["result_refs"] = list(result_refs)
                if started_at is not None:
                    patch["started_at"] = started_at
                if completed_at is not None:
                    patch["completed_at"] = completed_at
                item = item.model_copy(update=patch)
                changed = item
            items.append(item)
        self.background_jobs = items
        return changed

    async def upsert_heartbeat_opportunity(self, opportunity: HeartbeatOpportunity) -> HeartbeatOpportunity:
        self.heartbeat_opportunities = [
            existing
            for existing in self.heartbeat_opportunities
            if existing.opportunity_key != opportunity.opportunity_key
        ]
        self.heartbeat_opportunities.append(opportunity)
        return opportunity

    async def list_heartbeat_opportunities(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        kinds: list[str] | None = None,
        session_id: str | None = None,
    ) -> list[HeartbeatOpportunity]:
        _ = agent_namespace
        items = list(self.heartbeat_opportunities)
        if statuses:
            allowed = {str(item) for item in statuses}
            items = [item for item in items if item.status.value in allowed]
        if kinds:
            allowed_kinds = {str(item) for item in kinds}
            items = [item for item in items if item.kind.value in allowed_kinds]
        if session_id is not None:
            items = [item for item in items if str(item.session_id or "") == str(session_id)]
        items.sort(key=lambda item: (item.priority_score, item.earliest_send_at), reverse=True)
        return items[:limit]

    async def insert_heartbeat_dispatch(self, dispatch: HeartbeatDispatch) -> HeartbeatDispatch:
        if dispatch.id is None:
            dispatch = dispatch.model_copy(update={"id": uuid4()})
        self.heartbeat_dispatches.append(dispatch)
        self.heartbeat_dispatches.sort(key=lambda item: item.attempted_at, reverse=True)
        return dispatch

    async def list_heartbeat_dispatches(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        opportunity_key: str | None = None,
        session_id: str | None = None,
        since: datetime | None = None,
    ) -> list[HeartbeatDispatch]:
        _ = agent_namespace
        items = list(self.heartbeat_dispatches)
        if statuses:
            allowed = {str(item) for item in statuses}
            items = [item for item in items if item.dispatch_status.value in allowed]
        if opportunity_key is not None:
            items = [item for item in items if item.opportunity_key == opportunity_key]
        if session_id is not None:
            items = [item for item in items if str(item.session_id or "") == str(session_id)]
        if since is not None:
            items = [item for item in items if item.attempted_at >= since]
        items.sort(key=lambda item: item.attempted_at, reverse=True)
        return items[:limit]

    async def cancel_heartbeat_opportunity(
        self,
        opportunity_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        _ = agent_namespace
        cancelled = False
        updated: list[HeartbeatOpportunity] = []
        for item in self.heartbeat_opportunities:
            if item.opportunity_key == opportunity_key:
                item = item.model_copy(update={"status": HeartbeatOpportunityStatus.CANCELLED})
                cancelled = True
            updated.append(item)
        self.heartbeat_opportunities = updated
        return cancelled

    async def transition_heartbeat_opportunity(
        self,
        opportunity_key: str,
        *,
        status: str,
        agent_namespace: str | None = None,
    ) -> bool:
        _ = agent_namespace
        changed = False
        updated: list[HeartbeatOpportunity] = []
        for item in self.heartbeat_opportunities:
            if item.opportunity_key == opportunity_key:
                item = item.model_copy(update={"status": HeartbeatOpportunityStatus(status)})
                changed = True
            updated.append(item)
        self.heartbeat_opportunities = updated
        return changed


class SlowSessionTransport(InMemoryTransport):
    async def get_session(self, session_id: str) -> Session | None:
        session = await super().get_session(session_id)
        await asyncio.sleep(0.01)
        return session


@pytest.mark.asyncio
async def test_client_tracks_session_counts_and_history() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())

    session = await client.start_session()
    await client.store_message(str(session.id), "user", "I am excited and glad today.")
    fact = await client.add_fact("User likes tea.", "preference", tags=["drink"])
    updated = await client.update_fact(str(fact.id), "User likes green tea.", reason="refined preference")
    await client.delete_fact(str(updated.id), reason="stale")

    stored_session = transport.sessions[str(session.id)]
    assert stored_session.message_count == 1
    assert stored_session.user_message_count == 1
    assert stored_session.dominant_emotion_counts
    assert len(transport.history) == 3
    assert transport.facts[str(updated.id)].is_active is False


@pytest.mark.asyncio
async def test_update_session_stats_serializes_concurrent_updates() -> None:
    transport = SlowSessionTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session()
    now = datetime.now(timezone.utc)

    user_episode = Episode(
        id=uuid4(),
        session_id=session.id,
        role=EpisodeRole.USER,
        content="I am excited.",
        content_hash="user-episode",
        platform=session.platform,
        embedding=None,
        emotions={"joy": 0.9},
        dominant_emotion="joy",
        emotional_intensity=0.9,
        message_timestamp=now,
        message_metadata={},
    )
    assistant_episode = Episode(
        id=uuid4(),
        session_id=session.id,
        role=EpisodeRole.ASSISTANT,
        content="Let's ship carefully.",
        content_hash="assistant-episode",
        platform=session.platform,
        embedding=None,
        emotions={"trust": 0.6},
        dominant_emotion="trust",
        emotional_intensity=0.6,
        message_timestamp=now,
        message_metadata={},
    )

    await asyncio.gather(
        client._update_session_stats(str(session.id), [user_episode]),
        client._update_session_stats(str(session.id), [assistant_episode]),
    )

    stored_session = transport.sessions[str(session.id)]
    assert stored_session.message_count == 2
    assert stored_session.user_message_count == 1
    assert stored_session.dominant_emotion_counts["joy"] == 1
    assert stored_session.dominant_emotion_counts["trust"] == 1


@pytest.mark.asyncio
async def test_store_message_rejects_tool_and_system_roles() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())

    session = await client.start_session()

    with pytest.raises(ValueError, match="only accepts user/assistant roles"):
        await client.store_message(str(session.id), "tool", "tool call output blob")

    with pytest.raises(ValueError, match="only accepts user/assistant roles"):
        await client.store_message(str(session.id), "system", "internal system note")

    stored_session = transport.sessions[str(session.id)]
    assert stored_session.message_count == 0
    assert stored_session.user_message_count == 0


@pytest.mark.asyncio
async def test_enrich_context_formats_facts_and_episodes() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session()
    await client.store_message(str(session.id), "user", "I am nervous but hopeful about the launch.")
    fact = await client.add_fact("The launch is on Friday.", "project", tags=["launch"])

    context = await client.enrich_context("launch details", active_session_id=str(session.id))

    assert "Memory guidance:" in context
    assert "Relevant facts:" in context
    assert "Relevant prior conversations:" in context
    assert "Recent cross-session continuity:" in context


@pytest.mark.asyncio
async def test_refresh_session_handoff_persists_latest_baton() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="telegram", agent_namespace="main")

    await client.store_message(
        str(session.id),
        "user",
        "The bot is still too slow after restart, so please watch the next real message closely.",
        platform="telegram",
        agent_namespace="main",
    )
    await client.store_message(
        str(session.id),
        "assistant",
        "I moved the restart path to the warm bridge worker, so next we should verify a real reply stays fast.",
        platform="telegram",
        agent_namespace="main",
    )

    updated_session = await transport.update_session(
        str(session.id),
        {
            "summary": "Stabilized Telegram continuity and reduced reply latency after restart.",
            "dominant_emotions": ["frustration", "relief"],
        },
    )

    handoff = await client.refresh_session_handoff(str(updated_session.id), agent_namespace="main")

    assert handoff is not None
    assert handoff.last_thread == "Stabilized Telegram continuity and reduced reply latency after restart."
    assert handoff.assistant_context is not None
    assert "warm bridge worker" in handoff.assistant_context
    assert handoff.emotional_tone == "frustration, relief"
    assert transport.session_handoffs[0].handoff_key == f"auto:handoff:{updated_session.id}"


@pytest.mark.asyncio
async def test_record_presence_event_tracks_user_and_assistant_activity() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="telegram", agent_namespace="main")
    now = datetime.now(timezone.utc)

    user_state = await client.record_presence_event(
        role="user",
        session_id=str(session.id),
        platform="telegram",
        occurred_at=now,
        agent_namespace="main",
        thread_summary="Midway through a debugging thread.",
    )
    assistant_state = await client.record_presence_event(
        role="assistant",
        session_id=str(session.id),
        platform="telegram",
        occurred_at=now + timedelta(seconds=30),
        agent_namespace="main",
    )

    assert user_state.last_user_message_at == now
    assert assistant_state.last_agent_message_at == now + timedelta(seconds=30)
    assert assistant_state.current_thread_summary == "Midway through a debugging thread."
    assert assistant_state.active_platform.value == "telegram"


@pytest.mark.asyncio
async def test_ensure_conversation_dropoff_opportunity_creates_pending_record() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="telegram", agent_namespace="main")
    now = datetime.now(timezone.utc)

    await client.record_presence_event(
        role="assistant",
        session_id=str(session.id),
        platform="telegram",
        occurred_at=now - timedelta(minutes=6),
        agent_namespace="main",
        thread_summary="The user disappeared mid-debug after I asked a concrete question.",
    )

    opportunity = await client.ensure_conversation_dropoff_opportunity(
        agent_namespace="main",
        now=now,
        min_delay=timedelta(minutes=2),
        max_delay=timedelta(minutes=20),
    )

    assert opportunity is not None
    assert opportunity.kind.value == "conversation_dropoff"
    assert opportunity.opportunity_key == build_conversation_dropoff_key(session.id, now - timedelta(minutes=6))
    assert transport.presence_state is not None
    assert transport.presence_state.user_disappeared_mid_thread is True


@pytest.mark.asyncio
async def test_user_return_cancels_dropoff_opportunity() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="telegram", agent_namespace="main")
    now = datetime.now(timezone.utc)

    await client.record_presence_event(
        role="assistant",
        session_id=str(session.id),
        platform="telegram",
        occurred_at=now - timedelta(minutes=5),
        agent_namespace="main",
    )
    await client.ensure_conversation_dropoff_opportunity(
        agent_namespace="main",
        now=now,
        min_delay=timedelta(minutes=2),
    )

    cancelled_state = await client.record_presence_event(
        role="user",
        session_id=str(session.id),
        platform="telegram",
        occurred_at=now + timedelta(seconds=5),
        agent_namespace="main",
    )

    assert cancelled_state.user_disappeared_mid_thread is False
    assert any(item.status.value == "cancelled" for item in transport.heartbeat_opportunities)


@pytest.mark.asyncio
async def test_ensure_promise_followup_opportunities_derives_from_open_commitments() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    now = datetime.now(timezone.utc)

    await client.add_commitment(
        kind="follow_up",
        statement="Check whether the Telegram migration cleanup was actually finished.",
        commitment_key="migration-cleanup-followup",
        first_committed_at=now - timedelta(days=2),
        last_observed_at=now - timedelta(days=1),
        priority_score=0.81,
        confidence=0.9,
        agent_namespace="main",
    )

    opportunities = await client.ensure_promise_followup_opportunities(
        agent_namespace="main",
        now=now,
    )

    assert len(opportunities) == 1
    assert opportunities[0].kind.value == "promise_followup"
    assert opportunities[0].opportunity_key == "followup:migration-cleanup-followup"


@pytest.mark.asyncio
async def test_ensure_promise_followup_opportunities_does_not_recreate_suppressed_record() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    now = datetime.now(timezone.utc)

    await client.add_commitment(
        kind="follow_up",
        statement="Check whether the Gmail reconnect was finished.",
        commitment_key="gmail-reconnect-followup",
        first_committed_at=now - timedelta(days=2),
        last_observed_at=now - timedelta(days=1),
        priority_score=0.81,
        confidence=0.9,
        agent_namespace="main",
    )
    suppressed = HeartbeatOpportunity(
        id=uuid4(),
        agent_namespace="main",
        opportunity_key="followup:gmail-reconnect-followup",
        kind="promise_followup",
        status="suppressed",
        session_id=None,
        reason_summary="Still need to check the Gmail reconnect.",
        earliest_send_at=now - timedelta(hours=1),
        latest_useful_at=now + timedelta(days=2),
        priority_score=0.8,
        annoyance_risk=0.2,
        desired_pressure=0.4,
        warmth_target=0.6,
        requires_authored_llm_message=True,
        requires_main_agent_reasoning=False,
        source_refs=[],
        cancel_conditions=[],
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=1),
        last_scored_at=now - timedelta(hours=1),
    )
    transport.heartbeat_opportunities.append(suppressed)

    opportunities = await client.ensure_promise_followup_opportunities(
        agent_namespace="main",
        now=now,
    )

    assert len(opportunities) == 1
    assert opportunities[0].id == suppressed.id
    assert len(transport.heartbeat_opportunities) == 1


@pytest.mark.asyncio
async def test_ensure_conversation_dropoff_opportunity_does_not_recreate_suppressed_record() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="telegram", agent_namespace="main")
    now = datetime.now(timezone.utc)

    await client.record_presence_event(
        role="assistant",
        session_id=str(session.id),
        platform="telegram",
        occurred_at=now - timedelta(minutes=6),
        agent_namespace="main",
    )
    suppressed = HeartbeatOpportunity(
        id=uuid4(),
        agent_namespace="main",
        opportunity_key=build_conversation_dropoff_key(session.id, now - timedelta(minutes=6)),
        kind="conversation_dropoff",
        status="suppressed",
        session_id=session.id,
        reason_summary="The user vanished mid-thread.",
        earliest_send_at=now - timedelta(minutes=3),
        latest_useful_at=now + timedelta(minutes=5),
        priority_score=0.8,
        annoyance_risk=0.1,
        desired_pressure=0.4,
        warmth_target=0.6,
        requires_authored_llm_message=True,
        requires_main_agent_reasoning=False,
        source_refs=[],
        cancel_conditions=[],
        created_at=now - timedelta(minutes=4),
        updated_at=now - timedelta(minutes=3),
        last_scored_at=now - timedelta(minutes=3),
    )
    transport.heartbeat_opportunities.append(suppressed)

    opportunity = await client.ensure_conversation_dropoff_opportunity(
        agent_namespace="main",
        now=now,
        min_delay=timedelta(minutes=2),
        max_delay=timedelta(minutes=20),
    )

    assert opportunity is not None
    assert opportunity.id == suppressed.id
    assert len(transport.heartbeat_opportunities) == 1


@pytest.mark.asyncio
async def test_create_background_task_completion_opportunity_persists_record() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="telegram", agent_namespace="main")
    now = datetime.now(timezone.utc)

    created = await client.create_background_task_completion_opportunity(
        session_id=str(session.id),
        reason_summary="Finished tracing the reply latency issue and isolated the actual bottleneck.",
        agent_namespace="main",
        now=now,
        source_refs=["job:bg-42"],
    )

    assert created.kind.value == "background_task_completion"
    assert created.session_id == session.id
    assert any(item.opportunity_key == created.opportunity_key for item in transport.heartbeat_opportunities)


@pytest.mark.asyncio
async def test_complete_background_job_persists_job_and_creates_completion_opportunity() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="telegram", agent_namespace="main")
    now = datetime.now(timezone.utc)

    job = await client.create_background_job(
        title="Trace heartbeat dispatch path",
        session_id=str(session.id),
        agent_namespace="main",
        kind=BackgroundJobKind.TRACE.value,
        source_refs=["issue:dispatch"],
        now=now - timedelta(minutes=8),
    )

    completed = await client.complete_background_job(
        job.job_key,
        agent_namespace="main",
        completion_summary="Finished tracing the dispatch path and found the target-resolution bug.",
        result_refs=["file:gateway/heartbeat/dispatch.py"],
        now=now,
    )

    stored_job = completed["job"]
    opportunity = completed["opportunity"]
    assert stored_job is not None
    assert stored_job.status == BackgroundJobStatus.COMPLETED
    assert stored_job.completion_summary == "Finished tracing the dispatch path and found the target-resolution bug."
    assert opportunity is not None
    assert opportunity.kind.value == "background_task_completion"
    assert f"job:{job.job_key}" in opportunity.source_refs


@pytest.mark.asyncio
async def test_build_heartbeat_context_returns_memory_packet() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="telegram", agent_namespace="main")
    now = datetime.now(timezone.utc)

    await client.store_message(
        str(session.id),
        "user",
        "if i disappear, keep pushing on the gateway bug and tell me what you find",
        platform="telegram",
        agent_namespace="main",
    )
    await client.store_message(
        str(session.id),
        "assistant",
        "i think the real bug is in the dispatch handoff path, want me to trace it end to end?",
        platform="telegram",
        agent_namespace="main",
    )
    await client.add_directive(
        kind="communication",
        content="Be direct and concrete when following up.",
        directive_key="heartbeat:direct",
        status="active",
        confidence=0.9,
        priority_score=0.82,
        last_observed_at=now,
        agent_namespace="main",
    )
    await client.add_correction(
        kind="interpretation_rejection",
        statement="Do not use vague wellness-style check-ins.",
        correction_key="heartbeat:no-wellness-bot",
        first_observed_at=now - timedelta(days=1),
        last_observed_at=now,
        active=True,
        confidence=0.94,
        agent_namespace="main",
    )

    await client.record_presence_event(
        role="assistant",
        session_id=str(session.id),
        platform="telegram",
        occurred_at=now - timedelta(minutes=5),
        agent_namespace="main",
        thread_summary="The user vanished after I asked whether I should keep debugging.",
    )
    opportunity = await client.ensure_conversation_dropoff_opportunity(
        agent_namespace="main",
        now=now,
        min_delay=timedelta(minutes=2),
    )
    await client.record_heartbeat_dispatch(
        opportunity_key=opportunity.opportunity_key if opportunity else "",
        dispatch_status="suppressed",
        agent_namespace="main",
        opportunity_kind="conversation_dropoff",
        session_id=str(session.id),
        target="telegram:123",
        attempted_at=now - timedelta(minutes=1),
    )

    packet = await client.build_heartbeat_context(
        opportunity_key=opportunity.opportunity_key if opportunity else "",
        agent_namespace="main",
    )

    assert packet is not None
    assert packet["opportunity"]["opportunity_key"] == opportunity.opportunity_key
    assert packet["presence_state"]["active_session_id"] == str(session.id)
    assert packet["session"]["session_id"] == str(session.id)
    assert packet["recent_dispatches"][0]["dispatch_status"] == "suppressed"
    assert "response_profile" in packet
    assert "thread_emotion_profile" in packet
    assert packet["recent_messages"][-1]["role"] == "assistant"
    assert packet["communication_constraints"]["non_negotiables"]
    assert packet["authoring_brief"]["intent"] == "resume unfinished thread"
    assert "thread_emotion_profile" in packet["authoring_brief"]


@pytest.mark.asyncio
async def test_build_heartbeat_context_includes_linked_background_job() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="telegram", agent_namespace="main")
    now = datetime.now(timezone.utc)

    job = await client.create_background_job(
        title="Trace gateway heartbeat route",
        session_id=str(session.id),
        agent_namespace="main",
        kind=BackgroundJobKind.TRACE.value,
        description="Follow the route resolution path end to end.",
        now=now - timedelta(minutes=12),
    )
    await client.transition_background_job(
        job.job_key,
        status=BackgroundJobStatus.RUNNING.value,
        agent_namespace="main",
        progress_note="Found the route lookup seam.",
        started_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=10),
    )
    completed = await client.complete_background_job(
        job.job_key,
        agent_namespace="main",
        completion_summary="Finished the route trace and isolated the missing target issue.",
        result_refs=["file:gateway/heartbeat/dispatch.py"],
        now=now,
    )

    opportunity = completed["opportunity"]
    packet = await client.build_heartbeat_context(
        opportunity_key=opportunity.opportunity_key if opportunity else "",
        agent_namespace="main",
    )

    assert packet is not None
    assert packet["background_job"]["job_key"] == job.job_key
    assert packet["background_job"]["status"] == "completed"
    assert packet["background_jobs"][0]["job_key"] == job.job_key
    assert packet["authoring_brief"]["linked_background_job"]["job_key"] == job.job_key


@pytest.mark.asyncio
async def test_record_heartbeat_dispatch_persists_dispatch_history() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="telegram", agent_namespace="main")
    now = datetime.now(timezone.utc)

    dispatch = await client.record_heartbeat_dispatch(
        opportunity_key=f"dropoff:{session.id}",
        dispatch_status="sent",
        agent_namespace="main",
        opportunity_kind="conversation_dropoff",
        session_id=str(session.id),
        target="telegram:12345",
        send_score=0.83,
        response_preview="hey, where'd you vanish to?",
        attempted_at=now,
    )

    history = await client.list_heartbeat_dispatches(
        agent_namespace="main",
        session_id=str(session.id),
    )

    assert dispatch.dispatch_status == HeartbeatDispatchStatus.SENT
    assert history[0].target == "telegram:12345"


@pytest.mark.asyncio
async def test_heartbeat_dispatch_cooldown_blocks_recent_same_session_send() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="telegram", agent_namespace="main")
    now = datetime.now(timezone.utc)

    await client.record_heartbeat_dispatch(
        opportunity_key=f"dropoff:{session.id}",
        dispatch_status="sent",
        agent_namespace="main",
        opportunity_kind="conversation_dropoff",
        session_id=str(session.id),
        target="telegram:12345",
        attempted_at=now - timedelta(minutes=4),
    )

    cooldown = await client.heartbeat_dispatch_cooldown(
        opportunity_key=f"followup:{session.id}",
        agent_namespace="main",
        session_id=str(session.id),
        now=now,
    )

    assert cooldown["blocked"] is True
    assert cooldown["reason"] == "same-session-recent"


@pytest.mark.asyncio
async def test_store_messages_batch_accepts_none_metadata() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session()

    stored = await client.store_messages_batch(
        str(session.id),
        [
            {
                "role": "user",
                "content": "I feel excited.",
                "message_metadata": None,
            }
        ],
    )

    assert stored[0].message_metadata == {}


@pytest.mark.asyncio
async def test_client_preserves_arbitrary_platform_name() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())

    session = await client.start_session(platform="signal")
    episode = await client.store_message(str(session.id), "user", "Checking Signal continuity.", platform="signal")

    assert session.platform.value == "signal"
    assert episode.platform.value == "signal"


@pytest.mark.asyncio
async def test_store_messages_batch_drops_tool_and_blank_messages() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session()

    stored = await client.store_messages_batch(
        str(session.id),
        [
            {"role": "tool", "content": '{"output": "", "exit_code": 0, "error": null}'},
            {"role": "assistant", "content": "   "},
            {"role": "assistant", "content": "Visible reply"},
        ],
    )

    assert len(stored) == 1
    assert stored[0].role is EpisodeRole.ASSISTANT
    assert stored[0].content == "Visible reply"


@pytest.mark.asyncio
async def test_add_directive_persists_standing_rule() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())

    directive = await client.add_directive(
        kind="tooling",
        directive_key="auto:directive:test",
        title="Delegate tasks",
        content="Always delegate implementation tasks to subagents.",
    )

    assert directive.directive_key == "auto:directive:test"
    assert transport.directives[0].content == "Always delegate implementation tasks to subagents."


@pytest.mark.asyncio
async def test_add_timeline_event_persists_summary_event() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())

    event = await client.add_timeline_event(
        summary="Finished the first active_state rollout.",
        event_key="auto:timeline:test",
        event_time=datetime.now(timezone.utc),
        title="memory milestone",
    )

    assert event.event_key == "auto:timeline:test"
    assert transport.timeline_events[0].summary == "Finished the first active_state rollout."


@pytest.mark.asyncio
async def test_add_decision_outcome_persists_structured_outcome() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())

    outcome = await client.add_decision_outcome(
        title="Memory outcome",
        kind="memory",
        decision="Moved visible conversation persistence into the gateway path.",
        outcome="That stopped tool chatter from leaking into stored transcripts.",
        lesson="Persist only what actually surfaced to the user.",
        outcome_key="auto:decision:test",
        status="success",
        event_time=datetime.now(timezone.utc),
    )

    assert outcome.outcome_key == "auto:decision:test"
    assert transport.decision_outcomes[0].lesson == "Persist only what actually surfaced to the user."


@pytest.mark.asyncio
async def test_add_pattern_persists_recurring_tendency() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())

    pattern = await client.add_pattern(
        pattern_type="decision_style",
        statement="When something important feels broken, the user pushes for root-cause debugging before moving on.",
        description="Repeatedly asks what exactly happened and wants the underlying mechanism fixed.",
        pattern_key="auto:pattern:root-cause-debugging",
        confidence=0.9,
        frequency_score=0.75,
        impact_score=0.92,
        first_observed_at=datetime.now(timezone.utc),
        last_observed_at=datetime.now(timezone.utc),
    )

    assert pattern.pattern_key == "auto:pattern:root-cause-debugging"
    assert transport.patterns[0].statement.startswith("When something important feels broken")


@pytest.mark.asyncio
async def test_add_reflection_persists_tentative_hypothesis() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())

    reflection = await client.add_reflection(
        kind="workflow_hypothesis",
        statement="Possible workflow tendency: Validate boundary behavior before broad redesign.",
        evidence_summary="Appeared repeatedly in sessions where boundary-level checks resolved regressions quickly.",
        reflection_key="auto:reflection:boundary-first",
        status="tentative",
        confidence=0.76,
        first_observed_at=datetime.now(timezone.utc),
        last_observed_at=datetime.now(timezone.utc),
    )

    assert reflection.reflection_key == "auto:reflection:boundary-first"
    assert transport.reflections[0].status.value == "tentative"


@pytest.mark.asyncio
async def test_add_commitment_and_correction_persist() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())

    commitment = await client.add_commitment(
        kind="fix",
        statement="I'll verify the Telegram delivery path and get back to you.",
        commitment_key="auto:commitment:verify-delivery",
        first_committed_at=datetime.now(timezone.utc),
        last_observed_at=datetime.now(timezone.utc),
    )
    correction = await client.add_correction(
        kind="memory_dispute",
        statement="That's wrong, I never sent soul rules before this message.",
        target_text="sent soul rules before this message",
        correction_key="auto:correction:soul-rules",
        first_observed_at=datetime.now(timezone.utc),
        last_observed_at=datetime.now(timezone.utc),
    )

    assert commitment.commitment_key == "auto:commitment:verify-delivery"
    assert transport.commitments[0].statement.startswith("I'll verify")
    assert correction.correction_key == "auto:correction:soul-rules"
    assert transport.corrections[0].target_text == "sent soul rules before this message"


@pytest.mark.asyncio
async def test_session_dominant_emotion_counts_survive_multiple_updates() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session()

    await client.store_message(str(session.id), "user", "I am excited and glad.")
    await client.store_message(str(session.id), "user", "I am delighted and pleased.")
    await client.store_message(str(session.id), "user", "I am scared and worried.")
    await client.store_message(str(session.id), "user", "I am thrilled and hopeful.")

    stored_session = transport.sessions[str(session.id)]

    assert stored_session.dominant_emotion_counts["joy"] == 3
    assert stored_session.dominant_emotion_counts["fear"] == 1
    assert stored_session.dominant_emotions[0] == "joy"


def test_embedding_truncation_uses_first_512_dimensions() -> None:
    vector = [float(index) for index in range(1536)]

    truncated = truncate_embedding(vector, 512)

    assert len(truncated) == 512
    assert truncated[0] == 0.0
    assert truncated[-1] == 511.0


@pytest.mark.asyncio
async def test_openai_embedding_provider_truncates_api_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/embeddings"
        payload = {
            "data": [
                {"index": 0, "embedding": [float(index) for index in range(1536)]},
            ]
        }
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="https://example.test")
    provider = OpenAIEmbeddingProvider(api_key="test-key", base_url="https://example.test", http_client=http_client)

    embedding = await provider.embed_text("hello")

    assert len(embedding) == 512
    await http_client.aclose()


def test_openai_embedding_provider_uses_env_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_OPENAI_BASE_URL", "https://memory-openai.example/v1")
    provider = OpenAIEmbeddingProvider(api_key="test-key")

    assert provider.base_url == "https://memory-openai.example/v1"
    asyncio.run(provider.aclose())


def test_emotion_analyzer_detects_dominant_emotion() -> None:
    analyzer = EmotionAnalyzer()

    profile = analyzer.analyze("I am excited, hopeful, and glad about this victory.")

    assert profile.dominant_emotion in {"joy", "anticipation"}
    assert profile.intensity > 0.0


@pytest.mark.asyncio
async def test_new_session_prefers_live_baton_continuity() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())

    previous = await client.start_session(platform="signal", agent_namespace="main")
    await client.store_message(
        str(previous.id),
        "user",
        "Need to run a real Signal message check after gateway restart and confirm continuity stays intact.",
        platform="signal",
        agent_namespace="main",
    )
    await client.store_message(
        str(previous.id),
        "assistant",
        "I am helping with restart validation and continuity checks, and I will track what still needs verification.",
        platform="signal",
        agent_namespace="main",
    )
    await transport.update_session(
        str(previous.id),
        {
            "summary": "Debugged Signal continuity around restart and narrowed the remaining handoff gap.",
            "dominant_emotions": ["focus", "relief"],
        },
    )

    state = ActiveState.model_validate(
        {
            "id": str(uuid4()),
            "agent_namespace": "main",
            "kind": "open_loop",
            "title": "Open loop",
            "content": "Need to run one real Signal reply and verify baton carry-forward after rollover.",
            "state_key": "auto:open_loop:primary",
            "status": "active",
            "confidence": 0.82,
            "priority_score": 0.9,
            "valid_from": datetime.now(timezone.utc).isoformat(),
            "last_observed_at": datetime.now(timezone.utc).isoformat(),
            "source_episode_ids": [],
            "source_session_ids": [str(previous.id)],
            "supporting_fact_ids": [],
            "tags": ["derived", "open-loop"],
        }
    )
    await transport.upsert_active_state(state)
    await client.add_commitment(
        kind="follow_up",
        statement="I will verify the first post-restart Signal reply and report whether continuity held.",
        commitment_key="auto:commitment:signal-restart-continuity",
        first_committed_at=datetime.now(timezone.utc),
        last_observed_at=datetime.now(timezone.utc),
        agent_namespace="main",
    )
    handoff = await client.refresh_session_handoff(str(previous.id), agent_namespace="main")
    assert handoff is not None
    assert handoff.carry_forward is not None

    current = await client.start_session(platform="signal", agent_namespace="main")
    context = await client.enrich_context(
        "continue from where we left off",
        platform="signal",
        active_session_id=str(current.id),
        agent_namespace="main",
    )

    assert "Recent cross-session continuity:" in context
    assert "Last thread: Debugged Signal continuity around restart and narrowed the remaining handoff gap." in context
    assert "Carry forward: Need to run one real Signal reply and verify baton carry-forward after rollover." in context
    assert "Assistant was helping with:" in context


@pytest.mark.asyncio
async def test_warm_live_curator_refreshes_timeline_events(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="signal", agent_namespace="main")
    await transport.update_session(
        str(session.id),
        {
            "message_count": 6,
            "summary": "Finished restart fixes and verified faster Signal reply flow.",
        },
    )

    from memory import consolidation

    calls = {
        "session_consolidation": 0,
        "backlog_consolidation": 0,
        "active_state": 0,
        "commitments": 0,
        "corrections": 0,
        "directives": 0,
        "timeline_events": 0,
        "decision_outcomes": 0,
        "patterns": 0,
    }
    backlog_kwargs: dict[str, object] = {}

    async def fake_consolidate_session_if_needed(*args, **kwargs):
        calls["session_consolidation"] += 1
        return {
            "session_id": str(session.id),
            "session_processed": True,
            "summary_generated": False,
            "summary_skipped": True,
            "facts_extracted": 2,
            "errors": 0,
            "error": None,
            "reason": None,
        }

    async def fake_consolidate_recent_sessions(*args, **kwargs):
        calls["backlog_consolidation"] += 1
        backlog_kwargs.update(kwargs)
        return {
            "sessions_processed": 2,
            "facts_extracted": 4,
            "errors": 0,
            "error_details": [],
        }

    async def fake_refresh_active_state(*args, **kwargs):
        calls["active_state"] += 1
        return {"states_upserted": 1, "states_staled": 0, "state_keys": ["auto:open_loop:primary"]}

    async def fake_refresh_commitments(*args, **kwargs):
        calls["commitments"] += 1
        return {"commitments_upserted": 1, "commitment_count": 1}

    async def fake_refresh_corrections(*args, **kwargs):
        calls["corrections"] += 1
        return {"corrections_upserted": 0, "correction_count": 0}

    async def fake_refresh_directives(*args, **kwargs):
        calls["directives"] += 1
        return {"directives_upserted": 1, "directive_count": 1}

    async def fake_refresh_timeline_events(*args, **kwargs):
        calls["timeline_events"] += 1
        return {"timeline_events_upserted": 1, "timeline_event_count": 1}

    async def fake_refresh_decision_outcomes(*args, **kwargs):
        calls["decision_outcomes"] += 1
        return {"decision_outcomes_upserted": 1, "decision_outcome_count": 1}

    async def fake_refresh_patterns(*args, **kwargs):
        calls["patterns"] += 1
        return {"patterns_upserted": 1, "pattern_count": 1}

    monkeypatch.setattr(consolidation, "consolidate_session_if_needed", fake_consolidate_session_if_needed)
    monkeypatch.setattr(consolidation, "consolidate_recent_sessions", fake_consolidate_recent_sessions)
    monkeypatch.setattr(consolidation, "refresh_active_state", fake_refresh_active_state)
    monkeypatch.setattr(consolidation, "refresh_commitments", fake_refresh_commitments)
    monkeypatch.setattr(consolidation, "refresh_corrections", fake_refresh_corrections)
    monkeypatch.setattr(consolidation, "refresh_directives", fake_refresh_directives)
    monkeypatch.setattr(consolidation, "refresh_timeline_events", fake_refresh_timeline_events)
    monkeypatch.setattr(consolidation, "refresh_decision_outcomes", fake_refresh_decision_outcomes)
    monkeypatch.setattr(consolidation, "refresh_patterns", fake_refresh_patterns)

    result = await client.curate_live_continuity(
        str(session.id),
        agent_namespace="main",
        mode="warm",
        force=True,
    )

    assert result["hot_ran"] is True
    assert result["warm_ran"] is True
    assert "session_consolidation" in result
    assert "backlog_consolidation" in result
    assert "timeline_events" in result
    assert "decision_outcomes" in result
    assert "patterns" in result
    assert calls["session_consolidation"] == 1
    assert calls["backlog_consolidation"] == 1
    assert backlog_kwargs["lookback_hours"] == 24 * 3650
    assert backlog_kwargs["min_message_count"] == 3
    assert backlog_kwargs["agent_namespace"] == "main"
    assert backlog_kwargs["batch_limit"] == 8
    assert backlog_kwargs["cursor_started_after"] is None
    assert calls["active_state"] == 1
    assert calls["commitments"] == 1
    assert calls["corrections"] == 1
    assert calls["directives"] == 1
    assert calls["timeline_events"] == 1
    assert calls["decision_outcomes"] == 1
    assert calls["patterns"] == 1


@pytest.mark.asyncio
async def test_warm_live_curator_retries_backlog_when_current_consolidation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="signal", agent_namespace="main")
    await transport.update_session(
        str(session.id),
        {
            "message_count": 6,
        },
    )

    from memory import consolidation

    calls = {
        "session_consolidation": 0,
        "backlog_consolidation": 0,
    }

    async def fake_consolidate_session_if_needed(*args, **kwargs):
        calls["session_consolidation"] += 1
        return {
            "session_id": str(session.id),
            "session_processed": False,
            "summary_generated": False,
            "summary_skipped": False,
            "facts_extracted": 0,
            "errors": 1,
            "error": "llm unavailable",
            "reason": None,
        }

    async def fake_consolidate_recent_sessions(*args, **kwargs):
        calls["backlog_consolidation"] += 1
        return {
            "sessions_processed": 3,
            "facts_extracted": 7,
            "errors": 0,
            "error_details": [],
        }

    async def fake_refresh_active_state(*args, **kwargs):
        return {"states_upserted": 1, "states_staled": 0, "state_keys": ["auto:open_loop:primary"]}

    async def fake_refresh_commitments(*args, **kwargs):
        return {"commitments_upserted": 1, "commitment_count": 1}

    async def fake_refresh_corrections(*args, **kwargs):
        return {"corrections_upserted": 0, "correction_count": 0}

    async def fake_refresh_directives(*args, **kwargs):
        return {"directives_upserted": 1, "directive_count": 1}

    async def fake_refresh_timeline_events(*args, **kwargs):
        return {"timeline_events_upserted": 1, "timeline_event_count": 1}

    async def fake_refresh_decision_outcomes(*args, **kwargs):
        return {"decision_outcomes_upserted": 1, "decision_outcome_count": 1}

    async def fake_refresh_patterns(*args, **kwargs):
        return {"patterns_upserted": 1, "pattern_count": 1}

    monkeypatch.setattr(consolidation, "consolidate_session_if_needed", fake_consolidate_session_if_needed)
    monkeypatch.setattr(consolidation, "consolidate_recent_sessions", fake_consolidate_recent_sessions)
    monkeypatch.setattr(consolidation, "refresh_active_state", fake_refresh_active_state)
    monkeypatch.setattr(consolidation, "refresh_commitments", fake_refresh_commitments)
    monkeypatch.setattr(consolidation, "refresh_corrections", fake_refresh_corrections)
    monkeypatch.setattr(consolidation, "refresh_directives", fake_refresh_directives)
    monkeypatch.setattr(consolidation, "refresh_timeline_events", fake_refresh_timeline_events)
    monkeypatch.setattr(consolidation, "refresh_decision_outcomes", fake_refresh_decision_outcomes)
    monkeypatch.setattr(consolidation, "refresh_patterns", fake_refresh_patterns)

    result = await client.curate_live_continuity(
        str(session.id),
        agent_namespace="main",
        mode="warm",
        force=True,
    )

    assert calls["session_consolidation"] == 1
    assert calls["backlog_consolidation"] == 1
    assert result["session_consolidation"]["errors"] == 1
    assert result["backlog_consolidation"]["sessions_processed"] == 3


@pytest.mark.asyncio
async def test_warm_live_curator_skips_duplicate_event_with_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="signal", agent_namespace="main")
    ended_at = datetime.now(timezone.utc)
    message_count = 7
    duplicate_event_key = f"{session.id}:{message_count}:{ended_at.isoformat()}"
    await transport.update_session(
        str(session.id),
        {
            "ended_at": ended_at,
            "message_count": message_count,
            "model_config": {"atlas_warm_curator_last_event_key": duplicate_event_key},
        },
    )

    from memory import consolidation

    calls = {"session_consolidation": 0, "backlog_consolidation": 0}

    async def fake_consolidate_session_if_needed(*args, **kwargs):
        calls["session_consolidation"] += 1
        return {"session_processed": True, "facts_extracted": 1, "errors": 0}

    async def fake_consolidate_recent_sessions(*args, **kwargs):
        calls["backlog_consolidation"] += 1
        return {
            "sessions_processed": 1,
            "facts_extracted": 1,
            "errors": 0,
            "error_details": [],
            "backlog_total_unsummarized": 1,
            "backlog_attempted": 1,
            "backlog_remaining": 0,
            "backlog_cursor_after": ended_at.isoformat(),
            "backlog_cursor_wrapped": False,
        }

    async def fake_refresh_active_state(*args, **kwargs):
        return {"states_upserted": 1, "states_staled": 0, "state_keys": ["auto:open_loop:primary"]}

    async def fake_refresh_commitments(*args, **kwargs):
        return {"commitments_upserted": 1, "commitment_count": 1}

    async def fake_refresh_corrections(*args, **kwargs):
        return {"corrections_upserted": 0, "correction_count": 0}

    monkeypatch.setattr(consolidation, "consolidate_session_if_needed", fake_consolidate_session_if_needed)
    monkeypatch.setattr(consolidation, "consolidate_recent_sessions", fake_consolidate_recent_sessions)
    monkeypatch.setattr(consolidation, "refresh_active_state", fake_refresh_active_state)
    monkeypatch.setattr(consolidation, "refresh_commitments", fake_refresh_commitments)
    monkeypatch.setattr(consolidation, "refresh_corrections", fake_refresh_corrections)

    result = await client.curate_live_continuity(
        str(session.id),
        agent_namespace="main",
        mode="warm",
        force=True,
    )

    assert result["warm_ran"] is False
    assert result["warm_skipped"] == "duplicate-event"
    assert calls["session_consolidation"] == 0
    assert calls["backlog_consolidation"] == 0


@pytest.mark.asyncio
async def test_warm_live_curator_uses_backlog_cursor_and_updates_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session(platform="signal", agent_namespace="main")
    initial_cursor = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    await transport.update_session(
        str(session.id),
        {
            "message_count": 6,
            "model_config": {"atlas_warm_backlog_cursor_started_at": initial_cursor},
        },
    )

    from memory import consolidation

    captured_kwargs: dict[str, object] = {}
    next_cursor = datetime.now(timezone.utc).isoformat()

    async def fake_consolidate_session_if_needed(*args, **kwargs):
        return {"session_processed": True, "facts_extracted": 1, "errors": 0}

    async def fake_consolidate_recent_sessions(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return {
            "sessions_processed": 2,
            "facts_extracted": 4,
            "errors": 0,
            "error_details": [],
            "backlog_total_unsummarized": 5,
            "backlog_attempted": 2,
            "backlog_remaining": 3,
            "backlog_cursor_after": next_cursor,
            "backlog_cursor_wrapped": False,
        }

    async def fake_refresh_active_state(*args, **kwargs):
        return {"states_upserted": 1, "states_staled": 0, "state_keys": ["auto:open_loop:primary"]}

    async def fake_refresh_commitments(*args, **kwargs):
        return {"commitments_upserted": 1, "commitment_count": 1}

    async def fake_refresh_corrections(*args, **kwargs):
        return {"corrections_upserted": 0, "correction_count": 0}

    async def fake_refresh_directives(*args, **kwargs):
        return {"directives_upserted": 1, "directive_count": 1}

    async def fake_refresh_timeline_events(*args, **kwargs):
        return {"timeline_events_upserted": 1, "timeline_event_count": 1}

    async def fake_refresh_decision_outcomes(*args, **kwargs):
        return {"decision_outcomes_upserted": 1, "decision_outcome_count": 1}

    async def fake_refresh_patterns(*args, **kwargs):
        return {"patterns_upserted": 1, "pattern_count": 1}

    monkeypatch.setattr(consolidation, "consolidate_session_if_needed", fake_consolidate_session_if_needed)
    monkeypatch.setattr(consolidation, "consolidate_recent_sessions", fake_consolidate_recent_sessions)
    monkeypatch.setattr(consolidation, "refresh_active_state", fake_refresh_active_state)
    monkeypatch.setattr(consolidation, "refresh_commitments", fake_refresh_commitments)
    monkeypatch.setattr(consolidation, "refresh_corrections", fake_refresh_corrections)
    monkeypatch.setattr(consolidation, "refresh_directives", fake_refresh_directives)
    monkeypatch.setattr(consolidation, "refresh_timeline_events", fake_refresh_timeline_events)
    monkeypatch.setattr(consolidation, "refresh_decision_outcomes", fake_refresh_decision_outcomes)
    monkeypatch.setattr(consolidation, "refresh_patterns", fake_refresh_patterns)

    await client.curate_live_continuity(
        str(session.id),
        agent_namespace="main",
        mode="warm",
        force=True,
    )

    assert isinstance(captured_kwargs.get("cursor_started_after"), datetime)
    assert captured_kwargs.get("batch_limit") == 8
    updated_session = await transport.get_session(str(session.id))
    assert updated_session is not None
    assert updated_session.session_model_config.get("atlas_warm_backlog_cursor_started_at") == next_cursor
