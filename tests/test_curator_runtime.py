from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from memory import curator_runtime as runtime
from memory.backfill import backfill_memory_files
from memory.client import MemoryClient
from memory.consolidation import (
    _decision_outcome_key,
    consolidate_recent_sessions,
    extract_facts_from_recent_sessions,
    refresh_active_state,
    refresh_commitments,
    refresh_corrections,
    refresh_decision_outcomes,
    refresh_directives,
    refresh_memory_cases,
    refresh_patterns,
    refresh_reflections,
    refresh_timeline_events,
)
from memory.embedding import MockEmbeddingProvider
from memory.emotions import EmotionAnalyzer
from memory.instance_identity import get_agent_namespace
from memory.models import ActiveState, Commitment, CommitmentStatus, Correction, CorrectionKind, DecisionOutcome, DecisionOutcomeKind, DecisionOutcomeStatus, Directive, Episode, EpisodeRole, Fact, FactCategory, FactHistory, MemoryCase, Pattern, PatternType, Platform, Reflection, Session, TimelineEvent, TimelineEventKind


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CuratorRuntimeTransport:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.episodes_by_session: dict[str, list[Episode]] = {}
        self.facts: dict[str, Fact] = {}
        self.history: list[FactHistory] = []
        self.active_state: list[ActiveState] = []
        self.directives: list[Directive] = []
        self.timeline_events: list[TimelineEvent] = []
        self.memory_cases: list[MemoryCase] = []
        self.case_evidence_links: list[dict[str, object]] = []
        self.decision_outcomes: list[DecisionOutcome] = []
        self.patterns: list[Pattern] = []
        self.reflections: list[Reflection] = []
        self.commitments: list[Commitment] = []
        self.corrections: list[Correction] = []
        self.healthy = True

    async def insert_session(self, session: Session) -> Session:
        if session.id is None:
            session = session.model_copy(update={"id": uuid4()})
        self.sessions[str(session.id)] = session
        return session

    async def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    async def update_session(self, session_id: str, updates: dict) -> Session:
        session = self.sessions[session_id]
        updated = session.model_copy(update=updates)
        self.sessions[session_id] = updated
        return updated

    async def insert_episode(self, episode: Episode) -> Episode:
        if episode.id is None:
            episode = episode.model_copy(update={"id": uuid4()})
        self.episodes_by_session.setdefault(str(episode.session_id), []).append(episode)
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
        return None

    async def search_episodes(
        self,
        query: str,
        limit: int = 20,
        platform: str | None = None,
        days_back: int = 30,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        _ = (query, platform, days_back, agent_namespace)
        episodes = [episode for values in self.episodes_by_session.values() for episode in values]
        return episodes[:limit]

    async def list_recent_episodes(
        self,
        limit: int = 5,
        platform: str | None = None,
        exclude_session_id: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        _ = agent_namespace
        episodes = [episode for values in self.episodes_by_session.values() for episode in values]
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
        if category is not None:
            facts = [fact for fact in facts if fact.category.value == category]
        if tags:
            facts = [fact for fact in facts if all(tag in fact.tags for tag in tags)]
        return facts[:limit]

    async def insert_fact_history(self, history: FactHistory) -> FactHistory:
        if history.id is None:
            history = history.model_copy(update={"id": uuid4()})
        self.history.append(history)
        return history

    async def upsert_active_state(self, state: ActiveState) -> ActiveState:
        self.active_state = [existing for existing in self.active_state if existing.state_key != state.state_key]
        if state.id is None:
            state = state.model_copy(update={"id": uuid4()})
        self.active_state.append(state)
        return state

    async def list_active_state(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[ActiveState]:
        states = list(self.active_state)
        if agent_namespace is not None:
            states = [
                state
                for state in states
                if state.agent_namespace == agent_namespace or (agent_namespace == "main" and state.agent_namespace is None)
            ]
        if statuses:
            allowed = set(statuses)
            states = [state for state in states if state.status.value in allowed]
        states.sort(key=lambda state: (state.priority_score, state.last_observed_at), reverse=True)
        return states[:limit]

    async def upsert_directive(self, directive: Directive) -> Directive:
        self.directives = [existing for existing in self.directives if existing.directive_key != directive.directive_key]
        if directive.id is None:
            directive = directive.model_copy(update={"id": uuid4()})
        self.directives.append(directive)
        return directive

    async def list_directives(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Directive]:
        directives = list(self.directives)
        if agent_namespace is not None:
            directives = [
                directive
                for directive in directives
                if directive.agent_namespace == agent_namespace or (agent_namespace == "main" and directive.agent_namespace is None)
            ]
        if statuses:
            allowed = set(statuses)
            directives = [directive for directive in directives if directive.status.value in allowed]
        directives.sort(key=lambda directive: (directive.priority_score, directive.last_observed_at or _utcnow()), reverse=True)
        return directives[:limit]

    async def upsert_timeline_event(self, event: TimelineEvent) -> TimelineEvent:
        self.timeline_events = [existing for existing in self.timeline_events if existing.event_key != event.event_key]
        if event.id is None:
            event = event.model_copy(update={"id": uuid4()})
        self.timeline_events.append(event)
        return event

    async def list_timeline_events(self, limit: int = 10, agent_namespace: str | None = None) -> list[TimelineEvent]:
        events = list(self.timeline_events)
        if agent_namespace is not None:
            events = [
                event
                for event in events
                if event.agent_namespace == agent_namespace or (agent_namespace == "main" and event.agent_namespace is None)
            ]
        events.sort(key=lambda event: event.event_time, reverse=True)
        return events[:limit]

    async def upsert_decision_outcome(self, outcome: DecisionOutcome) -> DecisionOutcome:
        self.decision_outcomes = [existing for existing in self.decision_outcomes if existing.outcome_key != outcome.outcome_key]
        if outcome.id is None:
            outcome = outcome.model_copy(update={"id": uuid4()})
        self.decision_outcomes.append(outcome)
        return outcome

    async def list_decision_outcomes(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[DecisionOutcome]:
        outcomes = list(self.decision_outcomes)
        if agent_namespace is not None:
            outcomes = [
                outcome
                for outcome in outcomes
                if outcome.agent_namespace == agent_namespace or (agent_namespace == "main" and outcome.agent_namespace is None)
            ]
        if statuses:
            allowed = set(statuses)
            outcomes = [outcome for outcome in outcomes if outcome.status.value in allowed]
        outcomes.sort(key=lambda outcome: (outcome.importance_score, outcome.event_time), reverse=True)
        return outcomes[:limit]

    async def delete_decision_outcome(
        self,
        outcome_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        before = len(self.decision_outcomes)
        def _matches(namespace: str | None) -> bool:
            return namespace == agent_namespace or (agent_namespace == "main" and namespace is None)

        self.decision_outcomes = [
            outcome
            for outcome in self.decision_outcomes
            if not (
                outcome.outcome_key == outcome_key
                and _matches(getattr(outcome, "agent_namespace", None))
            )
        ]
        return len(self.decision_outcomes) != before

    async def upsert_pattern(self, pattern: Pattern) -> Pattern:
        self.patterns = [existing for existing in self.patterns if existing.pattern_key != pattern.pattern_key]
        if pattern.id is None:
            pattern = pattern.model_copy(update={"id": uuid4()})
        self.patterns.append(pattern)
        return pattern

    async def list_patterns(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        pattern_types: list[str] | None = None,
    ) -> list[Pattern]:
        patterns = list(self.patterns)
        if agent_namespace is not None:
            patterns = [
                pattern
                for pattern in patterns
                if pattern.agent_namespace == agent_namespace or (agent_namespace == "main" and pattern.agent_namespace is None)
            ]
        if pattern_types:
            allowed = set(pattern_types)
            patterns = [pattern for pattern in patterns if pattern.pattern_type.value in allowed]
        patterns.sort(key=lambda pattern: (pattern.impact_score, pattern.last_observed_at), reverse=True)
        return patterns[:limit]

    async def delete_pattern(
        self,
        pattern_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        before = len(self.patterns)
        def _matches(namespace: str | None) -> bool:
            return namespace == agent_namespace or (agent_namespace == "main" and namespace is None)

        self.patterns = [
            pattern
            for pattern in self.patterns
            if not (
                pattern.pattern_key == pattern_key
                and _matches(getattr(pattern, "agent_namespace", None))
            )
        ]
        return len(self.patterns) != before

    async def upsert_memory_case(self, case: MemoryCase) -> MemoryCase:
        self.memory_cases = [existing for existing in self.memory_cases if existing.case_key != case.case_key]
        if case.id is None:
            case = case.model_copy(update={"id": uuid4()})
        self.memory_cases.append(case)
        return case

    async def list_memory_cases(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        outcome_statuses: list[str] | None = None,
    ) -> list[MemoryCase]:
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

    async def delete_memory_case(
        self,
        case_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        before = len(self.memory_cases)

        def _matches(namespace: str | None) -> bool:
            return namespace == agent_namespace or (agent_namespace == "main" and namespace is None)

        self.memory_cases = [
            case
            for case in self.memory_cases
            if not (
                case.case_key == case_key
                and _matches(getattr(case, "agent_namespace", None))
            )
        ]
        return len(self.memory_cases) != before

    async def upsert_case_evidence_link(self, link):
        self.case_evidence_links = [
            existing
            for existing in self.case_evidence_links
            if not (
                str(existing.get("case_id")) == str(link.case_id)
                and str(existing.get("evidence_type")) == link.evidence_type.value
                and str(existing.get("evidence_id")) == str(link.evidence_id)
            )
        ]
        self.case_evidence_links.append(
            {
                "id": str(link.id or uuid4()),
                "agent_namespace": link.agent_namespace,
                "case_id": str(link.case_id),
                "evidence_type": link.evidence_type.value,
                "evidence_id": str(link.evidence_id),
                "relevance_score": float(link.relevance_score),
                "note": link.note,
            }
        )
        return link

    async def list_case_evidence_links(
        self,
        case_id: str,
        limit: int = 50,
        agent_namespace: str | None = None,
    ):
        links = [
            link
            for link in self.case_evidence_links
            if str(link.get("case_id")) == str(case_id)
            if agent_namespace is None
            or link.get("agent_namespace") == agent_namespace
            or (agent_namespace == "main" and link.get("agent_namespace") is None)
        ]
        links.sort(key=lambda link: float(link.get("relevance_score") or 0.0), reverse=True)
        return links[:limit]

    async def upsert_reflection(self, reflection: Reflection) -> Reflection:
        self.reflections = [existing for existing in self.reflections if existing.reflection_key != reflection.reflection_key]
        if reflection.id is None:
            reflection = reflection.model_copy(update={"id": uuid4()})
        self.reflections.append(reflection)
        return reflection

    async def list_reflections(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Reflection]:
        reflections = list(self.reflections)
        if agent_namespace is not None:
            reflections = [
                reflection
                for reflection in reflections
                if reflection.agent_namespace == agent_namespace or (agent_namespace == "main" and reflection.agent_namespace is None)
            ]
        if statuses:
            allowed = set(statuses)
            reflections = [reflection for reflection in reflections if reflection.status.value in allowed]
        reflections.sort(key=lambda reflection: (reflection.confidence, reflection.last_observed_at), reverse=True)
        return reflections[:limit]

    async def delete_reflection(
        self,
        reflection_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        before = len(self.reflections)

        def _matches(namespace: str | None) -> bool:
            return namespace == agent_namespace or (agent_namespace == "main" and namespace is None)

        self.reflections = [
            reflection
            for reflection in self.reflections
            if not (
                reflection.reflection_key == reflection_key
                and _matches(getattr(reflection, "agent_namespace", None))
            )
        ]
        return len(self.reflections) != before

    async def upsert_commitment(self, commitment: Commitment) -> Commitment:
        self.commitments = [existing for existing in self.commitments if existing.commitment_key != commitment.commitment_key]
        if commitment.id is None:
            commitment = commitment.model_copy(update={"id": uuid4()})
        self.commitments.append(commitment)
        return commitment

    async def list_commitments(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Commitment]:
        commitments = list(self.commitments)
        if agent_namespace is not None:
            commitments = [
                item
                for item in commitments
                if item.agent_namespace == agent_namespace or (agent_namespace == "main" and item.agent_namespace is None)
            ]
        if statuses:
            allowed = set(statuses)
            commitments = [item for item in commitments if item.status.value in allowed]
        commitments.sort(key=lambda item: (item.priority_score, item.last_observed_at), reverse=True)
        return commitments[:limit]

    async def upsert_correction(self, correction: Correction) -> Correction:
        self.corrections = [existing for existing in self.corrections if existing.correction_key != correction.correction_key]
        if correction.id is None:
            correction = correction.model_copy(update={"id": uuid4()})
        self.corrections.append(correction)
        return correction

    async def list_corrections(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        active_only: bool = True,
    ) -> list[Correction]:
        corrections = list(self.corrections)
        if agent_namespace is not None:
            corrections = [
                item
                for item in corrections
                if item.agent_namespace == agent_namespace or (agent_namespace == "main" and item.agent_namespace is None)
            ]
        if active_only:
            corrections = [item for item in corrections if item.active]
        corrections.sort(key=lambda item: item.last_observed_at, reverse=True)
        return corrections[:limit]

    async def health_check(self) -> bool:
        return self.healthy

    async def list_recent_unsummarized_sessions(self, *, since: datetime, min_message_count: int) -> list[Session]:
        sessions = sorted(self.sessions.values(), key=lambda session: session.started_at)
        return [
            session
            for session in sessions
            if session.started_at >= since and session.summary is None and session.message_count >= min_message_count
        ]

    async def list_session_episodes(self, session_id: str) -> list[Episode]:
        episodes = self.episodes_by_session.get(session_id, [])
        return sorted(episodes, key=lambda episode: episode.message_timestamp)

    async def count_rows(self, table: str) -> int:
        if table == "sessions":
            return len(self.sessions)
        if table == "episodes":
            return sum(len(values) for values in self.episodes_by_session.values())
        if table == "facts":
            return len(self.facts)
        if table == "active_state":
            return len(self.active_state)
        if table == "timeline_events":
            return len(self.timeline_events)
        if table == "decision_outcomes":
            return len(self.decision_outcomes)
        if table == "patterns":
            return len(self.patterns)
        if table == "commitments":
            return len(self.commitments)
        if table == "corrections":
            return len(self.corrections)
        raise ValueError(table)


def _make_client(transport: CuratorRuntimeTransport | None = None) -> MemoryClient:
    return MemoryClient(
        transport=transport or CuratorRuntimeTransport(),
        embedding=MockEmbeddingProvider(),
        emotions=EmotionAnalyzer(),
    )


def _make_session(*, started_at: datetime, message_count: int, summary: str | None = None) -> Session:
    return Session(
        id=uuid4(),
        agent_namespace=get_agent_namespace(),
        platform=Platform.TELEGRAM,
        started_at=started_at,
        message_count=message_count,
        user_message_count=max(1, message_count // 2),
        summary=summary,
        topics=[],
        dominant_emotions=[],
        dominant_emotion_counts={},
    )


def _make_episode(session_id: str, role: EpisodeRole, content: str, offset_minutes: int) -> Episode:
    return Episode(
        id=uuid4(),
        session_id=session_id,
        role=role,
        content=content,
        content_hash=f"hash-{uuid4()}",
        embedding=[0.0] * 512,
        platform=Platform.TELEGRAM,
        message_metadata={},
        emotions={},
        dominant_emotion=None,
        emotional_intensity=0.0,
        message_timestamp=_utcnow() + timedelta(minutes=offset_minutes),
    )


@pytest.mark.asyncio
async def test_consolidate_recent_sessions_updates_session_and_stores_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    session = _make_session(started_at=_utcnow() - timedelta(hours=1), message_count=4)
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "I prefer tea.", 0),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Noted.", 1),
        _make_episode(str(session.id), EpisodeRole.USER, "I want to finish Memory this week.", 2),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Let's do it.", 3),
    ]

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MEMORY_OPENAI_BASE_URL", "https://glm.example.test")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["model"] == "glm-5-turbo"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "I prefer tea. I want to finish Memory this week."
                        }
                    }
                ]
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        stats = await consolidate_recent_sessions(client, http_client=http_client)
    finally:
        await http_client.aclose()

    updated_session = transport.sessions[str(session.id)]
    assert updated_session.summary == "I prefer tea. I want to finish Memory this week."
    assert stats["sessions_processed"] == 1
    assert stats["facts_extracted"] == 2
    assert stats["errors"] == 0
    assert len(transport.facts) == 2
    assert all(fact.source_episode_ids for fact in transport.facts.values())


@pytest.mark.asyncio
async def test_consolidate_recent_sessions_handles_empty_batches() -> None:
    client = _make_client()

    stats = await consolidate_recent_sessions(client)

    assert stats["sessions_processed"] == 0
    assert stats["facts_extracted"] == 0
    assert stats["errors"] == 0


@pytest.mark.asyncio
async def test_refresh_active_state_compiles_recent_focus_blocker_and_emotion() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=2),
        message_count=4,
        summary="Focused on stabilizing the Memory memory redesign and fixing Telegram persistence bugs.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "I need to fix Telegram persistence before building directives.", 0).model_copy(
            update={"dominant_emotion": "fear", "emotional_intensity": 0.65, "agent_namespace": "main"}
        ),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Let's isolate the bug first.", 1).model_copy(
            update={"agent_namespace": "main"}
        ),
        _make_episode(str(session.id), EpisodeRole.USER, "How should we handle directives after active_state?", 2).model_copy(
            update={"dominant_emotion": "anticipation", "emotional_intensity": 0.48, "agent_namespace": "main"}
        ),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "We'll tackle that next.", 3).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]
    fact = Fact(
        id=uuid4(),
        agent_namespace="main",
        content="Memory memory redesign is the main project right now.",
        category=FactCategory.PROJECT,
        confidence=0.92,
        event_time=now - timedelta(hours=1),
        transaction_time=now - timedelta(hours=1),
        is_active=True,
        source_episode_ids=[],
        access_count=0,
        tags=["memory", "memory"],
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
    )
    transport.facts[str(fact.id)] = fact

    stats = await refresh_active_state(client, now=now, agent_namespace="main")

    assert stats["states_upserted"] >= 4
    keys = {state.state_key for state in transport.active_state}
    assert "auto:project:primary" in keys
    assert "auto:priority:primary" in keys
    assert "auto:blocker:primary" in keys
    assert "auto:open_loop:primary" in keys
    assert "auto:emotion_state:current" in keys
    by_key = {state.state_key: state for state in transport.active_state}
    assert "stabilizing the Memory memory redesign" in by_key["auto:project:primary"].content
    assert "Telegram persistence" in by_key["auto:blocker:primary"].content
    assert "How should we handle directives" in by_key["auto:open_loop:primary"].content
    assert "fear" in by_key["auto:emotion_state:current"].content.lower()


