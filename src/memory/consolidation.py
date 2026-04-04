from __future__ import annotations

import asyncio
from collections import Counter
import hashlib
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from memory.client import MemoryClient
from memory.config import MemoryConfig
from memory.fact_extraction import extract_and_store_facts
from memory.models import (
    ActiveStateStatus,
    CommitmentStatus,
    CorrectionKind,
    DecisionOutcomeStatus,
    DirectiveStatus,
    Episode,
    EpisodeRole,
    Fact,
    FactCategory,
    PatternType,
    ReflectionStatus,
    Session,
)
from memory.transport import (
    _agent_namespace_matches,
    _looks_like_operational_content,
    _looks_like_reference_content,
    _normalize_record,
)

DEFAULT_SUMMARY_MODEL = "glm-5-turbo"
MAX_SUMMARY_LENGTH = 500
_CONSOLIDATION_RETRY_ATTEMPTS = 3
_CONSOLIDATION_RETRY_BASE_DELAY_SECONDS = 0.4
_CONSOLIDATION_RETRY_MAX_DELAY_SECONDS = 2.0
ACTIVE_STATE_LOOKBACK_HOURS = 72
ACTIVE_STATE_SESSION_LIMIT = 8
ACTIVE_STATE_EPISODE_LIMIT = 24
ACTIVE_STATE_FACT_LIMIT = 64
DIRECTIVES_LOOKBACK_DAYS = 90
DIRECTIVES_SESSION_LIMIT = 64
COMMITMENTS_LOOKBACK_DAYS = 365
COMMITMENTS_SESSION_LIMIT = 24
CORRECTIONS_LOOKBACK_DAYS = 365
CORRECTIONS_SESSION_LIMIT = 24
_COMMITMENT_MAX_AGE_HOURS = {
    "message": 12,
    "fix": 24,
    "follow_up": 24,
    "tracking": 72,
    "reminder": 24 * 14,
}
TIMELINE_EVENT_LOOKBACK_DAYS = 3650
TIMELINE_EVENT_SESSION_LIMIT = 64
DECISION_OUTCOME_LOOKBACK_DAYS = 3650
DECISION_OUTCOME_SESSION_LIMIT = 64
PATTERNS_LOOKBACK_DAYS = 3650
PATTERNS_SESSION_LIMIT = 96
REFLECTIONS_LOOKBACK_DAYS = 3650
REFLECTIONS_SESSION_LIMIT = 96
_MANAGED_ACTIVE_STATE_KEYS = {
    "auto:project:primary",
    "auto:project:secondary",
    "auto:priority:primary",
    "auto:blocker:primary",
    "auto:open_loop:primary",
    "auto:emotion_state:current",
}
_DIRECTIVE_CLAUSE_SPLIT = re.compile(r"[.!?\n;]+")
_DIRECTIVE_MARKERS = (
    "always",
    "never",
    "do not",
    "don't",
    "dont",
    "must",
    "whenever i tell you",
    "if i tell you",
    "please don't",
    "please dont",
    "without asking",
    "no more",
    "from now on",
    "going forward",
    "talk",
    "speak",
    "write",
    "tone",
    "style",
    "prefer",
    "preference",
    "keep",
)
_DIRECTIVE_PREFIXES = (
    "always ",
    "never ",
    "don't ",
    "dont ",
    "do not ",
    "please don't ",
    "please dont ",
    "please do ",
    "please ",
    "if i tell you ",
    "when i tell you ",
    "whenever i tell you ",
    "from now on ",
    "going forward ",
    "keep ",
    "i prefer ",
    "my preference is ",
)
_DIRECTIVE_TARGET_HINTS = (
    "you",
    "your",
    "reply",
    "replies",
    "send me",
    "delegate",
    "use",
    "call",
    "message",
    "text",
    "respond",
    "tone",
    "style",
    "wording",
    "language",
)
_DIRECTIVE_ACTION_HINTS = (
    "delegate",
    "use",
    "send",
    "reply",
    "respond",
    "ignore",
    "restart",
    "remember",
    "store",
    "persist",
    "ask",
    "wait",
    "message",
    "call",
    "schedule",
    "talk",
    "speak",
    "write",
    "phrase",
    "format",
    "keep",
)
_DIRECTIVE_QUESTION_PREFIXES = (
    "what ",
    "why ",
    "how ",
    "when ",
    "where ",
    "can you",
    "should you",
    "would you",
    "do you",
)
_DIRECTIVE_NON_PERSIST_MARKERS = (
    "for this message",
    "for this reply",
    "for this response",
    "for this question",
    "just this message",
    "just this reply",
    "this time only",
    "for now only",
    "today only",
    "only for now",
)
_DIRECTIVE_REJECT_MARKERS = (
    "you died",
    "you forgot",
    "i never told you",
    "we dont",
    "we don't",
    "i dont use",
    "i don't use",
    "no you wont",
    "no you won't",
    "respond to the user",
    "[system:",
    "session is about to be automatically reset",
)
_DIRECTIVE_LEAD_IN_RE = re.compile(
    r"^(?:(?:and|but|so|okay|ok|hey|listen|note)\s*,?\s*)+",
    re.IGNORECASE,
)
_BLOCKER_MARKERS = (
    "blocked",
    "blocker",
    "stuck",
    "issue",
    "issues",
    "problem",
    "problems",
    "bug",
    "bugs",
    "not working",
    "broken",
    "failing",
    "failure",
    "debug",
    "fix",
)
_OPEN_LOOP_MARKERS = (
    "how do",
    "how should",
    "what should",
    "what do",
    "why is",
    "need to",
    "should we",
    "follow up",
    "figure out",
)
_DECISION_SENTENCE_SPLIT = re.compile(r"[.!?\n;]+")
_SUMMARY_PREFERENCE_RE = re.compile(
    r"\b(?:i|user)\s+(?:prefer|prefers|like|likes)\s+([a-z0-9][a-z0-9\s'/-]{1,80})",
    re.IGNORECASE,
)
_DECISION_MARKERS = (
    "decided",
    "choose",
    "chose",
    "switched",
    "moved",
    "removed",
    "added",
    "implemented",
    "started",
    "focused on",
    "rollout",
    "rolled out",
    "disabled",
    "enabled",
    "delegated",
    "fixed",
    "persisted",
    "stored",
    "created",
    "built",
    "migrated",
)
_STRONG_DECISION_MARKERS = (
    "decided",
    "chose",
    "switched",
    "moved",
    "removed",
    "added",
    "implemented",
    "disabled",
    "enabled",
    "delegated",
    "fixed",
    "persisted",
    "stored",
    "created",
    "built",
    "migrated",
    "rolled out",
)
_WEAK_DECISION_MARKERS = (
    "focused on",
    "started",
    "rollout",
)
_SUCCESS_OUTCOME_MARKERS = (
    "working",
    "works",
    "fixed",
    "verified",
    "confirmed",
    "removed",
    "eliminated",
    "stopped",
    "stored",
    "persisted",
    "resolved",
    "successful",
    "runs successfully",
    "clean",
    "live",
)
_FAILURE_OUTCOME_MARKERS = (
    "failed",
    "failure",
    "bug",
    "issue",
    "broken",
    "not working",
    "didn't",
    "didnt",
    "missing",
    "skipped",
    "wrong",
    "drift",
    "polluted",
    "duplicate",
    "leaked",
    "blocked",
    "stuck",
)
_COMMITMENT_SENTENCE_SPLIT = re.compile(r"[.!?\n;]+")
_COMMITMENT_MARKERS = ("i'll ", "i will ", "we'll ", "we will ")
_COMMITMENT_ACTION_MARKERS = (
    "check",
    "verify",
    "fix",
    "look into",
    "follow up",
    "message",
    "send",
    "remind",
    "track",
    "remember",
    "handle",
    "restart",
    "run",
    "delegate",
    "update",
)
_COMMITMENT_STRONG_HINTS = (
    "get back to you",
    "remind you",
    "follow up",
    "keep track",
    "i'll make sure",
    "i will make sure",
    "i'll remember",
    "i will remember",
    "i'll message you",
    "i will message you",
    "i'll send you",
    "i will send you",
    "i'll check on",
    "i will check on",
)
_COMMITMENT_COMPLETION_MARKERS = (
    "done",
    "fixed",
    "completed",
    "updated",
    "verified",
    "finished",
    "resolved",
    "rolled out",
)
_CORRECTION_MARKERS = (
    "that's wrong",
    "thats wrong",
    "you are wrong",
    "you're wrong",
    "youre wrong",
    "you remembered",
    "you misremembered",
    "i never said",
    "i never told you",
    "i didn't say",
    "i didnt say",
    "don't infer",
    "dont infer",
    "that's not true",
    "thats not true",
    "that is not true",
    "that wasn't",
    "that wasnt",
    "you got that wrong",
    "what do u mean",
    "what do you mean",
    "who is telling you",
    "why are you doing",
    "i still did not understand",
    "i still didn't understand",
    "i still dont understand",
    "i still don't understand",
    "i dont understand how",
    "i don't understand how",
)
_CORRECTION_STRONG_MARKERS = (
    "that's wrong",
    "thats wrong",
    "you are wrong",
    "you're wrong",
    "youre wrong",
    "you remembered",
    "you misremembered",
    "i never said",
    "i never told you",
    "i didn't say",
    "i didnt say",
    "don't infer",
    "dont infer",
    "that's not true",
    "thats not true",
    "that is not true",
    "that wasn't",
    "that wasnt",
    "you got that wrong",
    "what do u mean",
    "what do you mean",
    "who is telling you",
    "why are you doing",
    "i still did not understand",
    "i still didn't understand",
    "i still dont understand",
    "i still don't understand",
    "i dont understand how",
    "i don't understand how",
)
_PATTERN_SPECS: tuple[dict[str, Any], ...] = (
    {
        "pattern_key": "auto:pattern:root-cause-debugging",
        "pattern_type": "decision_style",
        "statement": "When something important feels broken, the user pushes for root-cause debugging before moving on.",
        "description": "Repeatedly asks what exactly happened, wants the real bug source, and prefers fixing the underlying mechanism instead of papering over symptoms.",
        "summary_markers": ("root cause", "underlying cause", "real bug source", "exact bug source", "fixing the underlying", "shallow patches"),
        "episode_markers": ("root cause", "real bug source", "what exactly happened", "need the exact", "underlying mechanism", "shallow patch"),
        "counter_markers": ("just patch it", "quick patch", "move on for now"),
        "pattern_type_enum": PatternType.DECISION_STYLE,
        "min_sessions": 2,
        "min_episodes": 2,
        "min_distinct_days": 2,
        "min_distinct_windows": 2,
        "min_summary_sessions": 1,
        "min_episode_sessions": 2,
        "episode_only_min_sessions": 3,
        "episode_only_min_support_score": 4.8,
        "min_support_score": 4.0,
        "max_counterexample_ratio": 0.49,
        "impact_score": 0.92,
    },
    {
        "pattern_key": "auto:pattern:foundational-redesign",
        "pattern_type": "work_pattern",
        "statement": "The user tends to redesign foundations when a system matters, instead of settling for shallow local fixes.",
        "description": "Repeatedly widens the lens to architecture, memory layers, retrieval quality, and durable system behavior when confidence in a local fix drops.",
        "summary_markers": ("architecture redesign", "foundational redesign", "memory layers", "retrieval quality", "durable system", "redesign foundations"),
        "episode_markers": ("redesign the architecture", "rebuild the foundation", "memory layers", "durable system", "not a shallow patch"),
        "counter_markers": ("quick local fix", "temporary patch"),
        "pattern_type_enum": PatternType.WORK_PATTERN,
        "min_sessions": 2,
        "min_episodes": 2,
        "min_distinct_days": 2,
        "min_distinct_windows": 2,
        "min_summary_sessions": 1,
        "min_episode_sessions": 2,
        "episode_only_min_sessions": 3,
        "episode_only_min_support_score": 4.8,
        "min_support_score": 4.0,
        "max_counterexample_ratio": 0.49,
        "impact_score": 0.9,
    },
    {
        "pattern_key": "auto:pattern:high-standards",
        "pattern_type": "quality_bar",
        "statement": "The user consistently pushes toward the strongest end state rather than accepting an okay version.",
        "description": "Repeatedly asks for the strongest, most solid version of the system and keeps iterating until the result feels durable enough to trust.",
        "summary_markers": ("strongest possible", "best version", "most solid", "powerful version", "feels right", "quality bar"),
        "episode_markers": ("strongest version", "best version", "solid version", "not an okay version", "until it feels right"),
        "counter_markers": ("good enough", "whatever works", "ship it for now"),
        "pattern_type_enum": PatternType.QUALITY_BAR,
        "min_sessions": 2,
        "min_episodes": 2,
        "min_distinct_days": 2,
        "min_distinct_windows": 2,
        "min_summary_sessions": 1,
        "min_episode_sessions": 2,
        "episode_only_min_sessions": 3,
        "episode_only_min_support_score": 4.8,
        "min_support_score": 4.0,
        "max_counterexample_ratio": 0.49,
        "impact_score": 0.86,
    },
    {
        "pattern_key": "auto:pattern:ambitious-end-state",
        "pattern_type": "strength",
        "statement": "When the long-term payoff feels meaningful, the user is willing to pursue unusually ambitious end states instead of shrinking the vision early.",
        "description": "Repeatedly frames the desired system in ambitious, companion-like, or magical terms and keeps investing in the stronger end state rather than settling for a basic version.",
        "summary_markers": ("always-on ai companion", "feel like magic", "real companion", "actual heartbeat", "most ambitious ai project", "insanely good pipeline"),
        "episode_markers": ("feel like talking to someone", "not triggering api calls", "feel like magic", "alive 24/7", "heartbeat-based", "actual heartbeat", "someone not triggering api calls"),
        "counter_markers": ("basic bot", "simple version is fine", "keep it small", "good enough"),
        "pattern_type_enum": PatternType.STRENGTH,
        "min_sessions": 2,
        "min_episodes": 2,
        "min_distinct_days": 2,
        "min_distinct_windows": 2,
        "min_summary_sessions": 1,
        "min_episode_sessions": 2,
        "episode_only_min_sessions": 3,
        "episode_only_min_support_score": 5.3,
        "min_support_score": 4.8,
        "max_counterexample_ratio": 0.4,
        "impact_score": 0.84,
    },
    {
        "pattern_key": "auto:pattern:trust-drives-depth",
        "pattern_type": "trust_pattern",
        "statement": "When system trust drops, the user shifts from surface progress into deeper reliability work until the system feels dependable again.",
        "description": "Continuity failures, reliability breaks, or trust loss repeatedly pull attention toward durability, confidence, and making the system dependable again.",
        "summary_markers": ("trust dropped", "continuity failure", "reliability break", "dependable again", "make the system trustworthy", "confidence in the system"),
        "episode_markers": ("trust the system", "reliability", "continuity", "dependable", "confidence in it", "can't trust this"),
        "counter_markers": ("leave it flaky", "good enough for now"),
        "pattern_type_enum": PatternType.TRUST_PATTERN,
        "min_sessions": 2,
        "min_episodes": 2,
        "min_distinct_days": 2,
        "min_distinct_windows": 2,
        "min_summary_sessions": 1,
        "min_episode_sessions": 2,
        "episode_only_min_sessions": 3,
        "episode_only_min_support_score": 4.8,
        "min_support_score": 4.0,
        "max_counterexample_ratio": 0.49,
        "impact_score": 0.88,
    },
    {
        "pattern_key": "auto:pattern:reliability-breaks-hit-hard",
        "pattern_type": "emotional_pattern",
        "statement": "Reliability and continuity failures hit hard for the user and quickly become concentrated repair work instead of a minor annoyance.",
        "description": "Reply delays, session resets, broken continuity, or persistence failures repeatedly trigger emotionally loaded messages and a rapid shift toward fixing reliability first.",
        "summary_markers": ("continuity failure", "reliability break", "gateway failure", "session resets", "no memory persistence", "reply delay", "trust in the system dropped", "gateway crashed", "not responding", "reply took", "stuck after restart"),
        "episode_markers": ("don't ignore my texts", "not replying", "not responding", "weren't responding", "takes a ton of time to reply", "reply in 3-10 seconds", "reply took", "mins to reply", "session is starting new again", "sessions were restarting", "gateway restarted", "gateway crashed", "no response yet", "stuck", "can't trust this", "20 minutes"),
        "counter_markers": ("leave latency for later", "not a big deal", "ignore the delay"),
        "pattern_type_enum": PatternType.EMOTIONAL_PATTERN,
        "min_sessions": 2,
        "min_episodes": 2,
        "min_distinct_days": 2,
        "min_distinct_windows": 2,
        "min_summary_sessions": 1,
        "min_episode_sessions": 2,
        "episode_only_min_sessions": 3,
        "episode_only_min_support_score": 5.3,
        "allow_single_day_cluster": True,
        "single_day_cluster_min_sessions": 4,
        "single_day_cluster_min_episode_sessions": 3,
        "single_day_cluster_min_support_score": 6.0,
        "min_support_score": 4.8,
        "max_counterexample_ratio": 0.4,
        "impact_score": 0.87,
    },
    {
        "pattern_key": "auto:pattern:boundary-first-verification",
        "pattern_type": "decision_style",
        "statement": "The user makes better progress when the problem is reduced to the boundary that actually writes, routes, or stores state, then verified end-to-end.",
        "description": "Repeatedly asks for the exact boundary or write path behind a problem and prefers verifying that slice directly before widening the redesign.",
        "summary_markers": ("write boundary", "write path", "routing boundary", "stores state", "verified end to end", "exact boundary"),
        "episode_markers": ("actually writes", "actually stores", "exact boundary", "write path", "end to end", "real write path"),
        "counter_markers": ("broad redesign first", "skip verification"),
        "pattern_type_enum": PatternType.DECISION_STYLE,
        "min_sessions": 2,
        "min_episodes": 2,
        "min_distinct_days": 2,
        "min_distinct_windows": 2,
        "min_summary_sessions": 1,
        "min_episode_sessions": 2,
        "episode_only_min_sessions": 3,
        "episode_only_min_support_score": 4.8,
        "min_support_score": 4.0,
        "max_counterexample_ratio": 0.49,
        "impact_score": 0.84,
    },
)


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_optional_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_retryable_consolidation_error(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException, httpx.TransportError, OSError)):
        return True
    if isinstance(exc, ValueError):
        lowered = str(exc).lower()
        if "must be configured" in lowered:
            return False
        return True
    return False


