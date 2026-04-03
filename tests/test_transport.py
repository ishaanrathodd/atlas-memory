from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from memory.embedding import MockEmbeddingProvider
from memory.models import Episode, EpisodeRole, Fact, FactCategory, Platform, Session, VECTOR_DIMENSIONS
from memory.transport import (
    LocalTransport,
    RemoteTransport,
    SupabaseTransport,
    _episode_rank_score,
    _looks_like_operational_content,
    _looks_like_reference_content,
    _vector_to_pg,
)


def _zero_vector() -> list[float]:
    return [0.0] * VECTOR_DIMENSIONS


class FakeQuery:
    def __init__(
        self,
        response: list[dict] | None = None,
        *,
        execute_side_effects: list[Exception | list[dict] | None] | None = None,
    ) -> None:
        self.response = response or []
        self.actions: list[tuple[str, object]] = []
        self.execute_side_effects = list(execute_side_effects or [])

    def insert(self, payload: dict) -> "FakeQuery":
        self.actions.append(("insert", dict(payload)))
        return self

    def select(self, payload: str) -> "FakeQuery":
        self.actions.append(("select", payload))
        return self

    def update(self, payload: dict) -> "FakeQuery":
        self.actions.append(("update", dict(payload)))
        return self

    def delete(self) -> "FakeQuery":
        self.actions.append(("delete", None))
        return self

    def eq(self, field: str, value: str) -> "FakeQuery":
        self.actions.append(("eq", (field, value)))
        return self

    def neq(self, field: str, value: str) -> "FakeQuery":
        self.actions.append(("neq", (field, value)))
        return self

    def gte(self, field: str, value: str) -> "FakeQuery":
        self.actions.append(("gte", (field, value)))
        return self

    def ilike(self, field: str, value: str) -> "FakeQuery":
        self.actions.append(("ilike", (field, value)))
        return self

    def in_(self, field: str, value: list[str]) -> "FakeQuery":
        self.actions.append(("in", (field, value)))
        return self

    def order(self, field: str, desc: bool = False) -> "FakeQuery":
        self.actions.append(("order", (field, desc)))
        return self

    def limit(self, count: int) -> "FakeQuery":
        self.actions.append(("limit", count))
        return self

    def execute(self) -> SimpleNamespace:
        if self.execute_side_effects:
            effect = self.execute_side_effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            if effect is not None:
                return SimpleNamespace(data=effect)
        return SimpleNamespace(data=self.response)


class FakeSchemaClient:
    def __init__(self, client: "FakeSupabaseClient", mapping: dict[str, FakeQuery]) -> None:
        self.client = client
        self.mapping = mapping

    def table(self, name: str) -> FakeQuery:
        return self.mapping[name]

    def rpc(self, name: str, params: dict) -> FakeQuery:
        self.client.schema_rpc_calls.append((self.client.last_schema, name, params))
        return FakeQuery(self.client.rpc_responses.get(name, []))


class FakeSupabaseClient:
    def __init__(self, mapping: dict[str, FakeQuery], rpc_responses: dict[str, list[dict]] | None = None) -> None:
        self.mapping = mapping
        self.rpc_responses = rpc_responses or {}
        self.last_schema: str | None = None
        self.last_rpc: tuple[str, dict] | None = None
        self.schema_rpc_calls: list[tuple[str | None, str, dict]] = []

    def schema(self, name: str) -> FakeSchemaClient:
        self.last_schema = name
        return FakeSchemaClient(self, self.mapping)

    def rpc(self, name: str, params: dict) -> FakeQuery:
        self.last_rpc = (name, params)
        return FakeQuery(self.rpc_responses.get(name, []))


class BrokenEmbeddingProvider:
    async def embed_text(self, text: str) -> list[float]:
        raise RuntimeError("embedding backend unavailable")


@pytest.mark.asyncio
async def test_transport_uses_memory_schema_for_crud() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    fake_client = FakeSupabaseClient(
        {
            "sessions": FakeQuery(
                [
                    {
                        "id": session_id,
                        "platform": "local",
                        "started_at": now.isoformat(),
                        "message_count": 0,
                        "user_message_count": 0,
                        "topics": [],
                        "dominant_emotions": [],
                        "dominant_emotion_counts": {},
                    }
                ]
            )
        }
    )
    transport = SupabaseTransport(client=fake_client)
    session = Session(id=session_id, platform=Platform.LOCAL, started_at=now)

    stored = await transport.insert_session(session)

    assert fake_client.last_schema == transport.schema
    assert stored.id is not None


