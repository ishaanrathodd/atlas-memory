from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field

from memory.models import (
    Episode,
    EpisodeRole,
    Fact,
    FactCategory,
    FactHistory,
    FactOperation,
    MemoryBaseModel,
)
from memory.transport import MemoryTransport


_CLAUSE_SPLIT_PATTERN = re.compile(r"[.!?;\n]+")
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "for",
    "from",
    "have",
    "i",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "that",
    "the",
    "this",
    "to",
    "we",
    "with",
    "you",
}
_EMOTION_WORDS = {
    "afraid",
    "anxious",
    "delighted",
    "excited",
    "glad",
    "happy",
    "hopeful",
    "nervous",
    "pleased",
    "sad",
    "scared",
    "stressed",
    "thrilled",
    "tired",
    "upset",
    "worried",
}
_PREFERENCE_PATTERNS = (
    re.compile(r"^(?:i|we)\s+(?:really\s+|usually\s+)?prefer\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^(?:i|we)\s+(?:really\s+|usually\s+)?(?:like|love|enjoy)\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(
        r"^(?:i|we)\s+(?:do not|don't|dislike|hate)\s+(?P<value>.+)$",
        re.IGNORECASE,
    ),
)
_HABIT_PATTERNS = (
    re.compile(
        r"^(?:i|we)\s+(?P<action>.+?)\s+(?P<frequency>every morning|every evening|every night|every day|daily|each morning|each evening|on weekdays|on weekends|every weekend)$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:i|we)\s+(?:usually|typically|routinely)\s+(?P<action>.+)$", re.IGNORECASE),
)
_GOAL_PATTERNS = (
    re.compile(
        r"^(?:i|we)\s+(?:want|need|plan|hope|aim|intend|try|trying)\s+to\s+(?P<goal>.+)$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:i|we)\s+(?:am|are)\s+going\s+to\s+(?P<goal>.+)$", re.IGNORECASE),
    re.compile(r"^(?:i|we)\s+would\s+like\s+to\s+(?P<goal>.+)$", re.IGNORECASE),
)
_IDENTITY_PATTERNS = (
    re.compile(r"^my name is\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^(?:i|we)\s+(?:am|are|'m)\s+(?:a|an)\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^(?:i|we)\s+work as\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^(?:i|we)\s+work at\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^(?:i|we)\s+live in\s+(?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^(?:i|we)\s+(?:am|are|'m)\s+from\s+(?P<value>.+)$", re.IGNORECASE),
)
_PROJECT_PATTERN = re.compile(
    r"\b(?:deadline|launch|release|project|migration|milestone|roadmap|rollout|ship)\b",
    re.IGNORECASE,
)
_PROJECT_STATEMENT_PATTERNS = (
    re.compile(
        r"^(?:i|we)\s+(?:am|are|'m)?\s*(?:building|working on|debugging|migrating|launching|shipping|rebuilding|finishing)\s+.+$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:my|our)\s+(?:project|company|startup|product|app)\b.+$", re.IGNORECASE),
    re.compile(r"^(?:project|initiative|roadmap)\s*(?::|-)\s*.+$", re.IGNORECASE),
)
_META_FACT_MARKERS = (
    "system prompt",
    "api key",
    "env var",
    "config.yaml",
    ".env",
    "stack trace",
    "traceback",
    "command output",
    "test run",
    "log output",
    "patch file",
    "pull request",
    "schema",
    "sql editor",
    "jsonl",
)
_NON_FACT_PREFIXES = (
    "what ",
    "why ",
    "how ",
    "when ",
    "where ",
    "who ",
    "let me ",
    "check ",
    "okay ",
    "ok ",
    "wait ",
    "so ",
    "now ",
    "then ",
    "please ",
)
_EPHEMERAL_GOAL_PREFIXES = (
    "check ",
    "see ",
    "figure out ",
    "restart ",
    "send ",
    "reply ",
    "apply ",
    "create ",
    "save ",
    "fix this",
    "fix that",
    "stop trying ",
)
_TRANSIENT_GOAL_MARKERS = (
    "json",
    "reply-to",
    "reply to",
    "api key",
    "service key",
    "env var",
    "schema",
    "sql editor",
    "session's context",
    "this session",
)
_LOW_VALUE_PROJECT_PREFIXES = (
    "okay now",
    "what's next",
    "what is left",
    "env file",
    "from the",
    "this is",
    "let me",
    "wait,",
    "- [project]",
)
_LOW_VALUE_PROJECT_MARKERS = (
    "schema",
    "notify reload",
    "build complete",
    "tests pass",
    "env file",
    "venv",
    "json objects",
    "sql editor",
    "reply-to",
    "reply to",
    "code patches",
    "important discussion",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _strip_trailing_punctuation(value: str) -> str:
    return value.strip().rstrip(".,;:!?")


def _sentence(value: str) -> str:
    collapsed = _strip_trailing_punctuation(_collapse_whitespace(value))
    if not collapsed:
        return ""
    return collapsed[0].upper() + collapsed[1:] + "."


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN_PATTERN.findall(value.lower())
        if token not in _STOPWORDS and len(token) > 1
    }


