from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from memory.models import PresenceState, normalize_platform


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_session_id(value: UUID | str | None) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _clamp_unit(value: float, *, floor: float = 0.0, ceiling: float = 1.0) -> float:
    return max(floor, min(ceiling, float(value)))


def _roll_proactive_count(
    current: PresenceState | None,
    *,
    occurred_at: datetime,
) -> int:
    if current is None or current.last_proactive_message_at is None:
        return 1
    if (occurred_at - current.last_proactive_message_at) > timedelta(hours=24):
        return 1
    return int(current.recent_proactive_count_24h or 0) + 1


def _decayed_proactive_count(current: PresenceState | None, *, reference_time: datetime) -> int:
    if current is None or current.last_proactive_message_at is None:
        return 0
    if (reference_time - current.last_proactive_message_at) > timedelta(hours=24):
        return 0
    return max(0, int(current.recent_proactive_count_24h or 0))


def apply_presence_event(
    current: PresenceState | None,
    *,
    role: str,
    occurred_at: datetime | None = None,
    agent_namespace: str | None = None,
    session_id: UUID | str | None = None,
    platform: str | None = None,
    thread_summary: str | None = None,
    proactive: bool = False,
) -> PresenceState:
    event_at = occurred_at or _utcnow()
    normalized_role = str(role or "").strip().lower()
    if normalized_role not in {"user", "assistant"}:
        raise ValueError(f"Unsupported presence role: {role!r}")

    base = current or PresenceState(agent_namespace=agent_namespace)
    update: dict[str, Any] = {
        "agent_namespace": agent_namespace if agent_namespace is not None else base.agent_namespace,
        "updated_at": event_at,
    }

    normalized_session_id = _coerce_session_id(session_id)
    if normalized_session_id is not None:
        update["active_session_id"] = normalized_session_id
    elif current is None:
        update["active_session_id"] = None

    if platform:
        update["active_platform"] = normalize_platform(platform)
    elif current is None:
        update["active_platform"] = None

    if thread_summary is not None:
        stripped = str(thread_summary).strip()
        update["current_thread_summary"] = stripped or None

    if normalized_role == "user":
        update["last_user_message_at"] = event_at
        update["last_user_presence_at"] = event_at
        update["user_disappeared_mid_thread"] = False
        update["conversation_energy"] = _clamp_unit((base.conversation_energy or 0.45) + 0.12)
        update["warmth_score"] = _clamp_unit(max(base.warmth_score or 0.6, 0.6))
    else:
        update["last_agent_message_at"] = event_at
        update["conversation_energy"] = _clamp_unit((base.conversation_energy or 0.45) + 0.04)
        if proactive:
            update["last_proactive_message_at"] = event_at
            update["recent_proactive_count_24h"] = _roll_proactive_count(base, occurred_at=event_at)

    return base.model_copy(update=update)


def refresh_presence_state(
    current: PresenceState | None,
    *,
    now: datetime | None = None,
    dropoff_after: timedelta = timedelta(minutes=2),
) -> PresenceState | None:
    if current is None:
        return None

    reference_time = now or _utcnow()
    disappeared = False
    if current.last_agent_message_at is not None:
        last_user = current.last_user_message_at
        if last_user is None or current.last_agent_message_at > last_user:
            disappeared = (reference_time - current.last_agent_message_at) >= dropoff_after

    decayed_energy = current.conversation_energy
    if current.last_agent_message_at is not None:
        idle_minutes = max(0.0, (reference_time - current.last_agent_message_at).total_seconds() / 60.0)
        decayed_energy = _clamp_unit((current.conversation_energy or 0.45) - min(idle_minutes / 240.0, 0.25))

    return current.model_copy(
        update={
            "user_disappeared_mid_thread": disappeared,
            "conversation_energy": decayed_energy,
            "recent_proactive_count_24h": _decayed_proactive_count(current, reference_time=reference_time),
            "updated_at": reference_time,
        }
    )