@pytest.mark.asyncio
async def test_transport_get_session_by_legacy_id_returns_row() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    fake_client = FakeSupabaseClient(
        {
            "sessions": FakeQuery(
                [
                    {
                        "id": session_id,
                        "agent_namespace": "default",
                        "platform": "local",
                        "legacy_session_id": "legacy-1",
                        "started_at": now.isoformat(),
                        "message_count": 0,
                        "user_message_count": 0,
                        "topics": [],
                        "dominant_emotions": [],
                        "dominant_emotion_counts": {},
                    }
                ]
            )
        }
    )
    transport = SupabaseTransport(client=fake_client)

    session = await transport.get_session_by_legacy_id("legacy-1")

    assert session is not None
    assert session.legacy_session_id == "legacy-1"


@pytest.mark.asyncio
async def test_transport_list_sessions_applies_platform_filter() -> None:
    now = datetime.now(timezone.utc)
    fake_client = FakeSupabaseClient(
        {
            "sessions": FakeQuery(
                [
                    {
                        "id": str(uuid4()),
                        "agent_namespace": "default",
                        "platform": "telegram",
                        "title": "Named",
                        "started_at": now.isoformat(),
                        "message_count": 2,
                        "user_message_count": 1,
                        "topics": [],
                        "dominant_emotions": [],
                        "dominant_emotion_counts": {},
                    }
                ]
            )
        }
    )
    transport = SupabaseTransport(client=fake_client)

    sessions = await transport.list_sessions(limit=5, platform="telegram")

    assert len(sessions) == 1
    actions = fake_client.mapping["sessions"].actions
    assert ("eq", ("platform", "telegram")) in actions
    assert ("order", ("started_at", True)) in actions


@pytest.mark.asyncio
async def test_transport_list_sessions_filters_agent_namespace_strictly() -> None:
    now = datetime.now(timezone.utc)
    fake_client = FakeSupabaseClient(
        {
            "sessions": FakeQuery(
                [
                    {
                        "id": str(uuid4()),
                        "agent_namespace": "default",
                        "platform": "telegram",
                        "started_at": now.isoformat(),
                        "message_count": 1,
                        "user_message_count": 1,
                        "topics": [],
                        "dominant_emotions": [],
                        "dominant_emotion_counts": {},
                    },
                    {
                        "id": str(uuid4()),
                        "agent_namespace": "main",
                        "platform": "telegram",
                        "started_at": now.isoformat(),
                        "message_count": 1,
                        "user_message_count": 1,
                        "topics": [],
                        "dominant_emotions": [],
                        "dominant_emotion_counts": {},
                    },
                    {
                        "id": str(uuid4()),
                        "agent_namespace": "dhruv",
                        "platform": "telegram",
                        "started_at": now.isoformat(),
                        "message_count": 1,
                        "user_message_count": 1,
                        "topics": [],
                        "dominant_emotions": [],
                        "dominant_emotion_counts": {},
                    },
                ]
            )
        }
    )
    transport = SupabaseTransport(client=fake_client)

    default_sessions = await transport.list_sessions(limit=10, platform="telegram", agent_namespace="default")
    dhruv_sessions = await transport.list_sessions(limit=10, platform="telegram", agent_namespace="dhruv")

    assert [session.agent_namespace for session in default_sessions] == ["default"]
    assert [session.agent_namespace for session in dhruv_sessions] == ["dhruv"]


@pytest.mark.asyncio
async def test_transport_list_sessions_defaults_to_current_profile_namespace() -> None:
    now = datetime.now(timezone.utc)
    fake_client = FakeSupabaseClient(
        {
            "sessions": FakeQuery(
                [
                    {
                        "id": str(uuid4()),
                        "agent_namespace": "default",
                        "platform": "telegram",
                        "started_at": now.isoformat(),
                        "message_count": 1,
                        "user_message_count": 1,
                        "topics": [],
                        "dominant_emotions": [],
                        "dominant_emotion_counts": {},
                    },
                    {
                        "id": str(uuid4()),
                        "agent_namespace": "research",
                        "platform": "telegram",
                        "started_at": now.isoformat(),
                        "message_count": 1,
                        "user_message_count": 1,
                        "topics": [],
                        "dominant_emotions": [],
                        "dominant_emotion_counts": {},
                    },
                ]
            )
        }
    )
    transport = SupabaseTransport(client=fake_client)

    sessions = await transport.list_sessions(limit=10, platform="telegram")

    assert [session.agent_namespace for session in sessions] == ["default"]


