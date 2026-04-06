from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from memory.bridge_server import _merge_session_updates, _resolve_live_route_for_session, _should_suppress_non_routable_opportunity


def test_merge_session_updates_preserves_existing_model_config_fields() -> None:
    existing_session = SimpleNamespace(
        session_model_config={
            "source": "signal",
            "routing": {
                "session_key": "agent:main:signal:dm:+917977457870",
                "platform": "signal",
            },
        }
    )

    merged = _merge_session_updates(
        existing_session,
        {
            "legacy_session_id": "agent:main:signal:dm:+917977457870",
            "model_config": {
                "routing": {
                    "chat_id": "+917977457870",
                    "bound_at": "2026-04-06T15:00:00+00:00",
                }
            },
        },
    )

    assert merged["legacy_session_id"] == "agent:main:signal:dm:+917977457870"
    assert merged["model_config"]["source"] == "signal"
    assert merged["model_config"]["routing"] == {
        "session_key": "agent:main:signal:dm:+917977457870",
        "platform": "signal",
        "chat_id": "+917977457870",
        "bound_at": "2026-04-06T15:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_resolve_live_route_for_session_uses_session_routing_metadata() -> None:
    session = SimpleNamespace(
        platform=SimpleNamespace(value="signal"),
        session_model_config={"routing": {"platform": "signal", "chat_id": "+15551234567", "thread_id": "thread-1"}},
    )
    async def _get_session(_session_id: str):
        return session
    client = SimpleNamespace(
        transport=SimpleNamespace(
            get_session=_get_session,
        )
    )

    route = await _resolve_live_route_for_session(client, session_id="session-1", agent_namespace="main")

    assert route == {"origin": {"platform": "signal", "chat_id": "+15551234567", "thread_id": "thread-1"}}


def test_should_suppress_non_routable_opportunity_for_old_inactive_session() -> None:
    now = datetime.now(timezone.utc)
    opportunity = SimpleNamespace(session_id="session-1")
    session = SimpleNamespace(updated_at=now - timedelta(hours=8), ended_at=None, started_at=now - timedelta(days=1))
    presence = SimpleNamespace(active_session_id="other-session")

    assert _should_suppress_non_routable_opportunity(
        opportunity=opportunity,
        session=session,
        presence=presence,
        now=now,
    ) is True


def test_should_not_suppress_non_routable_active_session() -> None:
    now = datetime.now(timezone.utc)
    opportunity = SimpleNamespace(session_id="session-1")
    session = SimpleNamespace(updated_at=now - timedelta(hours=8), ended_at=None, started_at=now - timedelta(days=1))
    presence = SimpleNamespace(active_session_id="session-1")

    assert _should_suppress_non_routable_opportunity(
        opportunity=opportunity,
        session=session,
        presence=presence,
        now=now,
    ) is False
