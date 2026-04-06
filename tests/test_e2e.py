from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any
from uuid import UUID, uuid4

import pytest

from memory.client import MemoryClient
from memory.embedding import MockEmbeddingProvider
from memory.emotions import EmotionAnalyzer
from memory.models import Episode, Fact, Platform, Session
from memory.transport import LocalTransport


SUPABASE_URL = os.getenv("MEMORY_SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("MEMORY_SUPABASE_KEY")
TEST_TAG = "memory-e2e"

pytestmark = pytest.mark.e2e


def _normalize_rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _uuid_sql_list(values: list[str]) -> str:
    normalized = [str(UUID(value)) for value in values]
    return ", ".join(f"'{value}'::uuid" for value in normalized)


@dataclass
class E2EContext:
    client: MemoryClient
    transport: LocalTransport
    run_id: str
    session_ids: list[str] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)

    def unique_text(self, label: str) -> str:
        return f"{TEST_TAG}:{label}:{self.run_id}"

    async def start_session(self, platform: str = "local") -> Session:
        session = await self.client.start_session(platform=platform)
        self.session_ids.append(str(session.id))
        return session

    async def store_message(self, session_id: str, role: str, content: str, platform: str = "local") -> Episode:
        episode = await self.client.store_message(session_id, role, content, platform=platform)
        self.episode_ids.append(str(episode.id))
        return episode

    async def store_messages_batch(
        self, session_id: str, messages: list[dict[str, Any]], platform: str = "local"
    ) -> list[Episode]:
        episodes = await self.client.store_messages_batch(session_id, messages, platform=platform)
        self.episode_ids.extend(str(episode.id) for episode in episodes)
        return episodes

    async def add_fact(
        self, content: str, category: str, *, confidence: float = 1.0, tags: list[str] | None = None
    ) -> Fact:
        fact = await self.client.add_fact(
            content,
            category=category,
            confidence=confidence,
            tags=[*(tags or []), TEST_TAG, self.run_id],
        )
        self.fact_ids.append(str(fact.id))
        return fact

    async def select_rows(self, query: str) -> list[dict[str, Any]]:
        response = await self.transport._run(lambda: self.transport._schema_rpc("execute_sql", {"query": query}).execute())
        return _normalize_rows(response)

    async def fetch_fact_history(self, fact_id: str) -> list[dict[str, Any]]:
        response = await self.transport._run(
            lambda: self.transport._schema_client()
            .table("fact_history")
            .select("*")
            .eq("fact_id", fact_id)
            .order("transaction_time")
            .execute()
        )
        return _normalize_rows(response)

    async def cleanup(self) -> None:
        if self.fact_ids:
            fact_sql = _uuid_sql_list(self.fact_ids)
            await self.select_rows(f'delete from "{self.transport.schema}"."fact_history" where fact_id in ({fact_sql})')
            await self.select_rows(f'delete from "{self.transport.schema}"."facts" where id in ({fact_sql})')
        if self.episode_ids:
            episode_sql = _uuid_sql_list(self.episode_ids)
            await self.select_rows(f'delete from "{self.transport.schema}"."episodes" where id in ({episode_sql})')
        if self.session_ids:
            session_sql = _uuid_sql_list(self.session_ids)
            await self.select_rows(f'delete from "{self.transport.schema}"."sessions" where id in ({session_sql})')


def _build_client() -> MemoryClient:
    embedding = MockEmbeddingProvider()
    transport = LocalTransport(
        supabase_url=SUPABASE_URL,
        supabase_key=SUPABASE_SERVICE_KEY,
        embedding_provider=embedding,
        schema="memory",
    )
    return MemoryClient(transport=transport, embedding=embedding, emotions=EmotionAnalyzer())