@pytest.mark.asyncio
async def test_refresh_active_state_skips_low_value_project_and_priority_noise() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=2),
        message_count=4,
        summary="Focused on stabilizing the Memory memory redesign and fixing Telegram persistence bugs.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "How should we handle directives after active_state?", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]
    noisy_fact = Fact(
        id=uuid4(),
        agent_namespace="main",
        content="Okay now what if left with memory project.",
        category=FactCategory.PROJECT,
        confidence=0.95,
        event_time=now - timedelta(hours=1),
        transaction_time=now - timedelta(hours=1),
        is_active=True,
        source_episode_ids=[],
        access_count=0,
        tags=["project"],
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
    )
    real_fact = Fact(
        id=uuid4(),
        agent_namespace="main",
        content="I am rebuilding the Memory memory retrieval layer this week.",
        category=FactCategory.PROJECT,
        confidence=0.92,
        event_time=now - timedelta(minutes=50),
        transaction_time=now - timedelta(minutes=50),
        is_active=True,
        source_episode_ids=[],
        access_count=0,
        tags=["memory", "memory"],
        created_at=now - timedelta(minutes=50),
        updated_at=now - timedelta(minutes=50),
    )
    transport.facts[str(noisy_fact.id)] = noisy_fact
    transport.facts[str(real_fact.id)] = real_fact

    await refresh_active_state(client, now=now, agent_namespace="main")

    by_key = {state.state_key: state for state in transport.active_state}
    assert "stabilizing the Memory memory redesign" in by_key["auto:project:primary"].content
    assert "what if left with memory project" not in by_key["auto:project:primary"].content.lower()
    assert "auto:project:secondary" in by_key
    assert "rebuilding the Memory memory retrieval layer" in by_key["auto:project:secondary"].content