async def _run_with_retries(
    operation,
    *,
    label: str,
    attempts: int = _CONSOLIDATION_RETRY_ATTEMPTS,
    base_delay_seconds: float = _CONSOLIDATION_RETRY_BASE_DELAY_SECONDS,
    max_delay_seconds: float = _CONSOLIDATION_RETRY_MAX_DELAY_SECONDS,
):
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await operation()
        except Exception as exc:
            can_retry = attempt < attempts and _is_retryable_consolidation_error(exc)
            if not can_retry:
                raise
            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
            _log(f"Retrying {label} in {delay:.1f}s after error ({attempt}/{attempts}): {exc}")
            await asyncio.sleep(delay)


def _coerce_session(value: Session | dict[str, Any]) -> Session:
    if isinstance(value, Session):
        return value
    return Session.model_validate(_normalize_record(value))


def _coerce_episode(value: Episode | dict[str, Any]) -> Episode:
    if isinstance(value, Episode):
        return value
    return Episode.model_validate(_normalize_record(value))


def _extract_response_rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if data is None:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


async def _list_recent_unsummarized_sessions(
    client: MemoryClient,
    *,
    since: datetime,
    min_message_count: int = 3,
    agent_namespace: str | None = None,
) -> list[Session]:
    transport = client.transport
    custom_loader = getattr(transport, "list_recent_unsummarized_sessions", None)
    if callable(custom_loader):
        sessions = await custom_loader(since=since, min_message_count=min_message_count)
        return [
            session
            for session in (_coerce_session(item) for item in sessions)
            if _agent_namespace_matches(session.agent_namespace, agent_namespace)
        ]

    schema_client_factory = getattr(transport, "_schema_client", None)
    runner = getattr(transport, "_run", None)
    if callable(schema_client_factory) and callable(runner):
        def _query() -> Any:
            query = (
                schema_client_factory()
                .table("sessions")
                .select("*")
                .gte("started_at", since.isoformat())
                .is_("summary", "null")
                .gt("message_count", min_message_count - 1)
                .order("started_at", desc=False)
            )
            return query.execute()

        response = await runner(_query)
        return [
            session
            for session in (_coerce_session(row) for row in _extract_response_rows(response))
            if _agent_namespace_matches(session.agent_namespace, agent_namespace)
        ]

    sessions_store = getattr(transport, "sessions", None)
    if isinstance(sessions_store, dict):
        sessions = [value for value in sessions_store.values() if isinstance(value, Session)]
        return [
            session
            for session in sorted(sessions, key=lambda item: item.started_at)
            if (
                session.summary is None
                and session.message_count >= min_message_count
                and session.started_at >= since
                and _agent_namespace_matches(session.agent_namespace, agent_namespace)
            )
        ]

    raise RuntimeError("Transport does not support loading recent sessions for consolidation.")


async def _list_recent_summarized_sessions(
    client: MemoryClient,
    *,
    since: datetime,
    min_message_count: int = 3,
    agent_namespace: str | None = None,
) -> list[Session]:
    transport = client.transport
    schema_client_factory = getattr(transport, "_schema_client", None)
    runner = getattr(transport, "_run", None)
    if callable(schema_client_factory) and callable(runner):
        def _query() -> Any:
            query = (
                schema_client_factory()
                .table("sessions")
                .select("*")
                .gte("started_at", since.isoformat())
                .not_.is_("summary", "null")
                .gt("message_count", min_message_count - 1)
                .order("started_at", desc=False)
            )
            return query.execute()

        response = await runner(_query)
        return [
            session
            for session in (_coerce_session(row) for row in _extract_response_rows(response))
            if _agent_namespace_matches(session.agent_namespace, agent_namespace)
        ]

    sessions_store = getattr(transport, "sessions", None)
    if isinstance(sessions_store, dict):
        sessions = [value for value in sessions_store.values() if isinstance(value, Session)]
        return [
            session
            for session in sorted(sessions, key=lambda item: item.started_at)
            if (
                session.summary is not None
                and session.message_count >= min_message_count
                and session.started_at >= since
                and _agent_namespace_matches(session.agent_namespace, agent_namespace)
            )
        ]

    custom_loader = getattr(transport, "list_recent_summarized_sessions", None)
    if callable(custom_loader):
        sessions = await custom_loader(since=since, min_message_count=min_message_count)
        return [
            session
            for session in (_coerce_session(item) for item in sessions)
            if _agent_namespace_matches(session.agent_namespace, agent_namespace)
        ]

    raise RuntimeError("Transport does not support loading recent summarized sessions for fact extraction.")


async def _list_session_episodes(client: MemoryClient, session_id: str) -> list[Episode]:
    transport = client.transport
    custom_loader = getattr(transport, "list_session_episodes", None)
    if callable(custom_loader):
        episodes = await custom_loader(session_id)
        return [_coerce_episode(episode) for episode in episodes]

    schema_client_factory = getattr(transport, "_schema_client", None)
    runner = getattr(transport, "_run", None)
    if callable(schema_client_factory) and callable(runner):
        def _query() -> Any:
            return (
                schema_client_factory()
                .table("episodes")
                .select("*")
                .eq("session_id", session_id)
                .order("message_timestamp", desc=False)
                .execute()
            )

        response = await runner(_query)
        return [_coerce_episode(row) for row in _extract_response_rows(response)]

    episodes_store = getattr(transport, "episodes", None)
    if isinstance(episodes_store, list):
        matching = [episode for episode in episodes_store if isinstance(episode, Episode) and str(episode.session_id) == session_id]
        return sorted(matching, key=lambda item: item.message_timestamp)

    raise RuntimeError(f"Transport does not support loading episodes for session {session_id}.")


def _render_transcript(episodes: list[Episode]) -> str:
    lines = [f"{episode.role.value.upper()}: {episode.content.strip()}" for episode in episodes if episode.content.strip()]
    return "\n".join(lines)


def _normalize_summary(value: Any) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text", "")))
        text = "".join(chunks)
    else:
        text = str(value or "")
    collapsed = " ".join(text.split())
    return collapsed[:MAX_SUMMARY_LENGTH].strip()


def _collapse_whitespace(value: str, *, max_length: int = 220) -> str:
    collapsed = " ".join((value or "").split())
    if len(collapsed) <= max_length:
        return collapsed
    return collapsed[: max_length - 3].rstrip() + "..."


def _meaningful_token_count(value: str) -> int:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "we",
        "what",
        "when",
        "where",
        "who",
        "with",
        "you",
        "your",
    }
    return len({token for token in re.findall(r"[a-z0-9]+", (value or "").lower()) if token not in stopwords and len(token) > 1})


_LOW_VALUE_STATE_MARKERS = (
    "reply to",
    "reply-to",
    "json objects",
    "supabase url",
    "supabase key",
    "memory schema",
    "sql editor",
    "migration",
    "gateway",
    "notify reload",
    "subtree migration",
    "venv",
    "env file",
    "r u feeling the upgrade",
    "or u r just lying to me",
    "what is left regarding the memory project",
    "session is about to be automatically reset",
    "current codex tasks",
    "force push",
    "hermes-agent at root level",
    "2342 messages",
    "md roadmap",
    "memory soul rules",
    "voice transcription working",
    "paused crons",
    "gpt 5.4",
    "openai api key",
    "current state:",
    "user:",
)
_LOW_VALUE_PROJECT_MARKERS = (
    "what is left regarding the memory project",
    "okay now what if left with memory project",
    "but now heres the question",
    "but now here's the question",
    "env file for the memory project",
    "from the memory project",
    "postgrest notify reload isn't working",
    "subtree migration",
    "venv got wiped",
    "this is likely a migration that hasn't been applied",
    "build complete",
    "phase 1:",
    "phase 2:",
    "phase 3:",
    "tests pass",
    "always_on_spec",
    "current codex tasks",
    "2342 messages",
    "md roadmap",
    "every atomic fact about you",
    "code patches",
    "bigger project",
    "important discussion",
    "postgrest notify reload",
    "project is active if",
    "memory purpose:",
    "memory vision:",
)
_LOW_VALUE_OUTCOME_MARKERS = (
    "polling now",
    "bubbling you when it's done",
    "build complete",
    "currently focused on",
    "codex is still working on the patch files",
    "process-memory",
    "skill creation",
    "nothing procedural to save from this session",
)
_TIMELINE_DAY_MIN_SESSION_COUNT = 2
_TIMELINE_DAY_MIN_MESSAGE_COUNT = 10
_TIMELINE_WEEK_MIN_SESSION_COUNT = 3
_TIMELINE_WEEK_MIN_DISTINCT_DAYS = 2
_TIMELINE_WEEK_MIN_MESSAGE_COUNT = 18
_GROUNDING_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "for",
    "from",
    "had",
    "has",
    "have",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "them",
    "there",
    "this",
    "to",
    "was",
    "we",
    "were",
    "which",
    "with",
}
_OUTCOME_RESULT_PREFIXES = ("that ", "this ", "which ")
_OUTCOME_SPLIT_MARKERS = (
    " stopped ",
    " fixed ",
    " prevented ",
    " reduced ",
    " kept ",
    " caused ",
    " led to ",
    " exposed ",
    " surfaced ",
)
_OUTCOME_CAUSAL_MARKERS = _OUTCOME_SPLIT_MARKERS + (
    " because ",
    " so ",
    " so that ",
    " which ",
    " letting ",
    " meant ",
    " meaning ",
)
_OUTCOME_GENERIC_OPENERS = (
    "it worked",
    "it works",
    "it was better",
    "it helped",
    "this helped",
    "that helped",
    "things improved",
    "it got better",
    "it was good",
    "it was bad",
)


def _looks_like_low_value_state_text(value: str) -> bool:
    lowered = _collapse_whitespace(value, max_length=260).lower()
    if not lowered:
        return True
    return any(marker in lowered for marker in _LOW_VALUE_STATE_MARKERS)


def _looks_like_low_value_project_content(value: str) -> bool:
    lowered = _collapse_whitespace(value, max_length=260).lower()
    if not lowered:
        return True
    if any(marker in lowered for marker in _LOW_VALUE_PROJECT_MARKERS):
        return True
    if lowered.startswith(("let me ", "this is ", "okay now ", "what's next ", "- [project]")):
        return True
    return False


def _looks_like_implementation_project_content(value: str, tags: list[str] | None = None) -> bool:
    lowered = _collapse_whitespace(value, max_length=320).lower()
    tag_text = " ".join(tags or []).lower()
    combined = f"{lowered} {tag_text}".strip()
    if _looks_like_reference_content(lowered) or _looks_like_operational_content(lowered):
        return True
    markers = (
        "supabase project",
        "memory supabase project",
        "project id",
        "migration",
        "terminal()",
        "terminal() calls",
        "git root",
        "project status",
        "creating the supabase project",
        "created the memory supabase project",
        "gateway",
        "session_search",
        "schema",
        "pgvector",
        "project is active if",
        "facts i have are mostly project-related",
        "repo config",
        "env vars",
        "/users/",
        "project: /users/",
    )
    if any(marker in combined for marker in markers):
        return True
    if lowered.startswith(("**", "\"**", "-", "from the memory project", "what's next on the memory project")):
        return True
    return False


