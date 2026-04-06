from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from memory.recall import (
    delete_session,
    export_sessions,
    find_live_session_route,
    load_session_transcript,
    list_all_sessions,
    list_live_session_routes,
    list_named_sessions,
    list_recent_sessions,
    normalize_current_session_id,
    normalize_memory_session_id,
    prune_sessions,
    resolve_session_reference,
    search_sessions,
    session_stats,
    update_session_title,
)


class _RecordingTransport:
    def __init__(self) -> None:
        self.exclude_session_id = None
        self.sessions: dict[str, object] = {}
        self.legacy_sessions: dict[str, object] = {}
        self.list_calls: list[dict[str, object]] = []
        self.session_episode_calls: list[dict[str, object]] = []
        self.session_episodes: dict[str, list[object]] = {}

    async def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    async def get_session_by_legacy_id(self, legacy_session_id: str):
        return self.legacy_sessions.get(legacy_session_id)

    async def list_sessions(
        self,
        limit: int = 20,
        platform: str | None = None,
        agent_namespace: str | None = None,
    ):
        self.list_calls.append({"limit": limit, "platform": platform, "agent_namespace": agent_namespace})
        sessions = list(self.sessions.values())
        if platform is not None:
            sessions = [
                session for session in sessions
                if getattr(getattr(session, "platform", None), "value", getattr(session, "platform", None)) == platform
            ]
        sessions.sort(key=lambda session: getattr(session, "started_at"), reverse=True)
        return sessions[:limit]

    async def list_episodes_for_session(self, session_id: str, limit: int | None = None):
        self.session_episode_calls.append({"session_id": session_id, "limit": limit})
        episodes = list(self.session_episodes.get(session_id, []))
        episodes.sort(key=lambda episode: getattr(episode, "message_timestamp"))
        if limit is not None:
            episodes = episodes[:limit]
        return episodes

    async def update_session(self, session_id: str, updates: dict[str, object]):
        session = self.sessions.get(session_id) or self.legacy_sessions.get(session_id)
        updated = SimpleNamespace(**{**session.__dict__, **updates})
        self.sessions[session_id] = updated
        legacy_session_id = getattr(updated, "legacy_session_id", None)
        if legacy_session_id:
            self.legacy_sessions[str(legacy_session_id)] = updated
        return updated

    async def delete_session(self, session_id: str):
        self.sessions.pop(session_id, None)
        self.session_episodes.pop(session_id, None)
        for key, value in list(self.legacy_sessions.items()):
            if getattr(value, "id", None) == session_id:
                self.legacy_sessions.pop(key, None)
        return True


class _RecordingClient:
    def __init__(self) -> None:
        self.transport = _RecordingTransport()
        self.recent_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self._episodes = []

    async def list_recent_episodes(self, **kwargs):
        self.recent_calls.append(kwargs)
        return list(self._episodes)

    async def search_memory(self, **kwargs):
        self.search_calls.append(kwargs)
        return list(self._episodes)


def test_normalize_memory_session_id_accepts_uuid_and_rejects_legacy_id():
    session_id = str(uuid4())
    assert normalize_memory_session_id(session_id) == session_id
    assert normalize_memory_session_id("20260401_193524_e17c68") is None


def test_normalize_current_session_id_maps_generic_hermes_session_id_to_uuid() -> None:
    normalized = normalize_current_session_id("agent:main:signal:dm:+917977457870")

    assert normalized is not None
    assert normalized == normalize_current_session_id("agent:main:signal:dm:+917977457870")


@pytest.mark.asyncio
async def test_list_recent_sessions_ignores_legacy_current_session_id():
    client = _RecordingClient()
    now = datetime.now(timezone.utc)
    memory_session_id = str(uuid4())
    client.transport.sessions[memory_session_id] = SimpleNamespace(
        id=memory_session_id,
        agent_namespace="default",
        platform=SimpleNamespace(value="telegram"),
        started_at=now,
        ended_at=None,
        message_count=3,
        summary="Recent Memory session.",
    )
    client._episodes = [
        SimpleNamespace(
            session_id=memory_session_id,
            content="We discussed launch plans.",
        )
    ]

    result = await list_recent_sessions(
        client,
        limit=3,
        current_session_id="20260401_193524_e17c68",
        platform="telegram",
    )

    assert client.recent_calls[0]["exclude_session_id"] is not None
    assert client.recent_calls[0]["platform"] is None
    assert result["success"] is True
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_search_sessions_ignores_legacy_current_session_id():
    client = _RecordingClient()
    now = datetime.now(timezone.utc)
    memory_session_id = str(uuid4())
    client.transport.sessions[memory_session_id] = SimpleNamespace(
        id=memory_session_id,
        agent_namespace="default",
        platform=SimpleNamespace(value="telegram"),
        started_at=now,
        ended_at=now,
        message_count=2,
        summary="Memory recall summary.",
    )
    client._episodes = [
        SimpleNamespace(
            session_id=memory_session_id,
            role=SimpleNamespace(value="user"),
            message_timestamp=now,
            content="Launch blockers are fixed.",
        )
    ]

    result = await search_sessions(
        client,
        query="launch blockers",
        role_filter=None,
        limit=3,
        current_session_id="20260401_193524_e17c68",
        platform="telegram",
    )

    assert result["success"] is True
    assert result["count"] == 1
    assert result["results"][0]["session_id"] == memory_session_id
    assert client.search_calls[0]["platform"] is None