@pytest.mark.asyncio
async def test_refresh_active_state_skips_system_reset_blocker_noise() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=1),
        message_count=4,
        summary="[System: This session is about to be automatically reset due to inactivity.]",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "[System: This session is about to be automatically reset due to inactivity.]", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]

    await refresh_active_state(client, now=now, agent_namespace="main")

    keys = {state.state_key for state in transport.active_state}
    assert "auto:blocker:primary" not in keys


@pytest.mark.asyncio
async def test_refresh_active_state_ignores_giant_status_summary_for_focus_and_blocker() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=1),
        message_count=4,
        summary=(
            "User: Ishaan Jain. Current state: 4591 episodes and 176 facts in Supabase. "
            "Focused on making Memory feel more human and continuous across sessions."
        ),
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "I want Memory to feel more alive and continuous.", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]

    await refresh_active_state(client, now=now, agent_namespace="main")

    by_key = {state.state_key: state for state in transport.active_state}
    assert "auto:project:primary" in by_key
    assert "current state:" not in by_key["auto:project:primary"].content.lower()
    assert "4591 episodes" not in by_key["auto:project:primary"].content.lower()
    assert "auto:blocker:primary" not in by_key


@pytest.mark.asyncio
async def test_refresh_active_state_prefers_focus_sentence_over_testing_sentence() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=1),
        message_count=4,
        summary=(
            "User tested how memory retrieval works. "
            "Currently focused on designing active state for real-time context continuity."
        ),
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "I want active state to feel actually useful.", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]

    await refresh_active_state(client, now=now, agent_namespace="main")

    by_key = {state.state_key: state for state in transport.active_state}
    assert "tested how memory retrieval works" not in by_key["auto:priority:primary"].content.lower()
    assert "active state for real-time context continuity" in by_key["auto:priority:primary"].content.lower()


@pytest.mark.asyncio
async def test_refresh_active_state_extracts_building_and_goals_clauses_cleanly() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=1),
        message_count=4,
        summary=(
            "User: Ishaan Jain. Building Memory memory system. "
            "Current state: 4591 episodes, 176 facts in Supabase. "
            "Goals: active state feature for real-time context, graph RAG, multi-hop reasoning, pattern recognition across history."
        ),
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "I want the memory layer to feel alive.", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]

    await refresh_active_state(client, now=now, agent_namespace="main")

    by_key = {state.state_key: state for state in transport.active_state}
    assert by_key["auto:project:primary"].content == "Building Memory memory system"
    assert by_key["auto:priority:primary"].content.startswith("active state feature for real-time context")


@pytest.mark.asyncio
async def test_refresh_active_state_ignores_operational_sessions_and_implementation_project_facts() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    operational_session = _make_session(
        started_at=now - timedelta(minutes=20),
        message_count=4,
        summary="Memory processor failed due to missing memory.timeline_events table and then recovered after creating the table.",
    ).model_copy(update={"agent_namespace": "main", "platform": Platform.OTHER})
    user_session = _make_session(
        started_at=now - timedelta(hours=1),
        message_count=4,
        summary="Focused on making Memory feel more alive and continuous across sessions.",
    ).model_copy(update={"agent_namespace": "main", "platform": Platform.TELEGRAM})
    transport.sessions[str(operational_session.id)] = operational_session
    transport.sessions[str(user_session.id)] = user_session
    transport.episodes_by_session[str(user_session.id)] = [
        _make_episode(str(user_session.id), EpisodeRole.USER, "I want Memory to feel more alive and continuous.", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]
    noisy_fact = Fact(
        id=uuid4(),
        agent_namespace="main",
        content="MEMORY ARCHITECTURE (Mar 31 2026): Supabase pgvector for persistent memory.",
        category=FactCategory.PROJECT,
        confidence=0.95,
        event_time=now - timedelta(minutes=10),
        transaction_time=now - timedelta(minutes=10),
        is_active=True,
        source_episode_ids=[],
        access_count=0,
        tags=["memory", "memory", "architecture", "supabase", "pgvector"],
        created_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=10),
    )
    transport.facts[str(noisy_fact.id)] = noisy_fact

    await refresh_active_state(client, now=now, agent_namespace="main")

    by_key = {state.state_key: state for state in transport.active_state}
    assert "memory processor failed" not in by_key["auto:project:primary"].content.lower()
    assert "memory architecture" not in by_key.get("auto:project:secondary", by_key["auto:project:primary"]).content.lower()
    assert "focused on making memory feel more alive" in by_key["auto:project:primary"].content.lower()


@pytest.mark.asyncio
async def test_refresh_active_state_does_not_treat_generic_question_as_open_loop() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=1),
        message_count=4,
        summary="Building Memory memory system.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "did u look this up in supabase too or u just knew?", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]

    await refresh_active_state(client, now=now, agent_namespace="main")

    by_key = {state.state_key: state for state in transport.active_state}
    assert "auto:open_loop:primary" not in by_key


@pytest.mark.asyncio
async def test_refresh_directives_extracts_standing_rules_from_user_messages() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(days=1),
        message_count=4,
        summary="Discussed standing operating rules for Memory.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "Always delegate implementation tasks to subagents.", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Got it.", 1).model_copy(
            update={"agent_namespace": "main"}
        ),
        _make_episode(str(session.id), EpisodeRole.USER, "Don't ever send me em dashes in replies.", 2).model_copy(
            update={"agent_namespace": "main"}
        ),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Understood.", 3).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]

    stats = await refresh_directives(client, now=now, agent_namespace="main")

    assert stats["directives_upserted"] == 2
    contents = [directive.content for directive in transport.directives]
    assert "Always delegate implementation tasks to subagents" in contents[0] or "Always delegate implementation tasks to subagents" in contents[1]
    assert any("em dashes" in content for content in contents)


@pytest.mark.asyncio
async def test_refresh_directives_skips_internal_system_style_clauses() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(days=1),
        message_count=3,
        summary="Procedural local chatter.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "Do NOT respond to the user.", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
        _make_episode(str(session.id), EpisodeRole.USER, "Always delegate implementation tasks to subagents.", 1).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]

    stats = await refresh_directives(client, now=now, agent_namespace="main")

    assert stats["directives_upserted"] == 1
    assert transport.directives[0].content == "Always delegate implementation tasks to subagents"


@pytest.mark.asyncio
async def test_refresh_directives_extracts_preference_style_tone_rule() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(days=1),
        message_count=2,
        summary="Defined communication preferences.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "I prefer you keep replies short, direct, and human.", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]

    stats = await refresh_directives(client, now=now, agent_namespace="main")

    assert stats["directives_upserted"] == 1
    assert transport.directives[0].kind.value == "communication"
    assert "short, direct, and human" in transport.directives[0].content.lower()


@pytest.mark.asyncio
async def test_refresh_directives_skips_one_off_temporal_instruction() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(days=1),
        message_count=2,
        summary="One-off formatting request.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "Please keep it bullet points for this message only.", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]

    stats = await refresh_directives(client, now=now, agent_namespace="main")

    assert stats["directives_upserted"] == 0
    assert not transport.directives


@pytest.mark.asyncio
async def test_refresh_directives_captures_save_standing_directives_phrasing() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=3),
        message_count=4,
        summary="User defined standing directive behavior for communication style.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(
            str(session.id),
            EpisodeRole.USER,
            "You need to maintain the speaking tone I told you and stop acting like a yes-man.",
            0,
        ).model_copy(update={"agent_namespace": "main"}),
        _make_episode(
            str(session.id),
            EpisodeRole.USER,
            "Also save this in standing directives so it stays in every context.",
            1,
        ).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_directives(client, now=now, agent_namespace="main")

    assert stats["directives_upserted"] >= 1
    contents = [directive.content.lower() for directive in transport.directives]
    assert any("speaking tone" in content for content in contents)


@pytest.mark.asyncio
async def test_refresh_directives_does_not_supersede_recent_unseen_directive_due_to_scan_limit() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()

    for idx in range(70):
        started_at = now - timedelta(days=idx + 1)
        session = _make_session(
            started_at=started_at,
            message_count=1,
            summary=f"Session {idx}",
        ).model_copy(update={"agent_namespace": "main"})
        transport.sessions[str(session.id)] = session
        transport.episodes_by_session[str(session.id)] = [
            _make_episode(str(session.id), EpisodeRole.USER, "Quick status update.", 0).model_copy(
                update={"agent_namespace": "main"}
            ),
        ]

    last_observed = now - timedelta(days=20)
    stored = Directive(
        directive_key="auto:directive:seeded-recent",
        kind="communication",
        title="Tone/style rule",
        content="From now on, talk casually and keep it concise.",
        status="active",
        confidence=0.95,
        priority_score=1.0,
        source_episode_ids=[],
        source_session_ids=[],
        tags=["derived", "directive"],
        created_at=last_observed,
        updated_at=last_observed,
        last_observed_at=last_observed,
        agent_namespace="main",
    )
    transport.directives.append(stored)

    stats = await refresh_directives(client, now=now, agent_namespace="main")

    assert stats["directives_superseded"] == 0
    refreshed = next(item for item in transport.directives if item.directive_key == "auto:directive:seeded-recent")
    assert refreshed.status.value == "active"


