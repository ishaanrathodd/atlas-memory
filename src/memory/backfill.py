from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from memory.client import MemoryClient
from memory.models import Fact, FactCategory
from memory.transport import _looks_like_operational_content, _looks_like_reference_content

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
_HEADER_PREFIXES = ("#", "§")
_BULLET_PREFIX = re.compile(r"^(?:[-*+]\s+|\d+\.\s+)")

_CATEGORY_HINTS: list[tuple[tuple[str, ...], FactCategory]] = [
    (("preference", "preferences", "like", "likes", "favorite", "favourites", "favorites"), FactCategory.PREFERENCE),
    (("goal", "goals", "plan", "plans", "roadmap"), FactCategory.GOAL),
    (("relationship", "relationships", "family", "friends"), FactCategory.RELATIONSHIP),
    (("project", "projects", "work", "memory", "build", "coding"), FactCategory.PROJECT),
    (("health", "wellness", "fitness"), FactCategory.HEALTH),
    (("finance", "money", "budget"), FactCategory.FINANCE),
    (("habit", "habits", "routine", "routines"), FactCategory.HABIT),
    (("environment", "setup", "workspace", "tools"), FactCategory.ENVIRONMENT),
    (("identity", "profile", "about"), FactCategory.IDENTITY),
]


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN_PATTERN.findall(value.lower())
        if token not in _STOPWORDS and len(token) > 1
    }


def _jaccard_similarity(left: str, right: str) -> float:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return len(left_tokens & right_tokens) / len(union)


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _normalize_entry(value: str) -> str:
    normalized = _normalize_text(_BULLET_PREFIX.sub("", value.strip()))
    return normalized.rstrip()


def _looks_like_header(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return stripped.startswith(_HEADER_PREFIXES)


def _category_from_header(line: str) -> FactCategory:
    normalized = _normalize_text(line.strip("#§ ").replace(":", " ").lower())
    for keywords, category in _CATEGORY_HINTS:
        if any(keyword in normalized for keyword in keywords):
            return category
    return FactCategory.FACT


@dataclass(slots=True)
class BackfillEntry:
    content: str
    category: FactCategory


def _parse_memory_file(path: Path) -> list[BackfillEntry]:
    if not path.exists():
        _log(f"Backfill source missing, skipping: {path}")
        return []

    current_category = FactCategory.FACT
    entries: list[BackfillEntry] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        if _looks_like_header(stripped):
            current_category = _category_from_header(stripped)
            continue

        content = _normalize_entry(stripped)
        if not content:
            continue
        entries.append(BackfillEntry(content=content, category=current_category))
    return entries


def _is_duplicate(candidate: BackfillEntry, known_facts: Iterable[Fact]) -> bool:
    for fact in known_facts:
        if not fact.is_active:
            continue
        if _jaccard_similarity(candidate.content, fact.content) > 0.8:
            return True
    return False


def _should_skip_backfill_entry(entry: BackfillEntry) -> bool:
    lowered = entry.content.lower()
    if _looks_like_reference_content(entry.content) or _looks_like_operational_content(entry.content):
        return True
    if entry.category in {FactCategory.FACT, FactCategory.PROJECT}:
        if any(
            marker in lowered
            for marker in (
                "build complete",
                "phase 1:",
                "phase 2:",
                "phase 3:",
                "tests pass",
                "supabase_service_key",
                "reply-threading",
                "telegraph_reply_to_mode",
                "whisper model",
                "config was corrected",
            )
        ):
            return True
    return False


async def backfill_memory_files(
    client: MemoryClient,
    *,
    hermes_home: str | Path | None = None,
) -> dict[str, int]:
    root = Path(hermes_home or client.transport.__dict__.get("hermes_home") or Path.home() / ".hermes").expanduser()
    entries = [
        *_parse_memory_file(root / "memories" / "MEMORY.md"),
        *_parse_memory_file(root / "memories" / "USER.md"),
    ]

    known_facts = list(await client.search_facts(limit=1000))
    stats = {
        "total_entries": len(entries),
        "new_facts": 0,
        "skipped_duplicates": 0,
    }

    for entry in entries:
        if _should_skip_backfill_entry(entry):
            stats["skipped_duplicates"] += 1
            continue
        if _is_duplicate(entry, known_facts):
            stats["skipped_duplicates"] += 1
            continue

        stored = await client.add_fact(
            entry.content,
            entry.category.value,
            tags=["backfill"],
        )
        known_facts.append(stored)
        stats["new_facts"] += 1

    return stats