@pytest.mark.asyncio
async def test_search_sessions_is_cross_platform_by_default():
    client = _RecordingClient()
    now = datetime.now(timezone.utc)
    memory_session_id = str(uuid4())
    client.transport.sessions[memory_session_id] = SimpleNamespace(
        id=memory_session_id,
        agent_namespace="default",
        platform=SimpleNamespace(value="whatsapp"),
        started_at=now,
        ended_at=now,
        message_count=2,
        summary=None,
    )
    client._episodes = [
        SimpleNamespace(
            session_id=memory_session_id,
            role=SimpleNamespace(value="user"),
            message_timestamp=now,
            content="We discussed this on another platform and it should still be recallable.",
        )
    ]

    result = await search_sessions(
        client,
        query="another platform",
        role_filter=None,
        limit=3,
        current_session_id=None,
        platform="telegram",
    )

    assert result["success"] is True
    assert result["count"] == 1
    assert result["results"][0]["source"] == "whatsapp"
    assert client.search_calls[0]["platform"] is None


@pytest.mark.asyncio
async def test_resolve_session_reference_prefers_legacy_session_id():
    client = _RecordingClient()
    now = datetime.now(timezone.utc)
    memory_session_id = str(uuid4())
    session = SimpleNamespace(
        id=memory_session_id,
        agent_namespace="default",
        title="Roadmap",
        legacy_session_id="20260401_193524_e17c68",
        platform=SimpleNamespace(value="telegram"),
        started_at=now,
        ended_at=None,
        message_count=4,
        summary="Roadmap planning.",
    )
    client.transport.legacy_sessions["20260401_193524_e17c68"] = session

    result = await resolve_session_reference(
        client,
        reference="20260401_193524_e17c68",
        platform="telegram",
    )

    assert result["success"] is True
    assert result["match_type"] == "legacy_session_id"
    assert result["session"]["session_id"] == memory_session_id


@pytest.mark.asyncio
async def test_resolve_session_reference_respects_agent_namespace() -> None:
    client = _RecordingClient()
    now = datetime.now(timezone.utc)
    memory_session_id = str(uuid4())
    session = SimpleNamespace(
        id=memory_session_id,
        agent_namespace="dhruv",
        title="Roadmap",
        legacy_session_id="20260401_193524_e17c68",
        platform=SimpleNamespace(value="telegram"),
        started_at=now,
        ended_at=None,
        message_count=4,
        summary="Roadmap planning.",
    )
    client.transport.legacy_sessions["20260401_193524_e17c68"] = session

    result = await resolve_session_reference(
        client,
        reference="20260401_193524_e17c68",
        platform="telegram",
        agent_namespace="main",
    )

    assert result["success"] is False
    assert result["error"] == "not_found"


@pytest.mark.asyncio
async def test_resolve_session_reference_matches_latest_numbered_title():
    client = _RecordingClient()
    older = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc)
    base_id = str(uuid4())
    numbered_id = str(uuid4())
    client.transport.sessions[base_id] = SimpleNamespace(
        id=base_id,
        agent_namespace="default",
        title="My Project",
        legacy_session_id=None,
        platform=SimpleNamespace(value="local"),
        started_at=older,
        ended_at=None,
        message_count=2,
        summary="Base session.",
    )
    client.transport.sessions[numbered_id] = SimpleNamespace(
        id=numbered_id,
        agent_namespace="default",
        title="My Project #2",
        legacy_session_id=None,
        platform=SimpleNamespace(value="local"),
        started_at=newer,
        ended_at=None,
        message_count=5,
        summary="Continuation session.",
    )

    result = await resolve_session_reference(
        client,
        reference="My Project",
        platform="cli",
    )

    assert result["success"] is True
    assert result["match_type"] == "title"
    assert result["session"]["session_id"] == numbered_id
    assert client.transport.list_calls[0]["platform"] == "local"