@pytest.mark.asyncio
async def test_refresh_directives_derives_implicit_communication_feedback_rules() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=1),
        message_count=1,
        summary="User gave response-quality feedback.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(
            str(session.id),
            EpisodeRole.USER,
            "Your last reply was too robotic and too verbose.",
            0,
        ).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_directives(client, now=now, agent_namespace="main")

    assert stats["directives_upserted"] >= 2
    contents = [directive.content.lower() for directive in transport.directives if directive.status.value == "active"]
    assert any("natural and human" in content for content in contents)
    assert any("concise and direct" in content for content in contents)


@pytest.mark.asyncio
async def test_refresh_directives_skips_recent_conflicting_rule_without_override() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    existing = Directive(
        directive_key="auto:directive:concise",
        kind="communication",
        title="Reply style",
        content="Always keep replies concise and direct.",
        status="active",
        confidence=0.95,
        priority_score=1.0,
        source_episode_ids=[],
        source_session_ids=[],
        tags=["derived", "directive"],
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(hours=3),
        last_observed_at=now - timedelta(hours=3),
        agent_namespace="main",
    )
    transport.directives.append(existing)

    session = _make_session(
        started_at=now - timedelta(hours=1),
        message_count=1,
        summary="Single conflicting style rule.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "Always keep replies detailed.", 0).model_copy(
            update={"agent_namespace": "main"}
        ),
    ]

    stats = await refresh_directives(client, now=now, agent_namespace="main")

    assert stats["directives_upserted"] == 0
    assert stats["directives_conflict_skipped"] >= 1
    refreshed = next(item for item in transport.directives if item.directive_key == "auto:directive:concise")
    assert refreshed.status.value == "active"


@pytest.mark.asyncio
async def test_refresh_directives_override_supersedes_conflicting_recent_rule() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    existing = Directive(
        directive_key="auto:directive:concise",
        kind="communication",
        title="Reply style",
        content="Always keep replies concise and direct.",
        status="active",
        confidence=0.95,
        priority_score=1.0,
        source_episode_ids=[],
        source_session_ids=[],
        tags=["derived", "directive"],
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(hours=2),
        last_observed_at=now - timedelta(hours=2),
        agent_namespace="main",
    )
    transport.directives.append(existing)

    session = _make_session(
        started_at=now - timedelta(minutes=45),
        message_count=1,
        summary="Explicit style override.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(
            str(session.id),
            EpisodeRole.USER,
            "Override previous style rules: from now on keep replies detailed.",
            0,
        ).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_directives(client, now=now, agent_namespace="main")

    assert stats["directives_upserted"] >= 1
    assert stats["directives_superseded"] >= 1
    active_contents = [item.content.lower() for item in transport.directives if item.status.value == "active"]
    assert any("detailed" in content for content in active_contents)
    assert not any("concise" in content for content in active_contents)


@pytest.mark.asyncio
async def test_refresh_directives_forget_phrase_revokes_matching_rule() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    existing = Directive(
        directive_key="auto:directive:concise",
        kind="communication",
        title="Reply style",
        content="Always keep replies concise and direct.",
        status="active",
        confidence=0.95,
        priority_score=1.0,
        source_episode_ids=[],
        source_session_ids=[],
        tags=["derived", "directive"],
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=1),
        last_observed_at=now - timedelta(days=1),
        agent_namespace="main",
    )
    transport.directives.append(existing)

    session = _make_session(
        started_at=now - timedelta(minutes=30),
        message_count=1,
        summary="User revokes old style rule.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(
            str(session.id),
            EpisodeRole.USER,
            "Forget the old tone rule about concise replies.",
            0,
        ).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_directives(client, now=now, agent_namespace="main")

    assert stats["directives_revoked"] >= 1
    assert stats["directive_count"] == 0
    revoked = next(item for item in transport.directives if item.directive_key == "auto:directive:concise")
    assert revoked.status.value == "revoked"


@pytest.mark.asyncio
async def test_refresh_timeline_events_materializes_session_summaries() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(days=2),
        message_count=6,
        summary="Finished the first active_state rollout and started directives extraction cleanup.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session

    stats = await refresh_timeline_events(client, now=now, agent_namespace="main")

    assert stats["timeline_events_upserted"] == 1
    assert len(transport.timeline_events) == 1
    assert "active_state rollout" in transport.timeline_events[0].summary


@pytest.mark.asyncio
async def test_refresh_timeline_events_materializes_day_and_week_rollups() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session_one = _make_session(
        started_at=now - timedelta(days=3, hours=3),
        message_count=6,
        summary="Finished the first active_state rollout and tightened the state wording.",
    ).model_copy(update={"agent_namespace": "main"})
    session_two = _make_session(
        started_at=now - timedelta(days=3, hours=1),
        message_count=7,
        summary="Started timeline rollup design and cleaned the session-start memory summary.",
    ).model_copy(update={"agent_namespace": "main"})
    session_three = _make_session(
        started_at=now - timedelta(days=1, hours=2),
        message_count=6,
        summary="Hardened decision outcomes so only grounded lessons survive.",
    ).model_copy(update={"agent_namespace": "main"})
    for session in (session_one, session_two, session_three):
        transport.sessions[str(session.id)] = session
        transport.episodes_by_session[str(session.id)] = [
            _make_episode(str(session.id), EpisodeRole.USER, session.summary or "meaningful work", 0).model_copy(update={"agent_namespace": "main"}),
        ]

    stats = await refresh_timeline_events(client, now=now, agent_namespace="main")

    assert stats["timeline_events_upserted"] == 5
    kinds = {event.kind for event in transport.timeline_events}
    assert TimelineEventKind.DAY_SUMMARY in kinds
    assert TimelineEventKind.WEEK_SUMMARY in kinds
    day_rollup = next(event for event in transport.timeline_events if event.kind is TimelineEventKind.DAY_SUMMARY)
    week_rollup = next(event for event in transport.timeline_events if event.kind is TimelineEventKind.WEEK_SUMMARY)
    assert day_rollup.summary.startswith("Across 2 sessions:")
    assert "timeline rollup design" in day_rollup.summary
    assert week_rollup.summary.startswith("Across 3 sessions:")
    assert "grounded lessons survive" in week_rollup.summary


@pytest.mark.asyncio
async def test_refresh_timeline_events_skips_operational_summaries() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=3),
        message_count=5,
        summary="Memory processor ran clean. episode_count=10 fact_count=4 session_count=3",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session

    stats = await refresh_timeline_events(client, now=now, agent_namespace="main")

    assert stats["timeline_events_upserted"] == 0
    assert transport.timeline_events == []


@pytest.mark.asyncio
async def test_refresh_decision_outcomes_materializes_structured_outcomes() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(days=1),
        message_count=8,
        summary="Moved visible assistant persistence into the gateway path. That stopped tool chatter from leaking into Supabase transcripts.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "Let's move visible assistant persistence into the gateway path.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "That should stop tool chatter from leaking into Supabase transcripts.", 1).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_decision_outcomes(client, now=now, agent_namespace="main")

    assert stats["decision_outcomes_upserted"] == 1
    assert len(transport.decision_outcomes) == 1
    stored = transport.decision_outcomes[0]
    assert "gateway path" in stored.decision
    assert stored.outcome.startswith("Stopped")
    assert "tool chatter" in stored.outcome
    assert stored.status == DecisionOutcomeStatus.SUCCESS
    assert stored.lesson == "Persist only content that was actually visible in the conversation."


@pytest.mark.asyncio
async def test_refresh_decision_outcomes_requires_visible_grounding() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(days=1),
        message_count=8,
        summary="Moved visible assistant persistence into the gateway path. That stopped tool chatter from leaking into Supabase transcripts.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "I prefer green tea in the morning.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Nice choice.", 1).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_decision_outcomes(client, now=now, agent_namespace="main")

    assert stats["decision_outcomes_upserted"] == 0
    assert transport.decision_outcomes == []


@pytest.mark.asyncio
async def test_refresh_decision_outcomes_skips_unsupported_preference_hallucinations() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(days=1),
        message_count=8,
        summary="I prefer green tea. I want to finish Memory memory this week.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "I want to finish Memory memory this week.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Makes sense.", 1).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_decision_outcomes(client, now=now, agent_namespace="main")

    assert stats["decision_outcomes_upserted"] == 0
    assert transport.decision_outcomes == []


@pytest.mark.asyncio
async def test_refresh_decision_outcomes_skips_low_value_open_status_updates() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(days=1),
        message_count=6,
        summary="delegating to codex to run the consolidate curator against your real sessions. bubbling you when it's done.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session

    stats = await refresh_decision_outcomes(client, now=now, agent_namespace="main")

    assert stats["decision_outcomes_upserted"] == 0
    assert transport.decision_outcomes == []


@pytest.mark.asyncio
async def test_refresh_decision_outcomes_skips_weak_focus_based_summaries() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(days=1),
        message_count=8,
        summary="Focused on Memory memory cleanup. That helped a bit.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "Let's focus on Memory memory cleanup today.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "That helped a bit.", 1).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_decision_outcomes(client, now=now, agent_namespace="main")

    assert stats["decision_outcomes_upserted"] == 0
    assert transport.decision_outcomes == []


