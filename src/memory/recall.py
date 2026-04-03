from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from memory.client import MemoryClient
from memory.transport import _agent_namespace_matches

_SESSION_DAYS_BACK = 3650
_LEGACY_SESSION_ID_RE = re.compile(r"^\d{8}_\d{6}_[A-Za-z0-9]+$")
_MEMORY_LIVE_SESSION_NAMESPACE = uuid5(NAMESPACE_URL, "memory://hermes-live-session")
_MAX_TITLE_LENGTH = 100
_SESSION_COMPAT_TOPIC_PREFIX = "__hermes_meta__:"


def format_timestamp(ts: datetime | str | None) -> str:
    if ts is None:
        return "unknown"
    if isinstance(ts, datetime):
        return ts.strftime("%B %d, %Y at %I:%M %p")
    return str(ts)


def normalize_platform_filter(platform: str | None) -> str | None:
    if not platform:
        return None
    lowered = str(platform).strip().lower()
    if lowered == "cli":
        return "local"
    if lowered in {"local", "telegram", "discord", "whatsapp"}:
        return lowered
    return None


def _query_variants(query: str) -> list[str]:
    normalized = " ".join((query or "").lower().split())
    if not normalized:
        return []
    variants = [normalized]
    tokens = normalized.split()
    for token in tokens:
        if len(token) > 2:
            variants.append(token)
        if token.endswith("ies") and len(token) > 4:
            variants.append(token[:-3] + "y")
        if token.endswith("es") and len(token) > 4:
            variants.append(token[:-2])
        if token.endswith("s") and len(token) > 3:
            variants.append(token[:-1])
    seen: set[str] = set()
    deduped: list[str] = []
    for variant in variants:
        if variant and variant not in seen:
            seen.add(variant)
            deduped.append(variant)
    return deduped