@pytest.mark.asyncio
async def test_resolve_session_reference_keeps_signal_platform_filter() -> None:
    client = _RecordingClient()
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    client.transport.sessions[session_id] = SimpleNamespace(
        id=session_id,
        agent_namespace="default",
        title="Signal Thread",
        legacy_session_id=None,
        platform=SimpleNamespace(value="signal"),
        started_at=now,
        ended_at=None,
        message_count=1,
        summary="Signal memory",
    )

    result = await resolve_session_reference(
        client,
        reference="Signal Thread",
        platform="signal",
    )

    assert result["success"] is True
    assert client.transport.list_calls[0]["platform"] == "signal"


@pytest.mark.asyncio
async def test_list_named_sessions_filters_empty_titles():
    client = _RecordingClient()
    now = datetime.now(timezone.utc)
    titled_id = str(uuid4())
    untitled_id = str(uuid4())
    client.transport.sessions[titled_id] = SimpleNamespace(
        id=titled_id,
        agent_namespace="default",
        title="Named Session",
        legacy_session_id="legacy-1",
        platform=SimpleNamespace(value="telegram"),
        started_at=now,
        ended_at=None,
        message_count=3,
        summary="Named summary.",
    )
    client.transport.sessions[untitled_id] = SimpleNamespace(
        id=untitled_id,
        agent_namespace="default",
        title=None,
        legacy_session_id=None,
        platform=SimpleNamespace(value="telegram"),
        started_at=now,
        ended_at=None,
        message_count=1,
        summary="Untitled summary.",
    )

    result = await list_named_sessions(client, limit=10, platform="telegram")

    assert result["success"] is True
    assert result["count"] == 1
    assert result["results"][0]["title"] == "Named Session"


@pytest.mark.asyncio
async def test_list_all_sessions_includes_model_config():
    client = _RecordingClient()
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    client.transport.sessions[session_id] = SimpleNamespace(
        id=session_id,
        agent_namespace="default",
        title="ACP Session",
        legacy_session_id="legacy-acp-1",
        platform=SimpleNamespace(value="other"),
        started_at=now,
        ended_at=None,
        end_reason="end_turn",
        model="claude-test",
        session_model_config={"cwd": "/repo", "source": "acp"},
        billing_provider="anthropic",
        billing_base_url="https://anthropic.example/v1",
        billing_mode="messages",
        message_count=4,
        summary="ACP summary.",
    )

    result = await list_all_sessions(client, limit=10, platform=None)

    assert result["success"] is True
    assert result["count"] == 1
    assert result["results"][0]["model_config"]["cwd"] == "/repo"
    assert result["results"][0]["billing_provider"] == "anthropic"


@pytest.mark.asyncio
async def test_list_live_session_routes_prefers_session_updated_at():
    client = _RecordingClient()
    started = datetime(2026, 4, 2, 11, 17, tzinfo=timezone.utc)
    updated = datetime(2026, 4, 2, 11, 19, tzinfo=timezone.utc)
    session_id = str(uuid4())
    client.transport.sessions[session_id] = SimpleNamespace(
        id=session_id,
        agent_namespace="default",
        title="Telegram DM",
        legacy_session_id="20260402_111723_6968b2d2",
        platform=SimpleNamespace(value="telegram"),
        started_at=started,
        updated_at=updated,
        ended_at=None,
        message_count=4,
        summary=None,
        model_config={
            "routing": {
                "session_key": "agent:main:telegram:dm:1821431987",
                "platform": "telegram",
                "chat_type": "dm",
                "bound_at": started.isoformat(),
            }
        },
        topics=[],
    )

    result = await list_live_session_routes(client, platform="telegram", limit=20)

    assert result["success"] is True
    assert result["count"] == 1
    assert result["results"][0]["updated_at"] == updated.isoformat()


@pytest.mark.asyncio
async def test_find_live_session_route_requires_matching_platform() -> None:
    client = _RecordingClient()
    base_time = datetime(2026, 4, 2, 11, 0, tzinfo=timezone.utc)

    telegram_session = str(uuid4())
    signal_session = str(uuid4())

    client.transport.sessions[telegram_session] = SimpleNamespace(
        id=telegram_session,
        agent_namespace="default",
        title="Telegram Route",
        legacy_session_id="legacy-telegram",
        platform=SimpleNamespace(value="telegram"),
        started_at=base_time,
        updated_at=base_time,
        ended_at=None,
        message_count=2,
        summary=None,
        model_config={
            "routing": {
                "session_key": "agent:main:telegram:dm:12345",
                "platform": "telegram",
                "chat_id": "12345",
                "bound_at": base_time.isoformat(),
            }
        },
        topics=[],
    )
    client.transport.sessions[signal_session] = SimpleNamespace(
        id=signal_session,
        agent_namespace="default",
        title="Signal Route",
        legacy_session_id="legacy-signal",
        platform=SimpleNamespace(value="signal"),
        started_at=base_time + timedelta(minutes=1),
        updated_at=base_time + timedelta(minutes=1),
        ended_at=None,
        message_count=2,
        summary=None,
        model_config={
            "routing": {
                "session_key": "agent:main:signal:dm:12345",
                "platform": "signal",
                "chat_id": "12345",
                "bound_at": (base_time + timedelta(minutes=1)).isoformat(),
            }
        },
        topics=[],
    )

    route = await find_live_session_route(
        client,
        platform="signal",
        chat_id="12345",
    )

    assert route["success"] is True
    assert route["route"]["origin"]["platform"] == "signal"
    assert route["route"]["session_id"] == "legacy-signal"


