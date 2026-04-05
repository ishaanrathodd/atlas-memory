from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from memory.bridge import MemoryBridge
from memory.client import MemoryClient
from memory.embedding import MockEmbeddingProvider
from memory.emotions import EmotionAnalyzer
from memory.enrichment import collect_enrichment_payload, enrich_context
from memory.models import (
    ActiveState,
    ActiveStateKind,
    ActiveStateStatus,
    CaseOutcomeStatus,
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    Correction,
    CorrectionKind,
    DecisionOutcome,
    DecisionOutcomeKind,
    DecisionOutcomeStatus,
    Directive,
    DirectiveKind,
    DirectiveScope,
    DirectiveStatus,
    Episode,
    EpisodeRole,
    Fact,
    FactCategory,
    MemoryCase,
    Pattern,
    PatternType,
    Platform,
    Reflection,
    ReflectionKind,
    ReflectionStatus,
    Session,
    SessionHandoff,
    TimelineEvent,
    TimelineEventKind,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MockTransport:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.episodes: list[Episode] = []
        self.facts: dict[str, Fact] = {}
        self.touched_facts: list[str] = []
        self.last_search_episodes_platform: str | None | object = "__unset__"
        self.last_recent_episodes_platform: str | None | object = "__unset__"
        self.last_search_episodes_limit: int | None = None
        self.last_recent_episodes_limit: int | None = None
        self.last_search_episodes_namespace: str | None | object = "__unset__"
        self.last_recent_episodes_namespace: str | None | object = "__unset__"
        self.last_search_facts_namespace: str | None | object = "__unset__"
        self.active_state: list[object] = []
        self.directives: list[Directive] = []
        self.timeline_events: list[TimelineEvent] = []
        self.memory_cases: list[MemoryCase] = []
        self.decision_outcomes: list[DecisionOutcome] = []
        self.patterns: list[Pattern] = []
        self.reflections: list[Reflection] = []
        self.commitments: list[Commitment] = []
        self.corrections: list[Correction] = []
        self.session_handoffs: list[SessionHandoff] = []
        self.last_list_active_state_namespace: str | None | object = "__unset__"
        self.last_list_directives_namespace: str | None | object = "__unset__"
        self.last_list_decision_outcomes_namespace: str | None | object = "__unset__"
        self.last_list_patterns_namespace: str | None | object = "__unset__"
        self.last_list_reflections_namespace: str | None | object = "__unset__"
        self.last_list_commitments_namespace: str | None | object = "__unset__"
        self.last_list_corrections_namespace: str | None | object = "__unset__"
        self.last_list_timeline_namespace: str | None | object = "__unset__"
        self.last_list_timeline_limit: int | None = None
        self.last_list_memory_cases_namespace: str | None | object = "__unset__"
        self.last_list_memory_cases_statuses: list[str] | None = None
        self.last_list_handoffs_namespace: str | None | object = "__unset__"

    async def insert_session(self, session: Session) -> Session:
        if session.id is None:
            session = session.model_copy(update={"id": uuid4()})
        self.sessions[str(session.id)] = session
        return session

    async def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    async def list_episodes_for_session(self, session_id: str, limit: int | None = None) -> list[Episode]:
        episodes = [episode for episode in self.episodes if str(episode.session_id) == str(session_id)]
        episodes.sort(key=lambda episode: episode.message_timestamp)
        if limit is not None:
            return episodes[-limit:]
        return episodes

    async def update_session(self, session_id: str, updates: dict[str, object]) -> Session:
        session = self.sessions[session_id]
        updated = session.model_copy(update=updates)
        self.sessions[session_id] = updated
        return updated

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

    async def update_fact(self, fact_id: str, updates: dict[str, object]) -> Fact:
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
        _ = (query, days_back)
        self.last_search_episodes_limit = limit
        self.last_search_episodes_platform = platform
        self.last_search_episodes_namespace = agent_namespace
        episodes = list(self.episodes)
        if platform is not None:
            episodes = [episode for episode in episodes if episode.platform.value == platform]
        if agent_namespace is not None:
            episodes = [
                episode
                for episode in episodes
                if episode.agent_namespace == agent_namespace or (agent_namespace == "main" and episode.agent_namespace is None)
            ]
        return episodes[:limit]

    async def list_recent_episodes(
        self,
        limit: int = 5,
        platform: str | None = None,
        exclude_session_id: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        self.last_recent_episodes_limit = limit
        self.last_recent_episodes_platform = platform
        self.last_recent_episodes_namespace = agent_namespace
        episodes = list(self.episodes)
        if platform is not None:
            episodes = [episode for episode in episodes if episode.platform.value == platform]
        if exclude_session_id is not None:
            episodes = [episode for episode in episodes if str(episode.session_id) != exclude_session_id]
        if agent_namespace is not None:
            episodes = [
                episode
                for episode in episodes
                if episode.agent_namespace == agent_namespace or (agent_namespace == "main" and episode.agent_namespace is None)
            ]
        episodes.sort(key=lambda episode: episode.message_timestamp, reverse=True)
        return episodes[:limit]

    async def search_facts(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        agent_namespace: str | None = None,
    ) -> list[Fact]:
        self.last_search_facts_namespace = agent_namespace
        facts = [fact for fact in self.facts.values() if fact.is_active]
        if category is not None:
            facts = [fact for fact in facts if fact.category.value == category]
        if tags:
            facts = [fact for fact in facts if all(tag in fact.tags for tag in tags)]
        if agent_namespace is not None:
            facts = [
                fact
                for fact in facts
                if fact.agent_namespace == agent_namespace or (agent_namespace == "main" and fact.agent_namespace is None)
            ]
        return facts[:limit]

    async def insert_fact_history(self, history):  # pragma: no cover - not used in this test module
        return history

    async def upsert_active_state(self, state):
        self.active_state = [existing for existing in self.active_state if getattr(existing, "state_key", None) != state.state_key]
        self.active_state.append(state)
        return state

    async def list_active_state(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        _ = statuses
        self.last_list_active_state_namespace = agent_namespace
        return list(self.active_state)[:limit]

    async def upsert_directive(self, directive: Directive):
        self.directives = [existing for existing in self.directives if existing.directive_key != directive.directive_key]
        self.directives.append(directive)
        return directive

    async def list_directives(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        _ = statuses
        self.last_list_directives_namespace = agent_namespace
        return list(self.directives)[:limit]

    async def upsert_timeline_event(self, event: TimelineEvent):
        self.timeline_events = [existing for existing in self.timeline_events if existing.event_key != event.event_key]
        self.timeline_events.append(event)
        return event

    async def list_timeline_events(self, limit: int = 10, agent_namespace: str | None = None):
        self.last_list_timeline_limit = limit
        self.last_list_timeline_namespace = agent_namespace
        events = sorted(self.timeline_events, key=lambda event: event.event_time, reverse=True)
        return events[:limit]

    async def list_memory_cases(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        outcome_statuses: list[str] | None = None,
    ) -> list[MemoryCase]:
        self.last_list_memory_cases_namespace = agent_namespace
        self.last_list_memory_cases_statuses = list(outcome_statuses or [])
        cases = list(self.memory_cases)
        if agent_namespace is not None:
            cases = [
                case
                for case in cases
                if case.agent_namespace == agent_namespace or (agent_namespace == "main" and case.agent_namespace is None)
            ]
        if outcome_statuses:
            allowed = set(outcome_statuses)
            cases = [case for case in cases if case.outcome_status.value in allowed]
        cases.sort(key=lambda case: (case.impact_score, case.last_observed_at), reverse=True)
        return cases[:limit]

    async def upsert_decision_outcome(self, outcome: DecisionOutcome):
        self.decision_outcomes = [existing for existing in self.decision_outcomes if existing.outcome_key != outcome.outcome_key]
        self.decision_outcomes.append(outcome)
        return outcome

    async def list_decision_outcomes(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        _ = statuses
        self.last_list_decision_outcomes_namespace = agent_namespace
        outcomes = sorted(self.decision_outcomes, key=lambda outcome: outcome.event_time, reverse=True)
        return outcomes[:limit]

    async def upsert_pattern(self, pattern: Pattern):
        self.patterns = [existing for existing in self.patterns if existing.pattern_key != pattern.pattern_key]
        self.patterns.append(pattern)
        return pattern

    async def list_patterns(self, limit: int = 10, agent_namespace: str | None = None, pattern_types: list[str] | None = None):
        _ = pattern_types
        self.last_list_patterns_namespace = agent_namespace
        patterns = sorted(self.patterns, key=lambda pattern: (pattern.impact_score, pattern.last_observed_at), reverse=True)
        return patterns[:limit]

    async def upsert_reflection(self, reflection: Reflection):
        self.reflections = [existing for existing in self.reflections if existing.reflection_key != reflection.reflection_key]
        self.reflections.append(reflection)
        return reflection

    async def list_reflections(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        _ = statuses
        self.last_list_reflections_namespace = agent_namespace
        reflections = sorted(self.reflections, key=lambda reflection: (reflection.confidence, reflection.last_observed_at), reverse=True)
        return reflections[:limit]

    async def delete_reflection(self, reflection_key: str, *, agent_namespace: str | None = None):
        _ = agent_namespace
        before = len(self.reflections)
        self.reflections = [existing for existing in self.reflections if existing.reflection_key != reflection_key]
        return len(self.reflections) < before

    async def upsert_commitment(self, commitment: Commitment):
        self.commitments = [existing for existing in self.commitments if existing.commitment_key != commitment.commitment_key]
        self.commitments.append(commitment)
        return commitment

    async def list_commitments(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None):
        _ = statuses
        self.last_list_commitments_namespace = agent_namespace
        commitments = sorted(self.commitments, key=lambda item: (item.priority_score, item.last_observed_at), reverse=True)
        return commitments[:limit]

    async def upsert_correction(self, correction: Correction):
        self.corrections = [existing for existing in self.corrections if existing.correction_key != correction.correction_key]
        self.corrections.append(correction)
        return correction

    async def list_corrections(self, limit: int = 10, agent_namespace: str | None = None, active_only: bool = True):
        _ = active_only
        self.last_list_corrections_namespace = agent_namespace
        corrections = sorted(self.corrections, key=lambda item: item.last_observed_at, reverse=True)
        return corrections[:limit]

    async def upsert_session_handoff(self, handoff: SessionHandoff):
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
        self.last_list_handoffs_namespace = agent_namespace
        handoffs = sorted(self.session_handoffs, key=lambda item: item.last_observed_at, reverse=True)
        if exclude_session_id is not None:
            handoffs = [item for item in handoffs if str(item.session_id) != str(exclude_session_id)]
        return handoffs[:limit]

    async def health_check(self) -> bool:
        return True


def _build_client_with_transport() -> tuple[MemoryClient, MemoryBridge, MockTransport]:
    transport = MockTransport()
    client = MemoryClient(
        transport=transport,
        embedding=MockEmbeddingProvider(),
        emotions=EmotionAnalyzer(),
    )
    return client, MemoryBridge(client), transport


def _make_fact(
    content: str,
    *,
    category: FactCategory = FactCategory.FACT,
    tags: list[str] | None = None,
    confidence: float = 1.0,
    hours_ago: int = 0,
) -> Fact:
    timestamp = _utcnow() - timedelta(hours=hours_ago)
    return Fact(
        id=uuid4(),
        content=content,
        category=category,
        confidence=confidence,
        event_time=timestamp,
        transaction_time=timestamp,
        is_active=True,
        access_count=hours_ago,
        tags=tags or [],
        created_at=timestamp,
        updated_at=timestamp,
    )


def _make_episode(content: str, *, session_id, minutes_ago: int, platform: Platform = Platform.LOCAL) -> Episode:
    timestamp = _utcnow() - timedelta(minutes=minutes_ago)
    return Episode(
        id=uuid4(),
        session_id=session_id,
        role=EpisodeRole.USER,
        content=content,
        content_hash=uuid4().hex,
        embedding=[0.0] * 512,
        platform=platform,
        message_timestamp=timestamp,
    )


@pytest.mark.asyncio
async def test_collect_enrichment_payload_ranks_facts_and_limits_recent_episodes() -> None:
    client, _, transport = _build_client_with_transport()
    active_session = await client.start_session(platform="local")
    previous_session = await client.start_session(platform="local")

    session_id = str(active_session.id)
    previous_session_id = previous_session.id
    assert previous_session_id is not None

    project_fact = _make_fact(
        "API launch deadline is Friday afternoon.",
        category=FactCategory.PROJECT,
        tags=["launch", "deadline"],
        hours_ago=1,
    )
    tea_fact = _make_fact(
        "User drinks green tea every morning.",
        category=FactCategory.HABIT,
        tags=["tea"],
        hours_ago=2,
    )
    misc_fact = _make_fact(
        "User prefers standing desks in the office.",
        category=FactCategory.ENVIRONMENT,
        tags=["workspace"],
        hours_ago=3,
    )
    transport.facts = {
        str(project_fact.id): project_fact,
        str(tea_fact.id): tea_fact,
        str(misc_fact.id): misc_fact,
    }

    current_episode = _make_episode(
        "Current active session message should not appear in recent history.",
        session_id=active_session.id,
        minutes_ago=0,
    )
    older_episodes = [
        _make_episode(f"Recent archived conversation #{index}", session_id=previous_session_id, minutes_ago=index)
        for index in range(1, 8)
    ]
    reference_like_recent = Episode(
        id=uuid4(),
        session_id=previous_session_id,
        role=EpisodeRole.ASSISTANT,
        content=(
            "yo bro. got it, soul rules updated. "
            "not gonna blindly act on them this time, just reading and storing them in context."
        ),
        content_hash=uuid4().hex,
        embedding=[0.0] * 512,
        platform=Platform.TELEGRAM,
        message_timestamp=_utcnow() - timedelta(seconds=30),
    )
    operational_recent = Episode(
        id=uuid4(),
        session_id=previous_session_id,
        role=EpisodeRole.ASSISTANT,
        content=(
            "Memory processor ran clean. Consolidated 1 session (2 facts extracted), "
            "skipped 1 trivial session. episode_count=10 fact_count=4 session_count=3"
        ),
        content_hash=uuid4().hex,
        embedding=[0.0] * 512,
        platform=Platform.OTHER,
        message_timestamp=_utcnow() - timedelta(seconds=20),
        message_metadata={"source_kind": "operational_status"},
    )
    transport.episodes = [current_episode, reference_like_recent, operational_recent, *older_episodes]

    payload = await collect_enrichment_payload(
        transport,
        "What is the launch deadline for the API?",
        platform="local",
        active_session_id=session_id,
        agent_namespace="main",
    )

    assert payload.facts[0].id == project_fact.id
    assert payload.relevant_episodes
    assert all(str(episode.session_id) != session_id for episode in payload.relevant_episodes)
    assert len(payload.recent_episodes) <= 3
    assert all(str(episode.session_id) != session_id for episode in payload.recent_episodes)
    assert all("soul rules updated" not in episode.content.lower() for episode in payload.recent_episodes)
    assert all("memory processor ran clean" not in episode.content.lower() for episode in payload.recent_episodes)
    assert all("memory processor ran clean" not in episode.content.lower() for episode in payload.relevant_episodes)
    assert payload.active_session is not None
    assert payload.active_session.id == active_session.id
    assert payload.active_state_lines
    assert any("Likely active project" in line for line in payload.active_state_lines)
    assert str(project_fact.id) in transport.touched_facts
    assert transport.last_search_episodes_platform is None
    assert transport.last_recent_episodes_platform is None
    assert transport.last_search_episodes_limit == 12
    assert transport.last_recent_episodes_limit == 3
    assert transport.last_list_timeline_limit == 12
    assert transport.last_search_episodes_namespace == "main"
    assert transport.last_recent_episodes_namespace == "main"
    assert transport.last_search_facts_namespace == "main"
    assert transport.last_list_directives_namespace == "main"
    assert transport.last_list_timeline_namespace == "main"
    assert transport.last_list_memory_cases_namespace == "main"
    assert transport.last_list_memory_cases_statuses == ["success", "failure", "mixed"]


@pytest.mark.asyncio
async def test_collect_enrichment_payload_filters_low_quality_facts() -> None:
    active_session_id = uuid4()
    _, _, transport = _build_client_with_transport()
    good_fact = _make_fact(
        "User works best by debugging root causes before patching symptoms.",
        category=FactCategory.HABIT,
        tags=["debugging"],
        hours_ago=1,
    )
    noisy_fact = _make_fact(
        "Memory overnight build COMPLETE. 32/42 tests pass, 10 skipped (need SUPABASE_SERVICE_KEY).",
        category=FactCategory.FACT,
        tags=["backfill"],
        hours_ago=0,
    ).model_copy(update={"source_episode_ids": []})
    noisy_goal = _make_fact(
        "User wants to add it so the E2E tests and curator can actually talk to the DB.",
        category=FactCategory.GOAL,
        tags=["goal", "e2e", "curator"],
        hours_ago=0,
    )
    noisy_preference = _make_fact(
        "User dislikes remember that.",
        category=FactCategory.PREFERENCE,
        tags=["preference", "remember"],
        hours_ago=0,
    )
    noisy_goal_2 = _make_fact(
        "User wants to just send the TTS and keep my actual response minimal or empty.",
        category=FactCategory.GOAL,
        tags=["goal", "tts"],
        hours_ago=0,
    )
    transport.facts = {
        str(good_fact.id): good_fact,
        str(noisy_fact.id): noisy_fact,
        str(noisy_goal.id): noisy_goal,
        str(noisy_preference.id): noisy_preference,
        str(noisy_goal_2.id): noisy_goal_2,
    }

    payload = await collect_enrichment_payload(
        transport,
        "how should I approach this debugging problem?",
        platform="local",
        active_session_id=str(active_session_id),
        agent_namespace="main",
    )

    rendered_facts = [fact.content for fact in payload.facts]
    assert good_fact.content in rendered_facts
    assert noisy_fact.content not in rendered_facts
    assert noisy_goal.content not in rendered_facts
    assert noisy_preference.content not in rendered_facts
    assert noisy_goal_2.content not in rendered_facts


@pytest.mark.asyncio
async def test_collect_enrichment_payload_excludes_memory_outcomes_for_generic_advice_queries() -> None:
    _, _, transport = _build_client_with_transport()
    transport.decision_outcomes = [
        DecisionOutcome(
            id=uuid4(),
            agent_namespace="main",
            kind=DecisionOutcomeKind.MEMORY,
            title="Memory outcome",
            decision="Moved visible assistant persistence into the gateway path.",
            outcome="That stopped tool chatter from leaking into Supabase transcripts.",
            lesson="Past prompt mechanics should not be treated as current user intent.",
            outcome_key="memory-1",
            status=DecisionOutcomeStatus.SUCCESS,
            confidence=0.9,
            importance_score=0.8,
            event_time=_utcnow() - timedelta(days=1),
            tags=["derived", "memory"],
        ),
        DecisionOutcome(
            id=uuid4(),
            agent_namespace="main",
            kind=DecisionOutcomeKind.TOOLING,
            title="Tooling outcome",
            decision="Switched to root-cause debugging before patching symptoms.",
            outcome="That surfaced the persistence bug faster.",
            lesson="Start with the boundary that actually writes state.",
            outcome_key="tooling-1",
            status=DecisionOutcomeStatus.SUCCESS,
            confidence=0.9,
            importance_score=0.85,
            event_time=_utcnow() - timedelta(hours=10),
            tags=["derived", "tooling", "debugging"],
        ),
    ]

    payload = await collect_enrichment_payload(
        transport,
        "how should I approach this debugging problem?",
        platform="telegram",
        agent_namespace="main",
    )

    assert len(payload.decision_outcomes) == 1
    assert payload.decision_outcomes[0].kind is DecisionOutcomeKind.TOOLING


@pytest.mark.asyncio
async def test_collect_enrichment_payload_keeps_operational_context_when_user_asks_for_it() -> None:
    _, _, transport = _build_client_with_transport()
    previous_session_id = uuid4()
    operational_episode = Episode(
        id=uuid4(),
        session_id=previous_session_id,
        role=EpisodeRole.ASSISTANT,
        content=(
            "Memory processor ran clean. Consolidated 1 session (2 facts extracted), "
            "skipped 1 trivial session. episode_count=10 fact_count=4 session_count=3"
        ),
        content_hash=uuid4().hex,
        embedding=[0.0] * 512,
        platform=Platform.OTHER,
        message_timestamp=_utcnow() - timedelta(minutes=3),
        message_metadata={"source_kind": "operational_status"},
    )
    transport.episodes = [operational_episode]

    payload = await collect_enrichment_payload(
        transport,
        "what did the memory processor do?",
        platform="telegram",
    )

    assert any("memory processor ran clean" in episode.content.lower() for episode in payload.relevant_episodes)


@pytest.mark.asyncio
async def test_collect_enrichment_payload_prefers_persistent_active_state_over_fallback() -> None:
    _, _, transport = _build_client_with_transport()
    transport.active_state = [
        ActiveState(
            id=uuid4(),
            agent_namespace="main",
            kind=ActiveStateKind.PROJECT,
            title="Primary focus",
            content="Memory memory redesign is the active focus right now.",
            content_hash=uuid4().hex,
            state_key="auto:project:primary",
            status=ActiveStateStatus.ACTIVE,
            confidence=0.9,
            priority_score=1.0,
            valid_from=_utcnow() - timedelta(hours=2),
            last_observed_at=_utcnow() - timedelta(minutes=5),
            tags=["derived"],
        )
    ]

    payload = await collect_enrichment_payload(
        transport,
        "how should i approach this memory project now?",
        platform="telegram",
        agent_namespace="main",
    )

    assert payload.active_state_lines == ["In focus: Memory memory redesign is the active focus right now."]


@pytest.mark.asyncio
async def test_collect_enrichment_payload_filters_project_build_state_for_generic_queries() -> None:
    _, _, transport = _build_client_with_transport()
    transport.active_state = [
        ActiveState(
            id=uuid4(),
            agent_namespace="main",
            kind=ActiveStateKind.PROJECT,
            title="Primary focus",
            content="Memory memory redesign is the active focus right now.",
            content_hash=uuid4().hex,
            state_key="auto:project:primary",
            status=ActiveStateStatus.ACTIVE,
            confidence=0.9,
            priority_score=1.0,
            valid_from=_utcnow() - timedelta(hours=2),
            last_observed_at=_utcnow() - timedelta(minutes=5),
            tags=["derived", "memory"],
        )
    ]
    transport.facts = {
        str(uuid4()): _make_fact(
            "Memory purpose is to become the ultimate memory layer for Hermes.",
            category=FactCategory.FACT,
            tags=["memory", "purpose"],
            hours_ago=1,
        )
    }

    payload = await collect_enrichment_payload(
        transport,
        "how should i approach this debugging problem?",
        platform="telegram",
        agent_namespace="main",
    )

    assert payload.active_state_lines == []
    assert all("memory purpose" not in fact.content.lower() for fact in payload.facts)


@pytest.mark.asyncio
async def test_collect_enrichment_payload_keeps_meaningful_priority_for_generic_queries() -> None:
    _, _, transport = _build_client_with_transport()
    transport.active_state = [
        ActiveState(
            id=uuid4(),
            agent_namespace="main",
            kind=ActiveStateKind.PRIORITY,
            title="Current priority",
            content="Wants Memory alive 24/7 with an actual heartbeat and genuine continuity.",
            content_hash=uuid4().hex,
            state_key="auto:priority:primary",
            status=ActiveStateStatus.ACTIVE,
            confidence=0.9,
            priority_score=1.0,
            valid_from=_utcnow() - timedelta(hours=2),
            last_observed_at=_utcnow() - timedelta(minutes=5),
            tags=["derived", "priority"],
        )
    ]

    payload = await collect_enrichment_payload(
        transport,
        "how should i approach this debugging problem?",
        platform="telegram",
        agent_namespace="main",
    )

    assert payload.active_state_lines == ["Priority: Wants Memory alive 24/7 with an actual heartbeat and genuine continuity."]


@pytest.mark.asyncio
async def test_enrich_context_includes_standing_directives() -> None:
    _, _, transport = _build_client_with_transport()
    transport.directives = [
        Directive(
            id=uuid4(),
            agent_namespace="main",
            kind=DirectiveKind.COMMUNICATION,
            scope=DirectiveScope.GLOBAL,
            title="No em dashes",
            content="Do not use em dashes in replies.",
            content_hash=uuid4().hex,
            directive_key="auto:directive:no-em-dash",
            status=DirectiveStatus.ACTIVE,
            confidence=0.95,
            priority_score=1.0,
            last_observed_at=_utcnow(),
        )
    ]

    context = await enrich_context(
        transport,
        "hey",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Standing directives:" in context
    assert "[communication] Do not use em dashes in replies." in context


@pytest.mark.asyncio
async def test_collect_enrichment_payload_reranks_directives_by_query_relevance() -> None:
    _, _, transport = _build_client_with_transport()
    transport.directives = [
        Directive(
            id=uuid4(),
            agent_namespace="main",
            kind=DirectiveKind.COMMUNICATION,
            scope=DirectiveScope.GLOBAL,
            title="Style",
            content="Always keep replies concise and natural.",
            content_hash=uuid4().hex,
            directive_key="auto:directive:style",
            status=DirectiveStatus.ACTIVE,
            confidence=0.95,
            priority_score=1.0,
            last_observed_at=_utcnow() - timedelta(days=1),
        ),
        Directive(
            id=uuid4(),
            agent_namespace="main",
            kind=DirectiveKind.TOOLING,
            scope=DirectiveScope.GLOBAL,
            title="Tooling",
            content="Always run SQL migration checks before applying schema changes.",
            content_hash=uuid4().hex,
            directive_key="auto:directive:sql-migration",
            status=DirectiveStatus.ACTIVE,
            confidence=0.9,
            priority_score=0.8,
            last_observed_at=_utcnow(),
        ),
    ]

    payload = await collect_enrichment_payload(
        transport,
        "before changing schema, should we run sql migration checks?",
        platform="telegram",
        agent_namespace="main",
    )

    assert payload.directives
    assert payload.directives[0].kind is DirectiveKind.TOOLING


@pytest.mark.asyncio
async def test_enrich_context_includes_recent_major_events() -> None:
    _, _, transport = _build_client_with_transport()
    transport.timeline_events = [
        TimelineEvent(
            id=uuid4(),
            agent_namespace="main",
            kind=TimelineEventKind.SESSION_SUMMARY,
            title="telegram session",
            summary="Finished active_state and directives rollout for Memory memory.",
            event_key="auto:timeline:test",
            event_time=_utcnow() - timedelta(minutes=20),
            tags=["derived"],
            importance_score=0.8,
        )
    ]

    context = await enrich_context(
        transport,
        "yo",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Recent major events:" in context
    assert "Finished active_state and directives rollout for Memory memory." in context


@pytest.mark.asyncio
async def test_enrich_context_filters_operational_timeline_noise() -> None:
    _, _, transport = _build_client_with_transport()
    transport.timeline_events = [
        TimelineEvent(
            id=uuid4(),
            agent_namespace="main",
            kind=TimelineEventKind.SESSION_SUMMARY,
            title="other session",
            summary="Memory processor failed due to missing timeline_events table. Created table with schema, RLS policies, and indexes.",
            event_key="auto:timeline:ops",
            event_time=_utcnow() - timedelta(minutes=5),
            tags=["derived"],
            importance_score=0.9,
        ),
        TimelineEvent(
            id=uuid4(),
            agent_namespace="main",
            kind=TimelineEventKind.SESSION_SUMMARY,
            title="telegram session",
            summary="Finished active_state and directives rollout for Memory memory.",
            event_key="auto:timeline:real",
            event_time=_utcnow() - timedelta(minutes=20),
            tags=["derived"],
            importance_score=0.8,
        ),
    ]

    context = await enrich_context(
        transport,
        "yo",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Finished active_state and directives rollout for Memory memory." in context
    assert "Memory processor failed due to missing timeline_events table." not in context


@pytest.mark.asyncio
async def test_enrich_context_humanizes_session_summary_timeline_events() -> None:
    _, _, transport = _build_client_with_transport()
    transport.timeline_events = [
        TimelineEvent(
            id=uuid4(),
            agent_namespace="main",
            kind=TimelineEventKind.SESSION_SUMMARY,
            title="telegram session",
            summary=(
                "User: Ishaan Jain (online: Ishaan Rathod). Building Memory memory system. "
                "Current state: 4591 episodes, 176 facts in Supabase, session_search functional. "
                "User tested how memory retrieval works; confirmed system is working. "
                "Goals: active state feature for real-time context."
            ),
            event_key="auto:timeline:summary-clean",
            event_time=_utcnow() - timedelta(minutes=15),
            tags=["derived"],
            importance_score=0.8,
        )
    ]

    context = await enrich_context(
        transport,
        "what is left regarding the memory project?",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Building Memory memory system." in context
    assert "Current state: 4591 episodes" not in context
    assert "session_search functional" not in context
    assert "User tested how memory retrieval works" not in context


@pytest.mark.asyncio
async def test_enrich_context_prefers_rollups_for_timeline_queries() -> None:
    _, _, transport = _build_client_with_transport()
    transport.timeline_events = [
        TimelineEvent(
            id=uuid4(),
            agent_namespace="main",
            kind=TimelineEventKind.SESSION_SUMMARY,
            title="telegram session",
            summary="Improved retrieval ranking and tested a few timeline queries.",
            event_key="auto:timeline:session-a",
            event_time=_utcnow() - timedelta(hours=1),
            tags=["derived"],
            importance_score=0.7,
        ),
        TimelineEvent(
            id=uuid4(),
            agent_namespace="main",
            kind=TimelineEventKind.DAY_SUMMARY,
            title="day summary",
            summary="Across 3 sessions: Focused on retrieval ranking, active state wording, and timeline cleanup.",
            event_key="auto:timeline:day-a",
            event_time=_utcnow() - timedelta(hours=1),
            tags=["derived", "timeline-rollup"],
            importance_score=0.88,
        ),
        TimelineEvent(
            id=uuid4(),
            agent_namespace="main",
            kind=TimelineEventKind.WEEK_SUMMARY,
            title="week summary",
            summary="Across 8 sessions: Focused on memory continuity, compiler cleanup, and timeline rollups.",
            event_key="auto:timeline:week-a",
            event_time=_utcnow() - timedelta(hours=1),
            tags=["derived", "timeline-rollup"],
            importance_score=0.92,
        ),
    ]

    context = await enrich_context(
        transport,
        "what happened last week?",
        platform="telegram",
        agent_namespace="main",
    )

    lines = [line for line in context.splitlines() if line.startswith("- 20")]
    assert lines[0].endswith("[week] Across 8 sessions: Focused on memory continuity, compiler cleanup, and timeline rollups.")
    assert lines[1].endswith("[day] Across 3 sessions: Focused on retrieval ranking, active state wording, and timeline cleanup.")


@pytest.mark.asyncio
async def test_enrich_context_filters_project_timeline_noise_for_generic_queries() -> None:
    _, _, transport = _build_client_with_transport()
    transport.timeline_events = [
        TimelineEvent(
            id=uuid4(),
            agent_namespace="main",
            kind=TimelineEventKind.SESSION_SUMMARY,
            title="memory build session",
            summary="Building Memory memory system. Goals: active state feature for real-time context.",
            event_key="auto:timeline:project",
            event_time=_utcnow() - timedelta(minutes=10),
            tags=["derived", "memory", "project"],
            importance_score=0.9,
        ),
        TimelineEvent(
            id=uuid4(),
            agent_namespace="main",
            kind=TimelineEventKind.SESSION_SUMMARY,
            title="personal session",
            summary="Family remains the emotional weak spot, especially around mom, dad, and Tanvi.",
            event_key="auto:timeline:personal",
            event_time=_utcnow() - timedelta(minutes=20),
            tags=["derived", "family"],
            importance_score=0.8,
        ),
    ]

    context = await enrich_context(
        transport,
        "how should I approach this debugging problem?",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Building Memory memory system." not in context
    assert "Family remains the emotional weak spot" in context


@pytest.mark.asyncio
async def test_enrich_context_humanizes_large_identity_profile_fact() -> None:
    _, _, transport = _build_client_with_transport()
    profile_fact = _make_fact(
        (
            "ISHAAN PROFILE FOR MEMORY: 21, Mumbai. Final sem B.Tech Computer Engineering, "
            "college ends Jul 2026. Solo founder upstorr.com (launched Mar 27 2026). "
            "MacBook Air M2 8GB. Works 15-hour days."
        ),
        category=FactCategory.IDENTITY,
        tags=["identity", "profile"],
        hours_ago=1,
    )
    transport.facts = {str(profile_fact.id): profile_fact}

    context = await enrich_context(
        transport,
        "tell me what you know about me",
        platform="telegram",
        agent_namespace="main",
    )

    assert "ISHAAN PROFILE FOR MEMORY" not in context
    assert "Ishaan profile:" in context
    assert "Solo founder upstorr.com" in context


@pytest.mark.asyncio
async def test_enrich_context_includes_relevant_prior_outcomes_for_advice_queries() -> None:
    _, _, transport = _build_client_with_transport()
    transport.decision_outcomes = [
        DecisionOutcome(
            id=uuid4(),
            agent_namespace="main",
            kind=DecisionOutcomeKind.MEMORY,
            title="Memory outcome",
            decision="Persisted visible assistant text only in the gateway path.",
            outcome="That stopped tool chatter from leaking into Supabase transcripts.",
            lesson="Persist only what actually surfaced to the user.",
            outcome_key="auto:outcome:gateway-visible",
            status=DecisionOutcomeStatus.SUCCESS,
            confidence=0.9,
            importance_score=0.9,
            event_time=_utcnow() - timedelta(hours=2),
            tags=["derived"],
        )
    ]

    context = await enrich_context(
        transport,
        "how should i approach telegram persistence now?",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Relevant prior outcomes:" in context
    assert "Persisted visible assistant text only in the gateway path." in context
    assert "Persist only what actually surfaced to the user." in context
    assert transport.last_list_decision_outcomes_namespace == "main"


@pytest.mark.asyncio
async def test_enrich_context_excludes_irrelevant_decision_outcomes_even_for_advice_queries() -> None:
    _, _, transport = _build_client_with_transport()
    transport.decision_outcomes = [
        DecisionOutcome(
            id=uuid4(),
            agent_namespace="main",
            kind=DecisionOutcomeKind.OTHER,
            title="Tea preference",
            decision="I prefer green tea.",
            outcome="I prefer green tea every morning before work.",
            lesson=None,
            outcome_key="auto:outcome:tea",
            status=DecisionOutcomeStatus.OPEN,
            confidence=0.8,
            importance_score=0.8,
            event_time=_utcnow() - timedelta(hours=1),
            tags=["derived"],
        )
    ]

    context = await enrich_context(
        transport,
        "how should i approach this memory project now?",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Relevant prior outcomes:" not in context


@pytest.mark.asyncio
async def test_enrich_context_does_not_match_decision_outcomes_from_generic_platform_tags_only() -> None:
    _, _, transport = _build_client_with_transport()
    transport.decision_outcomes = [
        DecisionOutcome(
            id=uuid4(),
            agent_namespace="main",
            kind=DecisionOutcomeKind.MEMORY,
            title="Cron outcome",
            decision="Overnight cron spam",
            outcome="Fixed via Codex patch.",
            lesson=None,
            outcome_key="auto:outcome:cron",
            status=DecisionOutcomeStatus.MIXED,
            confidence=0.8,
            importance_score=0.8,
            event_time=_utcnow() - timedelta(hours=1),
            tags=["telegram", "derived", "decision-outcome", "mixed", "memory"],
        )
    ]

    context = await enrich_context(
        transport,
        "how should i approach telegram gateway latency now?",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Relevant prior outcomes:" not in context
    assert "I prefer green tea." not in context


@pytest.mark.asyncio
async def test_enrich_context_excludes_open_decision_outcomes_even_when_query_overlaps() -> None:
    _, _, transport = _build_client_with_transport()
    transport.decision_outcomes = [
        DecisionOutcome(
            id=uuid4(),
            agent_namespace="main",
            kind=DecisionOutcomeKind.MEMORY,
            title="debugging status",
            decision="I am debugging the gateway persistence issue.",
            outcome="I am debugging the gateway persistence issue and will continue shortly.",
            outcome_key="open-debugging",
            status=DecisionOutcomeStatus.OPEN,
            confidence=0.8,
            importance_score=0.8,
            event_time=_utcnow() - timedelta(hours=1),
            tags=["derived", "debugging"],
        )
    ]

    context = await enrich_context(
        transport,
        "how should i approach this debugging problem now?",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Relevant prior outcomes:" not in context
    assert "gateway persistence issue" not in context


@pytest.mark.asyncio
async def test_enrich_context_ignores_trivial_prior_conversations_for_low_signal_greeting() -> None:
    _, _, transport = _build_client_with_transport()
    transport.episodes = [
        Episode(
            id=uuid4(),
            session_id=uuid4(),
            agent_namespace="main",
            role=EpisodeRole.USER,
            content="hey!",
            content_hash=uuid4().hex,
            embedding=[0.0] * 512,
            platform=Platform.TELEGRAM,
            message_timestamp=_utcnow() - timedelta(hours=2),
        ),
        Episode(
            id=uuid4(),
            session_id=uuid4(),
            agent_namespace="main",
            role=EpisodeRole.ASSISTANT,
            content="hey bro",
            content_hash=uuid4().hex,
            embedding=[0.0] * 512,
            platform=Platform.TELEGRAM,
            message_timestamp=_utcnow() - timedelta(hours=2),
        ),
    ]

    context = await enrich_context(
        transport,
        "hey!",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Relevant prior conversations:\n- No semantically relevant episodes found." in context
    assert "hey bro" not in context


@pytest.mark.asyncio
async def test_enrich_context_builds_session_handoff_for_new_session_bootstrap() -> None:
    client, bridge, transport = _build_client_with_transport()
    active_session = await bridge.start_conversation("telegram")
    assert active_session is not None
    active_session_id = str(active_session.id)

    previous_session = Session(
        id=uuid4(),
        agent_namespace="main",
        platform=Platform.TELEGRAM,
        started_at=_utcnow() - timedelta(hours=2),
        ended_at=_utcnow() - timedelta(hours=1, minutes=30),
        message_count=6,
        user_message_count=3,
        summary="Debugged Telegram reply delays and session continuity after restart.",
        dominant_emotions=["frustration", "relief"],
    )
    transport.sessions[str(previous_session.id)] = previous_session
    transport.episodes = [
        Episode(
            id=uuid4(),
            session_id=previous_session.id,
            agent_namespace="main",
            role=EpisodeRole.USER,
            content="The bot is replying after 20 minutes and the continuity keeps breaking after restart.",
            content_hash=uuid4().hex,
            embedding=[0.0] * 512,
            platform=Platform.TELEGRAM,
            message_timestamp=_utcnow() - timedelta(hours=1, minutes=50),
            dominant_emotion="frustration",
            emotional_intensity=0.9,
        ),
        Episode(
            id=uuid4(),
            session_id=previous_session.id,
            agent_namespace="main",
            role=EpisodeRole.ASSISTANT,
            content="I traced the hot path and moved session recovery onto the warm worker so replies stop stalling on restart.",
            content_hash=uuid4().hex,
            embedding=[0.0] * 512,
            platform=Platform.TELEGRAM,
            message_timestamp=_utcnow() - timedelta(hours=1, minutes=45),
        ),
    ]
    transport.active_state = [
        ActiveState(
            id=uuid4(),
            agent_namespace="main",
            kind=ActiveStateKind.OPEN_LOOP,
            title="Open loop",
            content="Verify that the next fresh Telegram message still replies at normal speed after restart.",
            content_hash=uuid4().hex,
            state_key="auto:open-loop:telegram-restart",
            status=ActiveStateStatus.ACTIVE,
            confidence=0.9,
            priority_score=1.0,
            valid_from=_utcnow() - timedelta(hours=1),
            last_observed_at=_utcnow() - timedelta(minutes=5),
            tags=["derived"],
        )
    ]

    context = await enrich_context(
        transport,
        "hey",
        platform="telegram",
        active_session_id=active_session_id,
        agent_namespace="main",
    )

    assert "Recent cross-session continuity:" in context
    assert "Last thread: Debugged Telegram reply delays and session continuity after restart." in context
    assert "Carry forward: Verify that the next fresh Telegram message still replies at normal speed after restart." in context
    assert "Assistant was helping with: I traced the hot path and moved session recovery onto the warm worker so replies stop stalling on restart." in context
    assert "Last tone: frustration, relief" in context
    assert "[user, telegram]" not in context


@pytest.mark.asyncio
async def test_enrich_context_skips_low_value_last_line_handoff_noise() -> None:
    client, bridge, transport = _build_client_with_transport()
    active_session = await bridge.start_conversation("telegram")
    assert active_session is not None

    previous_session = Session(
        id=uuid4(),
        agent_namespace="main",
        platform=Platform.TELEGRAM,
        started_at=_utcnow() - timedelta(hours=2),
        ended_at=_utcnow() - timedelta(hours=1, minutes=40),
        message_count=4,
        user_message_count=2,
        summary="Worked through restart-time reply delays and continuity breakage in Telegram.",
        dominant_emotions=["frustration", "relief"],
    )
    transport.sessions[str(previous_session.id)] = previous_session
    transport.episodes = [
        Episode(
            id=uuid4(),
            session_id=previous_session.id,
            agent_namespace="main",
            role=EpisodeRole.USER,
            content="so what do uk about me",
            content_hash=uuid4().hex,
            embedding=[0.0] * 512,
            platform=Platform.TELEGRAM,
            message_timestamp=_utcnow() - timedelta(hours=1, minutes=50),
        ),
        Episode(
            id=uuid4(),
            session_id=previous_session.id,
            agent_namespace="main",
            role=EpisodeRole.ASSISTANT,
            content="yo, what's good?",
            content_hash=uuid4().hex,
            embedding=[0.0] * 512,
            platform=Platform.TELEGRAM,
            message_timestamp=_utcnow() - timedelta(hours=1, minutes=48),
        ),
    ]
    transport.active_state = [
        ActiveState(
            id=uuid4(),
            agent_namespace="main",
            kind=ActiveStateKind.OPEN_LOOP,
            title="Open loop",
            content="so what do uk about me",
            content_hash=uuid4().hex,
            state_key="auto:open-loop:generic-about-me",
            status=ActiveStateStatus.ACTIVE,
            confidence=0.8,
            priority_score=1.0,
            valid_from=_utcnow() - timedelta(hours=1),
            last_observed_at=_utcnow() - timedelta(minutes=5),
            tags=["derived"],
        )
    ]

    context = await enrich_context(
        transport,
        "hey",
        platform="telegram",
        active_session_id=str(active_session.id),
        agent_namespace="main",
    )

    assert "Recent cross-session continuity:" in context
    assert "Last thread: Worked through restart-time reply delays and continuity breakage in Telegram." in context
    assert "Carry forward:" not in context
    assert "Assistant was helping with:" not in context
    assert "Last tone:" not in context


@pytest.mark.asyncio
async def test_enrich_context_prefers_dedicated_session_handoff_record() -> None:
    client, bridge, transport = _build_client_with_transport()
    active_session = await bridge.start_conversation("telegram")
    assert active_session is not None

    previous_session_id = uuid4()
    transport.session_handoffs = [
        SessionHandoff(
            id=uuid4(),
            agent_namespace="main",
            session_id=previous_session_id,
            handoff_key=f"auto:handoff:{previous_session_id}",
            last_thread="Stabilized Telegram continuity and reply latency after the restart fixes.",
            carry_forward="Watch the next real user message and confirm replies stay fast.",
            assistant_context="I moved the hot path off cold subprocesses and onto the warm Atlas bridge worker.",
            emotional_tone="frustration, relief",
            confidence=0.92,
            source_episode_ids=[],
            source_session_ids=[previous_session_id],
            last_observed_at=_utcnow() - timedelta(minutes=2),
        )
    ]
    transport.episodes = [
        Episode(
            id=uuid4(),
            session_id=previous_session_id,
            agent_namespace="main",
            role=EpisodeRole.USER,
            content="yo",
            content_hash=uuid4().hex,
            embedding=[0.0] * 512,
            platform=Platform.TELEGRAM,
            message_timestamp=_utcnow() - timedelta(hours=1),
        )
    ]

    context = await enrich_context(
        transport,
        "hey",
        platform="telegram",
        active_session_id=str(active_session.id),
        agent_namespace="main",
    )

    assert "Recent cross-session continuity:" in context
    assert "Last thread: Stabilized Telegram continuity and reply latency after the restart fixes." in context
    assert "Carry forward: Watch the next real user message and confirm replies stay fast." in context
    assert "Assistant was helping with: I moved the hot path off cold subprocesses and onto the warm Atlas bridge worker." in context
    assert "Last tone: frustration, relief" in context
    assert "yo, what's good?" not in context
    assert "what do uk about me" not in context


@pytest.mark.asyncio
async def test_enrich_context_includes_relevant_patterns_for_advice_queries() -> None:
    _, _, transport = _build_client_with_transport()
    transport.patterns = [
        Pattern(
            id=uuid4(),
            agent_namespace="main",
            pattern_type=PatternType.DECISION_STYLE,
            statement="When something important feels broken, the user pushes for root-cause debugging before moving on.",
            description="Repeatedly asks what exactly happened and wants the underlying mechanism fixed.",
            pattern_key="auto:pattern:root-cause-debugging",
            confidence=0.9,
            frequency_score=0.8,
            impact_score=0.92,
            first_observed_at=_utcnow() - timedelta(days=2),
            last_observed_at=_utcnow() - timedelta(hours=2),
            tags=["derived", "pattern", "decision_style"],
        )
    ]

    context = await enrich_context(
        transport,
        "how should i approach this debugging problem now?",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Relevant patterns:" in context
    assert "root-cause debugging" in context
    assert transport.last_list_patterns_namespace == "main"


@pytest.mark.asyncio
async def test_enrich_context_includes_active_commitments_when_relevant() -> None:
    _, _, transport = _build_client_with_transport()
    transport.commitments = [
        Commitment(
            id=uuid4(),
            agent_namespace="main",
            kind=CommitmentKind.FIX,
            statement="I'll verify the Telegram delivery path and get back to you.",
            commitment_key="auto:commitment:verify-delivery",
            status=CommitmentStatus.OPEN,
            confidence=0.9,
            priority_score=0.9,
            first_committed_at=_utcnow() - timedelta(hours=2),
            last_observed_at=_utcnow() - timedelta(hours=1),
            tags=["derived"],
        )
    ]

    context = await enrich_context(
        transport,
        "what are you tracking right now?",
        platform="telegram",
        agent_namespace="main",
    )

    assert "Active commitments:" in context
    assert "verify the Telegram delivery path" in context
    assert transport.last_list_commitments_namespace == "main"


@pytest.mark.asyncio
async def test_enrich_context_uses_corrections_to_suppress_bad_memory() -> None:
    _, _, transport = _build_client_with_transport()
    transport.corrections = [
        Correction(
            id=uuid4(),
            agent_namespace="main",
            kind=CorrectionKind.MEMORY_DISPUTE,
            statement="That's wrong, I never sent soul rules before this message.",
            target_text="sent soul rules before this message",
            correction_key="auto:correction:soul-rules",
            active=True,
            confidence=0.95,
            first_observed_at=_utcnow() - timedelta(hours=2),
            last_observed_at=_utcnow() - timedelta(hours=1),
            tags=["derived"],
        )
    ]
    transport.episodes = [
        Episode(
            id=uuid4(),
            session_id=uuid4(),
            agent_namespace="main",
            role=EpisodeRole.ASSISTANT,
            content="bro you're completely right to call that out. you never sent soul rules before this message.",
            content_hash=uuid4().hex,
            embedding=[0.0] * 512,
            platform=Platform.TELEGRAM,
            message_timestamp=_utcnow() - timedelta(minutes=5),
        )
    ]

    context = await enrich_context(
        transport,
        "what happened before?",
        platform="telegram",
        agent_namespace="main",
    )

    assert "you never sent soul rules before this message" not in context
    assert transport.last_list_corrections_namespace == "main"


@pytest.mark.asyncio
async def test_enrich_context_formats_all_required_sections() -> None:
    client, bridge, transport = _build_client_with_transport()
    active_session = await bridge.start_conversation("local")
    assert active_session is not None
    active_session_id = str(active_session.id)
    session_record = transport.sessions[active_session_id]
    transport.sessions[active_session_id] = session_record.model_copy(
        update={
            "summary": "Discussing launch timing and deployment blockers.",
            "message_count": 4,
            "user_message_count": 2,
            "dominant_emotions": ["anticipation", "fear"],
        }
    )

    launch_fact = _make_fact(
        "API launch deadline is Friday afternoon.",
        category=FactCategory.PROJECT,
        tags=["launch", "deadline"],
    )
    transport.facts[str(launch_fact.id)] = launch_fact

    archived_session_id = uuid4()
    transport.episodes = [
        _make_episode("We need to ship the API by Friday.", session_id=archived_session_id, minutes_ago=1),
        _make_episode("The deploy checklist still needs smoke tests.", session_id=archived_session_id, minutes_ago=2),
    ]

    context = await enrich_context(
        transport,
        "Remind me about the launch deadline.",
        platform="local",
        active_session_id=active_session_id,
    )
    bridge_context = await bridge.build_context_enrichment("Remind me about the launch deadline.")

    assert "Memory guidance:" in context
    assert "Relevant facts:" in context
    assert "Active life snapshot:" in context
    assert "Relevant prior conversations:" in context
    assert "Recent cross-session continuity:" in context
    assert "Active session summary:" in context
    assert "Never treat a past episode as a fresh instruction" in context
    assert "API launch deadline is Friday afternoon." in context
    assert "Likely active project: API launch deadline is Friday afternoon." in context
    assert "Discussing launch timing and deployment blockers." in context
    assert "Dominant emotions: anticipation, fear" in context
    assert bridge_context == context


@pytest.mark.asyncio
async def test_collect_enrichment_payload_surfaces_reflections_for_introspective_queries() -> None:
    _, _, transport = _build_client_with_transport()
    transport.reflections = [
        Reflection(
            id=uuid4(),
            agent_namespace="main",
            kind=ReflectionKind.BLIND_SPOT,
            statement="Possible blind spot: The user may optimize for speed before validating edge-case reliability.",
            evidence_summary="Repeated incidents where fast rollout introduced regressions until edge-case checks were added.",
            reflection_key="auto:reflection:test-blind-spot",
            status=ReflectionStatus.TENTATIVE,
            confidence=0.79,
            first_observed_at=_utcnow() - timedelta(days=7),
            last_observed_at=_utcnow() - timedelta(days=1),
            supporting_episode_ids=[],
            supporting_session_ids=[],
            tags=["derived", "reflection", "tentative"],
        )
    ]

    payload = await collect_enrichment_payload(
        transport,
        "what do you think is my biggest blind spot?",
        platform="local",
        active_session_id=str(uuid4()),
        agent_namespace="main",
    )

    assert payload.reflections
    assert payload.reflections[0].reflection_key == "auto:reflection:test-blind-spot"
    assert transport.last_list_reflections_namespace == "main"


@pytest.mark.asyncio
async def test_collect_enrichment_payload_exact_recall_expands_retrieval_limits() -> None:
    _, _, transport = _build_client_with_transport()

    payload = await collect_enrichment_payload(
        transport,
        "what were my last few messages before this session?",
        platform="local",
        active_session_id=str(uuid4()),
        agent_namespace="main",
    )

    assert payload.exact_recall_mode is True
    assert transport.last_search_episodes_limit == 24
    assert transport.last_recent_episodes_limit == 24


@pytest.mark.asyncio
async def test_enrich_context_exact_recall_returns_verbatim_recent_user_messages() -> None:
    client, _, transport = _build_client_with_transport()
    active_session = await client.start_session(platform="signal")
    previous_session = await client.start_session(platform="signal")

    previous_session_id = previous_session.id
    assert previous_session_id is not None

    transport.episodes = [
        _make_episode("how would u like this to be get better?", session_id=previous_session_id, minutes_ago=14, platform=Platform.LOCAL),
        _make_episode("yes please. Also let me know when u call supabase", session_id=previous_session_id, minutes_ago=13, platform=Platform.LOCAL),
        _make_episode("source freshness confidence and why this matched", session_id=previous_session_id, minutes_ago=12, platform=Platform.LOCAL),
    ]

    context = await enrich_context(
        transport,
        "before this session what were my last few texts to you?",
        platform="signal",
        active_session_id=str(active_session.id),
        agent_namespace="main",
    )

    assert "Exact recent user messages (verbatim):" in context
    assert "how would u like this to be get better?" in context
    assert "yes please. Also let me know when u call supabase" in context
    assert "Verbatim transcript evidence:" in context
    assert "quote_status=quote-backed" in context
    assert "Retrieval evidence:" in context
    assert "Mode: exact_transcript" in context
    assert "Relevant prior outcomes:" not in context
    assert "Relevant patterns:" not in context


@pytest.mark.asyncio
async def test_enrich_context_exact_recall_reports_missing_verbatim_history_cleanly() -> None:
    client, _, transport = _build_client_with_transport()
    active_session = await client.start_session(platform="local")

    context = await enrich_context(
        transport,
        "what were my last few messages before this session?",
        platform="local",
        active_session_id=str(active_session.id),
        agent_namespace="main",
    )

    assert "Exact recent user messages (verbatim):" in context
    assert "No exact prior user messages were available" in context
    assert "Verbatim transcript evidence:" in context
    assert "No raw transcript snippets were selected for this turn." in context
    assert "quote_status=no-quote-available" in context
    assert "Retrieval evidence:" in context
    assert "Mode: exact_transcript" in context


@pytest.mark.asyncio
async def test_enrich_context_injects_always_known_profile_for_non_profile_queries() -> None:
    _, _, transport = _build_client_with_transport()
    identity_fact = _make_fact(
        "Ishaan is a solo founder building Upstorr and optimizing AI-assisted workflows.",
        category=FactCategory.IDENTITY,
        tags=["identity", "career"],
        hours_ago=1,
    )
    goal_fact = _make_fact(
        "Current long-term goal is building a reliable always-on assistant for life and work.",
        category=FactCategory.GOAL,
        tags=["assistant", "long-term"],
        hours_ago=2,
    )
    transport.facts = {
        str(identity_fact.id): identity_fact,
        str(goal_fact.id): goal_fact,
    }

    context = await enrich_context(
        transport,
        "help me plan today's execution path",
        platform="local",
        agent_namespace="main",
    )

    assert "Always-known user profile:" in context
    assert "Ishaan is a solo founder building Upstorr" in context
    assert "Current long-term goal is building a reliable always-on assistant" in context


@pytest.mark.asyncio
async def test_enrich_context_keeps_concise_identity_in_always_known_profile() -> None:
    _, _, transport = _build_client_with_transport()
    identity_fact = _make_fact(
        "User is a marwari.",
        category=FactCategory.IDENTITY,
        tags=["identity"],
        hours_ago=1,
    )
    transport.facts = {
        str(identity_fact.id): identity_fact,
    }

    context = await enrich_context(
        transport,
        "help me plan this week",
        platform="local",
        agent_namespace="main",
    )

    assert "Always-known user profile:" in context
    assert "User is a marwari." in context


@pytest.mark.asyncio
async def test_enrich_context_injects_explicit_always_on_identity_layer() -> None:
    _, _, transport = _build_client_with_transport()
    transport.facts = {
        str(uuid4()): _make_fact(
            "User is from a marwari family.",
            category=FactCategory.IDENTITY,
            tags=["identity"],
            hours_ago=1,
        ),
        str(uuid4()): _make_fact(
            "User prefers concise and direct replies.",
            category=FactCategory.PREFERENCE,
            tags=["communication", "preference"],
            hours_ago=1,
        ),
    }
    transport.directives = [
        Directive(
            id=uuid4(),
            agent_namespace="main",
            kind=DirectiveKind.COMMUNICATION,
            scope=DirectiveScope.GLOBAL,
            title="Concise tone",
            content="Always keep replies concise and direct.",
            content_hash=uuid4().hex,
            directive_key="auto:directive:concise",
            status=DirectiveStatus.ACTIVE,
            confidence=0.95,
            priority_score=1.0,
            last_observed_at=_utcnow(),
        )
    ]

    context = await enrich_context(
        transport,
        "help me decide what to do next",
        platform="local",
        agent_namespace="main",
    )

    assert "Always-on identity layer:" in context
    assert "User is from a marwari family." in context
    assert "User prefers concise and direct replies." in context
    assert "[communication] directive: Always keep replies concise and direct." in context


@pytest.mark.asyncio
async def test_enrich_context_identity_layer_marks_confirmed_and_superseded_values() -> None:
    _, _, transport = _build_client_with_transport()
    transport.facts = {
        str(uuid4()): _make_fact(
            "User's religion is jain.",
            category=FactCategory.IDENTITY,
            tags=["identity", "identity_slot:religion", "identity_state:affirmed"],
            hours_ago=6,
            confidence=0.88,
        ),
        str(uuid4()): _make_fact(
            "User's religion is hindu.",
            category=FactCategory.IDENTITY,
            tags=["identity", "identity_slot:religion", "identity_state:affirmed"],
            hours_ago=2,
            confidence=0.9,
        ),
        str(uuid4()): _make_fact(
            "User's religion is hindu.",
            category=FactCategory.IDENTITY,
            tags=["identity", "identity_slot:religion", "identity_state:affirmed"],
            hours_ago=1,
            confidence=0.92,
        ),
    }

    context = await enrich_context(
        transport,
        "what should you keep in mind about me",
        platform="local",
        agent_namespace="main",
    )

    assert "Always-on identity layer:" in context
    assert "[religion] confirmed: hindu" in context.lower()
    assert "superseded: jain" in context.lower()


@pytest.mark.asyncio
async def test_enrich_context_identity_layer_marks_revoked_value() -> None:
    _, _, transport = _build_client_with_transport()
    transport.facts = {
        str(uuid4()): _make_fact(
            "User's religion is hindu.",
            category=FactCategory.IDENTITY,
            tags=["identity", "identity_slot:religion", "identity_state:affirmed"],
            hours_ago=8,
        ),
        str(uuid4()): _make_fact(
            "User's religion is not hindu.",
            category=FactCategory.IDENTITY,
            tags=["identity", "identity_slot:religion", "identity_state:revoked", "identity_revoked"],
            hours_ago=1,
        ),
    }

    context = await enrich_context(
        transport,
        "recall my stable identity",
        platform="local",
        agent_namespace="main",
    )

    assert "Always-on identity layer:" in context
    assert "[religion] revoked: hindu" in context.lower()


@pytest.mark.asyncio
async def test_enrich_context_identity_layer_marks_uncertain_when_no_affirmed_value() -> None:
    _, _, transport = _build_client_with_transport()
    transport.facts = {
        str(uuid4()): _make_fact(
            "User may be marwari.",
            category=FactCategory.IDENTITY,
            tags=["identity", "identity_slot:identity", "identity_state:uncertain", "identity_uncertain"],
            hours_ago=1,
            confidence=0.62,
        ),
    }

    context = await enrich_context(
        transport,
        "what do you know for sure",
        platform="local",
        agent_namespace="main",
    )

    assert "Always-on identity layer:" in context
    assert "[identity] uncertain: marwari" in context.lower()


@pytest.mark.asyncio
async def test_enrich_context_proactive_coach_warns_using_past_failures_and_wins() -> None:
    _, _, transport = _build_client_with_transport()
    now = _utcnow()
    transport.decision_outcomes = [
        DecisionOutcome(
            id=uuid4(),
            agent_namespace="main",
            kind=DecisionOutcomeKind.WORKFLOW,
            title="checkout migration failure",
            decision="Rushed checkout migration rollout without verification gates.",
            outcome="Production regressions and rework consumed two days.",
            lesson="Ship with replay-eval and CI regression gates before full rollout.",
            outcome_key="auto:outcome:checkout-failure",
            status=DecisionOutcomeStatus.FAILURE,
            confidence=0.95,
            importance_score=0.95,
            event_time=now - timedelta(days=20),
            tags=["checkout", "migration", "rollout"],
        ),
        DecisionOutcome(
            id=uuid4(),
            agent_namespace="main",
            kind=DecisionOutcomeKind.WORKFLOW,
            title="checkout migration success",
            decision="Staged checkout migration with replay harness and canary checks.",
            outcome="Cut regressions and reduced rollback risk significantly.",
            lesson="Start with canary plus replay coverage, then gradual traffic ramp.",
            outcome_key="auto:outcome:checkout-success",
            status=DecisionOutcomeStatus.SUCCESS,
            confidence=0.92,
            importance_score=0.91,
            event_time=now - timedelta(days=10),
            tags=["checkout", "migration", "rollout"],
        ),
    ]
    transport.patterns = [
        Pattern(
            id=uuid4(),
            agent_namespace="main",
            pattern_type=PatternType.TRAP,
            statement="Under deadline pressure, rollout speed tends to outrun verification depth.",
            description="Repeated cross-session failure mode during shipping crunch windows.",
            pattern_key="auto:pattern:verification-trap",
            confidence=0.9,
            frequency_score=0.85,
            impact_score=0.9,
            first_observed_at=now - timedelta(days=90),
            last_observed_at=now - timedelta(days=2),
            supporting_episode_ids=[],
            supporting_session_ids=[],
            tags=["rollout", "verification", "deadline"],
        )
    ]

    context = await enrich_context(
        transport,
        "I am working on checkout migration rollout right now, what should I do?",
        platform="local",
        agent_namespace="main",
    )

    assert "Proactive coach notes:" in context
    assert "Past risk to avoid:" in context
    assert "Proven better path:" in context
    assert "Recurring trap signal:" in context


@pytest.mark.asyncio
async def test_enrich_context_surfaces_analogous_memory_cases_for_advice_queries() -> None:
    _, _, transport = _build_client_with_transport()
    now = _utcnow()
    linked_outcome_id = uuid4()

    transport.decision_outcomes = [
        DecisionOutcome(
            id=linked_outcome_id,
            agent_namespace="main",
            kind=DecisionOutcomeKind.WORKFLOW,
            title="checkout migration failure",
            decision="Rushed checkout migration rollout without verification gates.",
            outcome="Production regressions and rework consumed two days.",
            lesson="Ship with replay-eval and CI regression gates before full rollout.",
            outcome_key="auto:outcome:checkout-failure",
            status=DecisionOutcomeStatus.FAILURE,
            confidence=0.95,
            importance_score=0.95,
            event_time=now - timedelta(days=20),
            tags=["checkout", "migration", "rollout"],
        ),
    ]
    transport.memory_cases = [
        MemoryCase(
            id=uuid4(),
            agent_namespace="main",
            case_key="auto:case:checkout-rollout",
            title="Checkout rollout without safety gates",
            problem_statement="Checkout migration rollout moved too fast without verification gates.",
            resolution_summary="Start with replay eval and canary gating before broad rollout.",
            outcome_status=CaseOutcomeStatus.FAILURE,
            confidence=0.88,
            impact_score=0.9,
            first_observed_at=now - timedelta(days=60),
            last_observed_at=now - timedelta(days=10),
            source_outcome_ids=[linked_outcome_id],
            source_pattern_ids=[],
            source_episode_ids=[],
            tags=["checkout", "migration", "rollout", "verification"],
            created_at=now - timedelta(days=10),
            updated_at=now - timedelta(days=10),
        )
    ]

    context = await enrich_context(
        transport,
        "I am working on checkout migration rollout right now, what should I do?",
        platform="local",
        agent_namespace="main",
    )

    assert "Analogous past cases:" in context
    assert "Checkout rollout without safety gates" in context
    assert transport.last_list_memory_cases_namespace == "main"


@pytest.mark.asyncio
async def test_enrich_context_analogous_case_second_pass_prefers_evidence_rich_failures() -> None:
    _, _, transport = _build_client_with_transport()
    now = _utcnow()
    linked_outcome_id = uuid4()

    transport.decision_outcomes = [
        DecisionOutcome(
            id=linked_outcome_id,
            agent_namespace="main",
            kind=DecisionOutcomeKind.WORKFLOW,
            title="checkout migration failure",
            decision="Rushed checkout migration rollout without verification gates.",
            outcome="Production regressions and rework consumed two days.",
            lesson="Ship with replay-eval and CI regression gates before full rollout.",
            outcome_key="auto:outcome:checkout-failure",
            status=DecisionOutcomeStatus.FAILURE,
            confidence=0.95,
            importance_score=0.95,
            event_time=now - timedelta(days=20),
            tags=["checkout", "migration", "rollout"],
        ),
    ]
    transport.memory_cases = [
        MemoryCase(
            id=uuid4(),
            agent_namespace="main",
            case_key="auto:case:generic-checklist",
            title="Generic migration checklist",
            problem_statement="Checkout migration checklist with broad steps.",
            resolution_summary="Follow the default checklist.",
            outcome_status=CaseOutcomeStatus.SUCCESS,
            confidence=0.62,
            impact_score=0.42,
            first_observed_at=now - timedelta(days=80),
            last_observed_at=now - timedelta(days=3),
            source_outcome_ids=[],
            source_pattern_ids=[],
            source_episode_ids=[],
            tags=["checkout", "migration", "rollout"],
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(days=3),
        ),
        MemoryCase(
            id=uuid4(),
            agent_namespace="main",
            case_key="auto:case:checkout-rollout",
            title="Checkout rollout without safety gates",
            problem_statement="Checkout migration rollout moved too fast without verification gates.",
            resolution_summary="Start with replay eval and canary gating before broad rollout.",
            outcome_status=CaseOutcomeStatus.FAILURE,
            confidence=0.9,
            impact_score=0.92,
            first_observed_at=now - timedelta(days=60),
            last_observed_at=now - timedelta(days=10),
            source_outcome_ids=[linked_outcome_id],
            source_pattern_ids=[],
            source_episode_ids=[uuid4()],
            tags=["checkout", "migration", "rollout", "verification"],
            created_at=now - timedelta(days=10),
            updated_at=now - timedelta(days=10),
        ),
    ]

    context = await enrich_context(
        transport,
        "I am working on checkout migration rollout right now, what should I do?",
        platform="local",
        agent_namespace="main",
    )

    assert "Analogous past cases:" in context
    assert context.index("Checkout rollout without safety gates") < context.index("Generic migration checklist")


@pytest.mark.asyncio
async def test_enrich_context_week_number_query_targets_requested_week_rollup() -> None:
    _, _, transport = _build_client_with_transport()
    week_32_event_time = datetime.fromisocalendar(2026, 32, 3).replace(tzinfo=timezone.utc)
    week_31_event_time = datetime.fromisocalendar(2026, 31, 3).replace(tzinfo=timezone.utc)
    week_32_title_date = datetime.fromisocalendar(2026, 32, 1).date().isoformat()
    week_31_title_date = datetime.fromisocalendar(2026, 31, 1).date().isoformat()

    transport.timeline_events = [
        TimelineEvent(
            id=uuid4(),
            agent_namespace="main",
            kind=TimelineEventKind.WEEK_SUMMARY,
            title=f"Week of {week_31_title_date}",
            summary="Focus drifted across too many initiatives and no single thread dominated.",
            event_key="auto:timeline-rollup:week-31",
            event_time=week_31_event_time,
            tags=["week-summary"],
            importance_score=0.72,
        ),
        TimelineEvent(
            id=uuid4(),
            agent_namespace="main",
            kind=TimelineEventKind.WEEK_SUMMARY,
            title=f"Week of {week_32_title_date}",
            summary="The main recurring topic was memory reliability, replay eval, and regression gates.",
            event_key="auto:timeline-rollup:week-32",
            event_time=week_32_event_time,
            tags=["week-summary", "memory"],
            importance_score=0.83,
        ),
    ]

    context = await enrich_context(
        transport,
        "in the timeline, what topic did we keep returning to in week 32 of 2026?",
        platform="local",
        agent_namespace="main",
    )

    assert "Recent major events:" in context
    assert "memory reliability, replay eval, and regression gates" in context
    assert "Focus drifted across too many initiatives" not in context


@pytest.mark.asyncio
async def test_enrich_context_emits_trust_ledger_with_source_tags_and_freshness() -> None:
    _, _, transport = _build_client_with_transport()
    now = _utcnow()
    transport.facts = {
        str(uuid4()): _make_fact(
            "Ishaan is a solo founder building Upstorr and optimizing AI-assisted workflows.",
            category=FactCategory.IDENTITY,
            tags=["identity", "career"],
            hours_ago=2,
        )
    }
    transport.decision_outcomes = [
        DecisionOutcome(
            id=uuid4(),
            agent_namespace="main",
            kind=DecisionOutcomeKind.WORKFLOW,
            title="checkout migration failure",
            decision="Rushed checkout migration rollout without verification gates.",
            outcome="Production regressions and rework consumed two days.",
            lesson="Ship with replay-eval and CI regression gates before full rollout.",
            outcome_key="auto:outcome:checkout-failure",
            status=DecisionOutcomeStatus.FAILURE,
            confidence=0.95,
            importance_score=0.95,
            event_time=now - timedelta(days=3),
            tags=["checkout", "migration", "rollout"],
        )
    ]
    transport.patterns = [
        Pattern(
            id=uuid4(),
            agent_namespace="main",
            pattern_type=PatternType.TRAP,
            statement="Under deadline pressure, rollout speed tends to outrun verification depth.",
            description="Repeated cross-session failure mode during shipping crunch windows.",
            pattern_key="auto:pattern:verification-trap",
            confidence=0.9,
            frequency_score=0.85,
            impact_score=0.9,
            first_observed_at=now - timedelta(days=90),
            last_observed_at=now - timedelta(days=2),
            supporting_episode_ids=[],
            supporting_session_ids=[],
            tags=["rollout", "verification", "deadline"],
        )
    ]

    context = await enrich_context(
        transport,
        "I am working on checkout migration rollout right now, what should I do?",
        platform="local",
        agent_namespace="main",
    )

    assert "Trust ledger (source tags + freshness):" in context
    assert "[source:fact]" in context
    assert "[source:outcome]" in context
    assert "[source:pattern]" in context
    assert "freshness=" in context
    assert "state=" in context
    assert "wording=" in context
    assert "certainty=" in context
    assert "Quote coverage checks:" in context
    assert "quote_status=no-quote-available" in context


@pytest.mark.asyncio
async def test_enrich_context_emits_verbatim_snippets_for_semantic_mode() -> None:
    client, _, transport = _build_client_with_transport()
    active_session = await client.start_session(platform="local")
    previous_session = await client.start_session(platform="local")
    previous_session_id = previous_session.id
    assert previous_session_id is not None

    transport.episodes = [
        _make_episode(
            "I messed this up before by rushing rollout without canary checks.",
            session_id=previous_session_id,
            minutes_ago=32,
            platform=Platform.LOCAL,
        ),
        _make_episode(
            "Exact quote should be preserved, not paraphrased away.",
            session_id=previous_session_id,
            minutes_ago=31,
            platform=Platform.LOCAL,
        ),
    ]
    rollout_fact = _make_fact(
        "User has repeatedly faced rollout regressions when verification was skipped.",
        category=FactCategory.PROJECT,
        tags=["rollout", "verification"],
        hours_ago=2,
    )
    transport.facts = {str(rollout_fact.id): rollout_fact}

    context = await enrich_context(
        transport,
        "how should i handle this rollout now?",
        platform="local",
        active_session_id=str(active_session.id),
        agent_namespace="main",
    )

    assert "Verbatim transcript evidence:" in context
    assert "Exact quote should be preserved, not paraphrased away." in context
    assert "wording=verbatim" in context
    assert "quote_status=quote-backed" in context


@pytest.mark.asyncio
async def test_enrich_context_quote_coverage_marks_no_quote_when_no_snippet_available() -> None:
    _, _, transport = _build_client_with_transport()
    transport.facts = {
        str(uuid4()): _make_fact(
            "Current long-term goal is building a reliable always-on assistant for life and work.",
            category=FactCategory.GOAL,
            tags=["assistant", "long-term"],
            hours_ago=2,
        )
    }

    context = await enrich_context(
        transport,
        "what do you know about my long-term direction?",
        platform="local",
        agent_namespace="main",
    )

    assert "Quote coverage checks:" in context
    assert "quote_status=no-quote-available" in context