@pytest.fixture
async def e2e_context() -> E2EContext:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        pytest.skip("E2E tests require MEMORY_SUPABASE_URL/SUPABASE_URL and MEMORY_SUPABASE_KEY/SUPABASE_SERVICE_KEY.")

    client = _build_client()
    context = E2EContext(
        client=client,
        transport=client.transport,
        run_id=uuid4().hex,
    )
    try:
        yield context
    finally:
        await context.cleanup()


@pytest.mark.asyncio
async def test_e2e_health_check_select_one(e2e_context: E2EContext) -> None:
    rows = await e2e_context.select_rows("select 1 as health_check")

    assert rows
    assert rows[0]["health_check"] == 1
    assert await e2e_context.client.health_check() is True


@pytest.mark.asyncio
async def test_e2e_session_lifecycle(e2e_context: E2EContext) -> None:
    session = await e2e_context.start_session(platform="local")

    assert session.id is not None
    assert session.platform is Platform.LOCAL

    fetched = await e2e_context.transport.get_session(str(session.id))
    assert fetched is not None
    assert fetched.id == session.id
    assert fetched.ended_at is None

    summary = e2e_context.unique_text("summary")
    ended = await e2e_context.client.end_session(str(session.id), summary=summary)

    assert ended.ended_at is not None
    assert ended.summary == summary
    assert ended.summary_embedding == [0.0] * 512

    raw = await e2e_context.transport._fetch_row("sessions", str(session.id))
    assert raw is not None
    assert raw["summary"] == summary
    assert raw["summary_embedding"] is not None


@pytest.mark.asyncio
async def test_e2e_store_episode_with_mock_embedding(e2e_context: E2EContext) -> None:
    session = await e2e_context.start_session(platform="local")
    content = f"{e2e_context.unique_text('episode')} I am excited and hopeful about this launch."

    episode = await e2e_context.store_message(str(session.id), "user", content, platform="local")

    assert episode.id is not None
    assert episode.embedding == [0.0] * 512
    assert episode.dominant_emotion is not None
    assert episode.emotional_intensity > 0.0

    raw = await e2e_context.transport._fetch_row("episodes", str(episode.id))
    assert raw is not None
    assert raw["embedding"] is not None


@pytest.mark.asyncio
async def test_e2e_fact_lifecycle_tracks_history(e2e_context: E2EContext) -> None:
    original_content = e2e_context.unique_text("fact-original")
    updated_content = e2e_context.unique_text("fact-updated")

    fact = await e2e_context.add_fact(
        original_content,
        category="preference",
        confidence=0.9,
        tags=["ui", "accessibility"],
    )
    assert fact.id is not None
    assert fact.is_active is True

    fetched = await e2e_context.transport.get_fact(str(fact.id))
    assert fetched is not None
    assert fetched.content == original_content

    updated = await e2e_context.client.update_fact(
        str(fact.id),
        updated_content,
        reason="preference refined",
    )
    assert updated.content == updated_content

    await e2e_context.client.delete_fact(str(updated.id), reason="preference retired")
    deleted = await e2e_context.transport.get_fact(str(updated.id))

    assert deleted is not None
    assert deleted.is_active is False
    assert deleted.replaced_by is None

    history_rows = await e2e_context.fetch_fact_history(str(fact.id))
    operations = [row["operation"] for row in history_rows]

    assert operations == ["add", "update", "delete"]
    assert history_rows[0]["new_content"] == original_content
    assert history_rows[1]["old_content"] == original_content
    assert history_rows[1]["new_content"] == updated_content
    assert history_rows[2]["old_content"] == updated_content


@pytest.mark.asyncio
async def test_e2e_vector_search_with_mock_embeddings(e2e_context: E2EContext) -> None:
    session = await e2e_context.start_session(platform="other")
    stored_one = await e2e_context.store_message(
        str(session.id),
        "user",
        f"{e2e_context.unique_text('search-a')} mountain planning notes",
        platform="other",
    )
    stored_two = await e2e_context.store_message(
        str(session.id),
        "assistant",
        f"{e2e_context.unique_text('search-b')} hiking checklist",
        platform="other",
    )

    results = await e2e_context.client.search_memory("anything", limit=10, platform="other")
    result_ids = {str(item.id) for item in results}

    assert result_ids.intersection({str(stored_one.id), str(stored_two.id)})
    assert all(item.platform is Platform.OTHER for item in results)