@pytest.mark.asyncio
async def test_load_session_transcript_returns_messages_with_metadata():
    client = _RecordingClient()
    now = datetime.now(timezone.utc)
    memory_session_id = str(uuid4())
    session = SimpleNamespace(
        id=memory_session_id,
        agent_namespace="default",
        title="Transcript Session",
        legacy_session_id="legacy-42",
        platform=SimpleNamespace(value="local"),
        started_at=now,
        ended_at=None,
        message_count=2,
        summary="Transcript summary.",
    )
    client.transport.legacy_sessions["legacy-42"] = session
    client.transport.session_episodes[memory_session_id] = [
        SimpleNamespace(
            role=SimpleNamespace(value="user"),
            content="Hello",
            message_timestamp=now,
            message_metadata={},
        ),
        SimpleNamespace(
            role=SimpleNamespace(value="assistant"),
            content="Hi there",
            message_timestamp=now,
            message_metadata={
                "tool_calls": [{"id": "call_1"}],
                "reasoning": "Thinking...",
            },
        ),
    ]

    result = await load_session_transcript(
        client,
        reference="legacy-42",
        platform="cli",
    )

    assert result["success"] is True
    assert result["count"] == 2
    assert result["session"]["legacy_session_id"] == "legacy-42"
    assert result["messages"][0]["id"] is None
    assert result["messages"][0]["timestamp"] == now.isoformat()
    assert result["messages"][1]["message_metadata"]["tool_calls"] == [{"id": "call_1"}]
    assert result["messages"][1]["tool_calls"] == [{"id": "call_1"}]
    assert result["messages"][1]["reasoning"] == "Thinking..."
    assert client.transport.session_episode_calls[0]["session_id"] == memory_session_id


@pytest.mark.asyncio
async def test_update_session_title_updates_title():
    client = _RecordingClient()
    now = datetime.now(timezone.utc)
    memory_session_id = str(uuid4())
    session = SimpleNamespace(
        id=memory_session_id,
        agent_namespace="default",
        title=None,
        legacy_session_id="legacy-7",
        platform=SimpleNamespace(value="local"),
        started_at=now,
        ended_at=None,
        message_count=0,
        summary=None,
    )
    client.transport.sessions[memory_session_id] = session
    client.transport.legacy_sessions["legacy-7"] = session

    result = await update_session_title(client, reference="legacy-7", title="Refactor auth", platform="cli")

    assert result["success"] is True
    assert result["title"] == "Refactor auth"


@pytest.mark.asyncio
async def test_delete_export_prune_and_stats_use_memory_sessions():
    client = _RecordingClient()
    old_now = datetime(2020, 1, 1, tzinfo=timezone.utc)
    recent_now = datetime.now(timezone.utc)
    old_id = str(uuid4())
    recent_id = str(uuid4())
    old_session = SimpleNamespace(
        id=old_id,
        agent_namespace="default",
        title="Old",
        legacy_session_id="old-1",
        platform=SimpleNamespace(value="local"),
        started_at=old_now,
        ended_at=old_now,
        message_count=2,
        summary="old summary",
    )
    recent_session = SimpleNamespace(
        id=recent_id,
        agent_namespace="default",
        title="Recent",
        legacy_session_id="recent-1",
        platform=SimpleNamespace(value="telegram"),
        started_at=recent_now,
        ended_at=None,
        message_count=3,
        summary="recent summary",
    )
    client.transport.sessions[old_id] = old_session
    client.transport.sessions[recent_id] = recent_session
    client.transport.legacy_sessions["old-1"] = old_session
    client.transport.legacy_sessions["recent-1"] = recent_session
    client.transport.session_episodes[old_id] = [
        SimpleNamespace(role=SimpleNamespace(value="user"), content="hello", message_timestamp=old_now, message_metadata={})
    ]

    exported = await export_sessions(client, reference="old-1", platform="cli")
    stats = await session_stats(client, platform=None)
    pruned = await prune_sessions(client, older_than_days=30, platform=None)
    deleted = await delete_session(client, reference="recent-1", platform="telegram")

    assert exported["success"] is True
    assert exported["results"][0]["messages"][0]["content"] == "hello"
    assert stats["total_sessions"] == 2
    assert stats["counts_by_source"]["telegram"] == 1
    assert pruned["count"] == 1
    assert deleted["success"] is True