def _looks_like_low_value_outcome_text(value: str) -> bool:
    lowered = _collapse_whitespace(value, max_length=320).lower()
    if not lowered:
        return True
    return any(marker in lowered for marker in _LOW_VALUE_OUTCOME_MARKERS)


def _directive_key(content: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", content.lower()).strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:18]
    return f"auto:directive:{digest}"


def _derived_memory_key(prefix: str, raw_value: str) -> str:
    digest = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()[:18]
    return f"{prefix}:{digest}"


def _timeline_event_key(session: Session) -> str:
    session_ref = str(session.id or session.legacy_session_id or session.started_at.isoformat())
    return _derived_memory_key("auto:timeline", session_ref)


def _decision_outcome_key(session: Session) -> str:
    session_ref = str(session.id or session.legacy_session_id or session.started_at.isoformat())
    return _derived_memory_key("auto:decision-outcome", session_ref)


def _directive_kind(content: str) -> str:
    lowered = content.lower()
    if any(marker in lowered for marker in ("em dash", "markdown", "emoji", "reply", "replies", "text me", "send me", "voice", "tone", "style", "speak", "talk", "write")):
        return "communication"
    if any(marker in lowered for marker in ("delegate", "tool", "codex", "subagent", "restart", "git", "sql")):
        return "tooling"
    if any(marker in lowered for marker in ("remember", "context", "memory", "persist", "store")):
        return "memory"
    return "behavior"


def _directive_title(content: str) -> str:
    lowered = content.lower()
    if "delegate" in lowered:
        return "Delegate tasks"
    if "em dash" in lowered:
        return "No em dashes"
    if "emoji" in lowered:
        return "Emoji rule"
    if "tone" in lowered or "style" in lowered or "speak" in lowered or "talk" in lowered:
        return "Tone/style rule"
    if "reply" in lowered or "send me" in lowered:
        return "Reply style"
    return "Standing rule"


def _normalize_directive_clause(clause: str) -> str:
    normalized = _collapse_whitespace(clause, max_length=280).strip(" -")
    normalized = _DIRECTIVE_LEAD_IN_RE.sub("", normalized).strip(" -")
    return normalized


def _directive_subclauses(content: str) -> list[str]:
    parts = [content]
    for separator in (",", ":", " - ", " — "):
        refined: list[str] = []
        for part in parts:
            refined.extend(part.split(separator))
        parts = refined
    return [_normalize_directive_clause(part) for part in parts if _normalize_directive_clause(part)]


def _is_directive_clause(clause: str) -> bool:
    lowered = clause.lower()
    if len(clause) < 14:
        return False
    if lowered.startswith(_DIRECTIVE_QUESTION_PREFIXES):
        return False
    if any(marker in lowered for marker in _DIRECTIVE_NON_PERSIST_MARKERS):
        return False
    if any(marker in lowered for marker in _DIRECTIVE_REJECT_MARKERS):
        return False
    if lowered.startswith(("i ", "we ", "my ", "our ")) and not lowered.startswith(
        (
            "if i tell you ",
            "when i tell you ",
            "whenever i tell you ",
            "i want you to ",
            "i prefer ",
            "my preference is ",
        )
    ):
        return False
    if not any(lowered.startswith(prefix) for prefix in _DIRECTIVE_PREFIXES):
        return False
    if not any(marker in lowered for marker in _DIRECTIVE_MARKERS):
        return False
    if not any(hint in lowered for hint in _DIRECTIVE_ACTION_HINTS):
        return False
    if not any(hint in lowered for hint in _DIRECTIVE_TARGET_HINTS):
        return False
    return True


def _extract_directive_clauses(content: str) -> list[str]:
    clauses: list[str] = []
    seen: set[str] = set()
    for raw_clause in _DIRECTIVE_CLAUSE_SPLIT.split(content or ""):
        normalized_raw = _normalize_directive_clause(raw_clause)
        if normalized_raw and _is_directive_clause(normalized_raw):
            key = normalized_raw.lower()
            if key not in seen:
                clauses.append(normalized_raw)
                seen.add(key)
            continue
        for subclause in _directive_subclauses(raw_clause):
            if _is_directive_clause(subclause):
                key = subclause.lower()
                if key not in seen:
                    clauses.append(subclause)
                    seen.add(key)
    return clauses


def _split_summary_sentences(summary: str) -> list[str]:
    return [_collapse_whitespace(chunk, max_length=220) for chunk in _DECISION_SENTENCE_SPLIT.split(summary or "") if _collapse_whitespace(chunk, max_length=220)]


def _meaningful_summary_sentences(summary: str) -> list[str]:
    sentences: list[str] = []
    for sentence in _split_summary_sentences(summary):
        lowered = sentence.lower()
        if lowered.startswith(("user:", "current state:", "key outcomes:", "goals:")):
            continue
        if _looks_like_reference_content(sentence) or _looks_like_operational_content(sentence):
            continue
        if _looks_like_low_value_outcome_text(sentence):
            continue
        if _meaningful_token_count(sentence) < 4:
            continue
        sentences.append(sentence)
    return sentences


def _generic_session_title(title: str | None, platform: str) -> bool:
    normalized = _collapse_whitespace(title or "", max_length=80).lower()
    return normalized in {"", f"{platform} session", "other session", "local session", "telegram session", "whatsapp session"}


def _decision_outcome_status(summary: str) -> DecisionOutcomeStatus:
    lowered = summary.lower()
    has_success = any(marker in lowered for marker in _SUCCESS_OUTCOME_MARKERS)
    has_failure = any(marker in lowered for marker in _FAILURE_OUTCOME_MARKERS)
    if has_success and has_failure:
        return DecisionOutcomeStatus.MIXED
    if has_failure:
        return DecisionOutcomeStatus.FAILURE
    if has_success:
        return DecisionOutcomeStatus.SUCCESS
    return DecisionOutcomeStatus.OPEN


def _decision_outcome_kind(summary: str) -> str:
    lowered = summary.lower()
    if any(marker in lowered for marker in ("memory", "supabase", "session", "directive", "active_state", "timeline")):
        return "memory"
    if any(marker in lowered for marker in ("telegram", "whatsapp", "reply", "voice", "transcrib", "message")):
        return "communication"
    if any(marker in lowered for marker in ("tool", "subagent", "delegate", "codex", "git", "sql", "gateway", "curator", "cron")):
        return "tooling"
    if any(marker in lowered for marker in ("workflow", "process", "approach", "plan", "rollout")):
        return "workflow"
    if any(marker in lowered for marker in ("project", "product", "business", "launch")):
        return "product"
    return "other"


def _timeline_clause_score(sentence: str) -> int:
    lowered = sentence.lower()
    score = _meaningful_token_count(sentence)
    if any(marker in lowered for marker in ("focused on", "building", "designing", "working on", "debugging")):
        score += 3
    if any(marker in lowered for marker in _DECISION_MARKERS):
        score += 2
    if any(marker in lowered for marker in _SUCCESS_OUTCOME_MARKERS + _FAILURE_OUTCOME_MARKERS):
        score += 1
    if any(marker in lowered for marker in ("tested", "confirmed", "checked")):
        score -= 2
    return score


def _clean_timeline_rollup_clause(sentence: str) -> str | None:
    clause = _collapse_whitespace(sentence, max_length=240).strip()
    lowered = clause.lower()
    if lowered.startswith(("goal:", "goals:", "current state:", "key outcomes:")):
        return None
    if lowered.startswith("user:"):
        return None
    if lowered.startswith("user is building "):
        cleaned = _clean_focus_clause(clause[len("user is "):], max_items=4)
        if not cleaned:
            return None
        return cleaned if cleaned.lower().startswith("building ") else f"Building {cleaned}"
    if lowered.startswith("session was "):
        trimmed = clause[len("session was "):].strip()
        return trimmed[:1].upper() + trimmed[1:] if trimmed else None
    if lowered.startswith("this week focused entirely on "):
        cleaned = _clean_focus_clause(clause[len("this week focused entirely on "):], max_items=4)
        return f"Focused on {cleaned}" if cleaned else None
    if lowered.startswith("this week focused on "):
        cleaned = _clean_focus_clause(clause[len("this week focused on "):], max_items=4)
        return f"Focused on {cleaned}" if cleaned else None
    if lowered.startswith("currently focused on:"):
        cleaned = _clean_focus_clause(clause.split(":", 1)[1], max_items=4)
        return f"Focused on {cleaned}" if cleaned else None
    if lowered.startswith("focused on "):
        cleaned = _clean_focus_clause(clause[len("focused on "):], max_items=4)
        return f"Focused on {cleaned}" if cleaned else None
    if lowered.startswith("building "):
        cleaned = _clean_focus_clause(clause[len("building "):], max_items=4)
        return f"Building {cleaned}" if cleaned else None
    if re.search(r"\b\d+\)", clause) and clause.count(",") >= 2:
        return None
    if ":" in clause and clause.count(",") >= 3 and not any(marker in lowered for marker in _SUCCESS_OUTCOME_MARKERS + _FAILURE_OUTCOME_MARKERS):
        return None
    return clause.rstrip(".")


def _timeline_rollup_clauses(sessions: list[Session], *, limit: int = 3) -> list[str]:
    ranked: list[tuple[int, datetime, str]] = []
    seen_keys: set[str] = set()
    for session in sessions:
        for sentence in _meaningful_summary_sentences(str(session.summary or "")):
            cleaned_clause = _clean_timeline_rollup_clause(sentence)
            if not cleaned_clause:
                continue
            if _looks_like_low_value_project_content(cleaned_clause):
                continue
            clause = cleaned_clause
            key = re.sub(r"[^a-z0-9]+", " ", clause.lower()).strip()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            ranked.append((_timeline_clause_score(clause), session.started_at, clause))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [clause for _score, _started_at, clause in ranked[:limit]]


def _render_timeline_rollup_summary(sessions: list[Session]) -> str | None:
    clauses = _timeline_rollup_clauses(sessions)
    if not clauses:
        return None
    summary = f"Across {len(sessions)} sessions: " + ". ".join(clauses)
    if not summary.endswith((".", "!", "?")):
        summary += "."
    return _collapse_whitespace(summary, max_length=360)


def _timeline_rollup_event_key(kind: str, period_key: str) -> str:
    return _derived_memory_key("auto:timeline-rollup", f"{kind}:{period_key}")


def _timeline_day_key(session: Session) -> str:
    return session.started_at.astimezone(timezone.utc).date().isoformat()


def _pattern_window_key(session: Session, *, hours: int = 8) -> str:
    started_at = session.started_at.astimezone(timezone.utc)
    bucket = started_at.hour // max(1, hours)
    return f"{started_at.date().isoformat()}:{bucket}"


def _timeline_week_key(session: Session) -> str:
    started_at = session.started_at.astimezone(timezone.utc)
    week_start = started_at.date() - timedelta(days=started_at.weekday())
    return week_start.isoformat()


def _timeline_rollup_title(kind: str, period_key: str) -> str:
    if kind == "day_summary":
        return f"Day summary for {period_key}"
    return f"Week of {period_key}"


def _timeline_rollup_importance(kind: str, sessions: list[Session]) -> float:
    total_messages = sum(max(int(session.message_count or 0), 0) for session in sessions)
    importance = 0.64 if kind == "day_summary" else 0.7
    importance += min(0.16, total_messages / 160.0)
    if any(session.dominant_emotions for session in sessions):
        importance += 0.04
    return min(1.0, importance)


def _timeline_rollup_source_episode_ids(episodes_by_session: dict[str, list[Episode]], sessions: list[Session]) -> list[str]:
    source_ids: list[str] = []
    for session in sessions:
        for episode in episodes_by_session.get(str(session.id), []):
            if episode.id is None:
                continue
            if _looks_like_reference_content(episode.content) or _looks_like_operational_content(episode.content):
                continue
            source_ids.append(str(episode.id))
            if len(source_ids) >= 12:
                return sorted(set(source_ids))
    return sorted(set(source_ids))


def _should_emit_day_rollup(sessions: list[Session]) -> bool:
    return len(sessions) >= _TIMELINE_DAY_MIN_SESSION_COUNT and sum(max(int(session.message_count or 0), 0) for session in sessions) >= _TIMELINE_DAY_MIN_MESSAGE_COUNT


def _should_emit_week_rollup(sessions: list[Session]) -> bool:
    distinct_days = {_timeline_day_key(session) for session in sessions}
    total_messages = sum(max(int(session.message_count or 0), 0) for session in sessions)
    return (
        len(sessions) >= _TIMELINE_WEEK_MIN_SESSION_COUNT
        and len(distinct_days) >= _TIMELINE_WEEK_MIN_DISTINCT_DAYS
        and total_messages >= _TIMELINE_WEEK_MIN_MESSAGE_COUNT
    )


def _grounding_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if token not in _GROUNDING_STOPWORDS and len(token) > 2
    }


def _visible_grounding_episodes(episodes: list[Episode]) -> list[Episode]:
    return [
        episode
        for episode in episodes
        if episode.role in {EpisodeRole.USER, EpisodeRole.ASSISTANT}
        if str(episode.content or "").strip()
        if not _looks_like_reference_content(episode.content)
        if not _looks_like_operational_content(episode.content)
    ]


def _episode_grounding_hits(text: str, episodes: list[Episode]) -> int:
    tokens = _grounding_tokens(text)
    if len(tokens) < 2:
        return 0
    hits = 0
    required_overlap = 1 if len(tokens) <= 4 else 2
    for episode in episodes:
        overlap = len(tokens & _grounding_tokens(episode.content))
        if overlap >= required_overlap:
            hits += 1
    return hits


def _split_inline_outcome(sentence: str) -> tuple[str, str] | None:
    lowered = sentence.lower()
    if not any(marker in lowered for marker in _DECISION_MARKERS):
        return None
    for marker in _OUTCOME_SPLIT_MARKERS:
        index = lowered.find(marker)
        if index <= 0:
            continue
        decision = _collapse_whitespace(sentence[:index], max_length=180).rstrip(" ,;:-")
        outcome = _collapse_whitespace(sentence[index + 1 :], max_length=180).rstrip(" ,;:-")
        if decision and outcome and decision != outcome:
            return decision, outcome[0].upper() + outcome[1:] if outcome else outcome
    return None


def _normalize_decision_candidate(sentence: str) -> str:
    normalized = _collapse_whitespace(sentence, max_length=180).strip(" ,;:-")
    return normalized[:1].upper() + normalized[1:] if normalized else normalized


def _normalize_outcome_candidate(sentence: str) -> str:
    normalized = _collapse_whitespace(sentence, max_length=180).strip(" ,;:-")
    lowered = normalized.lower()
    for prefix in _OUTCOME_RESULT_PREFIXES:
        if lowered.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
            break
    return normalized[:1].upper() + normalized[1:] if normalized else normalized