@pytest.mark.asyncio
async def test_e2e_search_facts_by_category(e2e_context: E2EContext) -> None:
    health_fact = await e2e_context.add_fact(
        e2e_context.unique_text("health-fact"),
        category="health",
        tags=["wellness"],
    )
    project_fact = await e2e_context.add_fact(
        e2e_context.unique_text("project-fact"),
        category="project",
        tags=["deadline"],
    )

    results = await e2e_context.client.search_facts(category="health")
    result_ids = {str(item.id) for item in results}

    assert str(health_fact.id) in result_ids
    assert str(project_fact.id) not in result_ids


@pytest.mark.asyncio
async def test_e2e_touch_fact_updates_access_metadata(e2e_context: E2EContext) -> None:
    fact = await e2e_context.add_fact(e2e_context.unique_text("touch"), category="fact")

    before = await e2e_context.transport.get_fact(str(fact.id))
    assert before is not None
    assert before.access_count == 0
    assert before.last_accessed_at is None

    await e2e_context.transport.touch_fact(str(fact.id))

    after = await e2e_context.transport.get_fact(str(fact.id))
    assert after is not None
    assert after.access_count == 1
    assert after.last_accessed_at is not None


@pytest.mark.asyncio
async def test_e2e_enrich_context_uses_real_supabase_data(e2e_context: E2EContext) -> None:
    session = await e2e_context.start_session(platform="other")
    fact_text = f"{e2e_context.unique_text('deadline-fact')} deadline is Friday"
    episode_text = f"{e2e_context.unique_text('deadline-episode')} I need to finish the API integration by Friday."

    await e2e_context.store_message(str(session.id), "user", episode_text, platform="other")
    fact = await e2e_context.add_fact(fact_text, category="project", tags=["deadline"])

    context = await e2e_context.client.enrich_context("What is my Friday deadline?", platform="other")

    assert "Relevant facts:" in context
    assert "Relevant prior conversations:" in context
    assert "Recent cross-session continuity:" in context
    assert "Active session summary:" in context
    assert fact_text in context
    assert episode_text in context

    touched = await e2e_context.transport.get_fact(str(fact.id))
    assert touched is not None
    assert touched.access_count >= 1


@pytest.mark.asyncio
async def test_e2e_store_messages_batch_tracks_emotions(e2e_context: E2EContext) -> None:
    session = await e2e_context.start_session(platform="other")
    messages = [
        {
            "role": "user",
            "content": f"{e2e_context.unique_text('batch-1')} I am thrilled and delighted about this progress.",
        },
        {
            "role": "assistant",
            "content": f"{e2e_context.unique_text('batch-2')} I understand the concern and I can help.",
            "message_metadata": {"source": "test"},
        },
        {
            "role": "user",
            "content": f"{e2e_context.unique_text('batch-3')} I am worried but still hopeful.",
            "message_metadata": None,
        },
    ]

    episodes = await e2e_context.store_messages_batch(str(session.id), messages, platform="other")

    assert len(episodes) == 3
    assert episodes[0].embedding == [0.0] * 512
    assert episodes[0].dominant_emotion is not None
    assert any(episode.emotional_intensity > 0.0 for episode in episodes)
    assert episodes[2].message_metadata == {}

    fetched_session = await e2e_context.transport.get_session(str(session.id))
    assert fetched_session is not None
    assert fetched_session.message_count == 3
    assert fetched_session.user_message_count == 2
    assert fetched_session.avg_emotional_intensity is not None
    assert fetched_session.avg_emotional_intensity > 0.0
    assert fetched_session.dominant_emotions
