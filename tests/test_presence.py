from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from memory.models import PresenceState
from memory.presence import apply_presence_event, refresh_presence_state


def test_apply_presence_event_tracks_user_presence() -> None:
    now = datetime.now(timezone.utc)
    session_id = uuid4()

    state = apply_presence_event(
        None,
        role="user",
        occurred_at=now,
        agent_namespace="main",
        session_id=session_id,
        platform="telegram",
        thread_summary="We were in the middle of a deploy debugging thread.",
    )

    assert state.agent_namespace == "main"
    assert state.active_session_id == session_id
    assert state.active_platform is not None
    assert state.active_platform.value == "telegram"
    assert state.last_user_message_at == now
    assert state.last_user_presence_at == now
    assert state.user_disappeared_mid_thread is False


def test_apply_presence_event_rolls_recent_proactive_count() -> None:
    now = datetime.now(timezone.utc)
    current = PresenceState(
        agent_namespace="main",
        recent_proactive_count_24h=2,
        last_proactive_message_at=now - timedelta(hours=1),
    )

    state = apply_presence_event(
        current,
        role="assistant",
        occurred_at=now,
        proactive=True,
    )

    assert state.last_proactive_message_at == now
    assert state.recent_proactive_count_24h == 3


def test_refresh_presence_state_marks_dropoff_after_gap() -> None:
    now = datetime.now(timezone.utc)
    state = PresenceState(
        agent_namespace="main",
        active_session_id=uuid4(),
        last_agent_message_at=now - timedelta(minutes=5),
        last_user_message_at=now - timedelta(minutes=8),
        conversation_energy=0.82,
    )

    refreshed = refresh_presence_state(state, now=now, dropoff_after=timedelta(minutes=2))

    assert refreshed is not None
    assert refreshed.user_disappeared_mid_thread is True
    assert refreshed.conversation_energy < state.conversation_energy