def _contains_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _decision_sentence_score(sentence: str) -> int:
    lowered = sentence.lower()
    score = _meaningful_token_count(sentence)
    if _contains_any_marker(sentence, _STRONG_DECISION_MARKERS):
        score += 5
    if _contains_any_marker(sentence, _WEAK_DECISION_MARKERS):
        score -= 2
    if _contains_any_marker(sentence, _OUTCOME_SPLIT_MARKERS):
        score += 4
    return score


def _outcome_sentence_score(sentence: str) -> int:
    lowered = sentence.lower()
    score = _meaningful_token_count(sentence)
    if lowered.startswith(_OUTCOME_RESULT_PREFIXES):
        score += 2
    if _contains_any_marker(sentence, _SUCCESS_OUTCOME_MARKERS + _FAILURE_OUTCOME_MARKERS):
        score += 3
    if _contains_any_marker(sentence, _OUTCOME_CAUSAL_MARKERS):
        score += 2
    if lowered.startswith(_OUTCOME_GENERIC_OPENERS):
        score -= 5
    return score


def _decision_sentence(summary: str) -> str | None:
    sentences = _meaningful_summary_sentences(summary)
    candidates = [
        sentence
        for sentence in sentences
        if _contains_any_marker(sentence, _DECISION_MARKERS)
    ]
    if not candidates:
        return None
    ranked = sorted(candidates, key=_decision_sentence_score, reverse=True)
    best = ranked[0]
    return best if _decision_sentence_score(best) >= 7 else None


def _outcome_sentence(summary: str, decision_sentence: str) -> str | None:
    sentences = _meaningful_summary_sentences(summary)
    candidates = []
    for sentence in sentences:
        if sentence == decision_sentence:
            continue
        lowered = sentence.lower()
        if lowered.startswith(_OUTCOME_RESULT_PREFIXES) or _contains_any_marker(sentence, _SUCCESS_OUTCOME_MARKERS + _FAILURE_OUTCOME_MARKERS):
            candidates.append(sentence)
    if not candidates:
        return None
    ranked = sorted(candidates, key=_outcome_sentence_score, reverse=True)
    best = ranked[0]
    return best if _outcome_sentence_score(best) >= 7 else None


def _decision_outcome_is_specific(decision: str, outcome: str) -> bool:
    decision_tokens = _grounding_tokens(decision)
    outcome_tokens = _grounding_tokens(outcome)
    if len(decision_tokens) < 3 or len(outcome_tokens) < 3:
        return False
    if _contains_any_marker(outcome, _OUTCOME_GENERIC_OPENERS):
        return False
    if _contains_any_marker(decision, _WEAK_DECISION_MARKERS) and not _contains_any_marker(outcome, _OUTCOME_CAUSAL_MARKERS + _SUCCESS_OUTCOME_MARKERS + _FAILURE_OUTCOME_MARKERS):
        return False
    return True


def _grounded_episode_ids(text: str, episodes: list[Episode]) -> set[str]:
    tokens = _grounding_tokens(text)
    if len(tokens) < 2:
        return set()
    required_overlap = 1 if len(tokens) <= 4 else 2
    matched: set[str] = set()
    for index, episode in enumerate(episodes):
        overlap = len(tokens & _grounding_tokens(episode.content))
        if overlap >= required_overlap:
            episode_id = str(episode.id) if episode.id is not None else f"episode:{index}"
            matched.add(episode_id)
    return matched


def _grounded_episode_roles(text: str, episodes: list[Episode]) -> set[EpisodeRole]:
    tokens = _grounding_tokens(text)
    if len(tokens) < 2:
        return set()
    required_overlap = 1 if len(tokens) <= 4 else 2
    roles: set[EpisodeRole] = set()
    for episode in episodes:
        overlap = len(tokens & _grounding_tokens(episode.content))
        if overlap >= required_overlap:
            roles.add(episode.role)
    return roles


def _extract_decision_outcome(summary: str) -> tuple[str, str] | None:
    decision = _decision_sentence(summary)
    if not decision:
        return None
    inline = _split_inline_outcome(decision)
    if inline is not None:
        normalized_decision = _normalize_decision_candidate(inline[0])
        normalized_outcome = _normalize_outcome_candidate(inline[1])
        if not _decision_outcome_is_specific(normalized_decision, normalized_outcome):
            return None
        return normalized_decision, normalized_outcome
    outcome = _outcome_sentence(summary, decision)
    if not outcome:
        return None
    normalized_decision = _normalize_decision_candidate(decision)
    normalized_outcome = _normalize_outcome_candidate(outcome)
    if _meaningful_token_count(normalized_decision) < 4 or _meaningful_token_count(normalized_outcome) < 4:
        return None
    if normalized_decision == normalized_outcome:
        return None
    if not _decision_outcome_is_specific(normalized_decision, normalized_outcome):
        return None
    return normalized_decision, normalized_outcome


def _decision_outcome_is_grounded(decision: str, outcome: str, episodes: list[Episode]) -> bool:
    grounding_episodes = _visible_grounding_episodes(episodes)
    if not grounding_episodes:
        return False
    decision_hits = _episode_grounding_hits(decision, grounding_episodes)
    outcome_hits = _episode_grounding_hits(outcome, grounding_episodes)
    combined_hits = _episode_grounding_hits(f"{decision} {outcome}", grounding_episodes)
    grounded_episode_ids = (
        _grounded_episode_ids(decision, grounding_episodes)
        | _grounded_episode_ids(outcome, grounding_episodes)
        | _grounded_episode_ids(f"{decision} {outcome}", grounding_episodes)
    )
    grounded_roles = (
        _grounded_episode_roles(decision, grounding_episodes)
        | _grounded_episode_roles(outcome, grounding_episodes)
        | _grounded_episode_roles(f"{decision} {outcome}", grounding_episodes)
    )
    return (
        decision_hits >= 1
        and (outcome_hits >= 1 or combined_hits >= 2)
        and len(grounded_episode_ids) >= 2
        and len(grounded_roles) >= 2
    )


def _decision_outcome_lesson(decision: str, outcome: str, status: DecisionOutcomeStatus) -> str | None:
    lowered = " ".join([decision, outcome]).lower()
    if ("auto-inject" in lowered or "injected" in lowered or "system prompt" in lowered) and any(marker in lowered for marker in ("intent", "preference", "user")):
        return "Past prompt mechanics should not be treated as current user intent."
    if "voice" in lowered and ("stored" in lowered or "transcrib" in lowered or "transcript" in lowered):
        return "Convert voice turns into visible transcript text before storing them as durable conversation memory."
    if any(marker in lowered for marker in ("directive", "standing rule", "standing rules")):
        return "Keep standing operating rules in durable directives instead of relying on session context."
    if any(marker in lowered for marker in ("persist", "stored", "store", "transcript")) and any(marker in lowered for marker in ("visible", "surfaced", "reply", "assistant text", "user-facing")):
        return "Persist only content that was actually visible in the conversation."
    if any(marker in lowered for marker in ("gateway", "boundary", "write path", "routing path")) and any(marker in lowered for marker in ("write", "stored", "state", "transcript", "leaking", "leak")):
        return "Fix the boundary that actually writes state before widening the redesign."
    if any(marker in lowered for marker in ("timeout", "timed out", "cold", "startup", "restart")) and any(marker in lowered for marker in ("bridge", "worker", "subprocess", "spawn")):
        return "Keep hot-path reads on a warm worker instead of spawning fresh processes per request."
    if any(marker in lowered for marker in ("route", "routing", "session recovery", "session restore", "bootstrap")) and any(marker in lowered for marker in ("restart", "reconnect", "startup", "worker", "bridge")):
        return "Make restart-time session recovery use the same durable path as live request routing."
    return None


def _decision_outcome_title(session: Session, summary: str) -> str:
    if not _generic_session_title(session.title, session.platform.value):
        return _collapse_whitespace(session.title or "", max_length=80)
    kind = _decision_outcome_kind(summary)
    if kind == "memory":
        return "Memory outcome"
    if kind == "communication":
        return "Delivery outcome"
    if kind == "tooling":
        return "Tooling outcome"
    if kind == "workflow":
        return "Workflow outcome"
    if kind == "product":
        return "Project outcome"
    return "Outcome"


def _summary_preference_supported(summary: str, episodes: list[Episode]) -> bool:
    match = _SUMMARY_PREFERENCE_RE.search(summary or "")
    if not match:
        return True

    preference = _collapse_whitespace(match.group(1), max_length=120).strip(" ,:-.").lower()
    if not preference:
        return True

    preference_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", preference)
        if len(token) > 1
    }
    user_episode_texts = [
        (episode.content or "").lower()
        for episode in episodes
        if episode.role == EpisodeRole.USER and episode.content
    ]
    if any(preference in text for text in user_episode_texts):
        return True

    min_overlap = max(1, min(2, len(preference_tokens)))
    for text in user_episode_texts:
        if "prefer" not in text and "like" not in text:
            continue
        text_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", text)
            if len(token) > 1
        }
        overlap = 0
        for token in preference_tokens:
            if token in text_tokens:
                overlap += 1
        if overlap >= min_overlap:
            return True

    return False


def _lowered(values: list[str]) -> list[str]:
    return [value.lower() for value in values]


