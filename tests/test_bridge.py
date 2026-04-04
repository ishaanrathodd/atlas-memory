from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from memory.bridge import MemoryBridge
from memory.client import MemoryClient
from memory.embedding import MockEmbeddingProvider
from memory.emotions import EmotionAnalyzer
from memory.models import Commitment, Correction, DecisionOutcome, Directive, Episode, Fact, FactHistory, Pattern, Reflection, Session, SessionHandoff, TimelineEvent


class MockTransport:
    def __init__(self, *, fail_ops: set[str] | None = None) -> None:
        self.fail_ops = fail_ops or set()
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

    def _maybe_fail(self, operation: str) -> None:
        if operation in self.fail_ops:
            raise RuntimeError(f"{operation} failed")

    async def insert_session(self, session: Session) -> Session:
        self._maybe_fail("insert_session")
        if session.id is None:
            session = session.model_copy(update={"id": uuid4()})
        self.sessions[str(session.id)] = session
        return session

    async def get_session(self, session_id: str) -> Session | None:
        self._maybe_fail("get_session")
        return self.sessions.get(session_id)

    async def get_session_by_legacy_id(self, legacy_session_id: str) -> Session | None:
        self._maybe_fail("get_session_by_legacy_id")
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
        self._maybe_fail("list_sessions")
        _ = agent_namespace
        sessions = list(self.sessions.values())
        if platform is not None:
            sessions = [session for session in sessions if session.platform.value == platform]
        sessions.sort(key=lambda session: session.started_at, reverse=True)
        return sessions[:limit]

    async def list_episodes_for_session(self, session_id: str, limit: int | None = None) -> list[Episode]:
        self._maybe_fail("list_episodes_for_session")
        episodes = [episode for episode in self.episodes if str(episode.session_id) == session_id]
        episodes.sort(key=lambda episode: episode.message_timestamp)
        if limit is not None:
            episodes = episodes[:limit]
        return episodes

    async def update_session(self, session_id: str, updates: dict[str, object]) -> Session:
        self._maybe_fail("update_session")
        session = self.sessions[session_id]
        updated = session.model_copy(update=updates)
        self.sessions[session_id] = updated
        return updated

    async def delete_session(self, session_id: str) -> bool:
        self._maybe_fail("delete_session")
        self.sessions.pop(session_id, None)
        self.episodes = [episode for episode in self.episodes if str(episode.session_id) != session_id]
        return True

    async def insert_episode(self, episode: Episode) -> Episode:
        self._maybe_fail("insert_episode")
        if episode.id is None:
            episode = episode.model_copy(update={"id": uuid4()})
        self.episodes.append(episode)
        return episode

    async def insert_fact(self, fact: Fact) -> Fact:
        self._maybe_fail("insert_fact")
        if fact.id is None:
            fact = fact.model_copy(update={"id": uuid4()})
        self.facts[str(fact.id)] = fact
        return fact

    async def get_fact(self, fact_id: str) -> Fact | None:
        self._maybe_fail("get_fact")
        return self.facts.get(fact_id)

    async def update_fact(self, fact_id: str, updates: dict[str, object]) -> Fact:
        self._maybe_fail("update_fact")
        fact = self.facts[fact_id]
        updated = fact.model_copy(update=updates)
        self.facts[fact_id] = updated
        return updated

    async def deactivate_fact(self, fact_id: str, replaced_by: str | None = None) -> None:
        self._maybe_fail("deactivate_fact")
        fact = self.facts[fact_id]
        self.facts[fact_id] = fact.model_copy(update={"is_active": False, "replaced_by": replaced_by})

    async def touch_fact(self, fact_id: str) -> None:
        self._maybe_fail("touch_fact")
        self.touched_facts.append(fact_id)

    async def search_episodes(
        self,
        query: str,
        limit: int = 20,
        platform: str | None = None,
        days_back: int = 30,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        self._maybe_fail("search_episodes")
        _ = (query, platform, days_back, agent_namespace)
        return self.episodes[:limit]

    async def list_recent_episodes(
        self,
        limit: int = 5,
        platform: str | None = None,
        exclude_session_id: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        self._maybe_fail("list_recent_episodes")
        _ = (platform, agent_namespace)
        episodes = list(self.episodes)
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
        self._maybe_fail("search_facts")
        _ = agent_namespace
        facts = [fact for fact in self.facts.values() if fact.is_active]
        if category is not None:
            facts = [fact for fact in facts if fact.category.value == category]
        if tags:
            facts = [fact for fact in facts if all(tag in fact.tags for tag in tags)]
        return facts[:limit]

    async def insert_fact_history(self, history: FactHistory) -> FactHistory:
        self._maybe_fail("insert_fact_history")
        if history.id is None:
            history = history.model_copy(update={"id": uuid4()})
        self.history.append(history)
        return history

    async def upsert_active_state(self, state):
        self._maybe_fail("upsert_active_state")
        self.active_state = [existing for existing in self.active_state if getattr(existing, "state_key", None) != state.state_key]
        self.active_state.append(state)
        return state

    async def list_active_state(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        self._maybe_fail("list_active_state")
        _ = (agent_namespace, statuses)
        return list(self.active_state)[:limit]

    async def upsert_directive(self, directive: Directive):
        self._maybe_fail("upsert_directive")
        self.directives = [existing for existing in self.directives if existing.directive_key != directive.directive_key]
        self.directives.append(directive)
        return directive

    async def list_directives(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        self._maybe_fail("list_directives")
        _ = (agent_namespace, statuses)
        return list(self.directives)[:limit]

    async def upsert_timeline_event(self, event: TimelineEvent):
        self._maybe_fail("upsert_timeline_event")
        self.timeline_events = [existing for existing in self.timeline_events if existing.event_key != event.event_key]
        self.timeline_events.append(event)
        return event

    async def list_timeline_events(self, limit: int = 10, agent_namespace: str | None = None):
        self._maybe_fail("list_timeline_events")
        _ = agent_namespace
        events = sorted(self.timeline_events, key=lambda event: event.event_time, reverse=True)
        return events[:limit]

    async def upsert_decision_outcome(self, outcome: DecisionOutcome):
        self._maybe_fail("upsert_decision_outcome")
        self.decision_outcomes = [existing for existing in self.decision_outcomes if existing.outcome_key != outcome.outcome_key]
        self.decision_outcomes.append(outcome)
        return outcome

    async def list_decision_outcomes(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        self._maybe_fail("list_decision_outcomes")
        _ = (agent_namespace, statuses)
        outcomes = sorted(self.decision_outcomes, key=lambda outcome: outcome.event_time, reverse=True)
        return outcomes[:limit]

    async def upsert_pattern(self, pattern: Pattern):
        self._maybe_fail("upsert_pattern")
        self.patterns = [existing for existing in self.patterns if existing.pattern_key != pattern.pattern_key]
        self.patterns.append(pattern)
        return pattern

    async def list_patterns(self, limit: int = 10, agent_namespace: str | None = None, pattern_types: list[str] | None = None):
        self._maybe_fail("list_patterns")
        _ = (agent_namespace, pattern_types)
        patterns = sorted(self.patterns, key=lambda pattern: (pattern.impact_score, pattern.last_observed_at), reverse=True)
        return patterns[:limit]

    async def upsert_reflection(self, reflection: Reflection):
        self._maybe_fail("upsert_reflection")
        self.reflections = [existing for existing in self.reflections if existing.reflection_key != reflection.reflection_key]
        self.reflections.append(reflection)
        return reflection

    async def list_reflections(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        self._maybe_fail("list_reflections")
        _ = (agent_namespace, statuses)
        reflections = sorted(self.reflections, key=lambda reflection: (reflection.confidence, reflection.last_observed_at), reverse=True)
        return reflections[:limit]

    async def delete_reflection(self, reflection_key: str, *, agent_namespace: str | None = None):
        self._maybe_fail("delete_reflection")
        _ = agent_namespace
        before = len(self.reflections)
        self.reflections = [existing for existing in self.reflections if existing.reflection_key != reflection_key]
        return len(self.reflections) < before

    async def upsert_commitment(self, commitment: Commitment):
        self._maybe_fail("upsert_commitment")
        self.commitments = [existing for existing in self.commitments if existing.commitment_key != commitment.commitment_key]
        self.commitments.append(commitment)
        return commitment

    async def list_commitments(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        self._maybe_fail("list_commitments")
        _ = (agent_namespace, statuses)
        commitments = sorted(self.commitments, key=lambda item: (item.priority_score, item.last_observed_at), reverse=True)
        return commitments[:limit]

    async def upsert_correction(self, correction: Correction):
        self._maybe_fail("upsert_correction")
        self.corrections = [existing for existing in self.corrections if existing.correction_key != correction.correction_key]
        self.corrections.append(correction)
        return correction

    async def list_corrections(self, limit: int = 10, agent_namespace: str | None = None, active_only: bool = True):
        self._maybe_fail("list_corrections")
        _ = agent_namespace
        corrections = list(self.corrections)
        if active_only:
            corrections = [item for item in corrections if item.active]
        corrections.sort(key=lambda item: item.last_observed_at, reverse=True)
        return corrections[:limit]

    async def upsert_session_handoff(self, handoff: SessionHandoff):
        self._maybe_fail("upsert_session_handoff")
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
    ):
        self._maybe_fail("list_session_handoffs")
        _ = agent_namespace
        handoffs = sorted(self.session_handoffs, key=lambda item: item.last_observed_at, reverse=True)
        if exclude_session_id is not None:
            handoffs = [item for item in handoffs if str(item.session_id) != str(exclude_session_id)]
        return handoffs[:limit]

    async def health_check(self) -> bool:
        self._maybe_fail("health_check")
        return True


def build_bridge(*, fail_ops: set[str] | None = None) -> tuple[MemoryBridge, MemoryClient, MockTransport]:
    transport = MockTransport(fail_ops=fail_ops)
    client = MemoryClient(
        transport=transport,
        embedding=MockEmbeddingProvider(),
        emotions=EmotionAnalyzer(),
    )
    bridge = MemoryBridge(client)
    return bridge, client, transport


@pytest.mark.asyncio
async def test_bridge_conversation_lifecycle() -> None:
    bridge, client, transport = build_bridge()

    session = await bridge.start_conversation("telegram")
    fact = await client.add_fact("User likes green tea.", "preference", tags=["tea"])
    episode = await bridge.log_turn("user", "Please remember that I like green tea.")
    enriched = await bridge.enrich_system_prompt("What do I like to drink?")
    ended = await bridge.end_conversation("Talked about tea preferences.")

    assert session is not None
    assert session.platform.value == "telegram"
    assert bridge.current_session_id is None
    assert bridge.current_platform == "telegram"
    assert episode is not None
    assert episode.platform.value == "telegram"
    assert "<memory>" in enriched
    assert "Relevant facts:" in enriched
    assert "Relevant prior conversations:" in enriched
    assert "Recent cross-session continuity:" in enriched
    assert "Active session summary:" in enriched
    assert str(fact.id) in transport.touched_facts
    assert ended is not None
    assert ended.summary == "Talked about tea preferences."


@pytest.mark.asyncio
async def test_extract_facts_returns_empty_list() -> None:
    bridge, _, _ = build_bridge()

    facts = await bridge.extract_facts("A conversation summary.")

    assert facts == []


@pytest.mark.asyncio
async def test_log_turn_returns_none_without_active_session() -> None:
    bridge, _, _ = build_bridge()

    episode = await bridge.log_turn("user", "hello")

    assert episode is None


@pytest.mark.asyncio
async def test_bridge_error_handling_does_not_raise() -> None:
    bridge, _, transport = build_bridge(fail_ops={"insert_session"})

    session = await bridge.start_conversation("local")
    assert session is None

    bridge._current_session_id = str(uuid4())
    bridge._current_platform = "local"
    current_session_id = bridge.current_session_id or ""
    transport.sessions[current_session_id] = Session(
        id=UUID(current_session_id),
        platform="local",
        started_at=datetime.now(timezone.utc),
        message_count=0,
        user_message_count=0,
        topics=[],
        dominant_emotions=[],
        dominant_emotion_counts={},
    )

    transport.fail_ops = {"insert_episode"}
    logged = await bridge.log_turn("assistant", "hello")
    assert logged is None

    transport.fail_ops = {"list_recent_episodes"}
    enriched = await bridge.enrich_system_prompt("hello")
    assert enriched == ""

    transport.fail_ops = {"update_session"}
    ended = await bridge.end_conversation("summary")
    assert ended is None


@pytest.mark.asyncio
async def test_end_conversation_returns_none_without_session() -> None:
    bridge, _, _ = build_bridge()

    ended = await bridge.end_conversation("summary")

    assert ended is None