def _similarity(left: str, right: str) -> float:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    shared = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    if union == 0:
        return 0.0
    return shared / union


def _content_key(category: FactCategory, content: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", content.lower()).strip()
    return f"{category.value}:{normalized}"


def _content_fingerprint(category: FactCategory, content: str) -> str:
    return _content_key(category, content)


def _sorted_unique_tags(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for value in values:
        normalized = value.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        tags.append(normalized)
    tags.sort()
    return tags


def _merge_episode_ids(*collections: Iterable[UUID]) -> list[UUID]:
    seen: set[UUID] = set()
    merged: list[UUID] = []
    for collection in collections:
        for value in collection:
            if value in seen:
                continue
            seen.add(value)
            merged.append(value)
    merged.sort(key=str)
    return merged


def _choose_content(left: str, right: str) -> str:
    if _content_key(FactCategory.OTHER, left) == _content_key(FactCategory.OTHER, right):
        return left if len(left) >= len(right) else right
    if right.lower().startswith(left[:-1].lower()):
        return right
    if left.lower().startswith(right[:-1].lower()):
        return left
    return left if len(left) >= len(right) else right


def _reference_key(reference: "FactSourceReference") -> tuple[str, int, str, str]:
    episode = str(reference.episode_id) if reference.episode_id is not None else ""
    return (episode, reference.turn_index, reference.role.value, reference.excerpt)


class ConversationTurn(MemoryBaseModel):
    episode_id: UUID | None = None
    role: EpisodeRole = EpisodeRole.USER
    content: str
    message_timestamp: AwareDatetime


class FactSourceReference(MemoryBaseModel):
    episode_id: UUID | None = None
    turn_index: int = Field(default=0, ge=0)
    role: EpisodeRole
    excerpt: str
    message_timestamp: AwareDatetime


class ExtractedFact(MemoryBaseModel):
    content: str
    category: FactCategory
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    event_time: AwareDatetime
    transaction_time: AwareDatetime
    tags: list[str] = Field(default_factory=list)
    source_episode_ids: list[UUID] = Field(default_factory=list)
    source_references: list[FactSourceReference] = Field(default_factory=list)

    def to_fact(self) -> Fact:
        now = self.transaction_time
        return Fact(
            content=self.content,
            category=self.category,
            content_fingerprint=_content_fingerprint(self.category, self.content),
            confidence=self.confidence,
            event_time=self.event_time,
            transaction_time=self.transaction_time,
            is_active=True,
            source_episode_ids=list(self.source_episode_ids),
            access_count=0,
            tags=list(self.tags),
            created_at=now,
            updated_at=now,
        )


def normalize_turns(
    turns: str | Episode | ConversationTurn | dict[str, Any] | Sequence[Episode | ConversationTurn | dict[str, Any] | str],
    *,
    now: datetime | None = None,
) -> list[ConversationTurn]:
    base_now = now or _utcnow()
    if isinstance(turns, (str, Episode, ConversationTurn, dict)):
        raw_turns: Sequence[Episode | ConversationTurn | dict[str, Any] | str] = [turns]
    else:
        raw_turns = turns

    normalized: list[ConversationTurn] = []
    for raw_turn in raw_turns:
        if isinstance(raw_turn, ConversationTurn):
            normalized.append(raw_turn)
            continue
        if isinstance(raw_turn, Episode):
            normalized.append(
                ConversationTurn(
                    episode_id=raw_turn.id,
                    role=raw_turn.role,
                    content=raw_turn.content,
                    message_timestamp=raw_turn.message_timestamp,
                )
            )
            continue
        if isinstance(raw_turn, str):
            normalized.append(
                ConversationTurn(
                    role=EpisodeRole.USER,
                    content=raw_turn,
                    message_timestamp=base_now,
                )
            )
            continue

        content = str(raw_turn.get("content", "")).strip()
        if not content:
            continue
        role = raw_turn.get("role", EpisodeRole.USER.value)
        episode_id = raw_turn.get("episode_id", raw_turn.get("id"))
        message_timestamp = raw_turn.get("message_timestamp", raw_turn.get("timestamp", base_now))
        normalized.append(
            ConversationTurn(
                episode_id=episode_id,
                role=EpisodeRole(str(role)),
                content=content,
                message_timestamp=message_timestamp,
            )
        )
    return normalized


def _split_clauses(content: str) -> list[str]:
    return [
        _strip_trailing_punctuation(clause)
        for clause in _CLAUSE_SPLIT_PATTERN.split(content)
        if _strip_trailing_punctuation(clause)
    ]


def _normalize_clause_for_matching(clause: str, turn: ConversationTurn) -> str:
    normalized = clause.strip()
    lowered = normalized.lower()
    if lowered.startswith("i'm "):
        normalized = "I am " + normalized[4:]
    if turn.role is EpisodeRole.ASSISTANT:
        lowered = normalized.lower()
        if lowered.startswith("you are "):
            normalized = "I am " + normalized[8:]
        elif lowered.startswith("you're "):
            normalized = "I am " + normalized[6:]
        elif lowered.startswith("you "):
            normalized = "I " + normalized[4:]
    return normalized


def _make_reference(turn: ConversationTurn, turn_index: int, excerpt: str) -> FactSourceReference:
    return FactSourceReference(
        episode_id=turn.episode_id,
        turn_index=turn_index,
        role=turn.role,
        excerpt=_sentence(excerpt),
        message_timestamp=turn.message_timestamp,
    )


def _candidate(
    *,
    content: str,
    category: FactCategory,
    confidence: float,
    turn: ConversationTurn,
    turn_index: int,
    excerpt: str,
    tags: Iterable[str],
    transaction_time: datetime,
) -> ExtractedFact:
    episode_ids = [turn.episode_id] if turn.episode_id is not None else []
    return ExtractedFact(
        content=_sentence(content),
        category=category,
        confidence=confidence,
        event_time=turn.message_timestamp,
        transaction_time=transaction_time,
        tags=_sorted_unique_tags(tags),
        source_episode_ids=episode_ids,
        source_references=[_make_reference(turn, turn_index, excerpt)],
    )


def _tags_from_text(text: str, *, extra: Iterable[str] = ()) -> list[str]:
    tokens = [token for token in _TOKEN_PATTERN.findall(text.lower()) if len(token) > 2 and token not in _STOPWORDS]
    return _sorted_unique_tags([*tokens[:4], *extra])


def _references_assistant(text: str) -> bool:
    return bool(re.search(r"\b(?:you|your|u)\b", text.lower()))


def _looks_like_meta_or_instruction_clause(clause: str) -> bool:
    lowered = _collapse_whitespace(clause).lower()
    if not lowered:
        return True
    if "`" in clause or "~/" in clause or ".md" in lowered or ".py" in lowered or ".yaml" in lowered:
        return True
    if any(lowered.startswith(prefix) for prefix in _NON_FACT_PREFIXES):
        return True
    if any(marker in lowered for marker in _META_FACT_MARKERS):
        return True
    return False


def _is_plausible_personal_value(value: str, *, max_tokens: int = 10) -> bool:
    normalized = _collapse_whitespace(value)
    if not normalized:
        return False
    if _references_assistant(normalized):
        return False
    tokens = [token for token in _TOKEN_PATTERN.findall(normalized.lower()) if token]
    if not tokens or len(tokens) > max_tokens:
        return False
    return True


def _looks_like_transient_goal(goal: str) -> bool:
    lowered = _collapse_whitespace(goal).lower()
    if any(lowered.startswith(prefix) for prefix in _EPHEMERAL_GOAL_PREFIXES):
        if any(marker in lowered for marker in _TRANSIENT_GOAL_MARKERS):
            return True
    if any(marker in lowered for marker in _TRANSIENT_GOAL_MARKERS):
        return True
    return False


def _looks_like_low_value_project_clause(clause: str) -> bool:
    lowered = _collapse_whitespace(clause).lower()
    if any(lowered.startswith(prefix) for prefix in _LOW_VALUE_PROJECT_PREFIXES):
        return True
    if any(marker in lowered for marker in _LOW_VALUE_PROJECT_MARKERS):
        return True
    return False


def _extract_preference(clause: str, turn: ConversationTurn, turn_index: int, now: datetime) -> list[ExtractedFact]:
    results: list[ExtractedFact] = []
    if turn.role is not EpisodeRole.USER:
        return results
    normalized_clause = _normalize_clause_for_matching(clause, turn)
    if _looks_like_meta_or_instruction_clause(normalized_clause):
        return results
    lowered = normalized_clause.lower()
    if "like to " in lowered or "would like to " in lowered:
        return results
    for pattern in _PREFERENCE_PATTERNS:
        match = pattern.match(normalized_clause)
        if not match:
            continue
        value = _strip_trailing_punctuation(match.group("value"))
        if not value:
            continue
        if not _is_plausible_personal_value(value):
            continue
        negative = any(token in lowered for token in ("don't", "do not", "dislike", "hate"))
        content = f"User {'dislikes' if negative else 'prefers'} {value}"
        confidence = 0.83
        results.append(
            _candidate(
                content=content,
                category=FactCategory.PREFERENCE,
                confidence=confidence,
                turn=turn,
                turn_index=turn_index,
                excerpt=clause,
                tags=_tags_from_text(value, extra=("preference",)),
                transaction_time=now,
            )
        )
        break
    return results


def _extract_habit(clause: str, turn: ConversationTurn, turn_index: int, now: datetime) -> list[ExtractedFact]:
    if turn.role is not EpisodeRole.USER:
        return []
    normalized_clause = _normalize_clause_for_matching(clause, turn)
    if _looks_like_meta_or_instruction_clause(normalized_clause):
        return []
    for pattern in _HABIT_PATTERNS:
        match = pattern.match(normalized_clause)
        if not match:
            continue
        if "frequency" in match.groupdict():
            action = _strip_trailing_punctuation(match.group("action"))
            frequency = _strip_trailing_punctuation(match.group("frequency"))
            content = f"User {action} {frequency}"
            tag_text = f"{action} {frequency}"
        else:
            action = _strip_trailing_punctuation(match.group("action"))
            content = f"User usually {action}"
            tag_text = action
        confidence = 0.86
        return [
            _candidate(
                content=content,
                category=FactCategory.HABIT,
                confidence=confidence,
                turn=turn,
                turn_index=turn_index,
                excerpt=clause,
                tags=_tags_from_text(tag_text, extra=("habit",)),
                transaction_time=now,
            )
        ]
    return []


def _extract_goal(clause: str, turn: ConversationTurn, turn_index: int, now: datetime) -> list[ExtractedFact]:
    if turn.role is not EpisodeRole.USER:
        return []
    normalized_clause = _normalize_clause_for_matching(clause, turn)
    if _looks_like_meta_or_instruction_clause(normalized_clause):
        return []
    for pattern in _GOAL_PATTERNS:
        match = pattern.match(normalized_clause)
        if not match:
            continue
        goal = _strip_trailing_punctuation(match.group("goal"))
        if not goal:
            continue
        lowered_goal = goal.lower()
        if _references_assistant(goal):
            return []
        if _looks_like_transient_goal(goal):
            return []
        return [
            _candidate(
                content=f"User wants to {goal}",
                category=FactCategory.GOAL,
                confidence=0.84,
                turn=turn,
                turn_index=turn_index,
                excerpt=clause,
                tags=_tags_from_text(goal, extra=("goal",)),
                transaction_time=now,
            )
        ]
    return []


def _extract_identity(clause: str, turn: ConversationTurn, turn_index: int, now: datetime) -> list[ExtractedFact]:
    if turn.role is not EpisodeRole.USER:
        return []
    normalized_clause = _normalize_clause_for_matching(clause, turn)
    if _looks_like_meta_or_instruction_clause(normalized_clause):
        return []
    lowered = normalized_clause.lower()
    for pattern in _IDENTITY_PATTERNS:
        match = pattern.match(normalized_clause)
        if not match:
            continue
        value = _strip_trailing_punctuation(match.group("value"))
        if not value:
            continue
        if lowered.startswith(("i am ", "i'm ")) and value.split(" ", 1)[0].lower() in _EMOTION_WORDS:
            return []
        if lowered.startswith("my name is"):
            content = f"User's name is {value}"
        elif "work as" in lowered:
            content = f"User works as {value}"
        elif "work at" in lowered:
            content = f"User works at {value}"
        elif "live in" in lowered:
            content = f"User lives in {value}"
        elif "from" in lowered:
            content = f"User is from {value}"
        else:
            content = f"User is a {value}"
        return [
            _candidate(
                content=content,
                category=FactCategory.IDENTITY,
                confidence=0.88,
                turn=turn,
                turn_index=turn_index,
                excerpt=clause,
                tags=_tags_from_text(value, extra=("identity",)),
                transaction_time=now,
            )
        ]
    return []


def _extract_project(clause: str, turn: ConversationTurn, turn_index: int, now: datetime) -> list[ExtractedFact]:
    if turn.role is not EpisodeRole.USER:
        return []
    normalized_clause = _normalize_clause_for_matching(clause, turn)
    if _looks_like_meta_or_instruction_clause(normalized_clause):
        return []
    if _looks_like_low_value_project_clause(normalized_clause):
        return []
    matches_project_pattern = any(pattern.match(normalized_clause) for pattern in _PROJECT_STATEMENT_PATTERNS)
    if not matches_project_pattern and not _PROJECT_PATTERN.search(normalized_clause):
        return []
    return [
        _candidate(
            content=normalized_clause,
            category=FactCategory.PROJECT,
            confidence=0.78,
            turn=turn,
            turn_index=turn_index,
            excerpt=clause,
            tags=_tags_from_text(normalized_clause, extra=("project",)),
            transaction_time=now,
        )
    ]


def extract_facts(
    turns: str | Episode | ConversationTurn | dict[str, Any] | Sequence[Episode | ConversationTurn | dict[str, Any] | str],
    *,
    now: datetime | None = None,
) -> list[ExtractedFact]:
    extraction_time = now or _utcnow()
    normalized_turns = normalize_turns(turns, now=extraction_time)
    results: list[ExtractedFact] = []
    for turn_index, turn in enumerate(normalized_turns):
        if turn.role is not EpisodeRole.USER:
            continue
        for clause in _split_clauses(turn.content):
            clause_results = (
                _extract_goal(clause, turn, turn_index, extraction_time)
                or _extract_habit(clause, turn, turn_index, extraction_time)
                or _extract_identity(clause, turn, turn_index, extraction_time)
                or _extract_preference(clause, turn, turn_index, extraction_time)
                or _extract_project(clause, turn, turn_index, extraction_time)
            )
            results.extend(clause_results)
    return results


def deduplicate_facts(facts: Sequence[ExtractedFact]) -> list[ExtractedFact]:
    deduplicated: list[ExtractedFact] = []
    for fact in facts:
        matched_index: int | None = None
        for index, existing in enumerate(deduplicated):
            same_key = _content_key(existing.category, existing.content) == _content_key(fact.category, fact.content)
            close_match = existing.category is fact.category and _similarity(existing.content, fact.content) >= 0.8
            if same_key or close_match:
                matched_index = index
                break
        if matched_index is None:
            deduplicated.append(fact)
            continue

        existing = deduplicated[matched_index]
        seen_reference_keys = {_reference_key(reference) for reference in existing.source_references}
        merged_references = list(existing.source_references)
        for reference in fact.source_references:
            key = _reference_key(reference)
            if key in seen_reference_keys:
                continue
            seen_reference_keys.add(key)
            merged_references.append(reference)

        deduplicated[matched_index] = existing.model_copy(
            update={
                "content": _choose_content(existing.content, fact.content),
                "confidence": max(existing.confidence, fact.confidence),
                "event_time": min(existing.event_time, fact.event_time),
                "transaction_time": max(existing.transaction_time, fact.transaction_time),
                "tags": _sorted_unique_tags([*existing.tags, *fact.tags]),
                "source_episode_ids": _merge_episode_ids(existing.source_episode_ids, fact.source_episode_ids),
                "source_references": merged_references,
            }
        )
    return deduplicated


def _match_existing_fact(existing_facts: Sequence[Fact], candidate: ExtractedFact) -> Fact | None:
    best_match: Fact | None = None
    best_score = 0.0
    candidate_key = _content_fingerprint(candidate.category, candidate.content)
    for existing in existing_facts:
        if not existing.is_active:
            continue
        existing_key = existing.content_fingerprint or _content_fingerprint(existing.category, existing.content)
        if existing_key == candidate_key:
            return existing
        if existing.category is not candidate.category:
            continue
        score = _similarity(existing.content, candidate.content)
        if score > best_score:
            best_match = existing
            best_score = score
    if best_score >= 0.8:
        return best_match
    return None


async def _insert_fact(transport: MemoryTransport, candidate: ExtractedFact) -> Fact:
    fact = candidate.to_fact()
    stored = await transport.insert_fact(fact)
    if stored.id is None:
        raise ValueError("Transport returned a fact without an id.")
    await transport.insert_fact_history(
        FactHistory(
            fact_id=stored.id,
            operation=FactOperation.ADD,
            new_content=stored.content,
            new_category=stored.category,
            event_time=candidate.event_time,
            transaction_time=candidate.transaction_time,
            reason="fact extraction insert",
        )
    )
    return stored


async def _load_known_facts(transport: MemoryTransport) -> list[Fact]:
    schema_client_factory = getattr(transport, "_schema_client", None)
    runner = getattr(transport, "_run", None)
    if callable(schema_client_factory) and callable(runner):
        facts: list[Fact] = []
        page_size = 500
        offset = 0
        while True:
            response = await runner(
                lambda current_offset=offset: (
                    schema_client_factory()
                    .table("facts")
                    .select("*")
                    .eq("is_active", True)
                    .order("updated_at", desc=True)
                    .range(current_offset, current_offset + page_size - 1)
                    .execute()
                )
            )
            rows = getattr(response, "data", []) or []
            if not rows:
                break
            facts.extend(Fact.model_validate(row) for row in rows if isinstance(row, dict))
            if len(rows) < page_size:
                break
            offset += page_size
        return facts

    return await transport.search_facts(limit=5000)


async def _merge_into_existing_fact(transport: MemoryTransport, existing: Fact, candidate: ExtractedFact) -> Fact:
    updated_values = {
        "content": _choose_content(existing.content, candidate.content),
        "content_fingerprint": _content_fingerprint(candidate.category, _choose_content(existing.content, candidate.content)),
        "confidence": max(existing.confidence, candidate.confidence),
        "event_time": min(existing.event_time, candidate.event_time),
        "transaction_time": candidate.transaction_time,
        "updated_at": candidate.transaction_time,
        "tags": _sorted_unique_tags([*existing.tags, *candidate.tags]),
        "source_episode_ids": _merge_episode_ids(existing.source_episode_ids, candidate.source_episode_ids),
    }
    changes = {
        key: value
        for key, value in updated_values.items()
        if getattr(existing, key) != value
    }
    if not changes:
        return existing

    updated = await transport.update_fact(str(existing.id), changes)
    await transport.insert_fact_history(
        FactHistory(
            fact_id=updated.id or existing.id,
            operation=FactOperation.UPDATE,
            old_content=existing.content,
            new_content=updated.content,
            old_category=existing.category,
            new_category=updated.category,
            event_time=candidate.event_time,
            transaction_time=candidate.transaction_time,
            reason="fact extraction merge",
        )
    )
    return updated


async def store_facts(
    transport: MemoryTransport,
    facts: Sequence[ExtractedFact],
    *,
    existing_facts: Sequence[Fact] | None = None,
) -> list[Fact]:
    deduplicated = deduplicate_facts(facts)
    known_facts = list(existing_facts) if existing_facts is not None else await _load_known_facts(transport)
    stored: list[Fact] = []
    for candidate in deduplicated:
        existing = _match_existing_fact(known_facts, candidate)
        if existing is None:
            inserted = await _insert_fact(transport, candidate)
            known_facts.append(inserted)
            stored.append(inserted)
            continue

        merged = await _merge_into_existing_fact(transport, existing, candidate)
        for index, known_fact in enumerate(known_facts):
            if known_fact.id == existing.id:
                known_facts[index] = merged
                break
        stored.append(merged)
    return stored


async def extract_and_store_facts(
    transport: MemoryTransport,
    turns: str | Episode | ConversationTurn | dict[str, Any] | Sequence[Episode | ConversationTurn | dict[str, Any] | str],
    *,
    now: datetime | None = None,
) -> list[Fact]:
    extracted = extract_facts(turns, now=now)
    if not extracted:
        return []
    return await store_facts(transport, extracted)