def _matches_markers(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _marker_hits(text: str, markers: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for marker in markers if marker in lowered)


def _pattern_candidate_from_sessions(
    sessions: list[Session],
    episodes_by_session: dict[str, list[Episode]],
    spec: dict[str, Any],
) -> dict[str, Any] | None:
    matched_session_ids: list[str] = []
    supporting_episode_ids: list[str] = []
    counterexample_episode_ids: list[str] = []
    distinct_days: set[str] = set()
    distinct_windows: set[str] = set()
    support_score = 0.0
    first_observed: datetime | None = None
    last_observed: datetime | None = None
    summary_supported_sessions = 0
    episode_supported_sessions = 0
    total_summary_hits = 0
    total_episode_hits = 0
    counterexample_sessions = 0

    for session in sessions:
        summary = _collapse_whitespace(str(session.summary or ""), max_length=400)
        summary_hits = 0
        if summary and not _looks_like_reference_content(summary) and not _looks_like_operational_content(summary):
            summary_hits = _marker_hits(summary, spec["summary_markers"])
        session_match = summary_hits > 0
        session_supporting_episode_ids: list[str] = []
        session_counterexample_ids: list[str] = []
        session_episode_hits = 0
        for episode in episodes_by_session.get(str(session.id), []):
            if episode.role != EpisodeRole.USER:
                continue
            if _looks_like_reference_content(episode.content) or _looks_like_operational_content(episode.content):
                continue
            episode_hits = _marker_hits(episode.content, spec["episode_markers"])
            if episode_hits > 0:
                if episode.id is not None:
                    session_supporting_episode_ids.append(str(episode.id))
                session_episode_hits += episode_hits
                if first_observed is None or episode.message_timestamp < first_observed:
                    first_observed = episode.message_timestamp
                if last_observed is None or episode.message_timestamp > last_observed:
                    last_observed = episode.message_timestamp
            elif spec.get("counter_markers") and _matches_markers(episode.content, tuple(spec["counter_markers"])):
                if episode.id is not None:
                    session_counterexample_ids.append(str(episode.id))
        if session_match or session_supporting_episode_ids:
            if session.id is not None:
                matched_session_ids.append(str(session.id))
            distinct_days.add(_timeline_day_key(session))
            distinct_windows.add(_pattern_window_key(session))
            if session_match:
                summary_supported_sessions += 1
                total_summary_hits += summary_hits
                support_score += min(2.2, summary_hits * 0.75)
            if session_supporting_episode_ids:
                episode_supported_sessions += 1
                total_episode_hits += session_episode_hits
            support_score += min(
                2.6,
                (len(set(session_supporting_episode_ids)) * 0.8) + (session_episode_hits * 0.12),
            )
            if session_match and session_supporting_episode_ids:
                support_score += 0.45
            supporting_episode_ids.extend(session_supporting_episode_ids)
            if first_observed is None or session.started_at < first_observed:
                first_observed = session.started_at
            if last_observed is None or session.started_at > last_observed:
                last_observed = session.started_at
        if session_counterexample_ids:
            counterexample_sessions += 1
        counterexample_episode_ids.extend(session_counterexample_ids)

    unique_episode_ids = sorted(set(supporting_episode_ids))
    unique_session_ids = sorted(set(matched_session_ids))
    if len(unique_session_ids) < int(spec["min_sessions"]):
        return None
    if len(unique_episode_ids) < int(spec["min_episodes"]):
        return None
    if episode_supported_sessions < int(spec.get("min_episode_sessions", 1)):
        return None
    if total_episode_hits < int(spec.get("min_episode_hits", max(2, int(spec.get("min_episode_sessions", 1))))):
        return None
    if support_score < float(spec.get("min_support_score", 0.0)):
        return None
    if first_observed is None or last_observed is None:
        return None
    min_summary_sessions = int(spec.get("min_summary_sessions", 0))
    has_summary_support = summary_supported_sessions >= min_summary_sessions
    if not has_summary_support and min_summary_sessions > 0:
        if len(unique_session_ids) < int(spec.get("episode_only_min_sessions", max(int(spec["min_sessions"]) + 1, 3))):
            return None
        if support_score < float(spec.get("episode_only_min_support_score", float(spec.get("min_support_score", 0.0)) + 0.8)):
            return None
    min_distinct_days = int(spec.get("min_distinct_days", 1))
    if len(distinct_days) < min_distinct_days:
        min_distinct_windows = int(spec.get("min_distinct_windows", min_distinct_days))
        if len(distinct_windows) < min_distinct_windows:
            if not spec.get("allow_single_day_cluster"):
                return None
            if len(unique_session_ids) < int(spec.get("single_day_cluster_min_sessions", max(int(spec["min_sessions"]) + 2, 4))):
                return None
            if episode_supported_sessions < int(spec.get("single_day_cluster_min_episode_sessions", max(int(spec.get("min_episode_sessions", 1)) + 1, 3))):
                return None
            if support_score < float(spec.get("single_day_cluster_min_support_score", float(spec.get("min_support_score", 0.0)) + 1.2)):
                return None
        else:
            if len(unique_session_ids) < int(spec.get("episode_only_min_sessions", max(int(spec["min_sessions"]) + 1, 3))):
                return None
            if support_score < float(spec.get("episode_only_min_support_score", float(spec.get("min_support_score", 0.0)) + 0.8)):
                return None
    if has_summary_support:
        if total_summary_hits < int(spec.get("min_summary_hits", max(1, min_summary_sessions))):
            return None
    unique_counterexample_ids = sorted(set(counterexample_episode_ids))
    if len(unique_counterexample_ids) >= len(unique_episode_ids):
        return None
    if unique_episode_ids and (len(unique_counterexample_ids) / len(unique_episode_ids)) > float(spec.get("max_counterexample_ratio", 1.0)):
        return None

    frequency_score = min(1.0, (len(unique_session_ids) * 0.18) + (len(unique_episode_ids) * 0.07) + (len(distinct_days) * 0.08))
    confidence = min(
        0.96,
        0.5
        + (len(unique_session_ids) * 0.08)
        + (len(unique_episode_ids) * 0.03)
        + (len(distinct_days) * 0.05)
        + (summary_supported_sessions * 0.03)
        + (episode_supported_sessions * 0.03)
        + (min(support_score, 6.0) * 0.02)
        - (len(unique_counterexample_ids) * 0.03)
        - (counterexample_sessions * 0.02),
    )
    if len(distinct_days) >= 2:
        evidence_suffix = f" Evidence repeats across {len(unique_session_ids)} sessions and {len(distinct_days)} distinct days."
    elif len(distinct_windows) >= 2:
        evidence_suffix = f" Evidence repeats across {len(unique_session_ids)} sessions over {len(distinct_windows)} separate time windows."
    else:
        evidence_suffix = f" Evidence repeats across {len(unique_session_ids)} sessions during a concentrated incident cluster."
    description = spec["description"] + evidence_suffix

    return {
        "pattern_key": spec["pattern_key"],
        "pattern_type": spec["pattern_type"],
        "statement": spec["statement"],
        "description": description,
        "confidence": confidence,
        "frequency_score": frequency_score,
        "impact_score": float(spec["impact_score"]),
        "first_observed_at": first_observed,
        "last_observed_at": last_observed,
        "supporting_episode_ids": unique_episode_ids,
        "supporting_session_ids": unique_session_ids,
        "counterexample_episode_ids": unique_counterexample_ids,
        "tags": ["derived", "pattern", spec["pattern_type"]],
    }


def _commitment_key(content: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", content.lower()).strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:18]
    return f"auto:commitment:{digest}"


def _correction_key(content: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", content.lower()).strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:18]
    return f"auto:correction:{digest}"


def _commitment_kind(content: str) -> str:
    lowered = content.lower()
    if any(marker in lowered for marker in ("remind", "remember")):
        return "reminder"
    if any(marker in lowered for marker in ("track", "keep an eye", "follow up")):
        return "tracking"
    if any(marker in lowered for marker in ("fix", "debug", "restart", "patch", "verify", "check")):
        return "fix"
    if any(marker in lowered for marker in ("message", "send", "text", "tell")):
        return "message"
    return "follow_up"


def _is_commitment_sentence(sentence: str) -> bool:
    lowered = sentence.lower().strip()
    if len(lowered) < 12:
        return False
    if not any(lowered.startswith(marker) for marker in _COMMITMENT_MARKERS):
        return False
    if any(marker in lowered for marker in _COMMITMENT_STRONG_HINTS):
        return True
    return any(marker in lowered for marker in _COMMITMENT_ACTION_MARKERS)


def _extract_commitment_sentences(content: str) -> list[str]:
    sentences = []
    for raw in _COMMITMENT_SENTENCE_SPLIT.split(content or ""):
        sentence = _collapse_whitespace(raw, max_length=220)
        if _is_commitment_sentence(sentence):
            sentences.append(sentence)
    return sentences


def _commitment_status_from_sentence(sentence: str) -> CommitmentStatus:
    lowered = sentence.lower()
    if any(marker in lowered for marker in _COMMITMENT_COMPLETION_MARKERS):
        return CommitmentStatus.COMPLETED
    return CommitmentStatus.OPEN


def _commitment_is_stale(kind: str, last_observed_at: datetime, *, reference_time: datetime) -> bool:
    max_age_hours = _COMMITMENT_MAX_AGE_HOURS.get(str(kind), 24)
    observed_at = last_observed_at
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    else:
        observed_at = observed_at.astimezone(timezone.utc)
    age = reference_time - observed_at
    return age > timedelta(hours=max_age_hours)


def _correction_kind(content: str) -> str:
    lowered = content.lower()
    if (
        "what do u mean" in lowered
        or "what do you mean" in lowered
        or "did not understand" in lowered
        or "didn't understand" in lowered
        or "dont understand" in lowered
        or "don't understand" in lowered
        or lowered.startswith("no no")
    ):
        return CorrectionKind.SCOPE_CLARIFICATION.value
    if (
        "remember" in lowered
        or "memory" in lowered
        or "never said" in lowered
        or "never told you" in lowered
        or "that's wrong" in lowered
        or "thats wrong" in lowered
        or "who is telling you" in lowered
    ):
        return CorrectionKind.MEMORY_DISPUTE.value
    if "directive" in lowered or "rule" in lowered:
        return CorrectionKind.DIRECTIVE_CLARIFICATION.value
    if "infer" in lowered or "assume" in lowered or "why are you doing" in lowered:
        return CorrectionKind.INTERPRETATION_REJECTION.value
    if "scope" in lowered or "not that" in lowered:
        return CorrectionKind.SCOPE_CLARIFICATION.value
    return CorrectionKind.FACT_CORRECTION.value


def _looks_like_correction(content: str) -> bool:
    lowered = content.lower()
    stripped = lowered.lstrip()
    if stripped.startswith("- ") and not any(marker in lowered for marker in _CORRECTION_STRONG_MARKERS):
        return False
    if any(marker in lowered for marker in _CORRECTION_MARKERS):
        return True
    return lowered.startswith("no no") and (
        "you" in lowered
        or "u " in lowered
        or "mean" in lowered
        or "understand" in lowered
    )


def _extract_correction_target(content: str) -> str | None:
    lowered = content.lower()
    quoted_matches = re.findall(r"[\"“']([^\"”']{3,180})[\"”']", content)
    if quoted_matches:
        quoted = _collapse_whitespace(max(quoted_matches, key=len), max_length=220).strip(" ,:-")
        if quoted:
            return quoted

    targeted_patterns = (
        r"what do (?:u|you) mean by\s+[\"“']?(.+?)[\"”']?$",
        r"who is telling you to\s+(.+?)(?:[.!?]|$)",
        r"why are you doing\s+(.+?)(?:[.!?]|$)",
        r"i never told you to\s+(.+?)(?:[.!?]|$)",
    )
    for pattern in targeted_patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if not match:
            continue
        start, end = match.span(1)
        candidate = _collapse_whitespace(content[start:end], max_length=220).strip(" ,:-")
        if candidate:
            return candidate

    for marker in _CORRECTION_MARKERS:
        if marker in lowered:
            marker_index = lowered.find(marker)
            suffix = content[marker_index + len(marker):]
            candidate = _collapse_whitespace(suffix, max_length=220).strip(" ,:-")
            return candidate or None
    if lowered.startswith("no no"):
        candidate = _collapse_whitespace(content[5:], max_length=220).strip(" ,:-")
        return candidate or None
    return None


async def _list_recent_candidate_sessions(
    client: MemoryClient,
    *,
    since: datetime,
    limit: int,
    agent_namespace: str | None,
) -> list[Session]:
    transport = client.transport
    list_sessions = getattr(transport, "list_sessions", None)
    if callable(list_sessions):
        sessions = await list_sessions(limit=limit, platform=None, agent_namespace=agent_namespace)
        return [
            session
            for session in sessions
            if session.started_at >= since and _agent_namespace_matches(session.agent_namespace, agent_namespace)
        ]

    sessions_store = getattr(transport, "sessions", None)
    if isinstance(sessions_store, dict):
        sessions = [value for value in sessions_store.values() if isinstance(value, Session)]
        sessions = sorted(sessions, key=lambda item: item.started_at, reverse=True)
        return [
            session
            for session in sessions[:limit]
            if session.started_at >= since and _agent_namespace_matches(session.agent_namespace, agent_namespace)
        ]

    raise RuntimeError("Transport does not support loading recent sessions for directive extraction.")


def _sort_recent(value: datetime | None) -> datetime:
    return value or datetime.min.replace(tzinfo=timezone.utc)


def _fact_sort_key(fact: Fact) -> tuple[datetime, float]:
    return (_sort_recent(fact.updated_at or fact.created_at), float(fact.confidence))


def _recent_user_episodes(episodes: list[Episode]) -> list[Episode]:
    return sorted(
        [
            episode
            for episode in episodes
            if episode.role == EpisodeRole.USER and str(episode.content or "").strip()
        ],
        key=lambda episode: episode.message_timestamp,
        reverse=True,
    )


def _pick_blocker_content(episodes: list[Episode], sessions: list[Session]) -> str | None:
    for episode in episodes:
        lowered = episode.content.lower()
        if any(marker in lowered for marker in _BLOCKER_MARKERS):
            candidate = _collapse_whitespace(episode.content)
            if not _looks_like_low_value_state_text(candidate):
                return candidate
    return None


def _pick_open_loop_content(episodes: list[Episode], sessions: list[Session]) -> str | None:
    for episode in episodes:
        lowered = episode.content.lower()
        if any(marker in lowered for marker in _OPEN_LOOP_MARKERS):
            candidate = _collapse_whitespace(episode.content)
            if not _looks_like_low_value_state_text(candidate):
                return candidate
    return None


def _session_focus_sentence(summary: str) -> str | None:
    def _score(sentence: str) -> int:
        lowered = sentence.lower()
        score = 0
        if any(marker in lowered for marker in ("currently focused on", "focused on", "building", "designing", "trying to", "working on", "wants memory", "goal:")):
            score += 4
        if any(marker in lowered for marker in ("alive 24/7", "heartbeat", "continuous", "human", "magic", "active state", "retrieval layer")):
            score += 3
        if any(marker in lowered for marker in ("tested", "confirmed", "verified", "checked", "looked up", "session_search functional", "system is working")):
            score -= 4
        return score

    candidates: list[tuple[int, str]] = []
    for sentence in _split_summary_sentences(summary):
        lowered = sentence.lower()
        if lowered.startswith(("currently focused on:", "key outcomes:", "current state:", "user:")):
            continue
        if _looks_like_low_value_project_content(sentence) or _looks_like_low_value_state_text(sentence):
            continue
        if _meaningful_token_count(sentence) < 5:
            continue
        candidates.append((_score(sentence), sentence))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    best_score, best_sentence = candidates[0]
    if best_score < 0:
        return None
    return best_sentence


def _clean_focus_clause(value: str, *, max_items: int = 3) -> str | None:
    clause = _collapse_whitespace(value, max_length=240).strip(" -:.")
    if not clause:
        return None
    clause = re.sub(r'^\d+\)\s*', '', clause)
    clause = re.sub(
        r'^(?:goals?|currently focused on|this week focused(?: entirely)? on|focused on|building)\s*:?\s*',
        '',
        clause,
        flags=re.IGNORECASE,
    )
    if ":" in clause:
        head, tail = clause.split(":", 1)
        if _meaningful_token_count(head) <= 2 or head.strip().lower() in {"memory", "project", "projects", "priority", "current priority"}:
            clause = tail.strip(" -:.")
    parts = [part.strip(" -:.") for part in re.split(r",|;|\band\b", clause) if part.strip(" -:.")]
    filtered: list[str] = []
    for part in parts:
        lowered = part.lower()
        if _looks_like_low_value_state_text(part) or _looks_like_low_value_project_content(part):
            continue
        if lowered in {"memory", "this week", "goals", "goal"}:
            continue
        filtered.append(part)
        if len(filtered) >= max_items:
            break
    if filtered:
        return ", ".join(filtered)
    if _meaningful_token_count(clause) >= 3 and not _looks_like_low_value_project_content(clause):
        return clause
    return None


def _extract_summary_clause(summary: str, patterns: list[tuple[str, str]]) -> str | None:
    for pattern, prefix in patterns:
        match = re.search(pattern, summary, re.IGNORECASE)
        if not match:
            continue
        clause = _clean_focus_clause(match.group(1))
        if not clause:
            continue
        return f"{prefix}{clause}".strip()
    return None


def _session_project_sentence(summary: str) -> str | None:
    explicit = _extract_summary_clause(
        summary,
        [
            (r"\bbuilding\s+([^.]+)", "Building "),
            (r"\bcurrently focused on:\s*([^.]+)", ""),
            (r"\bthis week focused(?: entirely)? on\s+([^.]+)", "Focused on "),
        ],
    )
    if explicit:
        return explicit
    return _session_focus_sentence(summary)


def _session_priority_sentence(summary: str) -> str | None:
    explicit = _extract_summary_clause(
        summary,
        [
            (r"\bcurrently focused on:\s*([^.]+)", ""),
            (r"\bthis week focused(?: entirely)? on\s+([^.]+)", "Focused on "),
            (r"\bgoals:\s*([^.]+)", ""),
        ],
    )
    if explicit:
        return explicit
    return _session_focus_sentence(summary)


def _pick_project_focus_content(
    project_facts: list[Fact],
    sessions: list[Session],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    primary: dict[str, Any] | None = None
    secondary: dict[str, Any] | None = None

    for session in sessions:
        summary = str(session.summary or "")
        sentence = _session_project_sentence(summary)
        if not sentence:
            continue
        primary = {
            "kind": "project",
            "state_key": "auto:project:primary",
            "title": "Primary focus",
            "content": sentence,
            "confidence": 0.74,
            "priority_score": 1.0,
            "source_episode_ids": [],
            "source_session_ids": [str(session.id)] if session.id is not None else [],
            "supporting_fact_ids": [],
            "tags": ["derived", "session-backed", "project"],
            "last_observed_at": _sort_recent(session.started_at),
        }
        break

    for fact in project_facts:
        content = _collapse_whitespace(fact.content)
        if _looks_like_low_value_project_content(content):
            continue
        payload = {
            "kind": "project",
            "state_key": "auto:project:secondary" if primary is not None else "auto:project:primary",
            "title": "Secondary focus" if primary is not None else "Primary focus",
            "content": content,
            "confidence": max(0.55, float(fact.confidence)),
            "priority_score": 0.8 if primary is not None else 1.0,
            "source_episode_ids": [str(value) for value in fact.source_episode_ids],
            "source_session_ids": [],
            "supporting_fact_ids": [str(fact.id)] if fact.id is not None else [],
            "tags": list(dict.fromkeys(["derived", "fact-backed", *fact.tags])),
            "last_observed_at": _sort_recent(fact.updated_at or fact.created_at),
        }
        if primary is None:
            primary = payload
        else:
            secondary = payload
            break

    return primary, secondary


def _pick_priority_content(
    project_facts: list[Fact],
    goal_facts: list[Fact],
    sessions: list[Session],
    recent_user_messages: list[Episode],
) -> tuple[str | None, list[str], list[str]]:
    for session in sessions:
        if session.summary:
            candidate = _session_priority_sentence(str(session.summary or ""))
            if candidate and not _looks_like_low_value_state_text(candidate):
                return candidate, [], [str(session.id)] if session.id is not None else []
    if recent_user_messages:
        episode = recent_user_messages[0]
        candidate = _collapse_whitespace(episode.content)
        if not _looks_like_low_value_state_text(candidate):
            return candidate, [], []
    if goal_facts:
        fact = goal_facts[0]
        candidate = _collapse_whitespace(fact.content)
        if not _looks_like_low_value_state_text(candidate) and not candidate.lower().startswith(("user wants to ", "user wants ")):
            return candidate, [str(fact.id)] if fact.id is not None else [], []
    if project_facts:
        fact = project_facts[0]
        candidate = _collapse_whitespace(fact.content)
        if not _looks_like_low_value_project_content(candidate):
            return candidate, [str(fact.id)] if fact.id is not None else [], []
    return None, [], []


def _pick_emotion_state(episodes: list[Episode], sessions: list[Session]) -> tuple[str | None, list[str]]:
    weighted = Counter()
    supporting_episode_ids: list[str] = []
    for episode in episodes:
        if not episode.dominant_emotion:
            continue
        intensity = max(float(episode.emotional_intensity or 0.0), 0.25)
        weighted[episode.dominant_emotion] += intensity
        if episode.id is not None and len(supporting_episode_ids) < 4:
            supporting_episode_ids.append(str(episode.id))
    if weighted:
        top = [name for name, _score in weighted.most_common(2)]
        if len(top) == 1:
            return f"Leaning toward {top[0]} lately.", supporting_episode_ids
        return f"Leaning toward {top[0]} and {top[1]} lately.", supporting_episode_ids
    for session in sessions:
        if session.dominant_emotions:
            top = session.dominant_emotions[:2]
            if len(top) == 1:
                return f"Leaning toward {top[0]} lately.", []
            return f"Leaning toward {top[0]} and {top[1]} lately.", []
    return None, []


async def refresh_active_state(
    client: MemoryClient,
    *,
    lookback_hours: int = ACTIVE_STATE_LOOKBACK_HOURS,
    min_message_count: int = 2,
    include_unsummarized: bool = False,
    now: datetime | None = None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    reference_time = now or _utcnow()
    since = reference_time - timedelta(hours=lookback_hours)
    if include_unsummarized:
        recent_sessions = await _list_recent_candidate_sessions(
            client,
            since=since,
            limit=max(ACTIVE_STATE_SESSION_LIMIT * 4, 24),
            agent_namespace=agent_namespace,
        )
        recent_sessions = [
            session
            for session in recent_sessions
            if int(session.message_count or 0) >= min_message_count
        ]
    else:
        recent_sessions = await _list_recent_summarized_sessions(
            client,
            since=since,
            min_message_count=min_message_count,
            agent_namespace=agent_namespace,
        )

    def _is_meaningful_summary(session: Session) -> bool:
        if not session.summary:
            return include_unsummarized
        summary_text = str(session.summary or "")
        return not _looks_like_operational_content(summary_text) and not _looks_like_reference_content(summary_text)

    recent_sessions = [
        session
        for session in recent_sessions
        if session.platform.value != "other"
        if _is_meaningful_summary(session)
    ]
    recent_sessions = sorted(recent_sessions, key=lambda session: session.started_at, reverse=True)[:ACTIVE_STATE_SESSION_LIMIT]
    active_facts = await client.search_facts(limit=ACTIVE_STATE_FACT_LIMIT, agent_namespace=agent_namespace)

    recent_user_messages: list[Episode] = []
    source_session_ids: list[str] = []
    for session in recent_sessions:
        if session.id is not None:
            source_session_ids.append(str(session.id))
        episodes = await _list_session_episodes(client, str(session.id))
        recent_user_messages.extend(_recent_user_episodes(episodes))

    recent_user_messages = sorted(
        recent_user_messages,
        key=lambda episode: episode.message_timestamp,
        reverse=True,
    )[:ACTIVE_STATE_EPISODE_LIMIT]

    project_facts = sorted(
        [
            fact
            for fact in active_facts
            if fact.category == FactCategory.PROJECT and fact.is_active
            if not _looks_like_low_value_project_content(fact.content)
            if not _looks_like_implementation_project_content(fact.content, fact.tags)
        ],
        key=_fact_sort_key,
        reverse=True,
    )
    goal_facts = sorted(
        [
            fact
            for fact in active_facts
            if fact.category == FactCategory.GOAL and fact.is_active
            if not _looks_like_low_value_state_text(fact.content)
        ],
        key=_fact_sort_key,
        reverse=True,
    )

    desired_states: list[dict[str, Any]] = []
    primary_project, secondary_project = _pick_project_focus_content(project_facts, recent_sessions)
    if primary_project:
        if not primary_project["source_session_ids"]:
            primary_project["source_session_ids"] = list(source_session_ids[:2])
        desired_states.append(primary_project)
    if secondary_project:
        if not secondary_project["source_session_ids"]:
            secondary_project["source_session_ids"] = list(source_session_ids[:2])
        desired_states.append(secondary_project)

    priority_content, priority_fact_ids, priority_session_ids = _pick_priority_content(
        project_facts,
        goal_facts,
        recent_sessions,
        recent_user_messages,
    )
    if priority_content:
        desired_states.append(
            {
                "kind": "priority",
                "state_key": "auto:priority:primary",
                "title": "Current priority",
                "content": priority_content,
                "confidence": 0.7,
                "priority_score": 0.95,
                "source_episode_ids": [],
                "source_session_ids": priority_session_ids or list(source_session_ids[:1]),
                "supporting_fact_ids": priority_fact_ids,
                "tags": ["derived", "priority"],
                "last_observed_at": reference_time,
            }
        )

    blocker_content = _pick_blocker_content(recent_user_messages, recent_sessions)
    if blocker_content:
        blocker_source_episode_ids = [
            str(episode.id)
            for episode in recent_user_messages
            if episode.id is not None and blocker_content.lower() in episode.content.lower()
        ][:3]
        desired_states.append(
            {
                "kind": "blocker",
                "state_key": "auto:blocker:primary",
                "title": "Current blocker",
                "content": blocker_content,
                "confidence": 0.68,
                "priority_score": 0.9,
                "source_episode_ids": blocker_source_episode_ids,
                "source_session_ids": list(source_session_ids[:2]),
                "supporting_fact_ids": [],
                "tags": ["derived", "blocker"],
                "last_observed_at": reference_time,
            }
        )

    open_loop_content = _pick_open_loop_content(recent_user_messages, recent_sessions)
    if open_loop_content:
        open_loop_source_episode_ids = [
            str(episode.id)
            for episode in recent_user_messages
            if episode.id is not None and open_loop_content.lower() in episode.content.lower()
        ][:3]
        desired_states.append(
            {
                "kind": "open_loop",
                "state_key": "auto:open_loop:primary",
                "title": "Open loop",
                "content": open_loop_content,
                "confidence": 0.64,
                "priority_score": 0.74,
                "source_episode_ids": open_loop_source_episode_ids,
                "source_session_ids": list(source_session_ids[:2]),
                "supporting_fact_ids": [],
                "tags": ["derived", "open-loop"],
                "last_observed_at": reference_time,
            }
        )

    emotion_content, emotion_source_episode_ids = _pick_emotion_state(recent_user_messages, recent_sessions)
    if emotion_content:
        desired_states.append(
            {
                "kind": "emotion_state",
                "state_key": "auto:emotion_state:current",
                "title": "Recent emotional tone",
                "content": emotion_content,
                "confidence": 0.58,
                "priority_score": 0.55,
                "source_episode_ids": emotion_source_episode_ids,
                "source_session_ids": list(source_session_ids[:2]),
                "supporting_fact_ids": [],
                "tags": ["derived", "emotion"],
                "last_observed_at": reference_time,
            }
        )

    upserted = 0
    emitted_keys: set[str] = set()
    for candidate in desired_states:
        content = _collapse_whitespace(candidate["content"])
        if candidate["kind"] == "project" and _looks_like_low_value_project_content(content):
            continue
        if candidate["kind"] in {"priority", "blocker", "open_loop"} and _looks_like_low_value_state_text(content):
            continue
        await client.add_active_state(
            kind=candidate["kind"],
            content=content,
            state_key=candidate["state_key"],
            title=candidate["title"],
            status="active",
            confidence=float(candidate["confidence"]),
            priority_score=float(candidate["priority_score"]),
            valid_from=since,
            last_observed_at=candidate["last_observed_at"],
            source_episode_ids=candidate["source_episode_ids"],
            source_session_ids=candidate["source_session_ids"],
            supporting_fact_ids=candidate["supporting_fact_ids"],
            tags=candidate["tags"],
            agent_namespace=agent_namespace,
        )
        emitted_keys.add(candidate["state_key"])
        upserted += 1

    staled = 0
    for existing in await client.list_active_state(limit=32, agent_namespace=agent_namespace):
        if existing.state_key not in _MANAGED_ACTIVE_STATE_KEYS or existing.state_key in emitted_keys:
            continue
        if existing.status == ActiveStateStatus.STALE:
            continue
        await client.upsert_active_state(
            existing.model_copy(
                update={
                    "status": ActiveStateStatus.STALE,
                    "priority_score": 0.0,
                    "updated_at": reference_time,
                }
            )
        )
        staled += 1

    return {
        "states_upserted": upserted,
        "states_staled": staled,
        "state_keys": sorted(emitted_keys),
    }


async def refresh_directives(
    client: MemoryClient,
    *,
    lookback_days: int = DIRECTIVES_LOOKBACK_DAYS,
    now: datetime | None = None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    reference_time = now or _utcnow()
    since = reference_time - timedelta(days=lookback_days)
    sessions = await _list_recent_candidate_sessions(
        client,
        since=since,
        limit=DIRECTIVES_SESSION_LIMIT,
        agent_namespace=agent_namespace,
    )

    candidates: dict[str, dict[str, Any]] = {}
    for session in sessions:
        episodes = await _list_session_episodes(client, str(session.id))
        for episode in episodes:
            if episode.role != EpisodeRole.USER:
                continue
            for clause in _extract_directive_clauses(episode.content):
                key = _directive_key(clause)
                candidate = candidates.get(key)
                directive_data = {
                    "kind": _directive_kind(clause),
                    "directive_key": key,
                    "title": _directive_title(clause),
                    "content": clause,
                    "confidence": 0.92,
                    "priority_score": 1.0 if any(token in clause.lower() for token in ("always", "never", "must")) else 0.85,
                    "source_episode_ids": [str(episode.id)] if episode.id is not None else [],
                    "source_session_ids": [str(session.id)] if session.id is not None else [],
                    "tags": ["derived", "directive"],
                    "last_observed_at": episode.message_timestamp,
                }
                if candidate is None:
                    candidates[key] = directive_data
                    continue
                candidate["source_episode_ids"] = sorted(set(candidate["source_episode_ids"] + directive_data["source_episode_ids"]))
                candidate["source_session_ids"] = sorted(set(candidate["source_session_ids"] + directive_data["source_session_ids"]))
                if directive_data["last_observed_at"] > candidate["last_observed_at"]:
                    candidate["last_observed_at"] = directive_data["last_observed_at"]
                    candidate["content"] = clause
                    candidate["title"] = directive_data["title"]

    upserted = 0
    emitted_keys: set[str] = set()
    for candidate in candidates.values():
        await client.add_directive(
            kind=candidate["kind"],
            directive_key=candidate["directive_key"],
            title=candidate["title"],
            content=candidate["content"],
            status="active",
            confidence=float(candidate["confidence"]),
            priority_score=float(candidate["priority_score"]),
            source_episode_ids=candidate["source_episode_ids"],
            source_session_ids=candidate["source_session_ids"],
            tags=candidate["tags"],
            last_observed_at=candidate["last_observed_at"],
            agent_namespace=agent_namespace,
        )
        emitted_keys.add(candidate["directive_key"])
        upserted += 1

    superseded = 0
    existing_directives = await client.list_directives(limit=64, agent_namespace=agent_namespace, statuses=["active"])
    for existing in existing_directives:
        if "derived" not in existing.tags:
            continue
        if existing.directive_key in emitted_keys:
            continue
        # Avoid false superseding when recent directives fall outside the session scan limit.
        if existing.last_observed_at is not None and existing.last_observed_at >= since:
            continue
        await client.upsert_directive(
            existing.model_copy(
                update={
                    "status": DirectiveStatus.SUPERSEDED,
                    "priority_score": 0.0,
                    "updated_at": reference_time,
                    "last_observed_at": reference_time,
                }
            )
        )
        superseded += 1

    active_directives = await client.list_directives(limit=32, agent_namespace=agent_namespace, statuses=["active"])
    return {
        "directives_upserted": upserted,
        "directive_count": len(active_directives),
        "directives_superseded": superseded,
    }


async def refresh_timeline_events(
    client: MemoryClient,
    *,
    lookback_days: int = TIMELINE_EVENT_LOOKBACK_DAYS,
    min_message_count: int = 3,
    now: datetime | None = None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    reference_time = now or _utcnow()
    since = reference_time - timedelta(days=lookback_days)
    sessions = await _list_recent_candidate_sessions(
        client,
        since=since,
        limit=PATTERNS_SESSION_LIMIT,
        agent_namespace=agent_namespace,
    )
    sessions = sorted(sessions, key=lambda session: session.started_at, reverse=True)[:TIMELINE_EVENT_SESSION_LIMIT]
    episodes_by_session: dict[str, list[Episode]] = {}
    meaningful_sessions: list[Session] = []

    upserted = 0
    for session in sessions:
        summary = _collapse_whitespace(str(session.summary or ""), max_length=320)
        if not summary:
            continue
        if _looks_like_reference_content(summary) or _looks_like_operational_content(summary):
            continue
        meaningful_sessions.append(session)
        title = session.title or f"{session.platform.value} session"
        importance = 0.55
        if session.message_count >= 20:
            importance += 0.1
        if session.dominant_emotions:
            importance += 0.05
        await client.add_timeline_event(
            summary=summary,
            event_key=_timeline_event_key(session),
            event_time=session.started_at,
            kind="session_summary",
            title=title,
            session_id=session.id,
            tags=[session.platform.value, "derived", "session-summary"],
            importance_score=min(1.0, importance),
            agent_namespace=agent_namespace,
        )
        upserted += 1
        episodes_by_session[str(session.id)] = await _list_session_episodes(client, str(session.id))

    sessions_by_day: dict[str, list[Session]] = {}
    sessions_by_week: dict[str, list[Session]] = {}
    for session in meaningful_sessions:
        sessions_by_day.setdefault(_timeline_day_key(session), []).append(session)
        sessions_by_week.setdefault(_timeline_week_key(session), []).append(session)

    for day_key, grouped_sessions in sessions_by_day.items():
        grouped_sessions = sorted(grouped_sessions, key=lambda item: item.started_at)
        if not _should_emit_day_rollup(grouped_sessions):
            continue
        summary = _render_timeline_rollup_summary(grouped_sessions)
        if not summary:
            continue
        await client.add_timeline_event(
            summary=summary,
            event_key=_timeline_rollup_event_key("day_summary", day_key),
            event_time=max(session.started_at for session in grouped_sessions),
            kind="day_summary",
            title=_timeline_rollup_title("day_summary", day_key),
            source_episode_ids=_timeline_rollup_source_episode_ids(episodes_by_session, grouped_sessions),
            tags=["derived", "timeline-rollup", "day-summary"],
            importance_score=_timeline_rollup_importance("day_summary", grouped_sessions),
            agent_namespace=agent_namespace,
        )
        upserted += 1

    for week_key, grouped_sessions in sessions_by_week.items():
        grouped_sessions = sorted(grouped_sessions, key=lambda item: item.started_at)
        if not _should_emit_week_rollup(grouped_sessions):
            continue
        summary = _render_timeline_rollup_summary(grouped_sessions)
        if not summary:
            continue
        await client.add_timeline_event(
            summary=summary,
            event_key=_timeline_rollup_event_key("week_summary", week_key),
            event_time=max(session.started_at for session in grouped_sessions),
            kind="week_summary",
            title=_timeline_rollup_title("week_summary", week_key),
            source_episode_ids=_timeline_rollup_source_episode_ids(episodes_by_session, grouped_sessions),
            tags=["derived", "timeline-rollup", "week-summary"],
            importance_score=_timeline_rollup_importance("week_summary", grouped_sessions),
            agent_namespace=agent_namespace,
        )
        upserted += 1

    visible_events = await client.list_timeline_events(limit=12, agent_namespace=agent_namespace)
    return {
        "timeline_events_upserted": upserted,
        "timeline_event_count": len(visible_events),
    }


async def refresh_decision_outcomes(
    client: MemoryClient,
    *,
    lookback_days: int = DECISION_OUTCOME_LOOKBACK_DAYS,
    min_message_count: int = 3,
    now: datetime | None = None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    reference_time = now or _utcnow()
    since = reference_time - timedelta(days=lookback_days)
    existing_outcomes = await client.list_decision_outcomes(limit=512, agent_namespace=agent_namespace)
    managed_existing_keys = {
        outcome.outcome_key
        for outcome in existing_outcomes
        if str(outcome.outcome_key or "").startswith("auto:decision-outcome:")
    }
    sessions = await _list_recent_candidate_sessions(
        client,
        since=since,
        limit=PATTERNS_SESSION_LIMIT,
        agent_namespace=agent_namespace,
    )
    sessions = sorted(sessions, key=lambda session: session.started_at, reverse=True)[:DECISION_OUTCOME_SESSION_LIMIT]

    upserted = 0
    managed_kept_keys: set[str] = set()
    for session in sessions:
        summary = _collapse_whitespace(str(session.summary or ""), max_length=360)
        if not summary:
            continue
        if _looks_like_reference_content(summary) or _looks_like_operational_content(summary):
            continue
        if _looks_like_low_value_outcome_text(summary):
            continue
        episodes = await _list_session_episodes(client, str(session.id))
        if not _summary_preference_supported(summary, episodes):
            continue
        extracted = _extract_decision_outcome(summary)
        if extracted is None:
            continue
        decision, outcome = extracted
        status = _decision_outcome_status(summary)
        if status == DecisionOutcomeStatus.OPEN:
            continue
        kind = _decision_outcome_kind(summary)
        if not _decision_outcome_is_grounded(decision, outcome, episodes):
            continue
        lesson = _decision_outcome_lesson(decision, outcome, status)
        if _looks_like_low_value_outcome_text(decision) or _looks_like_low_value_outcome_text(outcome):
            continue

        source_episode_ids = [
            str(episode.id)
            for episode in episodes
            if episode.id is not None
            and not _looks_like_reference_content(episode.content, episode.message_metadata)
            and not _looks_like_operational_content(episode.content, episode.message_metadata)
        ][:6]

        importance = 0.58
        if session.message_count >= 20:
            importance += 0.1
        if status in {DecisionOutcomeStatus.FAILURE, DecisionOutcomeStatus.MIXED}:
            importance += 0.08
        if session.dominant_emotions:
            importance += 0.04
        if lesson:
            importance += 0.04

        outcome_key = _decision_outcome_key(session)
        managed_kept_keys.add(outcome_key)
        await client.add_decision_outcome(
            kind=kind,
            title=_decision_outcome_title(session, summary),
            decision=decision,
            outcome=outcome,
            lesson=lesson,
            outcome_key=outcome_key,
            status=status.value,
            confidence=0.82 if status != DecisionOutcomeStatus.OPEN else 0.68,
            importance_score=min(1.0, importance),
            event_time=session.started_at,
            session_id=session.id,
            source_episode_ids=source_episode_ids,
            tags=[session.platform.value, "derived", "decision-outcome", status.value, kind],
            agent_namespace=agent_namespace,
        )
        upserted += 1

    pruned = 0
    delete_outcome = getattr(client.transport, "delete_decision_outcome", None)
    if callable(delete_outcome):
        for stale_key in sorted(managed_existing_keys - managed_kept_keys):
            removed = await delete_outcome(stale_key, agent_namespace=agent_namespace)
            if removed:
                pruned += 1

    visible_outcomes = await client.list_decision_outcomes(limit=12, agent_namespace=agent_namespace)
    return {
        "decision_outcomes_upserted": upserted,
        "decision_outcomes_pruned": pruned,
        "decision_outcome_count": len(visible_outcomes),
    }


def _reflection_key(source_kind: str, source_key: str) -> str:
    return _derived_memory_key("auto:reflection", f"{source_kind}:{source_key}")


def _reflection_kind_from_pattern(pattern_type: PatternType) -> str:
    if pattern_type in {PatternType.TRAP, PatternType.EMOTIONAL_PATTERN, PatternType.TRUST_PATTERN}:
        return "blind_spot"
    if pattern_type in {PatternType.STRENGTH, PatternType.QUALITY_BAR}:
        return "value_hypothesis"
    if pattern_type in {PatternType.WORK_PATTERN, PatternType.DECISION_STYLE}:
        return "workflow_hypothesis"
    return "communication_hypothesis"


def _reflection_statement_from_pattern(statement: str, kind: str) -> str:
    cleaned = _collapse_whitespace(statement, max_length=220).rstrip(".")
    if kind == "blind_spot":
        return f"Possible blind spot: {cleaned}."
    if kind == "value_hypothesis":
        return f"Possible value driver: {cleaned}."
    if kind == "workflow_hypothesis":
        return f"Possible workflow tendency: {cleaned}."
    return f"Possible communication tendency: {cleaned}."


def _reflection_statement_from_outcome(decision: str, outcome: str, lesson: str | None) -> str:
    if lesson:
        return f"Possible operating principle: {_collapse_whitespace(lesson, max_length=220).rstrip('.')}."
    decision_part = _collapse_whitespace(decision, max_length=140).rstrip(".")
    outcome_part = _collapse_whitespace(outcome, max_length=140).rstrip(".")
    return f"Possible pattern to monitor: {decision_part} -> {outcome_part}."


def _reflection_status_from_pattern(confidence: float, frequency_score: float, support_count: int) -> ReflectionStatus:
    if confidence >= 0.78 and frequency_score >= 0.68 and support_count >= 2:
        return ReflectionStatus.SUPPORTED
    return ReflectionStatus.TENTATIVE


def _reflection_status_from_outcome(confidence: float, importance_score: float) -> ReflectionStatus:
    if confidence >= 0.8 and importance_score >= 0.72:
        return ReflectionStatus.SUPPORTED
    return ReflectionStatus.TENTATIVE


async def refresh_reflections(
    client: MemoryClient,
    *,
    lookback_days: int = REFLECTIONS_LOOKBACK_DAYS,
    min_message_count: int = 3,
    now: datetime | None = None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    _ = min_message_count
    reference_time = now or _utcnow()
    since = reference_time - timedelta(days=lookback_days)
    patterns = await client.list_patterns(limit=REFLECTIONS_SESSION_LIMIT, agent_namespace=agent_namespace)
    outcomes = await client.list_decision_outcomes(
        limit=REFLECTIONS_SESSION_LIMIT,
        agent_namespace=agent_namespace,
        statuses=[DecisionOutcomeStatus.SUCCESS.value, DecisionOutcomeStatus.FAILURE.value, DecisionOutcomeStatus.MIXED.value],
    )
    existing_reflections = await client.list_reflections(limit=512, agent_namespace=agent_namespace)
    managed_existing_keys = {
        reflection.reflection_key
        for reflection in existing_reflections
        if str(reflection.reflection_key or "").startswith("auto:reflection:")
    }

    upserted = 0
    managed_kept_keys: set[str] = set()

    for pattern in patterns:
        if pattern.last_observed_at < since:
            continue
        if float(pattern.confidence) < 0.56 or float(pattern.impact_score) < 0.45:
            continue
        reflection_kind = _reflection_kind_from_pattern(pattern.pattern_type)
        reflection_key = _reflection_key("pattern", pattern.pattern_key)
        status = _reflection_status_from_pattern(
            float(pattern.confidence),
            float(pattern.frequency_score),
            len(pattern.supporting_session_ids),
        )
        confidence = min(
            0.92,
            max(
                0.55,
                (float(pattern.confidence) * 0.6)
                + (float(pattern.impact_score) * 0.2)
                + (float(pattern.frequency_score) * 0.2),
            ),
        )
        await client.add_reflection(
            kind=reflection_kind,
            statement=_reflection_statement_from_pattern(pattern.statement, reflection_kind),
            evidence_summary=_collapse_whitespace(pattern.description or pattern.statement, max_length=220),
            reflection_key=reflection_key,
            status=status.value,
            confidence=confidence,
            first_observed_at=pattern.first_observed_at,
            last_observed_at=pattern.last_observed_at,
            supporting_episode_ids=[str(value) for value in pattern.supporting_episode_ids],
            supporting_session_ids=[str(value) for value in pattern.supporting_session_ids],
            tags=["derived", "reflection", "pattern", pattern.pattern_type.value, status.value],
            agent_namespace=agent_namespace,
        )
        managed_kept_keys.add(reflection_key)
        upserted += 1

    for outcome in outcomes:
        if outcome.event_time < since:
            continue
        if outcome.status == DecisionOutcomeStatus.OPEN:
            continue
        if float(outcome.confidence) < 0.56 or float(outcome.importance_score) < 0.45:
            continue
        reflection_kind = "communication_hypothesis" if outcome.kind.value == "communication" else "workflow_hypothesis"
        reflection_key = _reflection_key("outcome", outcome.outcome_key)
        status = _reflection_status_from_outcome(float(outcome.confidence), float(outcome.importance_score))
        confidence = min(
            0.9,
            max(
                0.55,
                (float(outcome.confidence) * 0.65)
                + (float(outcome.importance_score) * 0.35),
            ),
        )
        evidence = _collapse_whitespace(
            " ".join(part for part in [outcome.decision, outcome.outcome, outcome.lesson or ""] if part),
            max_length=220,
        )
        await client.add_reflection(
            kind=reflection_kind,
            statement=_reflection_statement_from_outcome(outcome.decision, outcome.outcome, outcome.lesson),
            evidence_summary=evidence,
            reflection_key=reflection_key,
            status=status.value,
            confidence=confidence,
            first_observed_at=outcome.event_time,
            last_observed_at=outcome.event_time,
            supporting_episode_ids=[str(value) for value in outcome.source_episode_ids],
            supporting_session_ids=[str(outcome.session_id)] if outcome.session_id is not None else [],
            tags=["derived", "reflection", "outcome", outcome.kind.value, status.value],
            agent_namespace=agent_namespace,
        )
        managed_kept_keys.add(reflection_key)
        upserted += 1

    pruned = 0
    delete_reflection = getattr(client.transport, "delete_reflection", None)
    if callable(delete_reflection):
        for stale_key in sorted(managed_existing_keys - managed_kept_keys):
            removed = await delete_reflection(stale_key, agent_namespace=agent_namespace)
            if removed:
                pruned += 1

    visible_reflections = await client.list_reflections(
        limit=12,
        agent_namespace=agent_namespace,
        statuses=[ReflectionStatus.SUPPORTED.value, ReflectionStatus.TENTATIVE.value],
    )
    return {
        "reflections_upserted": upserted,
        "reflections_pruned": pruned,
        "reflection_count": len(visible_reflections),
    }


async def refresh_patterns(
    client: MemoryClient,
    *,
    lookback_days: int = PATTERNS_LOOKBACK_DAYS,
    min_message_count: int = 3,
    now: datetime | None = None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    reference_time = now or _utcnow()
    since = reference_time - timedelta(days=lookback_days)
    existing_patterns = await client.list_patterns(limit=512, agent_namespace=agent_namespace)
    managed_existing_keys = {
        pattern.pattern_key
        for pattern in existing_patterns
        if str(pattern.pattern_key or "").startswith("auto:pattern:")
    }
    sessions = await _list_recent_candidate_sessions(
        client,
        since=since,
        limit=PATTERNS_SESSION_LIMIT,
        agent_namespace=agent_namespace,
    )
    sessions = sorted(sessions, key=lambda session: session.started_at, reverse=True)[:PATTERNS_SESSION_LIMIT]

    episodes_by_session: dict[str, list[Episode]] = {}
    for session in sessions:
        episodes_by_session[str(session.id)] = await _list_session_episodes(client, str(session.id))

    candidates: list[dict[str, Any]] = []
    for spec in _PATTERN_SPECS:
        candidate = _pattern_candidate_from_sessions(sessions, episodes_by_session, spec)
        if candidate is not None:
            candidates.append(candidate)

    upserted = 0
    managed_kept_keys: set[str] = set()
    for candidate in candidates:
        managed_kept_keys.add(candidate["pattern_key"])
        await client.add_pattern(
            pattern_type=candidate["pattern_type"],
            statement=candidate["statement"],
            description=candidate["description"],
            pattern_key=candidate["pattern_key"],
            confidence=float(candidate["confidence"]),
            frequency_score=float(candidate["frequency_score"]),
            impact_score=float(candidate["impact_score"]),
            first_observed_at=candidate["first_observed_at"],
            last_observed_at=candidate["last_observed_at"],
            supporting_episode_ids=candidate["supporting_episode_ids"],
            supporting_session_ids=candidate["supporting_session_ids"],
            counterexample_episode_ids=candidate["counterexample_episode_ids"],
            tags=candidate["tags"],
            agent_namespace=agent_namespace,
        )
        upserted += 1

    pruned = 0
    delete_pattern = getattr(client.transport, "delete_pattern", None)
    if callable(delete_pattern):
        for stale_key in sorted(managed_existing_keys - managed_kept_keys):
            removed = await delete_pattern(stale_key, agent_namespace=agent_namespace)
            if removed:
                pruned += 1

    visible_patterns = await client.list_patterns(limit=12, agent_namespace=agent_namespace)
    return {
        "patterns_upserted": upserted,
        "patterns_pruned": pruned,
        "pattern_count": len(visible_patterns),
    }


async def refresh_commitments(
    client: MemoryClient,
    *,
    lookback_days: int = COMMITMENTS_LOOKBACK_DAYS,
    now: datetime | None = None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    reference_time = now or _utcnow()
    since = reference_time - timedelta(days=lookback_days)
    sessions = await _list_recent_candidate_sessions(
        client,
        since=since,
        limit=COMMITMENTS_SESSION_LIMIT,
        agent_namespace=agent_namespace,
    )

    candidates: dict[str, dict[str, Any]] = {}
    for session in sessions:
        episodes = await _list_session_episodes(client, str(session.id))
        for episode in episodes:
            if episode.role != EpisodeRole.ASSISTANT:
                continue
            if _looks_like_reference_content(episode.content) or _looks_like_operational_content(episode.content):
                continue
            for sentence in _extract_commitment_sentences(episode.content):
                key = _commitment_key(sentence)
                candidate = candidates.get(key)
                commitment_data = {
                    "kind": _commitment_kind(sentence),
                    "statement": sentence,
                    "commitment_key": key,
                    "status": _commitment_status_from_sentence(sentence),
                    "confidence": 0.86,
                    "priority_score": 0.8,
                    "source_episode_ids": [str(episode.id)] if episode.id is not None else [],
                    "source_session_ids": [str(session.id)] if session.id is not None else [],
                    "tags": ["derived", "commitment"],
                    "first_committed_at": episode.message_timestamp,
                    "last_observed_at": episode.message_timestamp,
                }
                if candidate is None:
                    candidates[key] = commitment_data
                    continue
                candidate["source_episode_ids"] = sorted(set(candidate["source_episode_ids"] + commitment_data["source_episode_ids"]))
                candidate["source_session_ids"] = sorted(set(candidate["source_session_ids"] + commitment_data["source_session_ids"]))
                if commitment_data["last_observed_at"] > candidate["last_observed_at"]:
                    candidate["last_observed_at"] = commitment_data["last_observed_at"]
                    candidate["statement"] = sentence
                    candidate["status"] = commitment_data["status"]

    upserted = 0
    emitted_keys: set[str] = set()
    for candidate in candidates.values():
        await client.add_commitment(
            kind=candidate["kind"],
            statement=candidate["statement"],
            commitment_key=candidate["commitment_key"],
            status=candidate["status"].value,
            confidence=float(candidate["confidence"]),
            priority_score=float(candidate["priority_score"]),
            first_committed_at=candidate["first_committed_at"],
            last_observed_at=candidate["last_observed_at"],
            source_episode_ids=candidate["source_episode_ids"],
            source_session_ids=candidate["source_session_ids"],
            tags=candidate["tags"],
            agent_namespace=agent_namespace,
        )
        emitted_keys.add(candidate["commitment_key"])
        upserted += 1

    closed = 0
    existing_commitments = await client.list_commitments(limit=128, agent_namespace=agent_namespace, statuses=["open"])
    for existing in existing_commitments:
        if "derived" not in existing.tags:
            continue
        if not _is_commitment_sentence(existing.statement):
            await client.upsert_commitment(
                existing.model_copy(
                    update={
                        "status": CommitmentStatus.CANCELLED,
                        "priority_score": 0.0,
                        "updated_at": reference_time,
                        "last_observed_at": reference_time,
                    }
                )
            )
            closed += 1
            continue
        if _commitment_is_stale(existing.kind.value, existing.last_observed_at, reference_time=reference_time):
            await client.upsert_commitment(
                existing.model_copy(
                    update={
                        "status": CommitmentStatus.CANCELLED,
                        "priority_score": 0.0,
                        "updated_at": reference_time,
                    }
                )
            )
            closed += 1
            continue
        if existing.commitment_key in emitted_keys:
            continue
        await client.upsert_commitment(
            existing.model_copy(
                update={
                    "status": CommitmentStatus.CANCELLED,
                    "priority_score": 0.0,
                    "updated_at": reference_time,
                    "last_observed_at": reference_time,
                }
            )
        )
        closed += 1

    visible_commitments = await client.list_commitments(limit=12, agent_namespace=agent_namespace, statuses=["open"])
    return {
        "commitments_upserted": upserted,
        "commitment_count": len(visible_commitments),
        "commitments_closed": closed,
    }


async def refresh_corrections(
    client: MemoryClient,
    *,
    lookback_days: int = CORRECTIONS_LOOKBACK_DAYS,
    now: datetime | None = None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    reference_time = now or _utcnow()
    since = reference_time - timedelta(days=lookback_days)
    sessions = await _list_recent_candidate_sessions(
        client,
        since=since,
        limit=CORRECTIONS_SESSION_LIMIT,
        agent_namespace=agent_namespace,
    )

    candidates: dict[str, dict[str, Any]] = {}
    for session in sessions:
        episodes = await _list_session_episodes(client, str(session.id))
        for episode in episodes:
            if episode.role != EpisodeRole.USER:
                continue
            if _looks_like_reference_content(episode.content) or _looks_like_operational_content(episode.content):
                continue
            statement = _collapse_whitespace(episode.content, max_length=260)
            if not _looks_like_correction(statement):
                continue
            key = _correction_key(statement)
            correction_data = {
                "kind": _correction_kind(statement),
                "statement": statement,
                "target_text": _extract_correction_target(statement),
                "correction_key": key,
                "active": True,
                "confidence": 0.93,
                "source_episode_ids": [str(episode.id)] if episode.id is not None else [],
                "source_session_ids": [str(session.id)] if session.id is not None else [],
                "tags": ["derived", "correction"],
                "first_observed_at": episode.message_timestamp,
                "last_observed_at": episode.message_timestamp,
            }
            existing = candidates.get(key)
            if existing is None:
                candidates[key] = correction_data
                continue
            existing["source_episode_ids"] = sorted(set(existing["source_episode_ids"] + correction_data["source_episode_ids"]))
            existing["source_session_ids"] = sorted(set(existing["source_session_ids"] + correction_data["source_session_ids"]))
            if correction_data["last_observed_at"] > existing["last_observed_at"]:
                existing["last_observed_at"] = correction_data["last_observed_at"]
                existing["statement"] = statement
                existing["target_text"] = correction_data["target_text"]

    upserted = 0
    emitted_keys: set[str] = set()
    for candidate in candidates.values():
        await client.add_correction(
            kind=candidate["kind"],
            statement=candidate["statement"],
            target_text=candidate["target_text"],
            correction_key=candidate["correction_key"],
            active=True,
            confidence=float(candidate["confidence"]),
            first_observed_at=candidate["first_observed_at"],
            last_observed_at=candidate["last_observed_at"],
            source_episode_ids=candidate["source_episode_ids"],
            source_session_ids=candidate["source_session_ids"],
            tags=candidate["tags"],
            agent_namespace=agent_namespace,
        )
        emitted_keys.add(candidate["correction_key"])
        upserted += 1

    deactivated = 0
    existing_corrections = await client.list_corrections(limit=128, agent_namespace=agent_namespace, active_only=True)
    for existing in existing_corrections:
        if "derived" not in existing.tags:
            continue
        if existing.correction_key in emitted_keys:
            continue
        if _looks_like_correction(existing.statement):
            continue
        await client.upsert_correction(
            existing.model_copy(
                update={
                    "active": False,
                    "updated_at": reference_time,
                }
            )
        )
        deactivated += 1

    visible_corrections = await client.list_corrections(limit=12, agent_namespace=agent_namespace, active_only=True)
    return {
        "corrections_upserted": upserted,
        "correction_count": len(visible_corrections),
        "corrections_deactivated": deactivated,
    }


async def summarize_session_with_llm(
    episodes: list[Episode],
    *,
    http_client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str = DEFAULT_SUMMARY_MODEL,
) -> str:
    if not episodes:
        return ""

    config = MemoryConfig.from_env()
    resolved_api_key = api_key or config.openai_api_key or os.getenv("OPENAI_API_KEY")
    resolved_base_url = (base_url or config.openai_base_url).rstrip("/")
    if not resolved_api_key:
        raise ValueError("OPENAI_API_KEY must be configured for session consolidation.")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Summarize the session in under 500 characters. Focus on durable user context, goals, "
                    "preferences, commitments, and important outcomes. Use plain factual language."
                ),
            },
            {
                "role": "user",
                "content": _render_transcript(episodes),
            },
        ],
        "temperature": 0.2,
    }

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.post(
            f"{resolved_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {resolved_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        summary = _normalize_summary(content)
        if not summary:
            raise ValueError("LLM returned an empty summary.")
        return summary
    finally:
        if owns_client:
            await client.aclose()


async def consolidate_session_if_needed(
    client: MemoryClient,
    session_id: str,
    *,
    min_message_count: int = 3,
    now: datetime | None = None,
    http_client: httpx.AsyncClient | None = None,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    reference_time = now or _utcnow()
    stats: dict[str, Any] = {
        "session_id": session_id,
        "session_processed": False,
        "summary_generated": False,
        "summary_skipped": False,
        "facts_extracted": 0,
        "errors": 0,
        "error": None,
        "reason": None,
    }

    try:
        session = await client.transport.get_session(session_id)
        if session is None:
            stats["reason"] = "missing-session"
            return stats
        if not _agent_namespace_matches(session.agent_namespace, agent_namespace):
            stats["reason"] = "namespace-mismatch"
            return stats

        episodes = await _list_session_episodes(client, session_id)
        if len(episodes) <= 2 or len(episodes) < min_message_count:
            stats["reason"] = f"insufficient-episodes:{len(episodes)}"
            return stats

        summary = session.summary
        if not summary:
            summary = await _run_with_retries(
                lambda: summarize_session_with_llm(
                    episodes,
                    http_client=http_client,
                    api_key=llm_api_key,
                    base_url=llm_base_url,
                    model=llm_model or DEFAULT_SUMMARY_MODEL,
                ),
                label=f"summary generation ({session_id})",
            )
            summary_embedding = await _run_with_retries(
                lambda: client.embedding.embed_text(summary),
                label=f"summary embedding ({session_id})",
            )
            await _run_with_retries(
                lambda: client.transport.update_session(
                    session_id,
                    {
                        "summary": summary,
                        "summary_embedding": summary_embedding,
                    },
                ),
                label=f"summary write ({session_id})",
            )
            stats["summary_generated"] = True
        else:
            stats["summary_skipped"] = True

        stored = await extract_and_store_facts(client.transport, episodes, now=reference_time)
        stats["session_processed"] = True
        stats["facts_extracted"] = len(stored)
        return stats
    except Exception as exc:  # pragma: no cover - exercised in tests
        stats["errors"] = 1
        stats["error"] = str(exc)
        _log(f"Failed to consolidate session {session_id}: {exc}")
        return stats


async def consolidate_recent_sessions(
    client: MemoryClient,
    *,
    lookback_hours: int = 6,
    min_message_count: int = 3,
    now: datetime | None = None,
    http_client: httpx.AsyncClient | None = None,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
    agent_namespace: str | None = None,
    batch_limit: int | None = None,
    cursor_started_after: datetime | str | None = None,
) -> dict[str, Any]:
    reference_time = now or _utcnow()
    since = reference_time - timedelta(hours=lookback_hours)
    parsed_cursor = _coerce_optional_datetime(cursor_started_after)
    stats: dict[str, Any] = {
        "sessions_processed": 0,
        "facts_extracted": 0,
        "errors": 0,
        "error_details": [],
        "backlog_total_unsummarized": 0,
        "backlog_attempted": 0,
        "backlog_remaining": 0,
        "backlog_cursor_after": parsed_cursor.isoformat() if parsed_cursor else None,
        "backlog_cursor_wrapped": False,
    }

    sessions = await _list_recent_unsummarized_sessions(
        client,
        since=since,
        min_message_count=min_message_count,
        agent_namespace=agent_namespace,
    )
    stats["backlog_total_unsummarized"] = len(sessions)

    ordered_sessions = list(sessions)
    if parsed_cursor is not None and ordered_sessions:
        newer = [session for session in ordered_sessions if session.started_at > parsed_cursor]
        older = [session for session in ordered_sessions if session.started_at <= parsed_cursor]
        ordered_sessions = newer + older
        stats["backlog_cursor_wrapped"] = bool(older and not newer)

    limited_sessions = ordered_sessions
    if batch_limit is not None and batch_limit > 0:
        limited_sessions = ordered_sessions[:batch_limit]

    stats["backlog_attempted"] = len(limited_sessions)
    stats["backlog_remaining"] = max(0, len(ordered_sessions) - len(limited_sessions))

    for session in limited_sessions:
        session_id = str(session.id)
        result = await consolidate_session_if_needed(
            client,
            session_id,
            min_message_count=min_message_count,
            now=reference_time,
            http_client=http_client,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            agent_namespace=agent_namespace,
        )
        if result.get("session_processed"):
            stats["sessions_processed"] += 1
            stats["facts_extracted"] += int(result.get("facts_extracted") or 0)
        if result.get("errors"):
            stats["errors"] += int(result.get("errors") or 0)
            stats["error_details"].append({"session_id": session_id, "error": str(result.get("error") or "unknown")})
        stats["backlog_cursor_after"] = session.started_at.isoformat()

    return stats


async def extract_facts_from_recent_sessions(
    client: MemoryClient,
    *,
    lookback_hours: int = 6,
    min_message_count: int = 3,
    now: datetime | None = None,
    agent_namespace: str | None = None,
) -> dict[str, Any]:
    reference_time = now or _utcnow()
    since = reference_time - timedelta(hours=lookback_hours)
    stats: dict[str, Any] = {
        "sessions_processed": 0,
        "facts_extracted": 0,
        "errors": 0,
        "error_details": [],
    }

    sessions = await _list_recent_summarized_sessions(
        client,
        since=since,
        min_message_count=min_message_count,
        agent_namespace=agent_namespace,
    )
    for session in sessions:
        session_id = str(session.id)
        try:
            episodes = await _list_session_episodes(client, session_id)
            if len(episodes) < min_message_count:
                continue

            stored = await extract_and_store_facts(client.transport, episodes, now=reference_time)
            stats["sessions_processed"] += 1
            stats["facts_extracted"] += len(stored)
            _log(f"Extracted facts for session {session_id}: facts={len(stored)}")
        except Exception as exc:  # pragma: no cover - exercised in tests
            stats["errors"] += 1
            stats["error_details"].append({"session_id": session_id, "error": str(exc)})
            _log(f"Failed fact extraction for session {session_id}: {exc}")

    return stats
