from __future__ import annotations

import asyncio
import hashlib
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
from memory.instance_identity import (
    get_agent_namespace,
    normalize_agent_namespace as normalize_instance_agent_namespace,
)
from memory.models import (
    ActiveState,
    BackgroundJob,
    CaseEvidenceLink,
    Commitment,
    Correction,
    DecisionOutcome,
    Directive,
    Episode,
    Fact,
    FactHistory,
    HeartbeatDispatch,
    HeartbeatOpportunity,
    MemoryCase,
    Pattern,
    PresenceState,
    Reflection,
    SessionHandoff,
    Session,
    TemporalGraphEdge,
    TemporalGraphNode,
    TemporalGraphPath,
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
    "python -m memory.curator_runtime process-memory",
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


def _temporal_graph_tokens(*parts: str) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        tokens.update(_query_term_variants(part))
    return tokens


def _temporal_graph_overlap(query_tokens: set[str], candidate_tokens: set[str]) -> int:
    if not query_tokens or not candidate_tokens:
        return 0
    return len(query_tokens.intersection(candidate_tokens))


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
    return source_kind in {"operational_prompt", "operational_status", "curator_status"}


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
        "curator",
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


def _explicit_agent_namespace(agent_namespace: str | None) -> str | None:
    cleaned = str(agent_namespace or "").strip()
    if not cleaned:
        return None
    return normalize_instance_agent_namespace(cleaned)


def _resolved_agent_namespace(agent_namespace: str | None) -> str:
    explicit = _explicit_agent_namespace(agent_namespace)
    if explicit is not None:
        return explicit
    return get_agent_namespace()


def _normalize_agent_namespace(agent_namespace: str | None) -> str | None:
    return _explicit_agent_namespace(agent_namespace)


def _agent_namespace_matches(record_namespace: Any, requested_namespace: str | None) -> bool:
    requested = _resolved_agent_namespace(requested_namespace)
    current = _explicit_agent_namespace(record_namespace)
    return current == requested


def _filter_records_by_agent_namespace(records: list[Any], requested_namespace: str | None) -> list[Any]:
    requested = _resolved_agent_namespace(requested_namespace)
    return [
        record
        for record in records
        if _agent_namespace_matches(getattr(record, "agent_namespace", None), requested)
    ]


def _fact_search_sort_key(fact: Fact) -> tuple[float, float]:
    event_time = fact.event_time.timestamp() if fact.event_time else 0.0
    return (float(fact.access_count), event_time)


def _ensure_payload_agent_namespace(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["agent_namespace"] = _resolved_agent_namespace(normalized.get("agent_namespace"))
    return normalized


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

    async def upsert_memory_case(self, case: MemoryCase) -> MemoryCase: ...

    async def list_memory_cases(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        outcome_statuses: list[str] | None = None,
    ) -> list[MemoryCase]: ...

    async def delete_memory_case(
        self,
        case_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool: ...

    async def upsert_case_evidence_link(self, link: CaseEvidenceLink) -> CaseEvidenceLink: ...

    async def list_case_evidence_links(
        self,
        case_id: str,
        limit: int = 50,
        agent_namespace: str | None = None,
    ) -> list[CaseEvidenceLink]: ...

    async def upsert_temporal_graph_node(self, node: TemporalGraphNode) -> TemporalGraphNode: ...

    async def list_temporal_graph_nodes(
        self,
        limit: int = 50,
        agent_namespace: str | None = None,
        node_types: list[str] | None = None,
    ) -> list[TemporalGraphNode]: ...

    async def delete_temporal_graph_node(
        self,
        node_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool: ...

    async def upsert_temporal_graph_edge(self, edge: TemporalGraphEdge) -> TemporalGraphEdge: ...

    async def list_temporal_graph_edges(
        self,
        limit: int = 200,
        agent_namespace: str | None = None,
        relation_types: list[str] | None = None,
    ) -> list[TemporalGraphEdge]: ...

    async def delete_temporal_graph_edge(
        self,
        edge_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool: ...

    async def search_temporal_graph_paths(
        self,
        query: str,
        limit: int = 6,
        max_hops: int = 3,
        agent_namespace: str | None = None,
    ) -> list[TemporalGraphPath]: ...

    async def upsert_reflection(self, reflection: Reflection) -> Reflection: ...

    async def list_reflections(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Reflection]: ...

    async def delete_reflection(
        self,
        reflection_key: str,
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

    async def upsert_presence_state(self, state: PresenceState) -> PresenceState: ...

    async def get_presence_state(
        self,
        agent_namespace: str | None = None,
    ) -> PresenceState | None: ...

    async def upsert_background_job(
        self,
        job: BackgroundJob,
    ) -> BackgroundJob: ...

    async def list_background_jobs(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        session_id: str | None = None,
        job_key: str | None = None,
    ) -> list[BackgroundJob]: ...

    async def transition_background_job(
        self,
        job_key: str,
        *,
        status: str,
        agent_namespace: str | None = None,
        progress_note: str | None = None,
        completion_summary: str | None = None,
        result_refs: list[str] | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> BackgroundJob | None: ...

    async def upsert_heartbeat_opportunity(
        self,
        opportunity: HeartbeatOpportunity,
    ) -> HeartbeatOpportunity: ...

    async def insert_heartbeat_dispatch(
        self,
        dispatch: HeartbeatDispatch,
    ) -> HeartbeatDispatch: ...

    async def list_heartbeat_dispatches(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        opportunity_key: str | None = None,
        session_id: str | None = None,
        since: datetime | None = None,
    ) -> list[HeartbeatDispatch]: ...

    async def list_heartbeat_opportunities(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        kinds: list[str] | None = None,
        session_id: str | None = None,
    ) -> list[HeartbeatOpportunity]: ...

    async def cancel_heartbeat_opportunity(
        self,
        opportunity_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool: ...

    async def transition_heartbeat_opportunity(
        self,
        opportunity_key: str,
        *,
        status: str,
        agent_namespace: str | None = None,
    ) -> bool: ...

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

    async def _insert_session_payload(self, payload: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        working = _ensure_payload_agent_namespace(payload)
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
        working = _ensure_payload_agent_namespace(payload)
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
            logger.info(
                "Memory compatibility telemetry: dropped unsupported session columns during insert: %s",
                sorted(dropped.keys()),
            )
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
        logger.info("Memory compatibility telemetry: legacy_session_id lookup attempted.")
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
        records = [
            Session.model_validate(_normalize_record(item))
            for item in _response_rows(response)
        ]
        matches = _filter_records_by_agent_namespace(records, None)
        if not matches:
            return None
        return matches[0]

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
            logger.info(
                "Memory compatibility telemetry: dropped unsupported session columns during update: %s",
                sorted(dropped.keys()),
            )
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
        payload = _ensure_payload_agent_namespace(_serialize_value(fact.model_dump(exclude_none=True)))
        try:
            response, _ = await self._insert_payload("facts", payload, operation="insert_fact")
            return Fact.model_validate(self._require_record(response, operation="insert_fact"))
        except Exception as exc:
            if not _is_duplicate_error(exc):
                raise
            if not fact.content_fingerprint:
                raise
            agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))
            response = await self._run(
                lambda: self._schema_client()
                .table("facts")
                .select("*")
                .eq("content_fingerprint", fact.content_fingerprint)
                .eq("agent_namespace", agent_namespace)
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
        fact = Fact.model_validate(record)
        if not _agent_namespace_matches(fact.agent_namespace, None):
            return None
        return fact

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
        normalized_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        candidate_limit = limit if not normalized_tags else max(limit * 25, 500)

        async def _run_fact_search(tag_filter: str | None) -> list[Fact]:
            response = await self._run(
                lambda: self._schema_rpc(
                    "search_facts",
                    {
                        "category_filter": category,
                        "tag_filter": tag_filter,
                        "limit_count": candidate_limit,
                    },
                ).execute()
            )
            return [Fact.model_validate(item) for item in response.data or []]

        if not normalized_tags:
            facts = await _run_fact_search(None)
        elif len(normalized_tags) == 1:
            facts = await _run_fact_search(normalized_tags[0])
        else:
            per_tag_results = await asyncio.gather(*(_run_fact_search(tag) for tag in normalized_tags))
            merged: dict[str, Fact] = {}
            occurrences: dict[str, int] = {}
            for result in per_tag_results:
                seen_in_result: set[str] = set()
                for fact in result:
                    if fact.id is None:
                        continue
                    fact_id = str(fact.id)
                    merged[fact_id] = fact
                    if fact_id in seen_in_result:
                        continue
                    occurrences[fact_id] = occurrences.get(fact_id, 0) + 1
                    seen_in_result.add(fact_id)
            required_occurrences = len(normalized_tags)
            facts = [
                fact
                for fact_id, fact in merged.items()
                if occurrences.get(fact_id, 0) >= required_occurrences
            ]
            facts.sort(key=_fact_search_sort_key, reverse=True)

        if normalized_tags:
            facts = [fact for fact in facts if all(tag in fact.tags for tag in normalized_tags)]
        return _filter_records_by_agent_namespace(facts, agent_namespace)[:limit]

    async def insert_fact_history(self, history: FactHistory) -> FactHistory:
        payload = _ensure_payload_agent_namespace(_serialize_value(history.model_dump(exclude_none=True)))
        response, _ = await self._insert_payload("fact_history", payload, operation="insert_fact_history")
        return FactHistory.model_validate(self._require_record(response, operation="insert_fact_history"))

    async def upsert_active_state(self, state: ActiveState) -> ActiveState:
        payload = _ensure_payload_agent_namespace(_serialize_value(state.model_dump(exclude_none=True)))
        state_key = str(payload.get("state_key") or "").strip()
        if not state_key:
            raise ValueError("ActiveState.state_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("active_state")
                .select("*")
                .eq("state_key", state_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

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
        payload = _ensure_payload_agent_namespace(_serialize_value(directive.model_dump(exclude_none=True)))
        directive_key = str(payload.get("directive_key") or "").strip()
        if not directive_key:
            raise ValueError("Directive.directive_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("directives")
                .select("*")
                .eq("directive_key", directive_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

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
        payload = _ensure_payload_agent_namespace(_serialize_value(event.model_dump(exclude_none=True)))
        event_key = str(payload.get("event_key") or "").strip()
        if not event_key:
            raise ValueError("TimelineEvent.event_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("timeline_events")
                .select("*")
                .eq("event_key", event_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

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
        payload = _ensure_payload_agent_namespace(_serialize_value(outcome.model_dump(exclude_none=True)))
        payload["lesson"] = _serialize_value(outcome.lesson)
        outcome_key = str(payload.get("outcome_key") or "").strip()
        if not outcome_key:
            raise ValueError("DecisionOutcome.outcome_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("decision_outcomes")
                .select("*")
                .eq("outcome_key", outcome_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

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
            return (
                self._schema_client()
                .table("decision_outcomes")
                .delete()
                .eq("outcome_key", normalized_key)
                .eq("agent_namespace", _resolved_agent_namespace(agent_namespace))
                .execute()
            )

        try:
            await self._run(_delete)
        except Exception as exc:
            if _is_missing_relation_error(exc, "decision_outcomes"):
                logger.info("Memory decision_outcomes table is not available yet; skipping decision outcome delete.")
                return False
            raise
        return True

    async def upsert_pattern(self, pattern: Pattern) -> Pattern:
        payload = _ensure_payload_agent_namespace(_serialize_value(pattern.model_dump(exclude_none=True)))
        pattern_key = str(payload.get("pattern_key") or "").strip()
        if not pattern_key:
            raise ValueError("Pattern.pattern_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("patterns")
                .select("*")
                .eq("pattern_key", pattern_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

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
            return (
                self._schema_client()
                .table("patterns")
                .delete()
                .eq("pattern_key", normalized_key)
                .eq("agent_namespace", _resolved_agent_namespace(agent_namespace))
                .execute()
            )

        try:
            await self._run(_delete)
        except Exception as exc:
            if _is_missing_relation_error(exc, "patterns"):
                logger.info("Memory patterns table is not available yet; skipping pattern delete.")
                return False
            raise
        return True

    async def upsert_memory_case(self, case: MemoryCase) -> MemoryCase:
        payload = _ensure_payload_agent_namespace(_serialize_value(case.model_dump(exclude_none=True)))
        case_key = str(payload.get("case_key") or "").strip()
        if not case_key:
            raise ValueError("MemoryCase.case_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("memory_cases")
                .select("*")
                .eq("case_key", case_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "memory_cases"):
                logger.info("Memory memory_cases table is not available yet; skipping memory case upsert.")
                return case
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "memory_cases",
                str(existing["id"]),
                payload,
                operation="upsert_memory_case",
            )
            if response is None:
                refreshed = await self._fetch_row("memory_cases", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_memory_case returned no rows.")
                return MemoryCase.model_validate(_normalize_record(refreshed))
            return MemoryCase.model_validate(_normalize_record(self._require_record(response, operation="upsert_memory_case")))

        response, _ = await self._insert_payload("memory_cases", payload, operation="insert_memory_case")
        return MemoryCase.model_validate(_normalize_record(self._require_record(response, operation="insert_memory_case")))

    async def list_memory_cases(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        outcome_statuses: list[str] | None = None,
    ) -> list[MemoryCase]:
        requested_statuses = [str(status).strip() for status in (outcome_statuses or []) if str(status).strip()]

        def _query() -> Any:
            query = self._schema_client().table("memory_cases").select("*")
            if requested_statuses:
                query = query.in_("outcome_status", requested_statuses)
            query = query.order("impact_score", desc=True).order("last_observed_at", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "memory_cases"):
                logger.info("Memory memory_cases table is not available yet; returning an empty memory case list.")
                return []
            raise

        cases = [MemoryCase.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        cases = _filter_records_by_agent_namespace(cases, agent_namespace)
        return cases[:limit]

    async def delete_memory_case(
        self,
        case_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        normalized_key = str(case_key or "").strip()
        if not normalized_key:
            return False

        def _delete() -> Any:
            return (
                self._schema_client()
                .table("memory_cases")
                .delete()
                .eq("case_key", normalized_key)
                .eq("agent_namespace", _resolved_agent_namespace(agent_namespace))
                .execute()
            )

        try:
            await self._run(_delete)
        except Exception as exc:
            if _is_missing_relation_error(exc, "memory_cases"):
                logger.info("Memory memory_cases table is not available yet; skipping memory case delete.")
                return False
            raise
        return True

    async def upsert_case_evidence_link(self, link: CaseEvidenceLink) -> CaseEvidenceLink:
        payload = _ensure_payload_agent_namespace(_serialize_value(link.model_dump(exclude_none=True)))
        case_id = str(payload.get("case_id") or "").strip()
        evidence_type = str(payload.get("evidence_type") or "").strip()
        evidence_id = str(payload.get("evidence_id") or "").strip()
        if not case_id or not evidence_type or not evidence_id:
            raise ValueError("CaseEvidenceLink requires case_id, evidence_type, and evidence_id.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("case_evidence_links")
                .select("*")
                .eq("case_id", case_id)
                .eq("evidence_type", evidence_type)
                .eq("evidence_id", evidence_id)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "case_evidence_links"):
                logger.info("Memory case_evidence_links table is not available yet; skipping evidence-link upsert.")
                return link
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "case_evidence_links",
                str(existing["id"]),
                payload,
                operation="upsert_case_evidence_link",
            )
            if response is None:
                refreshed = await self._fetch_row("case_evidence_links", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_case_evidence_link returned no rows.")
                return CaseEvidenceLink.model_validate(_normalize_record(refreshed))
            return CaseEvidenceLink.model_validate(
                _normalize_record(self._require_record(response, operation="upsert_case_evidence_link"))
            )

        response, _ = await self._insert_payload(
            "case_evidence_links",
            payload,
            operation="insert_case_evidence_link",
        )
        return CaseEvidenceLink.model_validate(
            _normalize_record(self._require_record(response, operation="insert_case_evidence_link"))
        )

    async def list_case_evidence_links(
        self,
        case_id: str,
        limit: int = 50,
        agent_namespace: str | None = None,
    ) -> list[CaseEvidenceLink]:
        normalized_case_id = str(case_id or "").strip()
        if not normalized_case_id:
            return []

        def _query() -> Any:
            query = (
                self._schema_client()
                .table("case_evidence_links")
                .select("*")
                .eq("case_id", normalized_case_id)
                .order("relevance_score", desc=True)
                .order("updated_at", desc=True)
                .limit(max(limit * 2, 20))
            )
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "case_evidence_links"):
                logger.info("Memory case_evidence_links table is not available yet; returning an empty link list.")
                return []
            raise

        links = [CaseEvidenceLink.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        links = _filter_records_by_agent_namespace(links, agent_namespace)
        return links[:limit]

    async def upsert_temporal_graph_node(self, node: TemporalGraphNode) -> TemporalGraphNode:
        payload = _ensure_payload_agent_namespace(_serialize_value(node.model_dump(exclude_none=True)))
        node_key = str(payload.get("node_key") or "").strip()
        if not node_key:
            raise ValueError("TemporalGraphNode.node_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("temporal_graph_nodes")
                .select("*")
                .eq("node_key", node_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "temporal_graph_nodes"):
                logger.info("Memory temporal_graph_nodes table is not available yet; skipping graph-node upsert.")
                return node
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "temporal_graph_nodes",
                str(existing["id"]),
                payload,
                operation="upsert_temporal_graph_node",
            )
            if response is None:
                refreshed = await self._fetch_row("temporal_graph_nodes", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_temporal_graph_node returned no rows.")
                return TemporalGraphNode.model_validate(_normalize_record(refreshed))
            return TemporalGraphNode.model_validate(
                _normalize_record(self._require_record(response, operation="upsert_temporal_graph_node"))
            )

        response, _ = await self._insert_payload("temporal_graph_nodes", payload, operation="insert_temporal_graph_node")
        return TemporalGraphNode.model_validate(
            _normalize_record(self._require_record(response, operation="insert_temporal_graph_node"))
        )

    async def list_temporal_graph_nodes(
        self,
        limit: int = 50,
        agent_namespace: str | None = None,
        node_types: list[str] | None = None,
    ) -> list[TemporalGraphNode]:
        requested_types = [str(value).strip() for value in (node_types or []) if str(value).strip()]

        def _query() -> Any:
            query = self._schema_client().table("temporal_graph_nodes").select("*")
            if requested_types:
                query = query.in_("node_type", requested_types)
            query = query.order("importance_score", desc=True).order("last_observed_at", desc=True).limit(max(limit * 4, 50))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "temporal_graph_nodes"):
                logger.info("Memory temporal_graph_nodes table is not available yet; returning an empty graph-node list.")
                return []
            raise

        nodes = [TemporalGraphNode.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        nodes = _filter_records_by_agent_namespace(nodes, agent_namespace)
        return nodes[:limit]

    async def delete_temporal_graph_node(
        self,
        node_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        normalized_key = str(node_key or "").strip()
        if not normalized_key:
            return False

        def _delete() -> Any:
            return (
                self._schema_client()
                .table("temporal_graph_nodes")
                .delete()
                .eq("node_key", normalized_key)
                .eq("agent_namespace", _resolved_agent_namespace(agent_namespace))
                .execute()
            )

        try:
            await self._run(_delete)
        except Exception as exc:
            if _is_missing_relation_error(exc, "temporal_graph_nodes"):
                logger.info("Memory temporal_graph_nodes table is not available yet; skipping graph-node delete.")
                return False
            raise
        return True

    async def upsert_temporal_graph_edge(self, edge: TemporalGraphEdge) -> TemporalGraphEdge:
        payload = _ensure_payload_agent_namespace(_serialize_value(edge.model_dump(exclude_none=True)))
        edge_key = str(payload.get("edge_key") or "").strip()
        if not edge_key:
            raise ValueError("TemporalGraphEdge.edge_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("temporal_graph_edges")
                .select("*")
                .eq("edge_key", edge_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "temporal_graph_edges"):
                logger.info("Memory temporal_graph_edges table is not available yet; skipping graph-edge upsert.")
                return edge
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "temporal_graph_edges",
                str(existing["id"]),
                payload,
                operation="upsert_temporal_graph_edge",
            )
            if response is None:
                refreshed = await self._fetch_row("temporal_graph_edges", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_temporal_graph_edge returned no rows.")
                return TemporalGraphEdge.model_validate(_normalize_record(refreshed))
            return TemporalGraphEdge.model_validate(
                _normalize_record(self._require_record(response, operation="upsert_temporal_graph_edge"))
            )

        response, _ = await self._insert_payload("temporal_graph_edges", payload, operation="insert_temporal_graph_edge")
        return TemporalGraphEdge.model_validate(
            _normalize_record(self._require_record(response, operation="insert_temporal_graph_edge"))
        )

    async def list_temporal_graph_edges(
        self,
        limit: int = 200,
        agent_namespace: str | None = None,
        relation_types: list[str] | None = None,
    ) -> list[TemporalGraphEdge]:
        requested_types = [str(value).strip() for value in (relation_types or []) if str(value).strip()]

        def _query() -> Any:
            query = self._schema_client().table("temporal_graph_edges").select("*")
            if requested_types:
                query = query.in_("relation", requested_types)
            query = query.order("weight", desc=True).order("last_observed_at", desc=True).limit(max(limit * 4, 100))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "temporal_graph_edges"):
                logger.info("Memory temporal_graph_edges table is not available yet; returning an empty graph-edge list.")
                return []
            raise

        edges = [TemporalGraphEdge.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        edges = _filter_records_by_agent_namespace(edges, agent_namespace)
        return edges[:limit]

    async def delete_temporal_graph_edge(
        self,
        edge_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        normalized_key = str(edge_key or "").strip()
        if not normalized_key:
            return False

        def _delete() -> Any:
            return (
                self._schema_client()
                .table("temporal_graph_edges")
                .delete()
                .eq("edge_key", normalized_key)
                .eq("agent_namespace", _resolved_agent_namespace(agent_namespace))
                .execute()
            )

        try:
            await self._run(_delete)
        except Exception as exc:
            if _is_missing_relation_error(exc, "temporal_graph_edges"):
                logger.info("Memory temporal_graph_edges table is not available yet; skipping graph-edge delete.")
                return False
            raise
        return True

    async def search_temporal_graph_paths(
        self,
        query: str,
        limit: int = 6,
        max_hops: int = 3,
        agent_namespace: str | None = None,
    ) -> list[TemporalGraphPath]:
        query_tokens = _query_term_variants(query)
        if not query_tokens:
            return []

        nodes = await self.list_temporal_graph_nodes(limit=max(limit * 20, 120), agent_namespace=agent_namespace)
        edges = await self.list_temporal_graph_edges(limit=max(limit * 50, 400), agent_namespace=agent_namespace)
        if not nodes or not edges:
            return []

        node_by_id = {str(node.id): node for node in nodes if node.id is not None}
        if not node_by_id:
            return []

        node_tokens = {
            node_id: _temporal_graph_tokens(node.title, node.summary or "", " ".join(node.tags))
            for node_id, node in node_by_id.items()
        }

        adjacency: dict[str, list[TemporalGraphEdge]] = {}
        for edge in edges:
            from_id = str(edge.from_node_id)
            to_id = str(edge.to_node_id)
            if from_id not in node_by_id or to_id not in node_by_id:
                continue
            adjacency.setdefault(from_id, []).append(edge)

        seed_ids = sorted(
            [
                node_id
                for node_id, tokens in node_tokens.items()
                if _temporal_graph_overlap(query_tokens, tokens) > 0
            ],
            key=lambda node_id: (
                _temporal_graph_overlap(query_tokens, node_tokens[node_id]),
                float(node_by_id[node_id].importance_score),
                float(node_by_id[node_id].confidence),
            ),
            reverse=True,
        )[: max(limit * 3, 8)]
        if not seed_ids:
            return []

        capped_hops = max(1, min(int(max_hops), 4))
        best_paths: dict[str, tuple[float, TemporalGraphPath]] = {}

        for seed_id in seed_ids:
            queue: list[tuple[str, list[str], list[TemporalGraphEdge], int]] = [(seed_id, [seed_id], [], 0)]
            while queue:
                current_id, node_chain, edge_chain, depth = queue.pop(0)
                if depth >= capped_hops:
                    continue
                for edge in adjacency.get(current_id, []):
                    next_id = str(edge.to_node_id)
                    if next_id in node_chain:
                        continue

                    next_node_chain = [*node_chain, next_id]
                    next_edge_chain = [*edge_chain, edge]
                    end_node = node_by_id[next_id]
                    end_overlap = _temporal_graph_overlap(query_tokens, node_tokens.get(next_id, set()))

                    if end_overlap > 0 and len(next_edge_chain) >= 1:
                        path_nodes = [node_by_id[node_id] for node_id in next_node_chain]
                        hop_count = len(next_edge_chain)
                        edge_confidence = sum(float(item.confidence) for item in next_edge_chain) / float(hop_count)
                        node_confidence = sum(float(item.confidence) for item in path_nodes) / float(len(path_nodes))
                        confidence = min(1.0, (edge_confidence * 0.6) + (node_confidence * 0.4))
                        evidence_score = sum(
                            max(0.0, float(item.weight)) + (float(item.evidence_count) * 0.1)
                            for item in next_edge_chain
                        )
                        latest_observed = max(
                            [item.last_observed_at for item in next_edge_chain] + [item.last_observed_at for item in path_nodes]
                        )

                        parts: list[str] = [path_nodes[0].title]
                        for hop_idx, hop_edge in enumerate(next_edge_chain):
                            target_node = path_nodes[hop_idx + 1]
                            parts.append(f"-[{hop_edge.relation}]-> {target_node.title}")
                        path_text = " ".join(parts)

                        node_key_chain = [node.node_key for node in path_nodes]
                        relation_chain = [edge_item.relation for edge_item in next_edge_chain]
                        signature = "|".join([*node_key_chain, "#", *relation_chain])
                        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:18]
                        score = (
                            (end_overlap * 2.0)
                            + confidence
                            + min(2.0, evidence_score * 0.2)
                            + max(0.0, float(end_node.importance_score) * 0.3)
                            - (hop_count * 0.05)
                        )

                        path = TemporalGraphPath(
                            path_key=f"auto:tgraph:path:{digest}",
                            start_node_key=path_nodes[0].node_key,
                            end_node_key=end_node.node_key,
                            hop_count=hop_count,
                            path_text=path_text,
                            confidence=confidence,
                            evidence_score=evidence_score,
                            last_observed_at=latest_observed,
                            supporting_node_keys=node_key_chain,
                            supporting_edge_keys=[edge_item.edge_key for edge_item in next_edge_chain],
                            tags=sorted({tag for node_item in path_nodes for tag in node_item.tags} | {"temporal-graph", "multi-hop"}),
                        )

                        existing = best_paths.get(signature)
                        if existing is None or score > existing[0]:
                            best_paths[signature] = (score, path)

                    if depth + 1 < capped_hops:
                        queue.append((next_id, next_node_chain, next_edge_chain, depth + 1))

        ranked = sorted(
            best_paths.values(),
            key=lambda item: (item[0], item[1].confidence, item[1].evidence_score, -item[1].hop_count),
            reverse=True,
        )
        return [path for _, path in ranked[:limit]]

    async def upsert_reflection(self, reflection: Reflection) -> Reflection:
        payload = _ensure_payload_agent_namespace(_serialize_value(reflection.model_dump(exclude_none=True)))
        reflection_key = str(payload.get("reflection_key") or "").strip()
        if not reflection_key:
            raise ValueError("Reflection.reflection_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("reflections")
                .select("*")
                .eq("reflection_key", reflection_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "reflections"):
                logger.info("Memory reflections table is not available yet; skipping reflection upsert.")
                return reflection
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "reflections",
                str(existing["id"]),
                payload,
                operation="upsert_reflection",
            )
            if response is None:
                refreshed = await self._fetch_row("reflections", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_reflection returned no rows.")
                return Reflection.model_validate(_normalize_record(refreshed))
            return Reflection.model_validate(_normalize_record(self._require_record(response, operation="upsert_reflection")))

        response, _ = await self._insert_payload("reflections", payload, operation="insert_reflection")
        return Reflection.model_validate(_normalize_record(self._require_record(response, operation="insert_reflection")))

    async def list_reflections(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Reflection]:
        requested_statuses = [str(status).strip() for status in (statuses or []) if str(status).strip()]

        def _query() -> Any:
            query = self._schema_client().table("reflections").select("*")
            if requested_statuses:
                query = query.in_("status", requested_statuses)
            query = query.order("confidence", desc=True).order("last_observed_at", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "reflections"):
                logger.info("Memory reflections table is not available yet; returning an empty reflections list.")
                return []
            raise

        reflections = [Reflection.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        reflections = _filter_records_by_agent_namespace(reflections, agent_namespace)
        return reflections[:limit]

    async def delete_reflection(
        self,
        reflection_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        normalized_key = str(reflection_key or "").strip()
        if not normalized_key:
            return False

        def _delete() -> Any:
            return (
                self._schema_client()
                .table("reflections")
                .delete()
                .eq("reflection_key", normalized_key)
                .eq("agent_namespace", _resolved_agent_namespace(agent_namespace))
                .execute()
            )

        try:
            await self._run(_delete)
        except Exception as exc:
            if _is_missing_relation_error(exc, "reflections"):
                logger.info("Memory reflections table is not available yet; skipping reflection delete.")
                return False
            raise
        return True

    async def upsert_commitment(self, commitment: Commitment) -> Commitment:
        payload = _ensure_payload_agent_namespace(_serialize_value(commitment.model_dump(exclude_none=True)))
        commitment_key = str(payload.get("commitment_key") or "").strip()
        if not commitment_key:
            raise ValueError("Commitment.commitment_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("commitments")
                .select("*")
                .eq("commitment_key", commitment_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

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
        payload = _ensure_payload_agent_namespace(_serialize_value(correction.model_dump(exclude_none=True)))
        correction_key = str(payload.get("correction_key") or "").strip()
        if not correction_key:
            raise ValueError("Correction.correction_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("corrections")
                .select("*")
                .eq("correction_key", correction_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

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
        payload = _ensure_payload_agent_namespace(_serialize_value(handoff.model_dump(exclude_none=True)))
        handoff_key = str(payload.get("handoff_key") or "").strip()
        if not handoff_key:
            raise ValueError("SessionHandoff.handoff_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("session_handoffs")
                .select("*")
                .eq("handoff_key", handoff_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

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

    async def upsert_presence_state(self, state: PresenceState) -> PresenceState:
        payload = _ensure_payload_agent_namespace(_serialize_value(state.model_dump(exclude_none=True)))
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("presence_state")
                .select("*")
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "presence_state"):
                logger.info("Memory presence_state table is not available yet; skipping presence upsert.")
                return state
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "presence_state",
                str(existing["id"]),
                payload,
                operation="upsert_presence_state",
            )
            if response is None:
                refreshed = await self._fetch_row("presence_state", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_presence_state returned no rows.")
                return PresenceState.model_validate(_normalize_record(refreshed))
            return PresenceState.model_validate(_normalize_record(self._require_record(response, operation="upsert_presence_state")))

        response, _ = await self._insert_payload("presence_state", payload, operation="insert_presence_state")
        return PresenceState.model_validate(_normalize_record(self._require_record(response, operation="insert_presence_state")))

    async def get_presence_state(
        self,
        agent_namespace: str | None = None,
    ) -> PresenceState | None:
        normalized_namespace = _resolved_agent_namespace(agent_namespace)

        def _query() -> Any:
            return (
                self._schema_client()
                .table("presence_state")
                .select("*")
                .eq("agent_namespace", normalized_namespace)
                .limit(1)
                .execute()
            )

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "presence_state"):
                logger.info("Memory presence_state table is not available yet; returning no presence state.")
                return None
            raise

        record = _first_response_record(response)
        if record is None:
            return None
        return PresenceState.model_validate(_normalize_record(record))

    async def upsert_background_job(
        self,
        job: BackgroundJob,
    ) -> BackgroundJob:
        payload = _ensure_payload_agent_namespace(_serialize_value(job.model_dump(exclude_none=True)))
        job_key = str(payload.get("job_key") or "").strip()
        if not job_key:
            raise ValueError("BackgroundJob.job_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("background_jobs")
                .select("*")
                .eq("job_key", job_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "background_jobs"):
                logger.info("Memory background_jobs table is not available yet; skipping background job upsert.")
                return job
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "background_jobs",
                str(existing["id"]),
                payload,
                operation="upsert_background_job",
            )
            if response is None:
                refreshed = await self._fetch_row("background_jobs", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_background_job returned no rows.")
                return BackgroundJob.model_validate(_normalize_record(refreshed))
            return BackgroundJob.model_validate(
                _normalize_record(self._require_record(response, operation="upsert_background_job"))
            )

        response, _ = await self._insert_payload(
            "background_jobs",
            payload,
            operation="insert_background_job",
        )
        return BackgroundJob.model_validate(
            _normalize_record(self._require_record(response, operation="insert_background_job"))
        )

    async def list_background_jobs(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        session_id: str | None = None,
        job_key: str | None = None,
    ) -> list[BackgroundJob]:
        requested_statuses = [str(status).strip() for status in (statuses or []) if str(status).strip()]
        normalized_session_id = str(session_id or "").strip()
        normalized_job_key = str(job_key or "").strip()

        def _query() -> Any:
            query = self._schema_client().table("background_jobs").select("*")
            if requested_statuses:
                query = query.in_("status", requested_statuses)
            if normalized_session_id:
                query = query.eq("session_id", normalized_session_id)
            if normalized_job_key:
                query = query.eq("job_key", normalized_job_key)
            query = query.order("updated_at", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "background_jobs"):
                logger.info("Memory background_jobs table is not available yet; returning empty job list.")
                return []
            raise

        jobs = [BackgroundJob.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        jobs = _filter_records_by_agent_namespace(jobs, agent_namespace)
        if normalized_session_id:
            jobs = [item for item in jobs if str(item.session_id or "") == normalized_session_id]
        if normalized_job_key:
            jobs = [item for item in jobs if item.job_key == normalized_job_key]
        return jobs[:limit]

    async def transition_background_job(
        self,
        job_key: str,
        *,
        status: str,
        agent_namespace: str | None = None,
        progress_note: str | None = None,
        completion_summary: str | None = None,
        result_refs: list[str] | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> BackgroundJob | None:
        normalized_job_key = str(job_key or "").strip()
        if not normalized_job_key:
            raise ValueError("job_key is required.")

        existing = await self.list_background_jobs(
            limit=1,
            agent_namespace=agent_namespace,
            job_key=normalized_job_key,
        )
        if not existing:
            return None

        reference_time = updated_at or _now_utc()
        payload: dict[str, Any] = {
            "status": str(status or "").strip(),
            "updated_at": reference_time.isoformat(),
        }
        if progress_note is not None:
            payload["progress_note"] = str(progress_note).strip() or None
            payload["last_progress_at"] = reference_time.isoformat()
        if completion_summary is not None:
            payload["completion_summary"] = str(completion_summary).strip() or None
        if result_refs is not None:
            payload["result_refs"] = [str(item).strip() for item in result_refs if str(item).strip()]
        if started_at is not None:
            payload["started_at"] = started_at.astimezone(timezone.utc).isoformat()
        if completed_at is not None:
            payload["completed_at"] = completed_at.astimezone(timezone.utc).isoformat()

        response, _ = await self._update_payload(
            "background_jobs",
            str(existing[0].id),
            payload,
            operation="transition_background_job",
        )
        if response is None:
            refreshed = await self._fetch_row("background_jobs", str(existing[0].id))
            if refreshed is None:
                return None
            return BackgroundJob.model_validate(_normalize_record(refreshed))
        return BackgroundJob.model_validate(
            _normalize_record(self._require_record(response, operation="transition_background_job"))
        )

    async def upsert_heartbeat_opportunity(
        self,
        opportunity: HeartbeatOpportunity,
    ) -> HeartbeatOpportunity:
        payload = _ensure_payload_agent_namespace(_serialize_value(opportunity.model_dump(exclude_none=True)))
        opportunity_key = str(payload.get("opportunity_key") or "").strip()
        if not opportunity_key:
            raise ValueError("HeartbeatOpportunity.opportunity_key is required.")
        agent_namespace = _resolved_agent_namespace(payload.get("agent_namespace"))

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("heartbeat_opportunities")
                .select("*")
                .eq("opportunity_key", opportunity_key)
                .eq("agent_namespace", agent_namespace)
                .limit(1)
                .execute()
            )

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "heartbeat_opportunities"):
                logger.info("Memory heartbeat_opportunities table is not available yet; skipping heartbeat upsert.")
                return opportunity
            raise

        existing = _first_response_record(response)
        if existing is not None:
            payload.setdefault("updated_at", _now_utc().isoformat())
            response, _ = await self._update_payload(
                "heartbeat_opportunities",
                str(existing["id"]),
                payload,
                operation="upsert_heartbeat_opportunity",
            )
            if response is None:
                refreshed = await self._fetch_row("heartbeat_opportunities", str(existing["id"]))
                if refreshed is None:
                    raise LookupError("upsert_heartbeat_opportunity returned no rows.")
                return HeartbeatOpportunity.model_validate(_normalize_record(refreshed))
            return HeartbeatOpportunity.model_validate(
                _normalize_record(self._require_record(response, operation="upsert_heartbeat_opportunity"))
            )

        response, _ = await self._insert_payload(
            "heartbeat_opportunities",
            payload,
            operation="insert_heartbeat_opportunity",
        )
        return HeartbeatOpportunity.model_validate(
            _normalize_record(self._require_record(response, operation="insert_heartbeat_opportunity"))
        )

    async def insert_heartbeat_dispatch(
        self,
        dispatch: HeartbeatDispatch,
    ) -> HeartbeatDispatch:
        payload = _ensure_payload_agent_namespace(_serialize_value(dispatch.model_dump(exclude_none=True)))
        try:
            response, _ = await self._insert_payload(
                "heartbeat_dispatches",
                payload,
                operation="insert_heartbeat_dispatch",
            )
        except Exception as exc:
            if _is_missing_relation_error(exc, "heartbeat_dispatches"):
                logger.info("Memory heartbeat_dispatches table is not available yet; skipping dispatch insert.")
                return dispatch
            raise
        return HeartbeatDispatch.model_validate(
            _normalize_record(self._require_record(response, operation="insert_heartbeat_dispatch"))
        )

    async def list_heartbeat_dispatches(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        opportunity_key: str | None = None,
        session_id: str | None = None,
        since: datetime | None = None,
    ) -> list[HeartbeatDispatch]:
        requested_statuses = [str(status).strip() for status in (statuses or []) if str(status).strip()]
        normalized_opportunity_key = str(opportunity_key or "").strip()
        normalized_session_id = str(session_id or "").strip()
        since_iso = since.astimezone(timezone.utc).isoformat() if since is not None else None

        def _query() -> Any:
            query = self._schema_client().table("heartbeat_dispatches").select("*")
            if requested_statuses:
                query = query.in_("dispatch_status", requested_statuses)
            if normalized_opportunity_key:
                query = query.eq("opportunity_key", normalized_opportunity_key)
            if normalized_session_id:
                query = query.eq("session_id", normalized_session_id)
            if since_iso:
                query = query.gte("attempted_at", since_iso)
            query = query.order("attempted_at", desc=True).limit(max(limit * 4, 20))
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "heartbeat_dispatches"):
                logger.info("Memory heartbeat_dispatches table is not available yet; returning empty dispatch history.")
                return []
            raise

        dispatches = [HeartbeatDispatch.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        dispatches = _filter_records_by_agent_namespace(dispatches, agent_namespace)
        if normalized_opportunity_key:
            dispatches = [item for item in dispatches if item.opportunity_key == normalized_opportunity_key]
        if normalized_session_id:
            dispatches = [item for item in dispatches if str(item.session_id or "") == normalized_session_id]
        return dispatches[:limit]

    async def list_heartbeat_opportunities(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        kinds: list[str] | None = None,
        session_id: str | None = None,
    ) -> list[HeartbeatOpportunity]:
        requested_statuses = [str(status).strip() for status in (statuses or []) if str(status).strip()]
        requested_kinds = [str(kind).strip() for kind in (kinds or []) if str(kind).strip()]
        normalized_session_id = str(session_id or "").strip()

        def _query() -> Any:
            query = self._schema_client().table("heartbeat_opportunities").select("*")
            if requested_statuses:
                query = query.in_("status", requested_statuses)
            if requested_kinds:
                query = query.in_("kind", requested_kinds)
            if normalized_session_id:
                query = query.eq("session_id", normalized_session_id)
            query = (
                query.order("priority_score", desc=True)
                .order("earliest_send_at", desc=False)
                .order("created_at", desc=True)
                .limit(max(limit * 4, 20))
            )
            return query.execute()

        try:
            response = await self._run(_query)
        except Exception as exc:
            if _is_missing_relation_error(exc, "heartbeat_opportunities"):
                logger.info("Memory heartbeat_opportunities table is not available yet; returning empty heartbeat list.")
                return []
            raise

        opportunities = [HeartbeatOpportunity.model_validate(_normalize_record(item)) for item in _response_rows(response)]
        opportunities = _filter_records_by_agent_namespace(opportunities, agent_namespace)
        if normalized_session_id:
            opportunities = [item for item in opportunities if str(item.session_id or "") == normalized_session_id]
        return opportunities[:limit]

    async def cancel_heartbeat_opportunity(
        self,
        opportunity_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        normalized_key = str(opportunity_key or "").strip()
        if not normalized_key:
            return False
        normalized_namespace = _resolved_agent_namespace(agent_namespace)

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("heartbeat_opportunities")
                .select("*")
                .eq("opportunity_key", normalized_key)
                .eq("agent_namespace", normalized_namespace)
                .limit(1)
                .execute()
            )

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "heartbeat_opportunities"):
                logger.info("Memory heartbeat_opportunities table is not available yet; skipping heartbeat cancel.")
                return False
            raise

        existing = _first_response_record(response)
        if existing is None:
            return False

        response, _ = await self._update_payload(
            "heartbeat_opportunities",
            str(existing["id"]),
            {
                "status": "cancelled",
                "updated_at": _now_utc().isoformat(),
            },
            operation="cancel_heartbeat_opportunity",
        )
        return response is not None or existing is not None

    async def transition_heartbeat_opportunity(
        self,
        opportunity_key: str,
        *,
        status: str,
        agent_namespace: str | None = None,
    ) -> bool:
        normalized_key = str(opportunity_key or "").strip()
        normalized_status = str(status or "").strip()
        if not normalized_key or not normalized_status:
            return False
        normalized_namespace = _resolved_agent_namespace(agent_namespace)

        def _find_existing() -> Any:
            return (
                self._schema_client()
                .table("heartbeat_opportunities")
                .select("*")
                .eq("opportunity_key", normalized_key)
                .eq("agent_namespace", normalized_namespace)
                .limit(1)
                .execute()
            )

        try:
            response = await self._run(_find_existing)
        except Exception as exc:
            if _is_missing_relation_error(exc, "heartbeat_opportunities"):
                logger.info("Memory heartbeat_opportunities table is not available yet; skipping heartbeat transition.")
                return False
            raise

        existing = _first_response_record(response)
        if existing is None:
            return False

        response, _ = await self._update_payload(
            "heartbeat_opportunities",
            str(existing["id"]),
            {
                "status": normalized_status,
                "updated_at": _now_utc().isoformat(),
                "last_scored_at": _now_utc().isoformat(),
            },
            operation="transition_heartbeat_opportunity",
        )
        return response is not None or existing is not None

    async def health_check(self) -> bool:
        try:
            await self._run(lambda: self._schema_client().table("sessions").select("id").limit(1).execute())
        except Exception:
            return False
        return True


class LocalTransport(SupabaseTransport):
    """Local direct connection to Supabase, used as the source-of-truth transport."""


class RemoteTransport:
    """HTTP client to VPS runtime service (future)."""

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

    async def upsert_memory_case(self, case: MemoryCase) -> MemoryCase:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_memory_cases(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        outcome_statuses: list[str] | None = None,
    ) -> list[MemoryCase]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def delete_memory_case(
        self,
        case_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_case_evidence_link(self, link: CaseEvidenceLink) -> CaseEvidenceLink:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_case_evidence_links(
        self,
        case_id: str,
        limit: int = 50,
        agent_namespace: str | None = None,
    ) -> list[CaseEvidenceLink]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_temporal_graph_node(self, node: TemporalGraphNode) -> TemporalGraphNode:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_temporal_graph_nodes(
        self,
        limit: int = 50,
        agent_namespace: str | None = None,
        node_types: list[str] | None = None,
    ) -> list[TemporalGraphNode]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def delete_temporal_graph_node(
        self,
        node_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_temporal_graph_edge(self, edge: TemporalGraphEdge) -> TemporalGraphEdge:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_temporal_graph_edges(
        self,
        limit: int = 200,
        agent_namespace: str | None = None,
        relation_types: list[str] | None = None,
    ) -> list[TemporalGraphEdge]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def delete_temporal_graph_edge(
        self,
        edge_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def search_temporal_graph_paths(
        self,
        query: str,
        limit: int = 6,
        max_hops: int = 3,
        agent_namespace: str | None = None,
    ) -> list[TemporalGraphPath]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_reflection(self, reflection: Reflection) -> Reflection:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_reflections(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
    ) -> list[Reflection]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def delete_reflection(
        self,
        reflection_key: str,
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

    async def upsert_presence_state(self, state: PresenceState) -> PresenceState:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def get_presence_state(
        self,
        agent_namespace: str | None = None,
    ) -> PresenceState | None:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_background_job(
        self,
        job: BackgroundJob,
    ) -> BackgroundJob:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_background_jobs(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        session_id: str | None = None,
        job_key: str | None = None,
    ) -> list[BackgroundJob]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def transition_background_job(
        self,
        job_key: str,
        *,
        status: str,
        agent_namespace: str | None = None,
        progress_note: str | None = None,
        completion_summary: str | None = None,
        result_refs: list[str] | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> BackgroundJob | None:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def upsert_heartbeat_opportunity(
        self,
        opportunity: HeartbeatOpportunity,
    ) -> HeartbeatOpportunity:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def insert_heartbeat_dispatch(
        self,
        dispatch: HeartbeatDispatch,
    ) -> HeartbeatDispatch:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_heartbeat_dispatches(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        opportunity_key: str | None = None,
        session_id: str | None = None,
        since: datetime | None = None,
    ) -> list[HeartbeatDispatch]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def list_heartbeat_opportunities(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        statuses: list[str] | None = None,
        kinds: list[str] | None = None,
        session_id: str | None = None,
    ) -> list[HeartbeatOpportunity]:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def cancel_heartbeat_opportunity(
        self,
        opportunity_key: str,
        *,
        agent_namespace: str | None = None,
    ) -> bool:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def transition_heartbeat_opportunity(
        self,
        opportunity_key: str,
        *,
        status: str,
        agent_namespace: str | None = None,
    ) -> bool:
        raise NotImplementedError("RemoteTransport is not implemented yet.")

    async def health_check(self) -> bool:
        raise NotImplementedError("RemoteTransport is not implemented yet.")