@pytest.mark.asyncio
async def test_transport_list_episodes_for_session_orders_by_timestamp() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    fake_client = FakeSupabaseClient(
        {
            "episodes": FakeQuery(
                [
                    {
                        "id": str(uuid4()),
                        "session_id": session_id,
                        "role": "user",
                        "content": "hello",
                        "content_hash": "hash-1",
                        "platform": "local",
                        "message_metadata": {},
                        "emotions": {},
                        "message_timestamp": now.isoformat(),
                    }
                ]
            )
        }
    )
    transport = SupabaseTransport(client=fake_client)

    episodes = await transport.list_episodes_for_session(session_id, limit=5)

    assert len(episodes) == 1
    actions = fake_client.mapping["episodes"].actions
    assert ("eq", ("session_id", session_id)) in actions
    assert ("order", ("message_timestamp", False)) in actions
    assert ("limit", 5) in actions


@pytest.mark.asyncio
async def test_transport_list_recent_episodes_filters_agent_namespace() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    fake_client = FakeSupabaseClient(
        {
            "episodes": FakeQuery(
                [
                    {
                        "id": str(uuid4()),
                        "session_id": session_id,
                        "agent_namespace": "default",
                        "role": "user",
                        "content": "main row",
                        "content_hash": "hash-1",
                        "platform": "local",
                        "message_metadata": {},
                        "emotions": {},
                        "message_timestamp": now.isoformat(),
                    },
                    {
                        "id": str(uuid4()),
                        "session_id": session_id,
                        "agent_namespace": "dhruv",
                        "role": "user",
                        "content": "dhruv row",
                        "content_hash": "hash-2",
                        "platform": "local",
                        "message_metadata": {},
                        "emotions": {},
                        "message_timestamp": now.isoformat(),
                    },
                ]
            )
        }
    )
    transport = SupabaseTransport(client=fake_client)

    episodes = await transport.list_recent_episodes(limit=5, agent_namespace="default")

    assert [episode.content for episode in episodes] == ["main row"]


