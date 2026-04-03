from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from memory.client import MemoryClient
from memory.embedding import MockEmbeddingProvider, OpenAIEmbeddingProvider, truncate_embedding
from memory.emotions import EmotionAnalyzer
from memory.models import Commitment, Correction, DecisionOutcome, Directive, Episode, EpisodeRole, Fact, FactHistory, Pattern, Session, TimelineEvent


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
        self.commitments: list[Commitment] = []
        self.corrections: list[Correction] = []

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
        updated = session.model_copy(update=updates)
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

    async def health_check(self) -> bool:
        return True


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
async def test_enrich_context_formats_facts_and_episodes() -> None:
    transport = InMemoryTransport()
    client = MemoryClient(transport=transport, embedding=MockEmbeddingProvider(), emotions=EmotionAnalyzer())
    session = await client.start_session()
    await client.store_message(str(session.id), "user", "I am nervous but hopeful about the launch.")
    fact = await client.add_fact("The launch is on Friday.", "project", tags=["launch"])

    context = await client.enrich_context("launch details", active_session_id=str(session.id))

    assert "Memory memory guidance:" in context
    assert "Relevant facts:" in context
    assert "Relevant prior conversations:" in context
    assert "Recent cross-session continuity:" in context
    assert "Active session summary:" in context
    assert str(fact.id) in transport.touched_facts


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