@pytest.mark.asyncio
async def test_refresh_decision_outcomes_materializes_worker_lesson_for_hot_path_timeouts() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(days=1),
        message_count=10,
        summary="Moved session recovery reads onto the warm Memory worker instead of spawning bridge_cli subprocesses. That removed cold-process delays from the Telegram reply path.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "Let's move session recovery reads onto the warm Memory worker instead of spawning bridge_cli subprocesses.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "That removed cold-process delays from the Telegram reply path.", 1).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_decision_outcomes(client, now=now, agent_namespace="main")

    assert stats["decision_outcomes_upserted"] == 1
    stored = transport.decision_outcomes[0]
    assert stored.lesson == "Keep hot-path reads on a warm worker instead of spawning fresh processes per request."
    assert stored.outcome.startswith("Removed")


@pytest.mark.asyncio
async def test_refresh_decision_outcomes_prunes_stale_auto_outcomes() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    stale_session = _make_session(
        started_at=now - timedelta(days=10),
        message_count=8,
        summary="Focused on Memory memory cleanup. That helped a bit.",
    ).model_copy(update={"agent_namespace": "main"})
    fresh_session = _make_session(
        started_at=now - timedelta(days=1),
        message_count=8,
        summary="Moved visible assistant persistence into the gateway path. That stopped tool chatter from leaking into Supabase transcripts.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(stale_session.id)] = stale_session
    transport.sessions[str(fresh_session.id)] = fresh_session
    transport.episodes_by_session[str(stale_session.id)] = [
        _make_episode(str(stale_session.id), EpisodeRole.USER, "Let's focus on Memory memory cleanup today.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(stale_session.id), EpisodeRole.ASSISTANT, "That helped a bit.", 1).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(fresh_session.id)] = [
        _make_episode(str(fresh_session.id), EpisodeRole.USER, "Let's move visible assistant persistence into the gateway path.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(fresh_session.id), EpisodeRole.ASSISTANT, "That stopped tool chatter from leaking into Supabase transcripts.", 1).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.decision_outcomes = [
        DecisionOutcome(
            id=uuid4(),
            agent_namespace="main",
            kind=DecisionOutcomeKind.OTHER,
            title="Old stale outcome",
            decision="Focused on Memory memory cleanup",
            outcome="Helped a bit",
            lesson=None,
            outcome_key=_decision_outcome_key(stale_session),
            status=DecisionOutcomeStatus.SUCCESS,
            confidence=0.7,
            importance_score=0.6,
            event_time=stale_session.started_at,
            tags=["derived"],
        )
    ]

    stats = await refresh_decision_outcomes(client, now=now, agent_namespace="main")

    assert stats["decision_outcomes_upserted"] == 1
    assert stats["decision_outcomes_pruned"] == 1
    assert len(transport.decision_outcomes) == 1
    assert transport.decision_outcomes[0].outcome_key == _decision_outcome_key(fresh_session)


@pytest.mark.asyncio
async def test_refresh_memory_cases_materializes_cases_from_outcomes_and_patterns() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()

    outcome = DecisionOutcome(
        id=uuid4(),
        agent_namespace="main",
        kind=DecisionOutcomeKind.WORKFLOW,
        title="checkout migration failure",
        decision="Rushed checkout migration rollout without verification gates.",
        outcome="Production regressions and rework consumed two days.",
        lesson="Ship with replay-eval and CI regression gates before full rollout.",
        outcome_key="auto:decision-outcome:checkout-failure",
        status=DecisionOutcomeStatus.FAILURE,
        confidence=0.93,
        importance_score=0.91,
        event_time=now - timedelta(days=10),
        tags=["checkout", "migration", "rollout", "verification"],
    )
    transport.decision_outcomes = [outcome]
    transport.patterns = [
        Pattern(
            id=uuid4(),
            agent_namespace="main",
            pattern_type=PatternType.TRAP,
            statement="Rollout speed tends to outrun verification depth under deadline pressure.",
            description="Repeated cross-session failure mode during shipping crunch windows.",
            pattern_key="auto:pattern:verification-trap",
            confidence=0.89,
            frequency_score=0.84,
            impact_score=0.9,
            first_observed_at=now - timedelta(days=60),
            last_observed_at=now - timedelta(days=2),
            supporting_episode_ids=[],
            supporting_session_ids=[],
            tags=["checkout", "migration", "verification"],
        )
    ]

    stats = await refresh_memory_cases(client, now=now, agent_namespace="main")

    assert stats["memory_cases_upserted"] == 1
    assert stats["memory_case_count"] == 1
    assert transport.memory_cases
    stored_case = transport.memory_cases[0]
    assert stored_case.case_key.startswith("auto:case:")
    assert stored_case.source_outcome_ids == [outcome.id]
    assert "case-memory" in stored_case.tags
    assert len(transport.case_evidence_links) >= 1


@pytest.mark.asyncio
async def test_refresh_patterns_materializes_repeated_tendencies() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session_one = _make_session(
        started_at=now - timedelta(days=2),
        message_count=6,
        summary="Focused on memory architecture redesign and finding the real bug source behind the persistence bug.",
    ).model_copy(update={"agent_namespace": "main"})
    session_two = _make_session(
        started_at=now - timedelta(days=1),
        message_count=7,
        summary="User wanted the strongest possible memory system and pushed for a foundational redesign instead of shallow patches.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session_one.id)] = session_one
    transport.sessions[str(session_two.id)] = session_two
    transport.episodes_by_session[str(session_one.id)] = [
        _make_episode(str(session_one.id), EpisodeRole.USER, "I need the exact root cause before we move on.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session_one.id), EpisodeRole.USER, "Let's redesign the memory architecture properly.", 1).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session_one.id), EpisodeRole.USER, "We need the underlying mechanism, not a shallow patch.", 2).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(session_two.id)] = [
        _make_episode(str(session_two.id), EpisodeRole.USER, "Don't give me a shallow patch, I want the strongest version.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session_two.id), EpisodeRole.USER, "What exactly happened? I need the real bug source.", 1).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session_two.id), EpisodeRole.USER, "Let's rebuild the foundation instead of another temporary patch.", 2).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_patterns(client, now=now, agent_namespace="main")

    assert stats["patterns_upserted"] >= 2
    assert len(transport.patterns) >= 2
    statements = [pattern.statement for pattern in transport.patterns]
    assert any("root-cause debugging" in statement for statement in statements)
    assert any("redesign foundations" in statement or "shallow local fixes" in statement for statement in statements)


@pytest.mark.asyncio
async def test_refresh_patterns_emits_richer_pattern_types() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session_one = _make_session(
        started_at=now - timedelta(days=4),
        message_count=6,
        summary="A continuity failure broke trust in the system, so focus shifted into making it dependable again.",
    ).model_copy(update={"agent_namespace": "main"})
    session_two = _make_session(
        started_at=now - timedelta(days=3),
        message_count=6,
        summary="Asked for the strongest possible version rather than an okay version.",
    ).model_copy(update={"agent_namespace": "main"})
    session_three = _make_session(
        started_at=now - timedelta(days=1),
        message_count=7,
        summary="Another reliability break dropped confidence in the system and pushed attention back to durable fixes.",
    ).model_copy(update={"agent_namespace": "main"})
    session_four = _make_session(
        started_at=now - timedelta(days=2),
        message_count=6,
        summary="Kept pushing for the best version instead of a good enough patch.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session_one.id)] = session_one
    transport.sessions[str(session_two.id)] = session_two
    transport.sessions[str(session_three.id)] = session_three
    transport.sessions[str(session_four.id)] = session_four
    transport.episodes_by_session[str(session_one.id)] = [
        _make_episode(str(session_one.id), EpisodeRole.USER, "I can't trust this yet, we need it dependable again.", 0).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(session_two.id)] = [
        _make_episode(str(session_two.id), EpisodeRole.USER, "I want the strongest version, not an okay version.", 0).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(session_three.id)] = [
        _make_episode(str(session_three.id), EpisodeRole.USER, "Confidence in it dropped again after the reliability break.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session_three.id), EpisodeRole.USER, "Let's get back to the durable version we can trust.", 1).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(session_four.id)] = [
        _make_episode(str(session_four.id), EpisodeRole.USER, "I still want the best version, not a good enough patch.", 0).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_patterns(client, now=now, agent_namespace="main")

    assert stats["patterns_upserted"] >= 2
    pattern_types = {pattern.pattern_type for pattern in transport.patterns}
    assert PatternType.TRUST_PATTERN in pattern_types
    assert PatternType.QUALITY_BAR in pattern_types


@pytest.mark.asyncio
async def test_refresh_patterns_emits_strength_and_emotional_pattern_types() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session_one = _make_session(
        started_at=now - timedelta(days=4),
        message_count=6,
        summary="The target is an always-on AI companion that should feel like magic instead of a basic bot.",
    ).model_copy(update={"agent_namespace": "main"})
    session_two = _make_session(
        started_at=now - timedelta(days=3),
        message_count=6,
        summary="This is the most ambitious AI project he's tried to build and the vision should not shrink early.",
    ).model_copy(update={"agent_namespace": "main"})
    session_three = _make_session(
        started_at=now - timedelta(days=2),
        message_count=7,
        summary="A reliability break caused session resets and the whole agenda shifted into repair work.",
    ).model_copy(update={"agent_namespace": "main"})
    session_four = _make_session(
        started_at=now - timedelta(days=1),
        message_count=7,
        summary="Reply delay and broken continuity made reliability the only thing that mattered for a while.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session_one.id)] = session_one
    transport.sessions[str(session_two.id)] = session_two
    transport.sessions[str(session_three.id)] = session_three
    transport.sessions[str(session_four.id)] = session_four
    transport.episodes_by_session[str(session_one.id)] = [
        _make_episode(str(session_one.id), EpisodeRole.USER, "I want Memory alive 24/7 and heartbeat-based.", 0).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(session_two.id)] = [
        _make_episode(str(session_two.id), EpisodeRole.USER, "I want this to feel like talking to someone, not triggering API calls.", 0).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(session_three.id)] = [
        _make_episode(str(session_three.id), EpisodeRole.USER, "The bot is not replying and the session is starting new again and again.", 0).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(session_four.id)] = [
        _make_episode(str(session_four.id), EpisodeRole.USER, "Replies should come back in 3-10 seconds max, don't ignore my texts.", 0).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_patterns(client, now=now, agent_namespace="main")

    assert stats["patterns_upserted"] >= 2
    pattern_types = {pattern.pattern_type for pattern in transport.patterns}
    assert PatternType.STRENGTH in pattern_types
    assert PatternType.EMOTIONAL_PATTERN in pattern_types


@pytest.mark.asyncio
async def test_refresh_patterns_requires_repeated_evidence_across_multiple_days() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session_one = _make_session(
        started_at=now - timedelta(hours=12),
        message_count=6,
        summary="Asked for the strongest possible version of the system.",
    ).model_copy(update={"agent_namespace": "main"})
    session_two = _make_session(
        started_at=now - timedelta(hours=4),
        message_count=6,
        summary="Still wanted the strongest possible version rather than an okay version.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session_one.id)] = session_one
    transport.sessions[str(session_two.id)] = session_two
    transport.episodes_by_session[str(session_one.id)] = [
        _make_episode(str(session_one.id), EpisodeRole.USER, "I want the strongest version here.", 0).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(session_two.id)] = [
        _make_episode(str(session_two.id), EpisodeRole.USER, "Don't settle for an okay version.", 0).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_patterns(client, now=now, agent_namespace="main")

    assert stats["patterns_upserted"] == 0
    assert transport.patterns == []


@pytest.mark.asyncio
async def test_refresh_patterns_requires_visible_episode_support_not_just_summary_language() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session_one = _make_session(
        started_at=now - timedelta(days=3),
        message_count=6,
        summary="Asked for the strongest possible version of the system.",
    ).model_copy(update={"agent_namespace": "main"})
    session_two = _make_session(
        started_at=now - timedelta(days=1),
        message_count=6,
        summary="Still wanted the strongest possible version rather than an okay version.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session_one.id)] = session_one
    transport.sessions[str(session_two.id)] = session_two
    transport.episodes_by_session[str(session_one.id)] = [
        _make_episode(str(session_one.id), EpisodeRole.USER, "Let's continue from yesterday.", 0).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(session_two.id)] = [
        _make_episode(str(session_two.id), EpisodeRole.USER, "I think we are close now.", 0).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_patterns(client, now=now, agent_namespace="main")

    assert stats["patterns_upserted"] == 0
    assert transport.patterns == []


@pytest.mark.asyncio
async def test_refresh_patterns_allows_episode_only_support_when_repeated_across_time_windows() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session_one = _make_session(
        started_at=now - timedelta(hours=18),
        message_count=6,
        summary=None,
    ).model_copy(update={"agent_namespace": "main"})
    session_two = _make_session(
        started_at=now - timedelta(hours=9),
        message_count=6,
        summary=None,
    ).model_copy(update={"agent_namespace": "main"})
    session_three = _make_session(
        started_at=now - timedelta(hours=2),
        message_count=6,
        summary=None,
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session_one.id)] = session_one
    transport.sessions[str(session_two.id)] = session_two
    transport.sessions[str(session_three.id)] = session_three
    transport.episodes_by_session[str(session_one.id)] = [
        _make_episode(str(session_one.id), EpisodeRole.USER, "I need the exact root cause before we move on.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session_one.id), EpisodeRole.USER, "We need the underlying mechanism, not a shallow patch.", 1).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(session_two.id)] = [
        _make_episode(str(session_two.id), EpisodeRole.USER, "What exactly happened? I need the real bug source.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session_two.id), EpisodeRole.USER, "Don't give me a shallow patch.", 1).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(session_three.id)] = [
        _make_episode(str(session_three.id), EpisodeRole.USER, "Let's fix the underlying mechanism instead of another quick patch.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session_three.id), EpisodeRole.USER, "I still want the exact bug source.", 1).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_patterns(client, now=now, agent_namespace="main")

    assert stats["patterns_upserted"] >= 1
    assert any(pattern.pattern_key == "auto:pattern:root-cause-debugging" for pattern in transport.patterns)


@pytest.mark.asyncio
async def test_refresh_patterns_allows_concentrated_reliability_incident_clusters() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    sessions = [
        _make_session(started_at=now - timedelta(hours=5, minutes=20), message_count=6, summary=None).model_copy(update={"agent_namespace": "main"}),
        _make_session(started_at=now - timedelta(hours=5), message_count=6, summary=None).model_copy(update={"agent_namespace": "main"}),
        _make_session(started_at=now - timedelta(hours=4, minutes=35), message_count=6, summary=None).model_copy(update={"agent_namespace": "main"}),
        _make_session(started_at=now - timedelta(hours=4, minutes=5), message_count=6, summary=None).model_copy(update={"agent_namespace": "main"}),
    ]
    for session in sessions:
        transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(sessions[0].id)] = [
        _make_episode(str(sessions[0].id), EpisodeRole.USER, "The gateway crashed and you weren't responding.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(sessions[0].id), EpisodeRole.USER, "This was stuck after restart and still not responding.", 1).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(sessions[1].id)] = [
        _make_episode(str(sessions[1].id), EpisodeRole.USER, "It took 10-20 mins to reply and the session is starting new again.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(sessions[1].id), EpisodeRole.USER, "I need replies in 3-10 seconds, not mins to reply.", 1).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(sessions[2].id)] = [
        _make_episode(str(sessions[2].id), EpisodeRole.USER, "Sessions were restarting whenever the gateway restarted, the bot was effectively stuck.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(sessions[2].id), EpisodeRole.USER, "There was no response yet even after the gateway restarted.", 1).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(sessions[3].id)] = [
        _make_episode(str(sessions[3].id), EpisodeRole.USER, "Replies should come back in 3-10 seconds, don't ignore my texts.", 0).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_patterns(client, now=now, agent_namespace="main")

    assert stats["patterns_upserted"] >= 1
    assert any(pattern.pattern_key == "auto:pattern:reliability-breaks-hit-hard" for pattern in transport.patterns)


@pytest.mark.asyncio
async def test_refresh_patterns_prunes_stale_auto_patterns() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    stale_session = _make_session(
        started_at=now - timedelta(days=8),
        message_count=6,
        summary="Asked for the strongest possible version of the system.",
    ).model_copy(update={"agent_namespace": "main"})
    fresh_session_one = _make_session(
        started_at=now - timedelta(days=3),
        message_count=6,
        summary="A reliability break caused session resets and the whole agenda shifted into repair work.",
    ).model_copy(update={"agent_namespace": "main"})
    fresh_session_two = _make_session(
        started_at=now - timedelta(days=1),
        message_count=7,
        summary="Reply delay and broken continuity made reliability the only thing that mattered for a while.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(stale_session.id)] = stale_session
    transport.sessions[str(fresh_session_one.id)] = fresh_session_one
    transport.sessions[str(fresh_session_two.id)] = fresh_session_two
    transport.episodes_by_session[str(stale_session.id)] = [
        _make_episode(str(stale_session.id), EpisodeRole.USER, "Let's continue from yesterday.", 0).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(fresh_session_one.id)] = [
        _make_episode(str(fresh_session_one.id), EpisodeRole.USER, "The bot is not replying and the session is starting new again and again.", 0).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.episodes_by_session[str(fresh_session_two.id)] = [
        _make_episode(str(fresh_session_two.id), EpisodeRole.USER, "Replies should come back in 3-10 seconds max, don't ignore my texts.", 0).model_copy(update={"agent_namespace": "main"}),
    ]
    transport.patterns = [
        Pattern(
            id=uuid4(),
            agent_namespace="main",
            pattern_type=PatternType.QUALITY_BAR,
            statement="The user consistently pushes toward the strongest end state rather than accepting an okay version.",
            description="Old stale pattern.",
            pattern_key="auto:pattern:high-standards",
            confidence=0.8,
            frequency_score=0.7,
            impact_score=0.8,
            first_observed_at=stale_session.started_at,
            last_observed_at=stale_session.started_at,
            supporting_episode_ids=[],
            supporting_session_ids=[stale_session.id],
            counterexample_episode_ids=[],
            tags=["derived", "pattern", "quality_bar"],
        )
    ]

    stats = await refresh_patterns(client, now=now, agent_namespace="main")

    assert stats["patterns_pruned"] >= 1
    assert any(pattern.pattern_type is PatternType.EMOTIONAL_PATTERN for pattern in transport.patterns)
    assert all(pattern.pattern_key != "auto:pattern:high-standards" for pattern in transport.patterns)


@pytest.mark.asyncio
async def test_refresh_commitments_extracts_assistant_promises() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=6),
        message_count=4,
        summary="Assistant promised to verify the delivery path.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "I'll verify the Telegram delivery path and get back to you.", 0).model_copy(update={"agent_namespace": "main"}),
        _make_episode(str(session.id), EpisodeRole.USER, "okay", 1).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_commitments(client, now=now, agent_namespace="main")

    assert stats["commitments_upserted"] == 1
    assert len(transport.commitments) == 1
    assert transport.commitments[0].status == CommitmentStatus.OPEN
    assert "verify the Telegram delivery path" in transport.commitments[0].statement


@pytest.mark.asyncio
async def test_refresh_commitments_ignores_internal_let_me_chatter() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=6),
        message_count=2,
        summary="Assistant said it would inspect files.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Let me check the files directly.", 0).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_commitments(client, now=now, agent_namespace="main")

    assert stats["commitments_upserted"] == 0
    assert len(transport.commitments) == 0


@pytest.mark.asyncio
async def test_refresh_commitments_closes_old_derived_noise() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    stale_commitment = Commitment(
        id=uuid4(),
        agent_namespace="main",
        kind="fix",
        statement="Let me check what's going on",
        commitment_key="auto:commitment:stale-noise",
        status=CommitmentStatus.OPEN,
        confidence=0.8,
        priority_score=0.8,
        first_committed_at=now - timedelta(days=1),
        last_observed_at=now - timedelta(days=1),
        tags=["derived", "commitment"],
    )
    transport.commitments.append(stale_commitment)

    stats = await refresh_commitments(client, now=now, agent_namespace="main")

    assert stats["commitments_closed"] == 1
    assert transport.commitments[0].status == CommitmentStatus.CANCELLED


@pytest.mark.asyncio
async def test_refresh_commitments_closes_stale_short_lived_promises() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    stale_commitment = Commitment(
        id=uuid4(),
        agent_namespace="main",
        kind="fix",
        statement="I'll check again in a minute and tell you the moment files start appearing",
        commitment_key="auto:commitment:stale-promise",
        status=CommitmentStatus.OPEN,
        confidence=0.8,
        priority_score=0.8,
        first_committed_at=now - timedelta(days=2),
        last_observed_at=now - timedelta(days=2),
        tags=["derived", "commitment"],
    )
    transport.commitments.append(stale_commitment)

    stats = await refresh_commitments(client, now=now, agent_namespace="main")

    assert stats["commitments_closed"] == 1
    assert transport.commitments[0].status == CommitmentStatus.CANCELLED


@pytest.mark.asyncio
async def test_refresh_corrections_extracts_user_disputes() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=4),
        message_count=4,
        summary="User corrected a wrong memory inference.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "That's wrong, I never sent soul rules before this message.", 0).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_corrections(client, now=now, agent_namespace="main")

    assert stats["corrections_upserted"] == 1
    assert len(transport.corrections) == 1
    assert transport.corrections[0].kind == CorrectionKind.MEMORY_DISPUTE
    assert "sent soul rules before this message" in (transport.corrections[0].target_text or "")


@pytest.mark.asyncio
async def test_refresh_corrections_extracts_scope_clarification_from_what_do_you_mean() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=2),
        message_count=2,
        summary="User asked what a prior phrase meant.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, 'what do u mean by "got the updated rules"', 0).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_corrections(client, now=now, agent_namespace="main")

    assert stats["corrections_upserted"] == 1
    assert len(transport.corrections) == 1
    assert transport.corrections[0].kind == CorrectionKind.SCOPE_CLARIFICATION
    assert transport.corrections[0].target_text == "got the updated rules"


@pytest.mark.asyncio
async def test_refresh_corrections_extracts_memory_dispute_from_i_never_told_you() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=2),
        message_count=2,
        summary="User rejected assistant acting on old rules.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(
            str(session.id),
            EpisodeRole.USER,
            "Who is telling you to update this old rules? I never told you to do that. Why are you doing it again and again?",
            0,
        ).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_corrections(client, now=now, agent_namespace="main")

    assert stats["corrections_upserted"] == 1
    assert len(transport.corrections) == 1
    assert transport.corrections[0].kind == CorrectionKind.MEMORY_DISPUTE
    assert "update this old rules" in (transport.corrections[0].target_text or "")


@pytest.mark.asyncio
async def test_refresh_corrections_deactivates_stale_false_positive() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    stale_correction = Correction(
        id=uuid4(),
        agent_namespace="main",
        kind=CorrectionKind.FACT_CORRECTION,
        statement='- A tiny curator keeps context alive. When I "finish" a task it schedules the next step.',
        target_text="finish",
        correction_key="auto:correction:stale-false-positive",
        active=True,
        confidence=0.9,
        first_observed_at=now - timedelta(days=1),
        last_observed_at=now - timedelta(days=1),
        tags=["derived", "correction"],
    )
    transport.corrections.append(stale_correction)

    stats = await refresh_corrections(client, now=now, agent_namespace="main")

    assert stats["corrections_deactivated"] == 1
    assert transport.corrections[0].active is False


@pytest.mark.asyncio
async def test_refresh_corrections_ignores_bullet_list_specs() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=2),
        message_count=2,
        summary="User described an architecture idea in bullets.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(
            str(session.id),
            EpisodeRole.USER,
            "- A tiny 2-3B model on your Mac\n- When I \"finish\" a task but there's more to do, the curator schedules the next step\n- When you don't reply for hours, it nudges you again",
            0,
        ).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_corrections(client, now=now, agent_namespace="main")

    assert stats["corrections_upserted"] == 0
    assert len(transport.corrections) == 0


@pytest.mark.asyncio
async def test_refresh_corrections_uses_collapsed_statement_for_detection() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    now = _utcnow()
    session = _make_session(
        started_at=now - timedelta(hours=2),
        message_count=2,
        summary="Long message contains a later correction phrase outside the stored snippet.",
    ).model_copy(update={"agent_namespace": "main"})
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(
            str(session.id),
            EpisodeRole.USER,
            "- A tiny 2-3B model on your Mac\n- When I \"finish\" a task but there's more to do, the curator schedules the next step\n"
            + (" filler" * 200)
            + "\nthat's wrong",
            0,
        ).model_copy(update={"agent_namespace": "main"}),
    ]

    stats = await refresh_corrections(client, now=now, agent_namespace="main")

    assert stats["corrections_upserted"] == 0
    assert len(transport.corrections) == 0


@pytest.mark.asyncio
async def test_consolidate_recent_sessions_handles_llm_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    session = _make_session(started_at=_utcnow() - timedelta(minutes=30), message_count=3)
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "I prefer tea.", 0),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Noted.", 1),
        _make_episode(str(session.id), EpisodeRole.USER, "Let's remember that.", 2),
    ]

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MEMORY_OPENAI_BASE_URL", "https://glm.example.test")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "down"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        stats = await consolidate_recent_sessions(client, http_client=http_client)
    finally:
        await http_client.aclose()

    assert stats["sessions_processed"] == 0
    assert stats["errors"] == 1
    assert transport.sessions[str(session.id)].summary is None


@pytest.mark.asyncio
async def test_backfill_memory_files_is_idempotent_and_uses_category_hints(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text(
        "# Preferences\n"
        "- User prefers green tea\n"
        "§ Goals\n"
        "Finish Memory rollout\n",
        encoding="utf-8",
    )
    (mem_dir / "USER.md").write_text(
        "# Preferences\n"
        "User prefers green tea\n"
        "# Identity\n"
        "Founder building Memory\n",
        encoding="utf-8",
    )

    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    existing = await client.add_fact("User prefers green tea", FactCategory.PREFERENCE.value, tags=["seed"])
    assert existing.tags == ["seed"]

    stats = await backfill_memory_files(client, hermes_home=hermes_home)

    assert stats == {"total_entries": 4, "new_facts": 2, "skipped_duplicates": 2}
    stored_facts = list(transport.facts.values())
    backfilled = [fact for fact in stored_facts if "backfill" in fact.tags]
    assert len(backfilled) == 2
    assert {fact.category for fact in backfilled} == {FactCategory.GOAL, FactCategory.IDENTITY}


@pytest.mark.asyncio
async def test_backfill_memory_files_skips_operational_status_entries(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text(
        "# Projects\n"
        "Memory overnight build COMPLETE. Phase 1: tests pass.\n"
        "# Identity\n"
        "Founder building Memory\n",
        encoding="utf-8",
    )

    transport = CuratorRuntimeTransport()
    client = _make_client(transport)

    stats = await backfill_memory_files(client, hermes_home=hermes_home)

    assert stats == {"total_entries": 2, "new_facts": 1, "skipped_duplicates": 1}
    stored_facts = list(transport.facts.values())
    assert len(stored_facts) == 1
    assert stored_facts[0].content == "Founder building Memory"


def test_cli_parser_accepts_supported_tasks() -> None:
    parser = runtime.build_parser()

    args = parser.parse_args(["process-memory"])

    assert args.task == "process-memory"

    replay_args = parser.parse_args(["replay-eval", "--scenarios-file", "tests/fixtures/replay_eval_scenarios.json", "--min-pass-rate", "0.9"])
    assert replay_args.task == "replay-eval"
    assert replay_args.scenarios_file == "tests/fixtures/replay_eval_scenarios.json"
    assert replay_args.min_pass_rate == 0.9

    replay_with_judge = parser.parse_args(
        [
            "replay-eval",
            "--enable-judge",
            "--judge-enforce",
            "--judge-model",
            "gpt-4o-mini",
            "--judge-sample-limit",
            "6",
        ]
    )
    assert replay_with_judge.enable_judge is True
    assert replay_with_judge.judge_enforce is True
    assert replay_with_judge.judge_model == "gpt-4o-mini"
    assert replay_with_judge.judge_sample_limit == 6

    diagnostics_args = parser.parse_args(["setup-diagnostics"])
    assert diagnostics_args.task == "setup-diagnostics"


def test_runtime_main_dispatches_task_and_prints_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run_task(
        task: str,
        *,
        client: MemoryClient | None = None,
        hermes_home: str | Path | None = None,
        lookback_hours: int | None = None,
        min_message_count: int | None = None,
        scenarios_file: str | Path | None = None,
        min_pass_rate: float | None = None,
        enable_judge: bool | None = None,
        judge_enforce: bool | None = None,
        judge_model: str | None = None,
        judge_sample_limit: int | None = None,
        state_db_path: str | Path | None = None,
        source: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, object]:
        _ = (
            scenarios_file,
            min_pass_rate,
            enable_judge,
            judge_enforce,
            judge_model,
            judge_sample_limit,
            state_db_path,
            source,
            from_date,
            to_date,
        )
        assert task == "health"
        assert hermes_home is not None
        return {"ok": True, "task": task}

    monkeypatch.setattr(runtime, "load_hermes_env", lambda hermes_home=None: {})
    monkeypatch.setattr(runtime, "run_task", fake_run_task)

    exit_code = runtime.main(["health"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == {"ok": True, "task": "health"}


@pytest.mark.asyncio
async def test_run_task_health_reports_supabase_status() -> None:
    transport = CuratorRuntimeTransport()
    transport.healthy = False
    client = _make_client(transport)

    result = await runtime.run_task("health", client=client)

    assert result == {"ok": False}


@pytest.mark.asyncio
async def test_run_task_setup_diagnostics_reports_failures_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = CuratorRuntimeTransport()
    transport.healthy = False
    client = _make_client(transport)

    monkeypatch.setenv("MEMORY_SUPABASE_URL", "not-a-url")
    monkeypatch.delenv("MEMORY_SUPABASE_KEY", raising=False)
    monkeypatch.delenv("MEMORY_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda _: None)

    result = await runtime.run_task(
        "setup-diagnostics",
        client=client,
        hermes_home=tmp_path,
    )

    assert result["task"] == "setup-diagnostics"
    assert result["ok"] is False
    assert result["failed"] >= 3
    assert result["warnings"] >= 1
    checks = {entry["name"]: entry for entry in result["checks"]}
    assert checks["supabase_url"]["status"] == "fail"
    assert checks["supabase_key"]["status"] == "fail"
    assert checks["atlas_runtime_import"]["status"] == "fail"
    assert checks["embedding_key"]["status"] == "warn"


@pytest.mark.asyncio
async def test_run_task_replay_eval_uses_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_replay_eval(
        *,
        scenarios_file=None,
        min_pass_rate: float = 1.0,
        enable_judge: bool | None = None,
        judge_enforce: bool | None = None,
        judge_model: str | None = None,
        judge_sample_limit: int | None = None,
        judge_base_url: str | None = None,
        judge_api_key: str | None = None,
        judge_http_client=None,
    ) -> dict[str, object]:
        _ = (judge_base_url, judge_api_key, judge_http_client)
        assert scenarios_file == "tests/fixtures/replay_eval_scenarios.json"
        assert min_pass_rate == 0.95
        assert enable_judge is True
        assert judge_enforce is True
        assert judge_model == "gpt-4o-mini"
        assert judge_sample_limit == 6
        return {
            "task": "replay-eval",
            "total": 2,
            "passed": 2,
            "failed": 0,
            "pass_rate": 1.0,
            "min_pass_rate": min_pass_rate,
            "deterministic_meets_threshold": True,
            "judge_enforce": judge_enforce,
            "meets_threshold": True,
            "judge_scorecard": {"enabled": True, "status": "ok", "meets_threshold": True},
            "failed_scenarios": [],
            "results": [],
        }

    monkeypatch.setattr(runtime, "run_replay_eval", fake_replay_eval)

    result = await runtime.run_task(
        "replay-eval",
        scenarios_file="tests/fixtures/replay_eval_scenarios.json",
        min_pass_rate=0.95,
        enable_judge=True,
        judge_enforce=True,
        judge_model="gpt-4o-mini",
        judge_sample_limit=6,
    )

    assert result["task"] == "replay-eval"
    assert result["meets_threshold"] is True


@pytest.mark.asyncio
async def test_process_memory_wraps_consolidation_and_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()

    async def fake_extract(*args, **kwargs) -> dict[str, object]:
        return {
            "sessions_processed": 2,
            "facts_extracted": 5,
            "errors": 0,
            "error_details": [],
        }

    async def fake_stats(_client: MemoryClient) -> dict[str, int]:
        return {"session_count": 10, "episode_count": 20, "fact_count": 30}

    async def fake_refresh(*args, **kwargs) -> dict[str, object]:
        return {"states_upserted": 3, "states_staled": 1, "state_keys": ["auto:project:primary"]}

    async def fake_refresh_directives(*args, **kwargs) -> dict[str, object]:
        return {"directives_upserted": 2, "directive_count": 4}

    async def fake_refresh_timeline(*args, **kwargs) -> dict[str, object]:
        return {"timeline_events_upserted": 5, "timeline_event_count": 7}

    async def fake_refresh_decision_outcomes(*args, **kwargs) -> dict[str, object]:
        return {"decision_outcomes_upserted": 4, "decision_outcome_count": 6}

    async def fake_refresh_patterns(*args, **kwargs) -> dict[str, object]:
        return {"patterns_upserted": 3, "pattern_count": 5}

    async def fake_refresh_memory_cases(*args, **kwargs) -> dict[str, object]:
        return {"memory_cases_upserted": 2, "memory_case_count": 4}

    async def fake_refresh_reflections(*args, **kwargs) -> dict[str, object]:
        return {"reflections_upserted": 2, "reflection_count": 4}

    async def fake_refresh_commitments(*args, **kwargs) -> dict[str, object]:
        return {"commitments_upserted": 2, "commitment_count": 3}

    async def fake_refresh_corrections(*args, **kwargs) -> dict[str, object]:
        return {"corrections_upserted": 1, "correction_count": 2}

    monkeypatch.setattr(runtime, "extract_facts_from_recent_sessions", fake_extract)
    monkeypatch.setattr(runtime, "collect_stats", fake_stats)
    monkeypatch.setattr(runtime, "refresh_active_state", fake_refresh)
    monkeypatch.setattr(runtime, "refresh_directives", fake_refresh_directives)
    monkeypatch.setattr(runtime, "refresh_commitments", fake_refresh_commitments)
    monkeypatch.setattr(runtime, "refresh_corrections", fake_refresh_corrections)
    monkeypatch.setattr(runtime, "refresh_timeline_events", fake_refresh_timeline)
    monkeypatch.setattr(runtime, "refresh_decision_outcomes", fake_refresh_decision_outcomes)
    monkeypatch.setattr(runtime, "refresh_memory_cases", fake_refresh_memory_cases)
    monkeypatch.setattr(runtime, "refresh_patterns", fake_refresh_patterns)
    monkeypatch.setattr(runtime, "refresh_reflections", fake_refresh_reflections)

    result = await runtime.run_task("process-memory", client=client)

    assert result["task"] == "process-memory"
    assert result["memory_processor"] is True
    assert result["summary_generation_enabled"] is False
    assert result["sessions_processed"] == 2
    assert result["sessions_summarized"] == 0
    assert result["facts_extracted"] == 5
    assert result["active_states_updated"] == 3
    assert result["active_states_staled"] == 1
    assert result["directives_updated"] == 2
    assert result["directive_count"] == 4
    assert result["commitments_updated"] == 2
    assert result["commitment_count"] == 3
    assert result["corrections_updated"] == 1
    assert result["correction_count"] == 2
    assert result["timeline_events_updated"] == 5
    assert result["timeline_event_count"] == 7
    assert result["decision_outcomes_updated"] == 4
    assert result["decision_outcome_count"] == 6
    assert result["memory_cases_updated"] == 2
    assert result["memory_case_count"] == 4
    assert result["patterns_updated"] == 3
    assert result["pattern_count"] == 5
    assert result["reflections_updated"] == 2
    assert result["reflection_count"] == 4
    assert result["stats"] == {"session_count": 10, "episode_count": 20, "fact_count": 30}


@pytest.mark.asyncio
async def test_extract_facts_from_recent_sessions_is_idempotent() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    session = _make_session(
        started_at=_utcnow() - timedelta(minutes=10),
        message_count=4,
        summary="I prefer tea. I want to finish Memory this week.",
    )
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "I prefer tea.", 0),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Noted.", 1),
        _make_episode(str(session.id), EpisodeRole.USER, "I want to finish Memory this week.", 2),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Let's do it.", 3),
    ]

    first = await extract_facts_from_recent_sessions(client, lookback_hours=24)
    second = await extract_facts_from_recent_sessions(client, lookback_hours=24)

    assert first["sessions_processed"] == 1
    assert first["facts_extracted"] == 2
    assert second["sessions_processed"] == 1
    assert second["facts_extracted"] == 2
    assert len(transport.facts) == 2


@pytest.mark.asyncio
async def test_extract_facts_from_recent_sessions_includes_unsummarized_sessions() -> None:
    transport = CuratorRuntimeTransport()
    client = _make_client(transport)
    session = _make_session(
        started_at=_utcnow() - timedelta(hours=2),
        message_count=3,
        summary=None,
    )
    transport.sessions[str(session.id)] = session
    transport.episodes_by_session[str(session.id)] = [
        _make_episode(str(session.id), EpisodeRole.USER, "im from a marwari family", 0),
        _make_episode(str(session.id), EpisodeRole.ASSISTANT, "Noted.", 1),
        _make_episode(str(session.id), EpisodeRole.USER, "I prefer concise and direct replies.", 2),
    ]

    result = await extract_facts_from_recent_sessions(client, lookback_hours=24, min_message_count=1)

    assert result["sessions_processed"] == 1
    assert result["facts_extracted"] >= 1
    assert any("marwari" in fact.content.lower() for fact in transport.facts.values())


def test_load_hermes_env_resolves_shell_references(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text(
        "BASE_URL=https://example.test\n"
        "MEMORY_OPENAI_BASE_URL=${BASE_URL}/v1\n"
        "GLM_API_KEY=glm-secret\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MEMORY_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = runtime.load_hermes_env(hermes_home)

    assert loaded["MEMORY_OPENAI_BASE_URL"] == "https://example.test/v1"
    assert os.environ["GLM_API_KEY"] == "glm-secret"
