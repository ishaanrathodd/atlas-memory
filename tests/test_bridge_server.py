from __future__ import annotations

from types import SimpleNamespace

from memory.bridge_server import _merge_session_updates


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
