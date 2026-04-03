from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from supabase import Client
else:
    Client = Any

try:
    from supabase import create_client
except ImportError:  # pragma: no cover - exercised indirectly in environments without supabase installed.
    create_client = None

from memory.config import MemoryConfig
from memory.embedding import EmbeddingProvider
from memory.models import (
    ActiveState,
    Commitment,
    Correction,
    DecisionOutcome,
    Directive,
    Episode,
    Fact,
    FactHistory,
    Pattern,
    SessionHandoff,
    Session,
    TimelineEvent,
    VECTOR_DIMENSIONS,
)


logger = logging.getLogger(__name__)
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_TOOL_JSON_MARKERS = ('"function"', '"tool_calls"', '"call_id"', '"response_item_id"')
_ASSISTANT_META_MARKERS = (
    "jsonl",
    "read_file",
    "search_files",
    "search tool",
    "session_search",
    "execute_sql",
    "mcp_supabase",
    "line 262",
    "let me search",
    "let me read",
    "the q4 answer",
    "the line is too long",
    "terminal to extract",
)
_REFERENCE_CONTENT_MARKERS = (
    "[memory soul rules]",
    "[/memory soul rules]",
    "<memory>",
    "relevant prior conversations",
    "recent cross-session continuity",
    "active session summary",
    "system prompt",
    "memory section",
    "injected into your messages",
    "injected into my system prompt",
    "soul skill updated",
    "soul rules updated",
    "updated soul rules",
    "got the updated soul rules",
    "soul rules loaded",
    "update the soul skill",
    "skills/memory/soul/skill.md",
    ".hermes/soul.md",
    "skill.md",
    "soul.md",
)
_OPERATIONAL_CONTENT_MARKERS = (
    "[system: if you have a meaningful status report or findings",
    "run the memory processor. execute:",
    "python -m memory.daemon process-memory",
    "memory processor failed",
    "memory processor ran clean",
    "no new memory processing needed",
    "created table with schema",
    "timeline_events table",
    "decision_outcomes table",
    "rls policies",
    "indexes",
    "facts extracted",
    "episode_count",
    "fact_count",
    "session_count",
    "nothing procedural to save from this session",
    "skipping skill creation",
    "session is about to be automatically reset",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _vector_to_pg(value: list[float] | None) -> str | None:
    if value is None:
        return None
    if len(value) != VECTOR_DIMENSIONS:
        raise ValueError(f"Vector must contain exactly {VECTOR_DIMENSIONS} dimensions.")
    return "[" + ",".join(f"{float(item):.8f}" for item in value) + "]"


def _parse_vector(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [float(item) for item in value]
    if isinstance(value, tuple):
        return [float(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("[") and stripped.endswith("]"):
            stripped = stripped[1:-1]
        if not stripped:
            return []
        return [float(item.strip()) for item in stripped.split(",") if item.strip()]
    raise TypeError(f"Unsupported vector value: {type(value)!r}")


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    return value


def _search_tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_PATTERN.findall((value or "").lower()) if len(token) > 1}


def _query_term_variants(value: str) -> set[str]:
    variants = set()
    for token in _search_tokens(value):
        variants.add(token)
        if token.endswith("ies") and len(token) > 4:
            variants.add(token[:-3] + "y")
        if token.endswith("es") and len(token) > 4:
            variants.add(token[:-2])
        if token.endswith("s") and len(token) > 3:
            variants.add(token[:-1])
    return {variant for variant in variants if len(variant) > 1}


def _lexical_search_patterns(query: str) -> list[str]:
    normalized = " ".join((query or "").lower().split())
    patterns: list[str] = []
    if normalized:
        patterns.append(normalized)
    patterns.extend(sorted(_query_term_variants(normalized), key=len, reverse=True))
    seen: set[str] = set()
    deduped: list[str] = []
    for pattern in patterns:
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        deduped.append(pattern)
    return deduped[:8]


def _fallback_search_score(query: str, content: str) -> float:
    normalized_query = " ".join((query or "").lower().split())
    normalized_content = " ".join((content or "").lower().split())
    if not normalized_query or not normalized_content:
        return 0.0
    score = 0.0
    if normalized_query in normalized_content:
        score += 10.0
    for pattern in _lexical_search_patterns(normalized_query):
        if pattern and pattern != normalized_query and pattern in normalized_content:
            score += 6.0
    query_tokens = _query_term_variants(normalized_query)
    content_tokens = _search_tokens(normalized_content)
    if not query_tokens or not content_tokens:
        return score
    overlap = query_tokens & content_tokens
    if overlap:
        score += float(len(overlap)) * 2.0
        score += len(overlap) / len(query_tokens | content_tokens)
    return score


def _looks_like_tool_payload(content: str, role: str, metadata: dict[str, Any] | None) -> bool:
    normalized = (content or "").lstrip()
    metadata = metadata or {}
    if role == "tool":
        return True
    if metadata.get("tool_name") and role != "user":
        return True
    if normalized.startswith("[{") and any(marker in normalized[:400] for marker in _TOOL_JSON_MARKERS):
        return True
    return False


def _assistant_meta_penalty(content: str, role: str) -> float:
    if role != "assistant":
        return 0.0
    lowered = (content or "").lower()
    hits = sum(1 for marker in _ASSISTANT_META_MARKERS if marker in lowered)
    if hits == 0:
        return 0.0
    return min(8.0, hits * 1.5)


def _looks_like_reference_content(content: str, metadata: dict[str, Any] | None = None) -> bool:
    lowered_content = (content or "").lower()
    metadata = metadata or {}
    if any(marker in lowered_content for marker in _REFERENCE_CONTENT_MARKERS):
        return True
    source_kind = str(metadata.get("source_kind") or "").lower()
    return source_kind.endswith("soul_rules") or source_kind == "prompt_reference"


def _looks_like_operational_content(content: str, metadata: dict[str, Any] | None = None) -> bool:
    lowered_content = (content or "").lower()
    metadata = metadata or {}
    if any(marker in lowered_content for marker in _OPERATIONAL_CONTENT_MARKERS):
        return True
    if "consolidated" in lowered_content and "session" in lowered_content and "fact" in lowered_content:
        return True
    source_kind = str(metadata.get("source_kind") or "").lower()
    return source_kind in {"operational_prompt", "operational_status", "daemon_status"}


def _reference_content_penalty(
    query: str,
    content: str,
    role: str,
    metadata: dict[str, Any] | None,
) -> float:
    lowered_query = (query or "").lower()
    metadata = metadata or {}
    if not _looks_like_reference_content(content, metadata):
        return 0.0

    query_hits = [marker for marker in _REFERENCE_CONTENT_MARKERS if marker in lowered_query]
    penalty = 7.5 if role == "assistant" else 5.5

    if len(content or "") > 600:
        penalty += 2.5
    if metadata.get("tool_name"):
        penalty += 2.0
    if query_hits:
        penalty = max(0.0, penalty - 6.5)

    return penalty


def _operational_content_penalty(
    query: str,
    content: str,
    role: str,
    metadata: dict[str, Any] | None,
) -> float:
    lowered_query = (query or "").lower()
    if not _looks_like_operational_content(content, metadata):
        return 0.0

    operational_query_markers = (
        "memory processor",
        "process-memory",
        "daemon",
        "status report",
        "facts extracted",
        "episode_count",
        "fact_count",
        "session_count",
    )
    if any(marker in lowered_query for marker in operational_query_markers):
        return 0.0

    return 10.0 if role == "assistant" else 8.0


def _episode_rank_score(
    query: str,
    episode: Episode,
    *,
    semantic_rank: int | None,
    lexical_hit: bool,
) -> float:
    role_name = getattr(episode.role, "value", str(episode.role)).lower()
    score = _fallback_search_score(query, episode.content)
    if lexical_hit:
        score += 3.0
    if semantic_rank is not None:
        score += max(0.0, 4.0 - (semantic_rank * 0.1))
    if role_name == "user":
        score += 4.0
    elif role_name == "assistant":
        score += 0.5
    else:
        score -= 8.0
    if len(episode.content or "") > 500 and role_name == "user":
        score += 0.5
    if _looks_like_tool_payload(episode.content or "", role_name, getattr(episode, "message_metadata", {}) or {}):
        score -= 14.0
    else:
        score -= _assistant_meta_penalty(episode.content or "", role_name)
        score -= _reference_content_penalty(
            query,
            episode.content or "",
            role_name,
            getattr(episode, "message_metadata", {}) or {},
        )
        score -= _operational_content_penalty(
            query,
            episode.content or "",
            role_name,
            getattr(episode, "message_metadata", {}) or {},
        )
    return score


def _episode_fingerprint(
    *,
    session_id: str | UUID,
    role: str,
    content_hash: str,
    message_timestamp: datetime,
    message_metadata: dict[str, Any] | None,
) -> str:
    metadata = message_metadata or {}
    source_line_number = metadata.get("memory_source_line_number")
    if source_line_number not in (None, ""):
        return f"{session_id}:line:{int(source_line_number)}"
    return f"{session_id}:{role}:{content_hash}:{message_timestamp.isoformat()}"


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    for key in ("embedding", "summary_embedding"):
        if key in normalized:
            normalized[key] = _parse_vector(normalized[key])
    return normalized


def _response_rows(response: Any) -> list[Any]:
    data = getattr(response, "data", None)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return [data]


def _first_response_record(response: Any) -> dict[str, Any] | None:
    rows = _response_rows(response)
    if not rows:
        return None
    first = rows[0]
    if not isinstance(first, dict):
        raise TypeError(f"Expected a record dictionary, received {type(first)!r}.")
    return first


def _is_duplicate_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "duplicate key value" in message or "unique constraint" in message


def _is_missing_relation_error(exc: Exception, relation_name: str) -> bool:
    message = str(exc).lower()
    relation = relation_name.lower()
    return (
        f"relation \"{relation}\" does not exist" in message
        or f"relation {relation} does not exist" in message
        or f"could not find the '{relation}'" in message
        or f"could not find the table '{relation}'" in message
        or f"could not find the table 'memory.{relation}'" in message
    )


def _is_platform_enum_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "invalid input value for enum platform" in message or "invalid input value for enum memory.platform" in message


_UNKNOWN_COLUMN_PATTERNS = (
    re.compile(r"could not find the '([^']+)' column", re.IGNORECASE),
    re.compile(r"column\s+[a-zA-Z_][a-zA-Z0-9_]*\.([a-zA-Z_][a-zA-Z0-9_]*)\s+does not exist", re.IGNORECASE),
    re.compile(r'column "([^"]+)" does not exist', re.IGNORECASE),
    re.compile(r"column ([a-zA-Z_][a-zA-Z0-9_]*) does not exist", re.IGNORECASE),
    re.compile(r"record \"([^\"]+)\" has no field", re.IGNORECASE),
)

_SESSION_COMPAT_TOPIC_PREFIX = "__hermes_meta__:"


def _extract_unknown_column(exc: Exception) -> str | None:
    message = str(exc)
    lowered = message.lower()
    if "column" not in lowered and "field" not in lowered:
        return None
    for pattern in _UNKNOWN_COLUMN_PATTERNS:
        match = pattern.search(message)
        if match:
            return match.group(1)
    return None


def _decode_session_compat_topics(topics: list[Any] | None) -> dict[str, Any]:
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


def _encode_session_compat_topics(topics: list[Any] | None, metadata: dict[str, Any]) -> list[str]:
    cleaned = [str(item) for item in (topics or []) if not str(item).startswith(_SESSION_COMPAT_TOPIC_PREFIX)]
    if not metadata:
        return cleaned
    encoded = base64.urlsafe_b64encode(
        json.dumps(_serialize_value(metadata), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    cleaned.append(_SESSION_COMPAT_TOPIC_PREFIX + encoded)
    return cleaned


def _normalize_agent_namespace(agent_namespace: str | None) -> str | None:
    cleaned = str(agent_namespace or "").strip()
    return cleaned or None


def _agent_namespace_matches(record_namespace: Any, requested_namespace: str | None) -> bool:
    requested = _normalize_agent_namespace(requested_namespace)
    if requested is None:
        return True
    current = _normalize_agent_namespace(record_namespace)
    if requested == "main":
        return current in (None, "main")
    return current == requested


def _filter_records_by_agent_namespace(records: list[Any], requested_namespace: str | None) -> list[Any]:
    requested = _normalize_agent_namespace(requested_namespace)
    if requested is None:
        return list(records)
    return [
        record
        for record in records
        if _agent_namespace_matches(getattr(record, "agent_namespace", None), requested)
    ]


class MemoryTransport(Protocol):
    """Interface for memory storage backends."""

    async def insert_session(self, session: Session) -> Session: ...

    async def get_session(self, session_id: str) -> Session | None: ...

    async def get_session_by_legacy_id(self, legacy_session_id: str) -> Session | None: ...

    async def list_sessions(
        self,
        limit: int = 20,
        platform: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Session]: ...

    async def list_episodes_for_session(self, session_id: str, limit: int | None = None) -> list[Episode]: ...

    async def update_session(self, session_id: str, updates: dict[str, Any]) -> Session: ...

    async def delete_session(self, session_id: str) -> bool: ...

    async def insert_episode(self, episode: Episode) -> Episode: ...

    async def insert_fact(self, fact: Fact) -> Fact: ...

    async def get_fact(self, fact_id: str) -> Fact | None: ...

    async def update_fact(self, fact_id: str, updates: dict[str, Any]) -> Fact: ...

    async def deactivate_fact(self, fact_id: str, replaced_by: str | None = None) -> None: ...

    async def touch_fact(self, fact_id: str) -> None: ...

    async def search_episodes(
        self,
        query: str,
        limit: int = 20,
        platform: str | None = None,
        days_back: int = 30,
        agent_namespace: str | None = None,
    ) -> list[Episode]: ...

    async def list_recent_episodes(
        self,
        limit: int = 5,
        platform: str | None = None,
        exclude_session_id: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Episode]: ...

    async def search_facts(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        agent_namespace: str | None = None,
    ) -> list[Fact]: ...

    async def insert_fact_history(self, history: FactHistory) -> FactHistory: ...

    async def upsert_active_state(self, state: ActiveState) -> ActiveState: ...

    async def list_active_state(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[ActiveState]: ...

    async def upsert_directive(self, directive: Directive) -> Directive: ...

    async def list_directives(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Directive]: ...

    async def upsert_timeline_event(self, event: TimelineEvent) -> TimelineEvent: ...

    async def list_timeline_events(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
    ) -> list[TimelineEvent]: ...

    async def upsert_decision_outcome(self, outcome: DecisionOutcome) -> DecisionOutcome: ...

    async def list_decision_outcomes(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[DecisionOutcome]: ...

    async def delete_decision_outcome(
        self,
        outcome_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool: ...

    async def upsert_pattern(self, pattern: Pattern) -> Pattern: ...

    async def list_patterns(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        pattern_types: list[str] | None = None,
    ) -> list[Pattern]: ...

    async def delete_pattern(
        self,
        pattern_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool: ...

    async def upsert_commitment(self, commitment: Commitment) -> Commitment: ...

    async def list_commitments(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Commitment]: ...

    async def upsert_correction(self, correction: Correction) -> Correction: ...

    async def list_corrections(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        active_only: bool = True,
    ) -> list[Correction]: ...

    async def upsert_session_handoff(self, handoff: SessionHandoff) -> SessionHandoff: ...

    async def list_session_handoffs(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        exclude_session_id: str | None = None,
    ) -> list[SessionHandoff]: ...

    async def health_check(self) -> bool: ...


class SupabaseTransport:
    """Direct Supabase connection (local or remote)."""

    def __init__(
        self,
        supabase_url: str | None = None,
        supabase_key: str | None = None,
        *,
        schema: str | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        client: Client | None = None,
    ) -> None:
        config = MemoryConfig.from_env()
        url = supabase_url or config.supabase_url
        key = supabase_key or config.supabase_key
        if client is None and (not url or not key):
            raise ValueError("MEMORY_SUPABASE_URL and MEMORY_SUPABASE_KEY must be configured.")
        if client is None and create_client is None:
            raise ImportError("supabase must be installed to use SupabaseTransport.")
        self.schema = schema or config.supabase_schema
        self.embedding_provider = embedding_provider
        self._client = client or create_client(url, key)

    async def _run(self, func: Any) -> Any:
        return await asyncio.to_thread(func)

    def _schema_client(self) -> Any:
        return self._client.schema(self.schema)

    def _schema_rpc(self, name: str, params: dict[str, Any]) -> Any:
        schema_client = self._schema_client()
        rpc = getattr(schema_client, "rpc", None)
        if callable(rpc):
            return rpc(name, params)
        return self._client.rpc(name, params)

    def _require_record(self, response: Any, *, operation: str) -> dict[str, Any]:
        record = _first_response_record(response)
        if record is None:
            raise LookupError(f"{operation} returned no rows.")
        return record

    def _drop_unsupported_column(self, payload: dict[str, Any], exc: Exception, *, table: str, operation: str) -> bool:
        column = _extract_unknown_column(exc)
        if not column or column not in payload:
            return False
        payload.pop(column, None)
        logger.info(
            "Memory %s schema does not support column %s during %s; retrying without it.",
            table,
            column,
            operation,
        )
        return True

    def _drop_unsupported_session_column(self, payload: dict[str, Any], exc: Exception, *, operation: str) -> bool:
        return self._drop_unsupported_column(payload, exc, table="sessions", operation=operation)

    async def _merge_session_compat_metadata(
        self,
        session_id: str,
        dropped: dict[str, Any],
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not dropped:
            return record
        current = record or await self._fetch_row("sessions", session_id)
        if current is None:
            return record
        existing_metadata = _decode_session_compat_topics(current.get("topics"))
        merged_metadata = dict(existing_metadata)
        merged_metadata.update(_serialize_value(dropped))
        payload = {"topics": _encode_session_compat_topics(current.get("topics"), merged_metadata)}
        response = await self._run(
            lambda: self._schema_client().table("sessions").update(payload).eq("id", session_id).execute()
        )
        return self._require_record(response, operation="update_session_topics_compat")

    async def _insert_session_payload(self, payload: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        working = dict(payload)
        dropped: dict[str, Any] = {}
        while True:
            try:
                response = await self._run(lambda: self._schema_client().table("sessions").insert(dict(working)).execute())
                return response, dropped
            except Exception as exc:
                if self._apply_session_platform_fallback(working, exc):
                    continue
                column = _extract_unknown_column(exc)
                if not self._drop_unsupported_session_column(working, exc, operation="insert_session"):
                    raise
                if column and column in payload:
                    dropped[column] = payload[column]

    async def _update_session_payload(self, session_id: str, payload: dict[str, Any]) -> tuple[Any | None, dict[str, Any]]:
        working = dict(payload)
        dropped: dict[str, Any] = {}
        while working:
            try:
                response = await self._run(
                    lambda: self._schema_client().table("sessions").update(dict(working)).eq("id", session_id).execute()
                )
                return response, dropped
            except Exception as exc:
                if self._apply_session_platform_fallback(working, exc):
                    continue
                column = _extract_unknown_column(exc)
                if not self._drop_unsupported_session_column(working, exc, operation="update_session"):
                    raise
                if column and column in payload:
                    dropped[column] = payload[column]
        return None, dropped

    async def _insert_payload(self, table: str, payload: dict[str, Any], *, operation: str) -> tuple[Any, dict[str, Any]]:
        working = dict(payload)
        dropped: dict[str, Any] = {}
        while True:
            try:
                response = await self._run(lambda: self._schema_client().table(table).insert(dict(working)).execute())
                return response, dropped
            except Exception as exc:
                if self._apply_record_platform_fallback(working, exc, table=table):
                    continue
                column = _extract_unknown_column(exc)
                if not self._drop_unsupported_column(working, exc, table=table, operation=operation):
                    raise
                if column and column in payload:
                    dropped[column] = payload[column]

    async def _update_payload(
        self,
        table: str,
        record_id: str,
        payload: dict[str, Any],
        *,
        operation: str,
    ) -> tuple[Any | None, dict[str, Any]]:
        working = dict(payload)
        dropped: dict[str, Any] = {}
        while working:
            try:
                response = await self._run(
                    lambda: self._schema_client().table(table).update(dict(working)).eq("id", record_id).execute()
                )
                return response, dropped
            except Exception as exc:
                if self._apply_record_platform_fallback(working, exc, table=table):
                    continue
                column = _extract_unknown_column(exc)
                if not self._drop_unsupported_column(working, exc, table=table, operation=operation):
                    raise
                if column and column in payload:
                    dropped[column] = payload[column]
        return None, dropped

    def _apply_session_platform_fallback(self, working: dict[str, Any], exc: Exception) -> bool:
        if not _is_platform_enum_error(exc):
            return False
        actual_platform = str(working.get("platform") or "").strip()
        if not actual_platform or actual_platform == "other":
            return False
        model_config = working.get("model_config")
        if not isinstance(model_config, dict):
            model_config = {}
        model_config.setdefault("source_platform", actual_platform)
        working["model_config"] = model_config
        working["platform"] = "other"
        return True

    def _apply_record_platform_fallback(self, working: dict[str, Any], exc: Exception, *, table: str) -> bool:
        if table != "episodes" or not _is_platform_enum_error(exc):
            return False
        actual_platform = str(working.get("platform") or "").strip()
        if not actual_platform or actual_platform == "other":
            return False
        metadata = working.get("message_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.setdefault("source_platform", actual_platform)
        working["message_metadata"] = metadata
        working["platform"] = "other"
        return True

    async def _fetch_row(self, table: str, record_id: str) -> dict[str, Any] | None:
        response = await self._run(
            lambda: self._schema_client().table(table).select("*").eq("id", record_id).limit(1).execute()
        )
        return _first_response_record(response)

    async def _write_vector_column(self, table: str, record_id: str, column: str, vector: list[float] | None) -> None:
        vector_literal = _vector_to_pg(vector)
        if vector_literal is None:
            return

        normalized_id = str(UUID(str(record_id)))
        query = (
            f'update "{self.schema}"."{table}" '
            f"set \"{column}\" = '{vector_literal}'::vector "
            f"where id = '{normalized_id}'::uuid"
        )
        try:
            await self._run(lambda: self._schema_rpc("execute_sql", {"query": query}).execute())
            return
        except Exception:
            # Some environments may not expose a SQL-executor RPC; keep a compatibility fallback.
            await self._run(
                lambda: self._schema_client().table(table).update({column: vector_literal}).eq("id", normalized_id).execute()
            )

    async def insert_session(self, session: Session) -> Session:
        payload = _serialize_value(session.model_dump(exclude_none=True, exclude_unset=True, by_alias=True))
        summary_embedding = session.summary_embedding
        payload.pop("summary_embedding", None)
        response, dropped = await self._insert_session_payload(payload)
        record = self._require_record(response, operation="insert_session")
        record_id = str(record["id"])
        if dropped:
            merged = await self._merge_session_compat_metadata(record_id, dropped, record=record)
            if merged is not None:
                record = merged
        if summary_embedding is not None:
            await self._write_vector_column("sessions", record_id, "summary_embedding", summary_embedding)
            refreshed = await self._fetch_row("sessions", record_id)
            if refreshed is not None:
                record = refreshed
        return Session.model_validate(_normalize_record(record))

    async def get_session(self, session_id: str) -> Session | None:
        response = await self._run(
            lambda: self._schema_client().table("sessions").select("*").eq("id", session_id).limit(1).execute()
        )
        record = _first_response_record(response)
        if record is None:
            return None
        return Session.model_validate(_normalize_record(record))

    async def get_session_by_legacy_id(self, legacy_session_id: str) -> Session | None:
        try:
            response = await self._run(
                lambda: self._schema_client()
                .table("sessions")
                .select("*")
                .eq("legacy_session_id", legacy_session_id)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            if _extract_unknown_column(exc) == "legacy_session_id":
                return None
            raise
        record = _first_response_record(response)
        if record is None:
            return None
        return Session.model_validate(_normalize_record(record))

    async def list_sessions(
        self,
        limit: int = 20,
        platform: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Session]:
        query = self._schema_client().table("sessions").select("*")
        if platform:
            query = query.eq("platform", platform)
        response = await self._run(lambda: query.order("started_at", desc=True).limit(max(limit * 6, 60)).execute())
        sessions = [Session.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        return _filter_records_by_agent_namespace(sessions, agent_namespace)[:limit]

    async def list_episodes_for_session(self, session_id: str, limit: int | None = None) -> list[Episode]:
        def _query() -> Any:
            query = self._schema_client().table("episodes").select("*").eq("session_id", session_id)
            query = query.order("message_timestamp")
            if limit is not None:
                query = query.limit(limit)
            return query.execute()

        response = await self._run(_query)
        return [Episode.model_validate(_normalize_record(item)) for item in _response_rows(response)]

    async def update_session(self, session_id: str, updates: dict[str, Any]) -> Session:
        payload = _serialize_value(dict(updates))
        summary_embedding = updates.get("summary_embedding")
        payload.pop("summary_embedding", None)
        record: dict[str, Any] | None = None
        dropped: dict[str, Any] = {}
        if payload:
            response, dropped = await self._update_session_payload(session_id, payload)
            if response is not None:
                record = self._require_record(response, operation="update_session")
        if dropped:
            merged = await self._merge_session_compat_metadata(session_id, dropped, record=record)
            if merged is not None:
                record = merged
        if "summary_embedding" in updates:
            await self._write_vector_column("sessions", session_id, "summary_embedding", summary_embedding)
            refreshed = await self._fetch_row("sessions", session_id)
            if refreshed is not None:
                record = refreshed
        if record is None:
            fetched = await self._fetch_row("sessions", session_id)
            if fetched is None:
                raise LookupError("update_session returned no rows.")
            record = fetched
        return Session.model_validate(_normalize_record(record))

    async def delete_session(self, session_id: str) -> bool:
        session_exists = await self._fetch_row("sessions", session_id)
        if session_exists is None:
            return False

        await self._run(
            lambda: self._schema_client().table("episodes").delete().eq("session_id", session_id).execute()
        )
        await self._run(
            lambda: self._schema_client().table("sessions").delete().eq("id", session_id).execute()
        )
        return True

    async def insert_episode(self, episode: Episode) -> Episode:
        payload = _serialize_value(episode.model_dump(exclude_none=True))
        embedding = episode.embedding
        payload.pop("embedding", None)
        fingerprint = _episode_fingerprint(
            session_id=episode.session_id,
            role=episode.role.value,
            content_hash=episode.content_hash,
            message_timestamp=episode.message_timestamp,
            message_metadata=episode.message_metadata,
        )
        try:
            response, _ = await self._insert_payload("episodes", payload, operation="insert_episode")
            record = self._require_record(response, operation="insert_episode")
        except Exception as exc:
            if not _is_duplicate_error(exc):
                raise
            response = await self._run(
                lambda: self._schema_client()
                .table("episodes")
                .select("*")
                .eq("episode_fingerprint", fingerprint)
                .limit(1)
                .execute()
            )
            record = self._require_record(response, operation="insert_episode_duplicate_lookup")
        record_id = str(record["id"])
        if embedding is not None:
            await self._write_vector_column("episodes", record_id, "embedding", embedding)
            refreshed = await self._fetch_row("episodes", record_id)
            if refreshed is not None:
                record = refreshed
        return Episode.model_validate(_normalize_record(record))

    async def insert_fact(self, fact: Fact) -> Fact:
        payload = _serialize_value(fact.model_dump(exclude_none=True))
        try:
            response, _ = await self._insert_payload("facts", payload, operation="insert_fact")
            return Fact.model_validate(self._require_record(response, operation="insert_fact"))
        except Exception as exc:
            if not _is_duplicate_error(exc):
                raise
            if not fact.content_fingerprint:
                raise
            response = await self._run(
                lambda: self._schema_client()
                .table("facts")
                .select("*")
                .eq("content_fingerprint", fact.content_fingerprint)
                .limit(1)
                .execute()
            )
            return Fact.model_validate(self._require_record(response, operation="insert_fact_duplicate_lookup"))

    async def get_fact(self, fact_id: str) -> Fact | None:
        response = await self._run(
            lambda: self._schema_client().table("facts").select("*").eq("id", fact_id).limit(1).execute()
        )
        record = _first_response_record(response)
        if record is None:
            return None
        return Fact.model_validate(record)

    async def update_fact(self, fact_id: str, updates: dict[str, Any]) -> Fact:
        payload = _serialize_value(dict(updates))
        response, _ = await self._update_payload("facts", fact_id, payload, operation="update_fact")
        if response is None:
            fetched = await self._fetch_row("facts", fact_id)
            if fetched is None:
                raise LookupError("update_fact returned no rows.")
            return Fact.model_validate(_normalize_record(fetched))
        return Fact.model_validate(self._require_record(response, operation="update_fact"))

    async def deactivate_fact(self, fact_id: str, replaced_by: str | None = None) -> None:
        payload: dict[str, Any] = {
            "is_active": False,
            "updated_at": _now_utc().isoformat(),
        }
        if replaced_by:
            payload["replaced_by"] = replaced_by
        await self._run(lambda: self._schema_client().table("facts").update(payload).eq("id", fact_id).execute())

    async def touch_fact(self, fact_id: str) -> None:
        await self._run(lambda: self._schema_rpc("touch_fact", {"fact_id": fact_id}).execute())

    async def search_episodes(
        self,
        query: str,
        limit: int = 20,
        platform: str | None = None,
        days_back: int = 30,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        query_embedding: list[float] | None = None
        if self.embedding_provider is not None:
            try:
                query_embedding = await self.embedding_provider.embed_text(query)
            except Exception as exc:
                logger.debug("search_episodes embedding failed; continuing with lexical fallback: %s", exc)
        vector_episodes: list[Episode] = []
        if query_embedding is not None:
            try:
                response = await self._run(
                    lambda: self._schema_rpc(
                        "search_episodes",
                        {
                            "query_embedding": _vector_to_pg(query_embedding),
                            "match_count": limit,
                            "platform_filter": platform,
                            "days_back": days_back,
                            "min_emotional_intensity": 0.0,
                        },
                    ).execute()
                )
                episode_ids = [
                    str(item["id"])
                    for item in _response_rows(response)
                    if isinstance(item, dict) and item.get("id")
                ]
                if episode_ids:
                    vector_episodes = _filter_records_by_agent_namespace(
                        await self._fetch_episodes_by_ids(episode_ids),
                        agent_namespace,
                    )
            except Exception as exc:
                logger.debug("search_episodes RPC failed; falling back to recent episode scan: %s", exc)
        lexical_episodes = await self._lexical_episode_search(
            query=query,
            limit=limit,
            platform=platform,
            days_back=days_back,
            agent_namespace=agent_namespace,
        )
        fallback_episodes: list[Episode] = []
        if not vector_episodes:
            fallback_episodes = await self._fallback_episode_search(
                query=query,
                limit=max(limit * 4, 40),
                platform=platform,
                days_back=days_back,
                agent_namespace=agent_namespace,
            )

        vector_positions = {
            str(episode.id): index
            for index, episode in enumerate(vector_episodes)
            if getattr(episode, "id", None) is not None
        }
        lexical_ids = {
            str(episode.id)
            for episode in lexical_episodes
            if getattr(episode, "id", None) is not None
        }
        merged: dict[str, Episode] = {}
        for episode in [*vector_episodes, *lexical_episodes, *fallback_episodes]:
            episode_id = getattr(episode, "id", None)
            if episode_id is None:
                continue
            merged[str(episode_id)] = episode

        scored: list[tuple[Episode, float]] = []
        for episode_id, episode in merged.items():
            score = _episode_rank_score(
                query,
                episode,
                semantic_rank=vector_positions.get(episode_id),
                lexical_hit=episode_id in lexical_ids,
            )
            if score <= 0.0 and episode_id not in vector_positions:
                continue
            scored.append((episode, score))

        scored.sort(
            key=lambda item: (
                -item[1],
                -(item[0].message_timestamp.timestamp() if item[0].message_timestamp else 0.0),
            )
        )
        return [episode for episode, _ in scored[:limit]]

    async def list_recent_episodes(
        self,
        limit: int = 5,
        platform: str | None = None,
        exclude_session_id: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        def _query() -> Any:
            query = self._schema_client().table("episodes").select("*")
            if platform is not None:
                query = query.eq("platform", platform)
            if exclude_session_id is not None:
                query = query.neq("session_id", exclude_session_id)
            query = query.order("message_timestamp", desc=True).limit(max(limit * 8, 40))
            return query.execute()

        response = await self._run(_query)
        episodes = [Episode.model_validate(_normalize_record(item)) for item in response.data or []]
        return _filter_records_by_agent_namespace(episodes, agent_namespace)[:limit]

    async def _fetch_episodes_by_ids(self, episode_ids: list[str]) -> list[Episode]:
        response = await self._run(
            lambda: self._schema_client().table("episodes").select("*").in_("id", episode_ids).execute()
        )
        rows = {
            str(item["id"]): Episode.model_validate(_normalize_record(item))
            for item in response.data or []
            if isinstance(item, dict) and item.get("id")
        }
        return [rows[episode_id] for episode_id in episode_ids if episode_id in rows]

    async def _fallback_episode_search(
        self,
        *,
        query: str,
        limit: int,
        platform: str | None,
        days_back: int,
        agent_namespace: str | None,
    ) -> list[Episode]:
        cutoff = (_now_utc() - timedelta(days=days_back)).isoformat()

        def _query() -> Any:
            candidate_limit = max(limit * 40, 250)
            query = self._schema_client().table("episodes").select("*")
            if platform is not None:
                query = query.eq("platform", platform)
            query = query.gte("message_timestamp", cutoff).order("message_timestamp", desc=True).limit(candidate_limit)
            return query.execute()

        response = await self._run(_query)
        episodes = [
            Episode.model_validate(_normalize_record(item))
            for item in response.data or []
            if _agent_namespace_matches(item.get("agent_namespace") if isinstance(item, dict) else None, agent_namespace)
        ]
        ranked = [
            (episode, _fallback_search_score(query, episode.content))
            for episode in episodes
        ]
        ranked = [item for item in ranked if item[1] > 0.0]
        ranked.sort(
            key=lambda item: (
                -item[1],
                -(item[0].message_timestamp.timestamp() if item[0].message_timestamp else 0.0),
            )
        )
        return [episode for episode, _ in ranked[:limit]]

    async def _lexical_episode_search(
        self,
        *,
        query: str,
        limit: int,
        platform: str | None,
        days_back: int,
        agent_namespace: str | None,
    ) -> list[Episode]:
        cutoff = (_now_utc() - timedelta(days=days_back)).isoformat()
        candidate_limit = max(limit * 6, 30)
        episodes_by_id: dict[str, Episode] = {}

        for pattern in _lexical_search_patterns(query):
            def _query() -> Any:
                table = self._schema_client().table("episodes").select("*")
                if platform is not None:
                    table = table.eq("platform", platform)
                table = table.gte("message_timestamp", cutoff)
                table = table.ilike("content", f"%{pattern}%")
                table = table.order("message_timestamp", desc=True).limit(candidate_limit)
                return table.execute()

            response = await self._run(_query)
            for item in response.data or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                if not _agent_namespace_matches(item.get("agent_namespace"), agent_namespace):
                    continue
                episode = Episode.model_validate(_normalize_record(item))
                episodes_by_id[str(episode.id)] = episode

        return list(episodes_by_id.values())

    async def search_facts(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        agent_namespace: str | None = None,
    ) -> list[Fact]:
        response = await self._run(
            lambda: self._schema_rpc(
                "search_facts",
                {
                    "category_filter": category,
                    "tag_filter": tags,
                    "limit_count": limit,
                },
            ).execute()
        )
        facts = [Fact.model_validate(item) for item in response.data or []]
        return _filter_records_by_agent_namespace(facts, agent_namespace)[:limit]

    async def insert_fact_history(self, history: FactHistory) -> FactHistory:
        payload = _serialize_value(history.model_dump(exclude_none=True))
        response, _ = await self._insert_payload("fact_history", payload, operation="insert_fact_history")
        return FactHistory.model_validate(self._require_record(response, operation="insert_fact_history"))

    async def upsert_active_state(self, state: ActiveState) -> ActiveState:
        payload = _serialize_value(state.model_dump(exclude_none=True))
        state_key = str(payload.get("state_key") or "").strip()
        if not state_key:
            raise ValueError("ActiveState.state_key is required.")
        agent_namespace = payload.get("agent_namespace")

        def _find_existing() -> Any:
            query = self._schema_client().table("active_state").select("*").eq("state_key", state_key)
            if agent_namespace is None:
                query = query.is_("agent_namespace", "null")
            else:
                query = query.eq("agent_namespace", agent_namespace)
            return query.limit(1).execute()

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "active_state"):
                logger.info("Memory active_state table is not available yet; skipping active_state upsert.")
                return state
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "active_state",
                str(existing["id"]),
                payload,
                operation="upsert_active_state",
            )
            if response is None:
                refreshed = await self._fetch_row("active_state", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_active_state returned no rows.")
                return ActiveState.model_validate(_normalize_record(refreshed))
            return ActiveState.model_validate(_normalize_record(self._require_record(response, operation="upsert_active_state")))

        response, _ = await self._insert_payload("active_state", payload, operation="insert_active_state")
        return ActiveState.model_validate(_normalize_record(self._require_record(response, operation="insert_active_state")))

    async def list_active_state(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[ActiveState]:
        requested_statuses = [str(status).strip() for status in (statuses or []) if str(status).strip()]

        def _query() -> Any:
            query = self._schema_client().table("active_state").select("*")
            if requested_statuses:
                query = query.in_("status", requested_statuses)
            query = query.order("priority_score", desc=True).order("last_observed_at", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "active_state"):
                logger.info("Memory active_state table is not available yet; returning an empty active_state list.")
                return []
            raise

        states = [ActiveState.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        states = _filter_records_by_agent_namespace(states, agent_namespace)
        return states[:limit]

    async def upsert_directive(self, directive: Directive) -> Directive:
        payload = _serialize_value(directive.model_dump(exclude_none=True))
        directive_key = str(payload.get("directive_key") or "").strip()
        if not directive_key:
            raise ValueError("Directive.directive_key is required.")
        agent_namespace = payload.get("agent_namespace")

        def _find_existing() -> Any:
            query = self._schema_client().table("directives").select("*").eq("directive_key", directive_key)
            if agent_namespace is None:
                query = query.is_("agent_namespace", "null")
            else:
                query = query.eq("agent_namespace", agent_namespace)
            return query.limit(1).execute()

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "directives"):
                logger.info("Memory directives table is not available yet; skipping directive upsert.")
                return directive
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "directives",
                str(existing["id"]),
                payload,
                operation="upsert_directive",
            )
            if response is None:
                refreshed = await self._fetch_row("directives", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_directive returned no rows.")
                return Directive.model_validate(_normalize_record(refreshed))
            return Directive.model_validate(_normalize_record(self._require_record(response, operation="upsert_directive")))

        response, _ = await self._insert_payload("directives", payload, operation="insert_directive")
        return Directive.model_validate(_normalize_record(self._require_record(response, operation="insert_directive")))

    async def list_directives(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Directive]:
        requested_statuses = [str(status).strip() for status in (statuses or []) if str(status).strip()]

        def _query() -> Any:
            query = self._schema_client().table("directives").select("*")
            if requested_statuses:
                query = query.in_("status", requested_statuses)
            query = query.order("priority_score", desc=True).order("last_observed_at", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "directives"):
                logger.info("Memory directives table is not available yet; returning an empty directives list.")
                return []
            raise

        directives = [Directive.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        directives = _filter_records_by_agent_namespace(directives, agent_namespace)
        return directives[:limit]

    async def upsert_timeline_event(self, event: TimelineEvent) -> TimelineEvent:
        payload = _serialize_value(event.model_dump(exclude_none=True))
        event_key = str(payload.get("event_key") or "").strip()
        if not event_key:
            raise ValueError("TimelineEvent.event_key is required.")
        agent_namespace = payload.get("agent_namespace")

        def _find_existing() -> Any:
            query = self._schema_client().table("timeline_events").select("*").eq("event_key", event_key)
            if agent_namespace is None:
                query = query.is_("agent_namespace", "null")
            else:
                query = query.eq("agent_namespace", agent_namespace)
            return query.limit(1).execute()

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "timeline_events"):
                logger.info("Memory timeline_events table is not available yet; skipping timeline event upsert.")
                return event
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "timeline_events",
                str(existing["id"]),
                payload,
                operation="upsert_timeline_event",
            )
            if response is None:
                refreshed = await self._fetch_row("timeline_events", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_timeline_event returned no rows.")
                return TimelineEvent.model_validate(_normalize_record(refreshed))
            return TimelineEvent.model_validate(_normalize_record(self._require_record(response, operation="upsert_timeline_event")))

        response, _ = await self._insert_payload("timeline_events", payload, operation="insert_timeline_event")
        return TimelineEvent.model_validate(_normalize_record(self._require_record(response, operation="insert_timeline_event")))

    async def list_timeline_events(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
    ) -> list[TimelineEvent]:
        def _query() -> Any:
            query = self._schema_client().table("timeline_events").select("*")
            query = query.order("event_time", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "timeline_events"):
                logger.info("Memory timeline_events table is not available yet; returning an empty timeline event list.")
                return []
            raise

        events = [TimelineEvent.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        events = _filter_records_by_agent_namespace(events, agent_namespace)
        return events[:limit]

    async def upsert_decision_outcome(self, outcome: DecisionOutcome) -> DecisionOutcome:
        payload = _serialize_value(outcome.model_dump(exclude_none=True))
        payload["lesson"] = _serialize_value(outcome.lesson)
        outcome_key = str(payload.get("outcome_key") or "").strip()
        if not outcome_key:
            raise ValueError("DecisionOutcome.outcome_key is required.")
        agent_namespace = payload.get("agent_namespace")

        def _find_existing() -> Any:
            query = self._schema_client().table("decision_outcomes").select("*").eq("outcome_key", outcome_key)
            if agent_namespace is None:
                query = query.is_("agent_namespace", "null")
            else:
                query = query.eq("agent_namespace", agent_namespace)
            return query.limit(1).execute()

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "decision_outcomes"):
                logger.info("Memory decision_outcomes table is not available yet; skipping decision outcome upsert.")
                return outcome
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "decision_outcomes",
                str(existing["id"]),
                payload,
                operation="upsert_decision_outcome",
            )
            if response is None:
                refreshed = await self._fetch_row("decision_outcomes", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_decision_outcome returned no rows.")
                return DecisionOutcome.model_validate(_normalize_record(refreshed))
            return DecisionOutcome.model_validate(_normalize_record(self._require_record(response, operation="upsert_decision_outcome")))

        response, _ = await self._insert_payload("decision_outcomes", payload, operation="insert_decision_outcome")
        return DecisionOutcome.model_validate(_normalize_record(self._require_record(response, operation="insert_decision_outcome")))

    async def list_decision_outcomes(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[DecisionOutcome]:
        requested_statuses = [str(status).strip() for status in (statuses or []) if str(status).strip()]

        def _query() -> Any:
            query = self._schema_client().table("decision_outcomes").select("*")
            if requested_statuses:
                query = query.in_("status", requested_statuses)
            query = query.order("importance_score", desc=True).order("event_time", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "decision_outcomes"):
                logger.info("Memory decision_outcomes table is not available yet; returning an empty decision outcome list.")
                return []
            raise

        outcomes = [DecisionOutcome.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        outcomes = _filter_records_by_agent_namespace(outcomes, agent_namespace)
        return outcomes[:limit]

    async def delete_decision_outcome(
        self,
        outcome_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        normalized_key = str(outcome_key or "").strip()
        if not normalized_key:
            return False

        def _delete() -> Any:
            query = self._schema_client().table("decision_outcomes").delete().eq("outcome_key", normalized_key)
            if agent_namespace is None:
                query = query.is_("agent_namespace", "null")
            else:
                query = query.eq("agent_namespace", agent_namespace)
            return query.execute()

        try:
            await self._run(_delete)
        except Exception as exc:
            if _is_missing_relation_error(exc, "decision_outcomes"):
                logger.info("Memory decision_outcomes table is not available yet; skipping decision outcome delete.")
                return False
            raise
        return True

    async def upsert_pattern(self, pattern: Pattern) -> Pattern:
        payload = _serialize_value(pattern.model_dump(exclude_none=True))
        pattern_key = str(payload.get("pattern_key") or "").strip()
        if not pattern_key:
            raise ValueError("Pattern.pattern_key is required.")
        agent_namespace = payload.get("agent_namespace")

        def _find_existing() -> Any:
            query = self._schema_client().table("patterns").select("*").eq("pattern_key", pattern_key)
            if agent_namespace is None:
                query = query.is_("agent_namespace", "null")
            else:
                query = query.eq("agent_namespace", agent_namespace)
            return query.limit(1).execute()

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "patterns"):
                logger.info("Memory patterns table is not available yet; skipping pattern upsert.")
                return pattern
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "patterns",
                str(existing["id"]),
                payload,
                operation="upsert_pattern",
            )
            if response is None:
                refreshed = await self._fetch_row("patterns", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_pattern returned no rows.")
                return Pattern.model_validate(_normalize_record(refreshed))
            return Pattern.model_validate(_normalize_record(self._require_record(response, operation="upsert_pattern")))

        response, _ = await self._insert_payload("patterns", payload, operation="insert_pattern")
        return Pattern.model_validate(_normalize_record(self._require_record(response, operation="insert_pattern")))

    async def list_patterns(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        pattern_types: list[str] | None = None,
    ) -> list[Pattern]:
        requested_types = [str(item).strip() for item in (pattern_types or []) if str(item).strip()]

        def _query() -> Any:
            query = self._schema_client().table("patterns").select("*")
            if requested_types:
                query = query.in_("pattern_type", requested_types)
            query = query.order("impact_score", desc=True).order("last_observed_at", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "patterns"):
                logger.info("Memory patterns table is not available yet; returning an empty patterns list.")
                return []
            raise

        patterns = [Pattern.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        patterns = _filter_records_by_agent_namespace(patterns, agent_namespace)
        return patterns[:limit]

    async def delete_pattern(
        self,
        pattern_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        normalized_key = str(pattern_key or "").strip()
        if not normalized_key:
            return False

        def _delete() -> Any:
            query = self._schema_client().table("patterns").delete().eq("pattern_key", normalized_key)
            if agent_namespace is None:
                query = query.is_("agent_namespace", "null")
            else:
                query = query.eq("agent_namespace", agent_namespace)
            return query.execute()

        try:
            await self._run(_delete)
        except Exception as exc:
            if _is_missing_relation_error(exc, "patterns"):
                logger.info("Memory patterns table is not available yet; skipping pattern delete.")
                return False
            raise
        return True

    async def upsert_commitment(self, commitment: Commitment) -> Commitment:
        payload = _serialize_value(commitment.model_dump(exclude_none=True))
        commitment_key = str(payload.get("commitment_key") or "").strip()
        if not commitment_key:
            raise ValueError("Commitment.commitment_key is required.")
        agent_namespace = payload.get("agent_namespace")

        def _find_existing() -> Any:
            query = self._schema_client().table("commitments").select("*").eq("commitment_key", commitment_key)
            if agent_namespace is None:
                query = query.is_("agent_namespace", "null")
            else:
                query = query.eq("agent_namespace", agent_namespace)
            return query.limit(1).execute()

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "commitments"):
                logger.info("Memory commitments table is not available yet; skipping commitment upsert.")
                return commitment
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "commitments",
                str(existing["id"]),
                payload,
                operation="upsert_commitment",
            )
            if response is None:
                refreshed = await self._fetch_row("commitments", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_commitment returned no rows.")
                return Commitment.model_validate(_normalize_record(refreshed))
            return Commitment.model_validate(_normalize_record(self._require_record(response, operation="upsert_commitment")))

        response, _ = await self._insert_payload("commitments", payload, operation="insert_commitment")
        return Commitment.model_validate(_normalize_record(self._require_record(response, operation="insert_commitment")))

    async def list_commitments(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Commitment]:
        requested_statuses = [str(status).strip() for status in (statuses or []) if str(status).strip()]

        def _query() -> Any:
            query = self._schema_client().table("commitments").select("*")
            if requested_statuses:
                query = query.in_("status", requested_statuses)
            query = query.order("priority_score", desc=True).order("last_observed_at", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "commitments"):
                logger.info("Memory commitments table is not available yet; returning an empty commitments list.")
                return []
            raise

        commitments = [Commitment.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        commitments = _filter_records_by_agent_namespace(commitments, agent_namespace)
        return commitments[:limit]

    async def upsert_correction(self, correction: Correction) -> Correction:
        payload = _serialize_value(correction.model_dump(exclude_none=True))
        correction_key = str(payload.get("correction_key") or "").strip()
        if not correction_key:
            raise ValueError("Correction.correction_key is required.")
        agent_namespace = payload.get("agent_namespace")

        def _find_existing() -> Any:
            query = self._schema_client().table("corrections").select("*").eq("correction_key", correction_key)
            if agent_namespace is None:
                query = query.is_("agent_namespace", "null")
            else:
                query = query.eq("agent_namespace", agent_namespace)
            return query.limit(1).execute()

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "corrections"):
                logger.info("Memory corrections table is not available yet; skipping correction upsert.")
                return correction
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "corrections",
                str(existing["id"]),
                payload,
                operation="upsert_correction",
            )
            if response is None:
                refreshed = await self._fetch_row("corrections", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_correction returned no rows.")
                return Correction.model_validate(_normalize_record(refreshed))
            return Correction.model_validate(_normalize_record(self._require_record(response, operation="upsert_correction")))

        response, _ = await self._insert_payload("corrections", payload, operation="insert_correction")
        return Correction.model_validate(_normalize_record(self._require_record(response, operation="insert_correction")))

    async def list_corrections(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        active_only: bool = True,
    ) -> list[Correction]:
        def _query() -> Any:
            query = self._schema_client().table("corrections").select("*")
            if active_only:
                query = query.eq("active", True)
            query = query.order("last_observed_at", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "corrections"):
                logger.info("Memory corrections table is not available yet; returning an empty corrections list.")
                return []
            raise

        corrections = [Correction.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        corrections = _filter_records_by_agent_namespace(corrections, agent_namespace)
        return corrections[:limit]

    async def upsert_session_handoff(self, handoff: SessionHandoff) -> SessionHandoff:
        payload = _serialize_value(handoff.model_dump(exclude_none=True))
        handoff_key = str(payload.get("handoff_key") or "").strip()
        if not handoff_key:
            raise ValueError("SessionHandoff.handoff_key is required.")
        agent_namespace = payload.get("agent_namespace")

        def _find_existing() -> Any:
            query = self._schema_client().table("session_handoffs").select("*").eq("handoff_key", handoff_key)
            if agent_namespace is None:
                query = query.is_("agent_namespace", "null")
            else:
                query = query.eq("agent_namespace", agent_namespace)
            return query.limit(1).execute()

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "session_handoffs"):
                logger.info("Memory session_handoffs table is not available yet; skipping handoff upsert.")
                return handoff
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "session_handoffs",
                str(existing["id"]),
                payload,
                operation="upsert_session_handoff",
            )
            if response is None:
                refreshed = await self._fetch_row("session_handoffs", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_session_handoff returned no rows.")
                return SessionHandoff.model_validate(_normalize_record(refreshed))
            return SessionHandoff.model_validate(
                _normalize_record(self._require_record(response, operation="upsert_session_handoff"))
            )

        response, _ = await self._insert_payload("session_handoffs", payload, operation="insert_session_handoff")
        return SessionHandoff.model_validate(
            _normalize_record(self._require_record(response, operation="insert_session_handoff"))
        )

    async def list_session_handoffs(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        exclude_session_id: str | None = None,
    ) -> list[SessionHandoff]:
        normalized_exclude = str(exclude_session_id or "").strip()

        def _query() -> Any:
            query = self._schema_client().table("session_handoffs").select("*")
            if normalized_exclude:
                query = query.neq("session_id", normalized_exclude)
            query = query.order("last_observed_at", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "session_handoffs"):
                logger.info("Memory session_handoffs table is not available yet; returning an empty handoff list.")
                return []
            raise

        handoffs = [SessionHandoff.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        handoffs = _filter_records_by_agent_namespace(handoffs, agent_namespace)
        if normalized_exclude:
            handoffs = [handoff for handoff in handoffs if str(handoff.session_id) != normalized_exclude]
        return handoffs[:limit]

    async def health_check(self) -> bool:
        try:
            await self._run(lambda: self._schema_client().table("sessions").select("id").limit(1).execute())
        except Exception:
            return False
        return True


class LocalTransport(SupabaseTransport):
    """Local direct connection to Supabase, used as the source-of-truth transport."""


class RemoteTransport:
    """HTTP client to VPS daemon (future)."""

    async def insert_session(self, session: Session) -> Session:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def get_session(self, session_id: str) -> Session | None:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def get_session_by_legacy_id(self, legacy_session_id: str) -> Session | None:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_sessions(
        self,
        limit: int = 20,
        platform: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Session]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_episodes_for_session(self, session_id: str, limit: int | None = None) -> list[Episode]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def update_session(self, session_id: str, updates: dict[str, Any]) -> Session:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def delete_session(self, session_id: str) -> bool:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def insert_episode(self, episode: Episode) -> Episode:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def insert_fact(self, fact: Fact) -> Fact:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def get_fact(self, fact_id: str) -> Fact | None:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def update_fact(self, fact_id: str, updates: dict[str, Any]) -> Fact:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def deactivate_fact(self, fact_id: str, replaced_by: str | None = None) -> None:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def touch_fact(self, fact_id: str) -> None:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def search_episodes(
        self,
        query: str,
        limit: int = 20,
        platform: str | None = None,
        days_back: int = 30,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_recent_episodes(
        self,
        limit: int = 5,
        platform: str | None = None,
        exclude_session_id: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def search_facts(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        agent_namespace: str | None = None,
    ) -> list[Fact]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def insert_fact_history(self, history: FactHistory) -> FactHistory:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_active_state(self, state: ActiveState) -> ActiveState:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_active_state(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[ActiveState]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_directive(self, directive: Directive) -> Directive:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_directives(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Directive]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_timeline_event(self, event: TimelineEvent) -> TimelineEvent:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_timeline_events(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
    ) -> list[TimelineEvent]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_decision_outcome(self, outcome: DecisionOutcome) -> DecisionOutcome:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_decision_outcomes(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[DecisionOutcome]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def delete_decision_outcome(
        self,
        outcome_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_pattern(self, pattern: Pattern) -> Pattern:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_patterns(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        pattern_types: list[str] | None = None,
    ) -> list[Pattern]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def delete_pattern(
        self,
        pattern_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_commitment(self, commitment: Commitment) -> Commitment:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_commitments(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Commitment]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_correction(self, correction: Correction) -> Correction:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_corrections(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        active_only: bool = True,
    ) -> list[Correction]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_session_handoff(self, handoff: SessionHandoff) -> SessionHandoff:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_session_handoffs(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        exclude_session_id: str | None = None,
    ) -> list[SessionHandoff]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def health_check(self) -> bool:
        raise NotImplementedError("RemoteTransport is not implemented yet.")