def episode_excerpt(content: str, *, query: str | None = None, limit: int = 280) -> str:
    normalized = " ".join((content or "").split())
    if len(normalized) <= limit:
        return normalized
    if query:
        lowered = normalized.lower()
        match_positions = [lowered.find(variant) for variant in _query_variants(query)]
        match_positions = [position for position in match_positions if position >= 0]
        if match_positions:
            first_match = min(match_positions)
            half = max(80, limit // 2)
            start = max(0, first_match - half)
            end = min(len(normalized), start + limit)
            if end - start < limit:
                start = max(0, end - limit)
            snippet = normalized[start:end].strip()
            if start > 0:
                snippet = "…" + snippet
            if end < len(normalized):
                snippet = snippet.rstrip() + "…"
            return snippet
    return normalized[: limit - 1].rstrip() + "…"


def _summary_matches_query(summary: str | None, query: str) -> bool:
    if not summary:
        return False
    lowered = summary.lower()
    return any(variant in lowered for variant in _query_variants(query))


def normalize_memory_session_id(session_id: str | None) -> str | None:
    """Return a canonical Memory session UUID string or None for non-UUID inputs."""
    if session_id is None:
        return None
    value = str(session_id).strip()
    if not value:
        return None
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def normalize_current_session_id(session_id: str | None) -> str | None:
    normalized = normalize_memory_session_id(session_id)
    if normalized is not None:
        return normalized
    value = str(session_id or "").strip()
    if _LEGACY_SESSION_ID_RE.match(value):
        return str(uuid5(_MEMORY_LIVE_SESSION_NAMESPACE, value))
    return None


def _decode_session_compat_topics(topics: Any) -> dict[str, Any]:
    if not isinstance(topics, list):
        return {}
    for item in topics:
        if not isinstance(item, str) or not item.startswith(_SESSION_COMPAT_TOPIC_PREFIX):
            continue
        encoded = item[len(_SESSION_COMPAT_TOPIC_PREFIX) :]
        try:
            decoded = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
            payload = json.loads(decoded)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _session_payload(session: Any) -> dict[str, Any]:
    compat = _decode_session_compat_topics(getattr(session, "topics", None))
    model_config = getattr(session, "session_model_config", None)
    if model_config is None:
        model_config = getattr(session, "model_config", None)
    if model_config is None and isinstance(compat.get("model_config"), dict):
        model_config = compat.get("model_config")
    if model_config is None:
        model_config = {}
    routing = {}
    if isinstance(model_config, dict):
        raw_routing = model_config.get("routing")
        if isinstance(raw_routing, dict):
            routing = dict(raw_routing)
    title = getattr(session, "title", None) or compat.get("title")
    legacy_session_id = getattr(session, "legacy_session_id", None) or compat.get("legacy_session_id")
    parent_session_id = getattr(session, "parent_session_id", None) or compat.get("parent_session_id")
    return {
        "session_id": str(session.id),
        "title": title,
        "source": getattr(session.platform, "value", str(session.platform)),
        "started_at": session.started_at.isoformat(),
        "updated_at": (
            session.updated_at.isoformat()
            if getattr(session, "updated_at", None)
            else session.started_at.isoformat()
        ),
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "end_reason": getattr(session, "end_reason", None) or compat.get("end_reason"),
        "model": getattr(session, "model", None) or compat.get("model"),
        "model_config": dict(model_config) if isinstance(model_config, dict) else {},
        "parent_session_id": str(parent_session_id or "") or None,
        "billing_provider": getattr(session, "billing_provider", None) or compat.get("billing_provider"),
        "billing_base_url": getattr(session, "billing_base_url", None) or compat.get("billing_base_url"),
        "billing_mode": getattr(session, "billing_mode", None) or compat.get("billing_mode"),
        "system_prompt_snapshot": getattr(session, "system_prompt_snapshot", None) or compat.get("system_prompt_snapshot"),
        "message_count": session.message_count,
        "summary": session.summary,
        "legacy_session_id": legacy_session_id,
        "routing": routing,
    }


def _routing_payload(session_payload: dict[str, Any]) -> dict[str, Any] | None:
    routing = session_payload.get("routing")
    if not isinstance(routing, dict):
        return None
    session_key = str(routing.get("session_key") or "").strip()
    if not session_key:
        return None
    return routing


def _routing_rank(session_payload: dict[str, Any]) -> tuple[str, str]:
    routing = _routing_payload(session_payload) or {}
    bound_at = str(routing.get("bound_at") or "").strip()
    started_at = str(session_payload.get("started_at") or "").strip()
    return (bound_at or started_at, str(session_payload.get("session_id") or ""))


def sanitize_session_title(title: str | None) -> str | None:
    if not title:
        return None

    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(title))
    cleaned = re.sub(
        r"[\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff\ufffc\ufff9-\ufffb]",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_TITLE_LENGTH:
        raise ValueError(f"Title too long ({len(cleaned)} chars, max {_MAX_TITLE_LENGTH})")
    return cleaned


def _episode_to_conversation_message(episode: Any) -> dict[str, Any]:
    metadata = getattr(episode, "message_metadata", {}) or {}
    message = {
        "id": str(getattr(episode, "id", "") or "") or None,
        "role": getattr(episode.role, "value", str(episode.role)),
        "content": episode.content,
        "timestamp": episode.message_timestamp.isoformat(),
    }
    if isinstance(metadata, dict) and metadata:
        message["message_metadata"] = dict(metadata)
    for key in (
        "tool_call_id",
        "tool_name",
        "tool_calls",
        "reasoning",
        "reasoning_details",
        "codex_reasoning_items",
        "finish_reason",
    ):
        if key in metadata and metadata.get(key) is not None:
            message[key] = metadata.get(key)
    return message


def _latest_numbered_title_match(title: str, sessions: list[Any]) -> dict[str, Any] | None:
    exact: dict[str, Any] | None = None
    numbered: dict[str, Any] | None = None
    prefix = f"{title} #"
    for session in sessions:
        session_title = str((_session_payload(session).get("title")) or "").strip()
        if not session_title:
            continue
        session_payload = {**_session_payload(session), "title": session_title}
        if session_title == title and exact is None:
            exact = session_payload
            continue
        if session_title.startswith(prefix) and numbered is None:
            numbered = session_payload
    return numbered or exact


async def resolve_session_reference(
    client: MemoryClient,
    *,
    reference: str,
    platform: str | None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    raw_reference = str(reference or "").strip()
    if not raw_reference:
        return {
            "success": False,
            "backend": "memory",
            "error": "empty_reference",
            "message": "No session reference was provided.",
        }

    normalized_session_id = normalize_memory_session_id(raw_reference)
    if normalized_session_id:
        session = await client.transport.get_session(normalized_session_id)
        if session is not None and not _agent_namespace_matches(getattr(session, "agent_namespace", None), agent_namespace):
            session = None
    else:
        session = await client.transport.get_session_by_legacy_id(raw_reference)
        if session is not None and not _agent_namespace_matches(getattr(session, "agent_namespace", None), agent_namespace):
            session = None
        recent_sessions = await client.transport.list_sessions(
            limit=200,
            platform=normalize_platform_filter(platform),
            agent_namespace=agent_namespace,
        )
        if session is None:
            for candidate in recent_sessions:
                payload = _session_payload(candidate)
                if str(payload.get("legacy_session_id") or "").strip() == raw_reference:
                    session = candidate
                    break
        if session is None:
            matched_payload = _latest_numbered_title_match(raw_reference, recent_sessions)
            if matched_payload is not None:
                return {
                    "success": True,
                    "backend": "memory",
                    "reference": raw_reference,
                    "match_type": "title",
                    "session": matched_payload,
                }

    if session is None:
        return {
            "success": False,
            "backend": "memory",
            "reference": raw_reference,
            "error": "not_found",
            "message": f"No Memory session matched '{raw_reference}'.",
        }

    return {
        "success": True,
        "backend": "memory",
        "reference": raw_reference,
        "match_type": "session_id" if normalized_session_id else "legacy_session_id",
        "session": _session_payload(session),
    }


async def list_named_sessions(
    client: MemoryClient,
    *,
    limit: int,
    platform: str | None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    sessions = await client.transport.list_sessions(
        limit=max(limit * 4, 40),
        platform=normalize_platform_filter(platform),
        agent_namespace=agent_namespace,
    )
    titled = []
    for session in sessions:
        title = str((_session_payload(session).get("title")) or "").strip()
        if not title:
            continue
        titled.append({**_session_payload(session), "title": title})
        if len(titled) >= limit:
            break

    return {
        "success": True,
        "backend": "memory",
        "mode": "named",
        "results": titled,
        "count": len(titled),
        "message": f"Showing {len(titled)} titled Memory sessions.",
    }


async def list_all_sessions(
    client: MemoryClient,
    *,
    limit: int,
    platform: str | None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    sessions = await client.transport.list_sessions(
        limit=max(limit, 1),
        platform=normalize_platform_filter(platform),
        agent_namespace=agent_namespace,
    )
    results = []
    for session in sessions[:limit]:
        payload = _session_payload(session)
        payload["preview"] = session.summary or ""
        results.append(payload)
    return {
        "success": True,
        "backend": "memory",
        "mode": "all",
        "results": results,
        "count": len(results),
        "message": f"Showing {len(results)} Memory sessions.",
    }


async def list_recent_sessions(
    client: MemoryClient,
    *,
    limit: int,
    current_session_id: str | None,
    platform: str | None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    normalized_current_session_id = normalize_current_session_id(current_session_id)
    recent = await client.list_recent_episodes(
        limit=max(limit * 8, 20),
        platform=None,
        exclude_session_id=normalized_current_session_id,
        agent_namespace=agent_namespace,
    )

    results = []
    seen: set[str] = set()
    for episode in recent:
        session_id = str(episode.session_id)
        if session_id in seen or session_id == normalized_current_session_id:
            continue
        session = await client.transport.get_session(session_id)
        if session is None:
            continue
        seen.add(session_id)
        preview = session.summary or episode_excerpt(episode.content)
        payload = _session_payload(session)
        payload["preview"] = preview
        results.append(payload)
        if len(results) >= limit:
            break

    return {
        "success": True,
        "mode": "recent",
        "backend": "memory",
        "results": results,
        "count": len(results),
        "message": f"Showing {len(results)} recent Memory sessions.",
    }


async def search_sessions(
    client: MemoryClient,
    *,
    query: str,
    role_filter: str | None,
    limit: int,
    current_session_id: str | None,
    platform: str | None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    normalized_current_session_id = normalize_current_session_id(current_session_id)
    allowed_roles = None
    if role_filter and role_filter.strip():
        allowed_roles = {role.strip().lower() for role in role_filter.split(",") if role.strip()}

    episodes = await client.search_memory(
        query=query,
        limit=max(limit * 25, 100),
        platform=None,
        days_back=_SESSION_DAYS_BACK,
        agent_namespace=agent_namespace,
    )

    grouped: dict[str, list[Any]] = {}
    for episode in episodes:
        session_id = str(episode.session_id)
        if normalized_current_session_id and session_id == normalized_current_session_id:
            continue
        role_name = getattr(episode.role, "value", str(episode.role)).lower()
        if allowed_roles and role_name not in allowed_roles:
            continue
        grouped.setdefault(session_id, []).append(episode)
        if len(grouped) >= limit and all(len(items) >= 3 for items in grouped.values()):
            break

    if not grouped:
        return {
            "success": True,
            "backend": "memory",
            "query": query,
            "results": [],
            "count": 0,
            "message": "No matching Memory sessions found.",
        }

    results = []
    for session_id, matched_episodes in list(grouped.items())[:limit]:
        session = await client.transport.get_session(session_id)
        if session is None:
            continue
        excerpts = [
            {
                "role": getattr(episode.role, "value", str(episode.role)),
                "timestamp": episode.message_timestamp.isoformat(),
                "content": episode_excerpt(episode.content, query=query),
            }
            for episode in matched_episodes[:3]
        ]
        summary = session.summary
        if not _summary_matches_query(summary, query):
            summary = "Matched episodic recall from memory:\n" + "\n".join(
                f"- [{item['role']}] {item['content']}" for item in excerpts
            )
        results.append(
            {
                "session_id": session_id,
                "when": format_timestamp(session.ended_at or session.started_at),
                "source": getattr(session.platform, "value", str(session.platform)),
                "message_count": session.message_count,
                "summary": summary,
                "matched_episodes": excerpts,
            }
        )

    return {
        "success": True,
        "backend": "memory",
        "query": query,
        "results": results,
        "count": len(results),
        "sessions_searched": len(results),
    }


async def load_session_transcript(
    client: MemoryClient,
    *,
    reference: str,
    platform: str | None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    resolved = await resolve_session_reference(
        client,
        reference=reference,
        platform=platform,
        agent_namespace=agent_namespace,
    )
    if not resolved.get("success"):
        return resolved

    session = resolved.get("session") or {}
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        return {
            "success": False,
            "backend": "memory",
            "reference": reference,
            "error": "invalid_session_payload",
            "message": "Memory session payload is missing a session_id.",
        }

    episodes = await client.transport.list_episodes_for_session(session_id)
    return {
        "success": True,
        "backend": "memory",
        "reference": reference,
        "match_type": resolved.get("match_type"),
        "session": session,
        "messages": [_episode_to_conversation_message(episode) for episode in episodes],
        "count": len(episodes),
        "message": f"Loaded {len(episodes)} messages from memory.",
    }


async def update_session_title(
    client: MemoryClient,
    *,
    reference: str,
    title: str | None,
    platform: str | None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    resolved = await resolve_session_reference(
        client,
        reference=reference,
        platform=platform,
        agent_namespace=agent_namespace,
    )
    if not resolved.get("success"):
        return resolved

    session_payload = resolved.get("session") or {}
    session_id = str(session_payload.get("session_id") or "").strip()
    if not session_id:
        return {
            "success": False,
            "backend": "memory",
            "reference": reference,
            "error": "invalid_session_payload",
            "message": "Memory session payload is missing a session_id.",
        }

    sanitized = sanitize_session_title(title)
    if sanitized:
        existing = await resolve_session_reference(
            client,
            reference=sanitized,
            platform=platform,
            agent_namespace=agent_namespace,
        )
        if existing.get("success"):
            existing_session = existing.get("session") or {}
            existing_id = str(existing_session.get("session_id") or "").strip()
            if existing_id and existing_id != session_id:
                return {
                    "success": False,
                    "backend": "memory",
                    "reference": reference,
                    "error": "title_conflict",
                    "message": f"Title '{sanitized}' is already in use by session {existing_session.get('legacy_session_id') or existing_id}.",
                }

    updated = await client.transport.update_session(session_id, {"title": sanitized})
    return {
        "success": True,
        "backend": "memory",
        "reference": reference,
        "session": _session_payload(updated),
        "title": sanitized,
        "message": "Session title updated." if sanitized else "Session title cleared.",
    }


async def delete_session(
    client: MemoryClient,
    *,
    reference: str,
    platform: str | None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    resolved = await resolve_session_reference(
        client,
        reference=reference,
        platform=platform,
        agent_namespace=agent_namespace,
    )
    if not resolved.get("success"):
        return resolved

    session_payload = resolved.get("session") or {}
    session_id = str(session_payload.get("session_id") or "").strip()
    if not session_id:
        return {
            "success": False,
            "backend": "memory",
            "reference": reference,
            "error": "invalid_session_payload",
            "message": "Memory session payload is missing a session_id.",
        }

    deleted = await client.transport.delete_session(session_id)
    return {
        "success": bool(deleted),
        "backend": "memory",
        "reference": reference,
        "session": session_payload,
        "message": "Session deleted." if deleted else f"No Memory session matched '{reference}'.",
    }


async def export_sessions(
    client: MemoryClient,
    *,
    reference: str | None,
    platform: str | None,
    limit: int = 1000,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    if reference:
        transcript = await load_session_transcript(
            client,
            reference=reference,
            platform=platform,
            agent_namespace=agent_namespace,
        )
        if not transcript.get("success"):
            return transcript
        export_payload = {
            **(transcript.get("session") or {}),
            "messages": transcript.get("messages") or [],
        }
        return {
            "success": True,
            "backend": "memory",
            "mode": "single",
            "results": [export_payload],
            "count": 1,
            "message": "Exported 1 Memory session.",
        }

    listing = await list_all_sessions(client, limit=limit, platform=platform, agent_namespace=agent_namespace)
    results: list[dict[str, Any]] = []
    for session in listing.get("results") or []:
        session_id = str(session.get("session_id") or "").strip()
        if not session_id:
            continue
        episodes = await client.transport.list_episodes_for_session(session_id)
        results.append({**session, "messages": [_episode_to_conversation_message(episode) for episode in episodes]})
    return {
        "success": True,
        "backend": "memory",
        "mode": "all",
        "results": results,
        "count": len(results),
        "message": f"Exported {len(results)} Memory sessions.",
    }


async def prune_sessions(
    client: MemoryClient,
    *,
    older_than_days: int,
    platform: str | None,
    limit: int = 5000,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    listing = await list_all_sessions(client, limit=limit, platform=platform, agent_namespace=agent_namespace)
    sessions = listing.get("results") or []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(int(older_than_days), 0))
    deleted: list[str] = []

    for session in sessions:
        ended_at = session.get("ended_at")
        started_at = session.get("started_at")
        if not ended_at:
            continue
        try:
            started_dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        except Exception:
            continue
        if started_dt > cutoff:
            continue
        session_id = str(session.get("session_id") or "").strip()
        if not session_id:
            continue
        if await client.transport.delete_session(session_id):
            deleted.append(str(session.get("legacy_session_id") or session_id))

    return {
        "success": True,
        "backend": "memory",
        "count": len(deleted),
        "deleted": deleted,
        "message": f"Pruned {len(deleted)} Memory session(s).",
    }


async def session_stats(
    client: MemoryClient,
    *,
    platform: str | None,
    limit: int = 5000,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    listing = await list_all_sessions(client, limit=limit, platform=platform, agent_namespace=agent_namespace)
    sessions = listing.get("results") or []
    counts_by_source: dict[str, int] = {}
    total_messages = 0

    for session in sessions:
        source = str(session.get("source") or "unknown")
        counts_by_source[source] = counts_by_source.get(source, 0) + 1
        total_messages += int(session.get("message_count") or 0)

    return {
        "success": True,
        "backend": "memory",
        "total_sessions": len(sessions),
        "total_messages": total_messages,
        "counts_by_source": counts_by_source,
        "message": f"Counted {len(sessions)} Memory sessions.",
    }


async def list_live_session_routes(
    client: MemoryClient,
    *,
    platform: str | None,
    limit: int = 1000,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    sessions = await client.transport.list_sessions(
        limit=max(limit * 8, 200),
        platform=normalize_platform_filter(platform),
        agent_namespace=agent_namespace,
    )
    latest_by_key: dict[str, dict[str, Any]] = {}

    for session in sessions:
        payload = _session_payload(session)
        routing = _routing_payload(payload)
        if routing is None:
            continue
        session_key = str(routing.get("session_key") or "").strip()
        if not session_key:
            continue
        existing = latest_by_key.get(session_key)
        if existing is None or _routing_rank(payload) > _routing_rank(existing):
            latest_by_key[session_key] = payload

    results = []
    for session_key, payload in latest_by_key.items():
        routing = payload.get("routing") or {}
        results.append(
            {
                "session_key": session_key,
                "session_id": payload.get("legacy_session_id") or payload.get("session_id"),
                "memory_session_id": payload.get("session_id"),
                "platform": routing.get("platform") or payload.get("source"),
                "chat_type": routing.get("chat_type") or "dm",
                "display_name": routing.get("display_name") or routing.get("chat_name") or routing.get("user_name"),
                "bound_at": routing.get("bound_at") or payload.get("started_at"),
                "updated_at": (
                    payload.get("updated_at")
                    or routing.get("updated_at")
                    or routing.get("bound_at")
                    or payload.get("started_at")
                ),
                "origin": {
                    "platform": routing.get("platform") or payload.get("source"),
                    "chat_id": routing.get("chat_id"),
                    "chat_name": routing.get("chat_name"),
                    "chat_type": routing.get("chat_type") or "dm",
                    "user_id": routing.get("user_id"),
                    "user_name": routing.get("user_name"),
                    "thread_id": routing.get("thread_id"),
                    "chat_topic": routing.get("chat_topic"),
                    "user_id_alt": routing.get("user_id_alt"),
                    "chat_id_alt": routing.get("chat_id_alt"),
                },
            }
        )

    results.sort(key=lambda item: str(item.get("bound_at") or ""), reverse=True)
    results = results[:limit]
    return {
        "success": True,
        "backend": "memory",
        "results": results,
        "count": len(results),
        "message": f"Loaded {len(results)} live Memory session route(s).",
    }


async def find_live_session_route(
    client: MemoryClient,
    *,
    platform: str,
    chat_id: str,
    thread_id: str | None = None,
    session_key: str | None = None,
    limit: int = 1000,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    listing = await list_live_session_routes(
        client,
        platform=platform,
        limit=limit,
        agent_namespace=agent_namespace,
    )
    results = listing.get("results") or []
    for item in results:
        if session_key and str(item.get("session_key") or "") == str(session_key):
            return {"success": True, "backend": "memory", "route": item}
        origin = item.get("origin") or {}
        if str(origin.get("chat_id") or "") != str(chat_id):
            continue
        if thread_id is not None and str(origin.get("thread_id") or "") != str(thread_id):
            continue
        return {"success": True, "backend": "memory", "route": item}
    return {
        "success": False,
        "backend": "memory",
        "error": "not_found",
        "message": "No live memory route matched the requested chat.",
    }