@pytest.mark.asyncio
async def test_transport_get_fact_does_not_cross_profile_boundaries() -> None:
    fact_id = str(uuid4())
    fake_client = FakeSupabaseClient(
        {
            "facts": FakeQuery(
                [
                    {
                        "id": fact_id,
                        "agent_namespace": "research",
                        "content": "secret research fact",
                        "category": "fact",
                        "confidence": 1.0,
                        "event_time": datetime.now(timezone.utc).isoformat(),
                        "transaction_time": datetime.now(timezone.utc).isoformat(),
                        "is_active": True,
                        "source_episode_ids": [],
                        "access_count": 0,
                        "tags": [],
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ]
            )
        }
    )
    transport = SupabaseTransport(client=fake_client)

    fact = await transport.get_fact(fact_id)

    assert fact is None


@pytest.mark.asyncio
async def test_transport_insert_session_retries_without_unknown_columns() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    sessions_query = FakeQuery(
        [
            {
                "id": session_id,
                "platform": "local",
                "legacy_session_id": "legacy-1",
                "model": "gpt-test",
                "started_at": now.isoformat(),
                "message_count": 0,
                "user_message_count": 0,
                "tool_call_count": 0,
                "topics": [],
                "dominant_emotions": [],
                "dominant_emotion_counts": {},
            }
        ],
        execute_side_effects=[
            RuntimeError("Could not find the 'legacy_session_id' column of 'sessions' in the schema cache"),
            None,
        ],
    )
    fake_client = FakeSupabaseClient({"sessions": sessions_query})
    transport = SupabaseTransport(client=fake_client)

    stored = await transport.insert_session(
        Session(
            id=session_id,
            platform=Platform.LOCAL,
            legacy_session_id="legacy-1",
            model="gpt-test",
            started_at=now,
        )
    )

    insert_payloads = [payload for action, payload in sessions_query.actions if action == "insert"]
    assert insert_payloads[0]["legacy_session_id"] == "legacy-1"
    assert "legacy_session_id" not in insert_payloads[1]
    assert stored.model == "gpt-test"


@pytest.mark.asyncio
async def test_transport_update_session_retries_without_unknown_columns() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    sessions_query = FakeQuery(
        [
            {
                "id": session_id,
                "platform": "local",
                "started_at": now.isoformat(),
                "message_count": 3,
                "user_message_count": 1,
                "tool_call_count": 1,
                "topics": [],
                "dominant_emotions": [],
                "dominant_emotion_counts": {},
            }
        ],
        execute_side_effects=[
            RuntimeError('column "system_prompt_snapshot" does not exist'),
            None,
        ],
    )
    fake_client = FakeSupabaseClient({"sessions": sessions_query})
    transport = SupabaseTransport(client=fake_client)

    updated = await transport.update_session(
        session_id,
        {
            "system_prompt_snapshot": "prompt",
            "tool_call_count": 1,
        },
    )

    update_payloads = [payload for action, payload in sessions_query.actions if action == "update"]
    assert update_payloads[0]["system_prompt_snapshot"] == "prompt"
    assert "system_prompt_snapshot" not in update_payloads[1]
    assert update_payloads[1]["tool_call_count"] == 1
    assert updated.tool_call_count == 1


@pytest.mark.asyncio
async def test_transport_delete_session_deletes_episodes_and_session() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    sessions_query = FakeQuery(
        [
            {
                "id": session_id,
                "platform": "local",
                "started_at": now.isoformat(),
                "message_count": 1,
                "user_message_count": 1,
                "tool_call_count": 0,
                "topics": [],
                "dominant_emotions": [],
                "dominant_emotion_counts": {},
            }
        ]
    )
    episodes_query = FakeQuery([])
    fake_client = FakeSupabaseClient({"sessions": sessions_query, "episodes": episodes_query})
    transport = SupabaseTransport(client=fake_client)

    deleted = await transport.delete_session(session_id)

    assert deleted is True
    assert ("delete", None) in episodes_query.actions
    assert ("eq", ("session_id", session_id)) in episodes_query.actions
    assert ("delete", None) in sessions_query.actions
    assert ("eq", ("id", session_id)) in sessions_query.actions


@pytest.mark.asyncio
async def test_transport_search_episodes_uses_schema_rpc_and_formats_vector() -> None:
    now = datetime.now(timezone.utc)
    episode_id = str(uuid4())
    session_id = str(uuid4())
    vector_literal = _vector_to_pg(_zero_vector())
    fake_client = FakeSupabaseClient(
        {
            "sessions": FakeQuery([]),
            "episodes": FakeQuery(
                [
                        {
                            "id": episode_id,
                            "session_id": session_id,
                            "agent_namespace": "default",
                            "role": "user",
                        "content": "hello",
                        "content_hash": "hash",
                        "embedding": vector_literal,
                        "platform": "local",
                        "message_metadata": {},
                        "emotions": {},
                        "dominant_emotion": None,
                        "emotional_intensity": 0.0,
                        "message_timestamp": now.isoformat(),
                    }
                ]
            ),
        },
        rpc_responses={
            "search_episodes": [
                {
                    "id": episode_id,
                }
            ]
        },
    )
    transport = SupabaseTransport(client=fake_client, embedding_provider=MockEmbeddingProvider())

    episodes = await transport.search_episodes("hello", limit=5, platform="local", days_back=7)

    assert fake_client.last_rpc is None
    assert fake_client.schema_rpc_calls[-1][0] == transport.schema
    assert fake_client.schema_rpc_calls[-1][1] == "search_episodes"
    assert fake_client.schema_rpc_calls[-1][2]["query_embedding"] == vector_literal
    assert episodes[0].embedding == _zero_vector()


@pytest.mark.asyncio
async def test_transport_search_episodes_falls_back_to_query_ranked_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    fake_client = FakeSupabaseClient(
        {
            "episodes": FakeQuery(
                [
                        {
                            "id": str(uuid4()),
                            "session_id": session_id,
                            "agent_namespace": "default",
                            "role": "user",
                        "content": "this mentions fallback query exactly",
                        "content_hash": "hash",
                        "embedding": _vector_to_pg(_zero_vector()),
                        "platform": "local",
                        "message_metadata": {},
                        "emotions": {},
                        "dominant_emotion": None,
                        "emotional_intensity": 0.0,
                        "message_timestamp": now.isoformat(),
                    },
                        {
                            "id": str(uuid4()),
                            "session_id": str(uuid4()),
                            "agent_namespace": "default",
                            "role": "user",
                        "content": "completely unrelated recent chatter",
                        "content_hash": "hash-2",
                        "embedding": _vector_to_pg(_zero_vector()),
                        "platform": "local",
                        "message_metadata": {},
                        "emotions": {},
                        "dominant_emotion": None,
                        "emotional_intensity": 0.0,
                        "message_timestamp": now.isoformat(),
                    }
                ]
            )
        }
    )
    transport = SupabaseTransport(client=fake_client, embedding_provider=MockEmbeddingProvider())

    def _raise(*args, **kwargs):
        raise RuntimeError("rpc drift")

    monkeypatch.setattr(transport, "_schema_rpc", _raise)

    episodes = await transport.search_episodes("fallback query", limit=3, platform="local", days_back=7)

    assert len(episodes) >= 1
    assert episodes[0].content == "this mentions fallback query exactly"


@pytest.mark.asyncio
async def test_transport_search_episodes_survives_embedding_failure() -> None:
    now = datetime.now(timezone.utc)
    fake_client = FakeSupabaseClient(
        {
            "episodes": FakeQuery(
                [
                        {
                            "id": str(uuid4()),
                            "session_id": str(uuid4()),
                            "agent_namespace": "default",
                            "role": "user",
                        "content": "we worked on signal memory recall yesterday",
                        "content_hash": "hash",
                        "embedding": _vector_to_pg(_zero_vector()),
                        "platform": "signal",
                        "message_metadata": {},
                        "emotions": {},
                        "dominant_emotion": None,
                        "emotional_intensity": 0.0,
                        "message_timestamp": now.isoformat(),
                    }
                ]
            )
        }
    )
    transport = SupabaseTransport(client=fake_client, embedding_provider=BrokenEmbeddingProvider())

    episodes = await transport.search_episodes("signal recall yesterday", limit=3, platform=None, days_back=7)

    assert len(episodes) == 1
    assert episodes[0].platform.value == "signal"
    assert fake_client.schema_rpc_calls == []


@pytest.mark.asyncio
async def test_transport_insert_session_retries_unknown_platform_as_other() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    sessions_query = FakeQuery(
        execute_side_effects=[
            Exception('invalid input value for enum platform: "signal"'),
            [{"id": session_id, "platform": "other", "started_at": now.isoformat(), "topics": [], "dominant_emotions": [], "dominant_emotion_counts": {}, "message_count": 0, "user_message_count": 0, "tool_call_count": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0, "model_config": {"source_platform": "signal"}}],
        ]
    )
    fake_client = FakeSupabaseClient({"sessions": sessions_query})
    transport = SupabaseTransport(client=fake_client, embedding_provider=MockEmbeddingProvider())

    stored = await transport.insert_session(Session(id=session_id, platform="signal", started_at=now))

    assert stored.platform.value == "other"
    assert stored.session_model_config["source_platform"] == "signal"


@pytest.mark.asyncio
async def test_transport_insert_episode_retries_unknown_platform_as_other() -> None:
    now = datetime.now(timezone.utc)
    episode_id = str(uuid4())
    session_id = str(uuid4())
    episodes_query = FakeQuery(
        execute_side_effects=[
            Exception('invalid input value for enum platform: "signal"'),
            [{"id": episode_id, "session_id": session_id, "role": "user", "content": "hello", "content_hash": "hash", "embedding": None, "platform": "other", "message_metadata": {"source_platform": "signal"}, "emotions": {}, "dominant_emotion": None, "emotional_intensity": 0.0, "message_timestamp": now.isoformat()}],
        ]
    )
    fake_client = FakeSupabaseClient({"episodes": episodes_query})
    transport = SupabaseTransport(client=fake_client, embedding_provider=MockEmbeddingProvider())

    stored = await transport.insert_episode(
        Episode(
            id=episode_id,
            session_id=session_id,
            role=EpisodeRole.USER,
            content="hello",
            content_hash="hash",
            embedding=None,
            platform="signal",
            message_metadata={},
            emotions={},
            emotional_intensity=0.0,
            message_timestamp=now,
        )
    )

    assert stored.platform.value == "other"
    assert stored.message_metadata["source_platform"] == "signal"


@pytest.mark.asyncio
async def test_transport_search_episodes_merges_lexical_hits_and_penalizes_meta_tool_chatter() -> None:
    now = datetime.now(timezone.utc)
    raw_episode_id = str(uuid4())
    meta_episode_id = str(uuid4())
    session_id = str(uuid4())
    fake_client = FakeSupabaseClient(
        {
            "episodes": FakeQuery(
                [
                        {
                            "id": meta_episode_id,
                            "session_id": str(uuid4()),
                            "agent_namespace": "default",
                            "role": "assistant",
                        "content": "The Q4 answer is in line 262 of the JSONL. Let me search the file for insecurity details.",
                        "content_hash": "meta-hash",
                        "embedding": _vector_to_pg(_zero_vector()),
                        "platform": "telegram",
                        "message_metadata": {"memory_source": "state_db", "memory_source_line_number": 53},
                        "emotions": {},
                        "dominant_emotion": None,
                        "emotional_intensity": 0.0,
                        "message_timestamp": now.isoformat(),
                    },
                        {
                            "id": raw_episode_id,
                            "session_id": session_id,
                            "agent_namespace": "default",
                            "role": "user",
                        "content": (
                            "I have one insecurity that really gets me. "
                            "My most recent girlfriend was being harrased by someone and I felt embarrassed "
                            "that I could not protect her."
                        ),
                        "content_hash": "raw-hash",
                        "embedding": _vector_to_pg(_zero_vector()),
                        "platform": "telegram",
                        "message_metadata": {"memory_source_line_number": 262},
                        "emotions": {},
                        "dominant_emotion": None,
                        "emotional_intensity": 0.0,
                        "message_timestamp": now.isoformat(),
                    },
                ]
            )
        },
        rpc_responses={"search_episodes": [{"id": meta_episode_id}]},
    )
    transport = SupabaseTransport(client=fake_client, embedding_provider=MockEmbeddingProvider())

    episodes = await transport.search_episodes("insecurities", limit=2, platform="telegram", days_back=3650)

    assert len(episodes) >= 1
    assert str(episodes[0].id) == raw_episode_id
    actions = fake_client.mapping["episodes"].actions
    assert any(action == "ilike" and "insecurity" in value[1] for action, value in actions)


def test_episode_rank_score_penalizes_reference_content_for_generic_queries() -> None:
    episode = Episode(
        id=uuid4(),
        session_id=uuid4(),
        role=EpisodeRole.ASSISTANT,
        content=(
            "soul skill updated. the subagent confirmed the content is now live. "
            "the gateway reads skills/memory/soul/SKILL.md and injects it into your messages."
        ),
        content_hash="skill-meta",
        embedding=_zero_vector(),
        platform=Platform.TELEGRAM,
        message_timestamp=datetime.now(timezone.utc),
        message_metadata={"memory_source_line_number": 11},
    )

    generic_score = _episode_rank_score("hey!", episode, semantic_rank=0, lexical_hit=False)
    topical_score = _episode_rank_score("update your soul skill file", episode, semantic_rank=0, lexical_hit=True)

    assert generic_score < 0.0
    assert topical_score > generic_score


def test_looks_like_reference_content_detects_prompt_mechanics_markers() -> None:
    assert _looks_like_reference_content(
        "the gateway reads skills/memory/soul/SKILL.md and injects it into your messages."
    )
    assert not _looks_like_reference_content("yo bro what are you doing awake")


def test_episode_rank_score_penalizes_operational_content_for_generic_queries() -> None:
    episode = Episode(
        id=uuid4(),
        session_id=uuid4(),
        role=EpisodeRole.ASSISTANT,
        content=(
            "Memory processor ran clean. Consolidated 1 session (2 facts extracted), "
            "skipped 1 trivial session. episode_count=10 fact_count=4 session_count=3"
        ),
        content_hash="operational-meta",
        embedding=_zero_vector(),
        platform=Platform.OTHER,
        message_timestamp=datetime.now(timezone.utc),
        message_metadata={"source_kind": "operational_status"},
    )

    generic_score = _episode_rank_score("hey!", episode, semantic_rank=0, lexical_hit=False)
    topical_score = _episode_rank_score("what did the memory processor do?", episode, semantic_rank=0, lexical_hit=True)

    assert _looks_like_operational_content(episode.content, episode.message_metadata)
    assert generic_score < 0.0
    assert topical_score > generic_score


@pytest.mark.asyncio
async def test_transport_writes_session_vectors_via_sql_rpc() -> None:
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    session_row = {
        "id": session_id,
        "platform": "local",
        "started_at": now.isoformat(),
        "message_count": 0,
        "user_message_count": 0,
        "summary": "hello",
        "summary_embedding": _vector_to_pg(_zero_vector()),
        "topics": [],
        "dominant_emotions": [],
        "dominant_emotion_counts": {},
    }
    sessions_query = FakeQuery([session_row])
    fake_client = FakeSupabaseClient(
        {"sessions": sessions_query},
        rpc_responses={"execute_sql": []},
    )
    transport = SupabaseTransport(client=fake_client)

    stored = await transport.insert_session(
        Session(
            id=session_id,
            platform=Platform.LOCAL,
            started_at=now,
            summary="hello",
            summary_embedding=_zero_vector(),
        )
    )

    insert_payload = next(payload for action, payload in sessions_query.actions if action == "insert")
    assert "summary_embedding" not in insert_payload
    assert any(call[1] == "execute_sql" for call in fake_client.schema_rpc_calls)
    assert stored.summary_embedding == _zero_vector()


@pytest.mark.asyncio
async def test_transport_writes_episode_vectors_via_sql_rpc() -> None:
    now = datetime.now(timezone.utc)
    episode_id = str(uuid4())
    session_id = str(uuid4())
    episode_row = {
        "id": episode_id,
        "session_id": session_id,
        "role": "user",
        "content": "hello",
        "content_hash": "hash",
        "embedding": _vector_to_pg(_zero_vector()),
        "platform": "local",
        "message_metadata": {},
        "emotions": {},
        "dominant_emotion": None,
        "emotional_intensity": 0.0,
        "message_timestamp": now.isoformat(),
    }
    episodes_query = FakeQuery([episode_row])
    fake_client = FakeSupabaseClient(
        {"episodes": episodes_query},
        rpc_responses={"execute_sql": []},
    )
    transport = SupabaseTransport(client=fake_client)

    stored = await transport.insert_episode(
        Episode(
            id=episode_id,
            session_id=session_id,
            role=EpisodeRole.USER,
            content="hello",
            content_hash="hash",
            embedding=_zero_vector(),
            platform=Platform.LOCAL,
            message_metadata={},
            emotions={},
            message_timestamp=now,
        )
    )

    insert_payload = next(payload for action, payload in episodes_query.actions if action == "insert")
    assert "embedding" not in insert_payload
    assert any(call[1] == "execute_sql" for call in fake_client.schema_rpc_calls)
    assert stored.embedding == _zero_vector()


@pytest.mark.asyncio
async def test_transport_raises_lookup_error_on_empty_insert_response() -> None:
    fake_client = FakeSupabaseClient({"facts": FakeQuery([])})
    transport = SupabaseTransport(client=fake_client)
    now = datetime.now(timezone.utc)

    with pytest.raises(LookupError, match="insert_fact returned no rows"):
        await transport.insert_fact(
            Fact(
                content="User likes tea.",
                category=FactCategory.PREFERENCE,
                confidence=1.0,
                event_time=now,
                transaction_time=now,
            )
        )


def test_vector_to_pg_requires_exact_dimensions() -> None:
    with pytest.raises(ValueError, match="exactly 512 dimensions"):
        _vector_to_pg([0.0, 1.0])


@pytest.mark.asyncio
async def test_remote_transport_is_explicit_placeholder() -> None:
    transport = RemoteTransport()
    with pytest.raises(NotImplementedError):
        await transport.health_check()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not (os.getenv("MEMORY_SUPABASE_URL") and os.getenv("MEMORY_SUPABASE_KEY")),
    reason="Supabase integration tests require MEMORY_SUPABASE_URL and MEMORY_SUPABASE_KEY",
)
async def test_local_transport_health_check_integration() -> None:
    transport = LocalTransport(
        supabase_url=os.environ["MEMORY_SUPABASE_URL"],
        supabase_key=os.environ["MEMORY_SUPABASE_KEY"],
        embedding_provider=MockEmbeddingProvider(),
    )

    assert await transport.health_check() is True
