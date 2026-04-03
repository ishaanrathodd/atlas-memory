from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from memory.models import ActiveState, Commitment, Correction, DecisionOutcome, DecisionOutcomeStatus, Directive, Episode, EpisodeRole, Fact, FactCategory, Pattern, Session, TimelineEvent
from memory.transport import (
    MemoryTransport,
    _looks_like_operational_content,
    _looks_like_reference_content,
)


MAX_FACTS_IN_CONTEXT = 10
MAX_RELEVANT_EPISODES = 6
MAX_RECENT_EPISODES = 3
MAX_HANDOFF_EPISODES = 12
MAX_DECISION_OUTCOMES_IN_CONTEXT = 4
MAX_PATTERNS_IN_CONTEXT = 4
MAX_COMMITMENTS_IN_CONTEXT = 4
MAX_TIMELINE_EVENTS_IN_CONTEXT = 4
FACT_SEARCH_LIMIT = 100
EPISODE_SEARCH_LIMIT = 12
EPISODE_SEARCH_DAYS_BACK = 3650
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
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
    "the",
    "that",
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
_GENERIC_PROJECT_MEMORY_TOKENS = {
    "actually",
    "can",
    "approach",
    "build",
    "just",
    "build",
    "current",
    "focus",
    "goal",
    "goals",
    "make",
    "memory",
    "need",
    "next",
    "priority",
    "problem",
    "project",
    "projects",
    "remember",
    "state",
    "should",
    "system",
    "user",
    "wants",
    "work",
    "working",
}
_LOW_SIGNAL_MESSAGES = {
    "hey",
    "heyy",
    "heyyy",
    "hi",
    "hello",
    "yo",
    "yoo",
    "sup",
    "whats up",
    "what's up",
    "ok",
    "okay",
    "k",
    "cool",
    "cools",
    "hmm",
    "hmmm",
    "nevermind",
}
_GENERIC_DECISION_OUTCOME_TAGS = {
    "communication",
    "decision-outcome",
    "delivery",
    "derived",
    "discord",
    "failure",
    "local",
    "memory",
    "mixed",
    "open",
    "other",
    "signal",
    "slack",
    "success",
    "telegram",
    "tooling",
    "webhook",
    "whatsapp",
    "workflow",
}
_LOW_VALUE_DECISION_OUTCOME_LESSONS = {
    "Keep the part that worked, but preserve the failure mode as a visible constraint.",
    "Re-check the exact failure path before repeating the same change.",
}
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\s*;\s*")


def _normalize_text(value: str, *, max_length: int = 240) -> str:
    collapsed = " ".join(value.split())
    if len(collapsed) <= max_length:
        return collapsed
    return collapsed[: max_length - 3].rstrip() + "..."


def _summary_sentences(value: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(_normalize_text(value, max_length=800)) if part.strip()]


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN_PATTERN.findall(value.lower())
        if token not in _STOPWORDS and len(token) > 1
    }


def _token_overlap_count(query_tokens: set[str], candidate_tokens: set[str]) -> int:
    overlap: set[str] = set()
    for query_token in query_tokens:
        for candidate_token in candidate_tokens:
            if query_token == candidate_token:
                overlap.add(query_token)
                break
            if len(query_token) >= 6 and len(candidate_token) >= 6:
                if query_token.startswith(candidate_token) or candidate_token.startswith(query_token):
                    overlap.add(query_token)
                    break
    return len(overlap)


def _sort_datetime(value: datetime | None) -> datetime:
    return value or datetime.min.replace(tzinfo=timezone.utc)


def _query_targets_operational_context(value: str) -> bool:
    lowered = (value or "").lower()
    markers = (
        "memory processor",
        "process-memory",
        "daemon",
        "status report",
        "facts extracted",
        "episode_count",
        "fact_count",
        "session_count",
    )
    return any(marker in lowered for marker in markers)


def _query_targets_advice(value: str) -> bool:
    lowered = (value or "").lower()
    markers = (
        "how should",
        "how do i",
        "how do we",
        "what should",
        "should i",
        "should we",
        "approach this",
        "handle this",
        "best way",
        "advice",
        "what worked",
        "what failed",
        "problem",
        "issue",
        "stuck",
        "fix this",
    )
    return any(marker in lowered for marker in markers)


def _query_targets_patterns(value: str) -> bool:
    lowered = (value or "").lower()
    markers = (
        "how should",
        "what should",
        "why do i",
        "why do we",
        "what pattern",
        "biggest fear",
        "what do you think about me",
        "what do you notice",
        "tendency",
        "approach this",
        "handle this",
        "what am i doing",
        "what keeps happening",
    )
    return any(marker in lowered for marker in markers)


def _query_targets_memory_context(value: str) -> bool:
    lowered = (value or "").lower()
    markers = (
        "memory",
        "supabase",
        "session",
        "prompt",
        "context",
        "retrieval",
        "facts",
        "episodes",
    )
    return any(marker in lowered for marker in markers)


def _query_targets_timeline(value: str) -> bool:
    lowered = (value or "").lower()
    markers = (
        "what happened",
        "last week",
        "last month",
        "yesterday",
        "recently",
        "timeline",
        "that day",
        "earlier this week",
        "what was i doing",
        "what were we doing",
    )
    return any(marker in lowered for marker in markers)


def _query_targets_continuity(value: str) -> bool:
    lowered = (value or "").lower()
    markers = (
        "what were we doing",
        "what were we talking",
        "what did we last talk",
        "what did we last discuss",
        "what were we working on",
        "continue where",
        "pick up where",
        "pick up from",
        "continue from",
        "last chatted",
        "last talked",
        "previous session",
        "new session",
        "ongoing chat",
        "last 15",
        "last 20",
        "last 30",
        "recent messages",
    )
    return any(marker in lowered for marker in markers)


def _query_targets_delivery_memory(value: str) -> bool:
    lowered = (value or "").lower()
    markers = (
        "persistence",
        "persist",
        "telegram",
        "whatsapp",
        "voice",
        "transcript",
        "reply",
        "message",
        "supabase",
    )
    return any(marker in lowered for marker in markers)


def _query_targets_project_context(value: str) -> bool:
    lowered = (value or "").lower()
    markers = (
        "memory project",
        "supabase",
        "gateway",
        "schema",
        "migration",
        "directive",
        "active state",
        "retrieval",
        "context injection",
        "session",
    )
    return any(marker in lowered for marker in markers)


def _looks_like_project_build_memory(text: str, tags: Sequence[str] | None = None) -> bool:
    lowered = (text or "").lower()
    tag_text = " ".join(tags or []).lower()
    combined = f"{lowered} {tag_text}".strip()
    markers = (
        "memory",
        "supabase",
        "gateway",
        "migration",
        "schema",
        "directive",
        "active state",
        "context injection",
        "pre_llm_call",
        "build roadmap",
        "memory project status",
        "git root",
        "reply-to-message",
        "reply to message",
        "roadmap",
        "pipeline",
        "mcp",
        "e2e",
        "daemon",
        "gw restart",
        "file operations",
        "subagent",
    )
    return any(marker in combined for marker in markers)


def _project_build_overlap(text: str, tags: Sequence[str] | None, query_tokens: set[str]) -> int:
    meaningful_query_tokens = {token for token in query_tokens if token not in _GENERIC_PROJECT_MEMORY_TOKENS}
    if not meaningful_query_tokens:
        return 0
    candidate_tokens = _tokenize(" ".join(part for part in [text, " ".join(tags or [])] if part))
    candidate_tokens = {token for token in candidate_tokens if token not in _GENERIC_PROJECT_MEMORY_TOKENS}
    return _token_overlap_count(meaningful_query_tokens, candidate_tokens)


def _clean_fact_content(fact: Fact) -> str:
    text = _normalize_text(fact.content, max_length=260)
    lowered = text.lower()
    if lowered.startswith("ishaan profile for "):
        profile_blob = text.split(":", 1)[1].strip() if ":" in text else text
        preferred_bits: list[str] = []
        fallback_bits: list[str] = []
        for sentence in _summary_sentences(profile_blob):
            lowered_sentence = sentence.lower()
            if any(
                marker in lowered_sentence
                for marker in (
                    "mumbai",
                    "b.tech",
                    "computer engineering",
                    "solo founder",
                    "upstorr.com",
                    "macbook",
                    "college ends",
                    "works 15-hour days",
                )
            ):
                preferred_bits.append(sentence.rstrip("."))
            elif len(fallback_bits) < 2:
                fallback_bits.append(sentence.rstrip("."))
        bits = preferred_bits[:5] or fallback_bits[:3]
        if bits:
            return _normalize_text("Ishaan profile: " + "; ".join(bits), max_length=220)
    return text


def _clean_timeline_summary(event: TimelineEvent) -> str:
    summary = _normalize_text(event.summary, max_length=320)
    if event.kind.value != "session_summary":
        return summary

    clauses: list[str] = []
    for sentence in _summary_sentences(summary):
        lowered = sentence.lower()
        if lowered.startswith("user:"):
            continue
        if any(
            marker in lowered
            for marker in (
                "current state:",
                "episodes",
                "facts in supabase",
                "session_search functional",
                "system is working",
                "tested how memory retrieval works",
                "confirmed system is working",
                "memory retrieval works",
            )
        ):
            continue
        cleaned = sentence.rstrip(".")
        if cleaned:
            clauses.append(cleaned)
    if clauses:
        rendered = ". ".join(clauses[:2])
        if not rendered.endswith((".", "!", "?")):
            rendered += "."
        return _normalize_text(rendered, max_length=220)
    return summary


def _clean_handoff_text(value: str, *, max_length: int = 180) -> str:
    normalized = _normalize_text(value, max_length=max_length)
    if not normalized:
        return normalized
    sentences: list[str] = []
    for sentence in _summary_sentences(normalized):
        lowered = sentence.lower()
        if _looks_like_reference_content(sentence) or _looks_like_operational_content(sentence):
            continue
        if any(
            marker in lowered
            for marker in (
                "current state:",
                "facts in supabase",
                "session_search functional",
                "system is working",
                "memory processor",
            )
        ):
            continue
        cleaned = sentence.strip().strip("- ").rstrip(".")
        if cleaned:
            sentences.append(cleaned)
    if not sentences:
        return normalized
    rendered = ". ".join(sentences[:2]).strip()
    if rendered and rendered[-1] not in ".!?":
        rendered += "."
    return _normalize_text(rendered, max_length=max_length)


def _timeline_event_overlap(event: TimelineEvent, query_tokens: set[str]) -> int:
    tokens = _tokenize(" ".join([event.title or "", event.summary, " ".join(event.tags)]))
    return _token_overlap_count(query_tokens, tokens)


def _looks_like_low_quality_timeline_summary(summary: str) -> bool:
    lowered = (summary or "").lower()
    return any(
        marker in lowered
        for marker in (
            "memory processor",
            "missing timeline_events table",
            "created table with schema",
            "rls policies",
            "indexes.",
        )
    )


def _query_targets_commitments(value: str) -> bool:
    lowered = (value or "").lower()
    markers = (
        "what are you tracking",
        "what are you remembering",
        "what did you promise",
        "did you remember",
        "follow up",
        "remind me",
        "check on",
        "status",
        "what's next",
    )
    return any(marker in lowered for marker in markers)


def _query_is_low_signal(value: str) -> bool:
    normalized = " ".join((value or "").lower().split())
    if not normalized:
        return True
    normalized = re.sub(r"[^a-z0-9\s']+", "", normalized).strip()
    if normalized in _LOW_SIGNAL_MESSAGES:
        return True
    tokens = [token for token in normalized.split() if token]
    if len(tokens) <= 2 and all(token in {"hey", "hi", "hello", "yo", "sup", "ok", "okay", "cool", "cools", "hmm", "bro", "bhai"} for token in tokens):
        return True
    return False


def _fact_relevance_score(fact: Fact, query_tokens: set[str]) -> float:
    content_tokens = _tokenize(fact.content)
    tag_tokens = {token for tag in fact.tags for token in _tokenize(tag)}
    category_tokens = _tokenize(fact.category.value)

    content_overlap = _token_overlap_count(query_tokens, content_tokens)
    tag_overlap = _token_overlap_count(query_tokens, tag_tokens)
    category_overlap = _token_overlap_count(query_tokens, category_tokens)

    return (
        (content_overlap * 4.0)
        + (tag_overlap * 2.5)
        + (category_overlap * 1.5)
        + float(fact.confidence)
        + (float(fact.access_count) * 0.05)
    )


def _fact_overlap(fact: Fact, query_tokens: set[str]) -> int:
    content_tokens = _tokenize(fact.content)
    tag_tokens = {token for tag in fact.tags for token in _tokenize(tag)}
    category_tokens = _tokenize(fact.category.value)
    return max(
        _token_overlap_count(query_tokens, content_tokens),
        _token_overlap_count(query_tokens, tag_tokens),
        _token_overlap_count(query_tokens, category_tokens),
    )


def _looks_like_low_quality_fact(fact: Fact) -> bool:
    lowered = (fact.content or "").lower()
    if _looks_like_reference_content(fact.content):
        return True
    if _looks_like_operational_content(fact.content):
        return True
    if "backfill" in fact.tags and not fact.source_episode_ids:
        if len(fact.content) > 320:
            return True
        if any(
            marker in lowered
            for marker in (
                "build complete",
                "tests pass",
                "skipped",
                "supabase_service_key",
                "config was corrected",
                "operating standard",
                "reply-threading",
                "telegraph_reply_to_mode",
                "text_to_speech",
                "whisper model",
            )
        ):
            return True
    if fact.category is FactCategory.GOAL and lowered.startswith("user wants to "):
        if any(
            marker in lowered
            for marker in (
                "send you",
                "check ",
                "see ",
                "figure out ",
                "restart ",
                "reply",
                "bubble",
                "gateway",
                "supabase",
                "schema",
                "migration",
                "delegate this to codex",
                "save this session",
                "return json objects",
                "know becuase",
            )
        ):
            return True
    if fact.category is FactCategory.FACT and lowered.startswith("user wants "):
        if any(
            marker in lowered
            for marker in (
                "telegram dm",
                "home destination",
                "voice replies",
                "text and voice replies",
                "deliver='origin'",
            )
        ):
            return True
    if fact.category is FactCategory.FACT:
        if any(
            marker in lowered
            for marker in (
                "no emojis in skills",
                "notify new skills only",
                "cron: skip delivery on no-op",
                "memory purpose",
                "memory vision",
                "memory build roadmap",
                "memory project status",
                "locally they live under one git root",
                "always test gw restart before pushing",
                "memory rebrand in progress",
            )
        ):
            return True
    if fact.category is FactCategory.PREFERENCE and (
        len(_tokenize(fact.content)) > 12 or re.search(r"\b(?:you|your|u)\b", lowered)
    ):
        return True
    if fact.category is FactCategory.PREFERENCE and "remember that" in lowered:
        return True
    if fact.category is FactCategory.PREFERENCE and "need to be perfect" in lowered:
        return True
    if fact.category is FactCategory.PROJECT and not fact.source_episode_ids:
        if any(marker in lowered for marker in ("let me ", "what's next", "what is left", "table was missing")):
            return True
    if fact.category is FactCategory.PROJECT:
        if lowered.startswith(
            (
                "i should ",
                "let me ",
                "but the gateway was restarted",
                "overight build complete",
                "wait,",
                "this is likely",
                "- [project]",
            )
        ):
            return True
        if any(
            marker in lowered
            for marker in (
                "overnight build complete",
                "build complete",
                "phase 1:",
                "phase 2:",
                "phase 3:",
                "tests pass",
                "memorybridge",
                "always_on_spec",
                "gateway was restarted",
                "subtree migration",
                "not been applied",
                "every atomic fact about you",
                "env file for the memory project",
                "postgrest notify reload",
                "code patches",
                "bigger project",
                "important discussion",
                "2342 messages",
                "mcp knows which project to target",
                "patch file rule",
                "same upstorr project",
                "lmao are u crazy",
            )
        ):
            return True
    if fact.category is FactCategory.GOAL:
        if any(
            marker in lowered
            for marker in (
                "delegate the file operations",
                "e2e tests and daemon can actually talk to the db",
                "patch file rule",
                "tts and keep my actual response minimal or empty",
                "force push since the memory repo on github",
            )
        ):
            return True
    return False


def _looks_like_low_quality_active_state_line(line: str) -> bool:
    lowered = (line or "").lower()
    if any(
        marker in lowered
        for marker in (
            "what is left regarding the memory project",
            "return json objects",
            "this is likely a migration that hasn't been applied",
            "r u feeling the upgrade",
            "or u r just lying to me",
            "reply to",
            "voice transcription working",
            "paused crons",
            "gpt 5.4",
            "openai api key",
            "memory build roadmap",
            "memory project status",
            "pre_llm_call",
            "git root",
            "heres the question",
            "here's the question",
        )
    ):
        return True
    return False


def _episode_relevance_score(episode: Episode, query_tokens: set[str]) -> float:
    content_tokens = _tokenize(episode.content)
    metadata_tokens = _tokenize(" ".join(str(value) for value in episode.message_metadata.values()))
    return (
        (_token_overlap_count(query_tokens, content_tokens) * 4.0)
        + (_token_overlap_count(query_tokens, metadata_tokens) * 1.5)
        + float(episode.emotional_intensity)
    )


def _decision_outcome_relevance_score(outcome: DecisionOutcome, query_tokens: set[str]) -> float:
    lesson = outcome.lesson or ""
    if lesson in _LOW_VALUE_DECISION_OUTCOME_LESSONS:
        lesson = ""
    content_tokens = _tokenize(
        " ".join(
            part for part in [outcome.title or "", outcome.decision, outcome.outcome, lesson] if part
        )
    )
    tag_tokens = {
        token
        for tag in outcome.tags
        if str(tag or "").strip().lower() not in _GENERIC_DECISION_OUTCOME_TAGS
        for token in _tokenize(tag)
    }
    return (
        (_token_overlap_count(query_tokens, content_tokens) * 4.0)
        + (_token_overlap_count(query_tokens, tag_tokens) * 2.0)
        + (float(outcome.importance_score) * 0.35)
        + (float(outcome.confidence) * 0.25)
    )


def _pattern_relevance_score(pattern: Pattern, query_tokens: set[str]) -> float:
    tokens = _tokenize(
        " ".join(
            [
                pattern.pattern_type.value,
                pattern.statement,
                pattern.description or "",
                " ".join(pattern.tags),
            ]
        )
    )
    return (
        (_token_overlap_count(query_tokens, tokens) * 4.0)
        + (float(pattern.impact_score) * 0.4)
        + (float(pattern.frequency_score) * 0.3)
        + (float(pattern.confidence) * 0.25)
    )


def _looks_like_trivial_episode(content: str) -> bool:
    normalized = re.sub(r"[^a-z0-9\s']+", "", (content or "").lower()).strip()
    if not normalized:
        return True
    if normalized in _LOW_SIGNAL_MESSAGES:
        return True
    tokens = [token for token in normalized.split() if token]
    if len(tokens) <= 3 and all(token in {"hey", "hi", "hello", "yo", "sup", "ok", "okay", "cool", "cools", "hmm", "bro", "bhai", "man"} for token in tokens):
        return True
    return False


def _dedupe_decision_outcomes(outcomes: list[DecisionOutcome]) -> list[DecisionOutcome]:
    deduped: list[DecisionOutcome] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for outcome in outcomes:
        key = (
            _normalize_text(outcome.decision, max_length=160).lower(),
            _normalize_text(outcome.outcome, max_length=160).lower(),
            _normalize_text(outcome.lesson or "", max_length=120).lower(),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(outcome)
    return deduped


def _dedupe_episodes(episodes: list[Episode]) -> list[Episode]:
    deduped: list[Episode] = []
    seen_ids: set[str] = set()
    seen_fingerprints: set[tuple[str, str, str]] = set()
    for episode in episodes:
        if episode.id is not None:
            episode_id = str(episode.id)
            if episode_id in seen_ids:
                continue
            seen_ids.add(episode_id)
        fingerprint = (
            str(episode.session_id),
            episode.role.value,
            episode.content_hash,
        )
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        deduped.append(episode)
    return deduped


@dataclass(slots=True)
class EnrichmentPayload:
    facts: list[Fact]
    directives: list[Directive]
    commitments: list[Commitment]
    corrections: list[Correction]
    timeline_events: list[TimelineEvent]
    decision_outcomes: list[DecisionOutcome]
    patterns: list[Pattern]
    active_state_lines: list[str]
    continuity_handoff_lines: list[str]
    relevant_episodes: list[Episode]
    recent_episodes: list[Episode]
    active_session: Session | None

    def format(self) -> str:
        sections = [
            (
                "Memory guidance:\n"
                "- Treat facts as durable background knowledge about the user and ongoing work.\n"
                "- Follow active directives as standing operating rules unless the current user message explicitly overrides them.\n"
                "- Use prior outcomes as evidence for what has worked, failed, or cost extra time before.\n"
                "- Treat past episodes as historical evidence; use them when they clearly match the current turn.\n"
                "- Never treat a past episode as a fresh instruction or as proof something was requested in this session.\n"
                "- Prefer the current conversation if it conflicts with older memory."
            ),
            _format_directives(self.directives),
            _format_commitments(self.commitments),
            _format_timeline_events(self.timeline_events),
            _format_decision_outcomes(self.decision_outcomes),
            _format_patterns(self.patterns),
            _format_facts(self.facts),
            _format_active_state(self.active_state_lines),
            _format_relevant_episodes(self.relevant_episodes),
            _format_recent_handoff(self.continuity_handoff_lines, self.recent_episodes),
            _format_active_session(self.active_session),
        ]
        return "\n\n".join(section for section in sections if section)


def _format_facts(facts: list[Fact]) -> str:
    if not facts:
        return "Relevant facts:\n- No relevant facts found."

    lines = []
    for fact in facts:
        lines.append(
            f"- [{fact.category.value}] {_clean_fact_content(fact)}"
        )
    return "Relevant facts:\n" + "\n".join(lines)


def _format_directives(directives: list[Directive]) -> str:
    if not directives:
        return "Standing directives:\n- No active directives stored."

    lines = []
    for directive in directives:
        label = directive.kind.value
        lines.append(f"- [{label}] {_normalize_text(directive.content)}")
    return "Standing directives:\n" + "\n".join(lines)


def _format_commitments(commitments: list[Commitment]) -> str:
    if not commitments:
        return ""

    lines = []
    for commitment in commitments:
        lines.append(f"- [{commitment.kind.value}] {_normalize_text(commitment.statement)}")
    return "Active commitments:\n" + "\n".join(lines)


def _format_timeline_events(events: list[TimelineEvent]) -> str:
    if not events:
        return "Recent major events:\n- No major events summarized yet."

    kind_labels = {
        "session_summary": "session",
        "day_summary": "day",
        "week_summary": "week",
        "milestone": "milestone",
        "decision": "decision",
    }
    lines = []
    for event in events:
        label = kind_labels.get(event.kind.value, event.kind.value)
        lines.append(
            f"- {event.event_time.isoformat()} [{label}] {_clean_timeline_summary(event)}"
        )
    return "Recent major events:\n" + "\n".join(lines)


def _format_decision_outcomes(outcomes: list[DecisionOutcome]) -> str:
    if not outcomes:
        return ""

    lines = []
    for outcome in outcomes:
        lesson_suffix = (
            f" Lesson: {_normalize_text(outcome.lesson)}"
            if outcome.lesson and outcome.lesson not in _LOW_VALUE_DECISION_OUTCOME_LESSONS
            else ""
        )
        lines.append(
            f"- [{outcome.status.value}] {_normalize_text(outcome.decision)} -> {_normalize_text(outcome.outcome)}{lesson_suffix}"
        )
    return "Relevant prior outcomes:\n" + "\n".join(lines)


def _format_patterns(patterns: list[Pattern]) -> str:
    if not patterns:
        return ""

    lines = []
    for pattern in patterns:
        description_suffix = f" Evidence: {_normalize_text(pattern.description)}" if pattern.description else ""
        lines.append(
            f"- [{pattern.pattern_type.value}] {_normalize_text(pattern.statement)}{description_suffix}"
        )
    return "Relevant patterns:\n" + "\n".join(lines)


def _decision_outcome_overlap(outcome: DecisionOutcome, query_tokens: set[str]) -> int:
    lesson = outcome.lesson or ""
    if lesson in _LOW_VALUE_DECISION_OUTCOME_LESSONS:
        lesson = ""
    combined_tokens = _tokenize(
        " ".join(
            [
                outcome.title or "",
                outcome.decision,
                outcome.outcome,
                lesson,
                " ".join(
                    tag
                    for tag in outcome.tags
                    if str(tag or "").strip().lower() not in _GENERIC_DECISION_OUTCOME_TAGS
                ),
            ]
        )
    )
    return _token_overlap_count(query_tokens, combined_tokens)


def _pattern_overlap(pattern: Pattern, query_tokens: set[str]) -> int:
    combined_tokens = _tokenize(
        " ".join(
            [
                pattern.pattern_type.value,
                pattern.statement,
                pattern.description or "",
                " ".join(pattern.tags),
            ]
        )
    )
    return _token_overlap_count(query_tokens, combined_tokens)


def _correction_overlap(correction: Correction, text: str) -> int:
    correction_tokens = _tokenize(" ".join(part for part in [correction.statement, correction.target_text or ""] if part))
    text_tokens = _tokenize(text)
    return _token_overlap_count(correction_tokens, text_tokens)


def _is_corrected_text(text: str, corrections: list[Correction]) -> bool:
    for correction in corrections:
        if _correction_overlap(correction, text) >= 3:
            return True
    return False


def _format_recent_episodes(episodes: list[Episode]) -> str:
    if not episodes:
        return "Recent cross-session continuity:\n- No recent episodes found."

    lines = []
    for episode in episodes:
        timestamp = episode.message_timestamp.isoformat()
        lines.append(
            f"- {timestamp} [{episode.role.value}, {episode.platform.value}] {_normalize_text(episode.content)}"
        )
    return "Recent cross-session continuity:\n" + "\n".join(lines)


def _continuity_bootstrap_active(active_session: Session | None, *, continuity_query: bool) -> bool:
    if continuity_query:
        return True
    if active_session is None:
        return True
    return int(active_session.message_count or 0) <= 2


def _continuity_candidate_episodes(episodes: list[Episode], corrections: list[Correction]) -> list[Episode]:
    filtered: list[Episode] = []
    for episode in _dedupe_episodes(episodes):
        if _looks_like_trivial_episode(episode.content):
            continue
        if _is_corrected_text(episode.content, corrections):
            continue
        if _looks_like_reference_content(episode.content, getattr(episode, "message_metadata", {}) or {}):
            continue
        if _looks_like_operational_content(episode.content, getattr(episode, "message_metadata", {}) or {}):
            continue
        filtered.append(episode)
    filtered.sort(key=lambda episode: _sort_datetime(episode.message_timestamp), reverse=True)
    return filtered


def _recent_session_id_for_handoff(episodes: list[Episode]) -> str | None:
    if not episodes:
        return None
    return str(episodes[0].session_id)


def _meaningful_session_episodes(episodes: list[Episode], corrections: list[Correction]) -> list[Episode]:
    filtered = _continuity_candidate_episodes(episodes, corrections)
    filtered.sort(key=lambda episode: _sort_datetime(episode.message_timestamp))
    return filtered


def _most_recent_episode_for_role(episodes: list[Episode], role: EpisodeRole) -> Episode | None:
    candidates = [episode for episode in episodes if episode.role == role]
    if not candidates:
        return None
    return max(candidates, key=lambda episode: _sort_datetime(episode.message_timestamp))


def _episode_handoff_text(episode: Episode | None, *, max_length: int = 180) -> str:
    if episode is None:
        return ""
    return _clean_handoff_text(episode.content, max_length=max_length)


def _is_meaningful_assistant_handoff(episode: Episode | None) -> bool:
    text = _episode_handoff_text(episode)
    if not text:
        return False
    lowered = text.lower()
    if _looks_like_trivial_episode(lowered):
        return False
    if lowered.startswith(("yo", "hey", "hi", "hello", "sup", "what's good")):
        return False
    return len(_tokenize(text)) >= 6


def _is_meaningful_carry_forward_text(text: str, *, prior_summary_exists: bool) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if _looks_like_trivial_episode(lowered):
        return False
    if prior_summary_exists and len(_tokenize(text)) < 10:
        return False
    if any(
        marker in lowered
        for marker in (
            "what do you know about me",
            "what do uk about me",
            "tell me about me",
            "who am i",
            "how are you",
            "what's up",
            "whats up",
        )
    ):
        return False
    token_count = len(_tokenize(text))
    if token_count < 5:
        return False
    return any(
        marker in lowered
        for marker in (
            "?",
            "continue",
            "pick up",
            "finish",
            "fix",
            "debug",
            "verify",
            "check",
            "test",
            "ship",
            "launch",
            "review",
            "sync",
            "follow up",
            "next",
            "need to",
            "need ",
            "needs ",
            "want to",
            "working on",
            "in progress",
            "blocked on",
            "should",
            "can you",
            "let's",
            "lets",
        )
    )


def _is_meaningful_user_carry_forward(episode: Episode | None, *, prior_summary_exists: bool) -> bool:
    text = _episode_handoff_text(episode)
    return _is_meaningful_carry_forward_text(text, prior_summary_exists=prior_summary_exists)


def _continuity_summary_line(previous_session: Session | None, previous_episodes: list[Episode]) -> str | None:
    if previous_session and previous_session.summary:
        summary = _clean_handoff_text(previous_session.summary, max_length=200)
        if summary:
            return f"Last thread: {_sentence_start(summary)}"

    last_user = _most_recent_episode_for_role(previous_episodes, EpisodeRole.USER)
    if _is_meaningful_user_carry_forward(last_user, prior_summary_exists=False):
        cleaned = _episode_handoff_text(last_user, max_length=180)
        if cleaned:
            return f"Last thread: {_sentence_start(cleaned)}"

    last_assistant = _most_recent_episode_for_role(previous_episodes, EpisodeRole.ASSISTANT)
    if _is_meaningful_assistant_handoff(last_assistant):
        cleaned = _episode_handoff_text(last_assistant, max_length=180)
        if cleaned:
            return f"Last thread: {_sentence_start(cleaned)}"
    return None


def _continuity_carry_forward_line(
    previous_session: Session | None,
    active_state_records: list[ActiveState],
    commitments: list[Commitment],
    previous_episodes: list[Episode],
) -> str | None:
    priority_order = ("open_loop", "blocker")
    ranked_records = sorted(
        [
            record
            for record in active_state_records
            if record.kind.value in priority_order
        ],
        key=lambda record: (
            priority_order.index(record.kind.value),
            -float(record.priority_score),
            -_sort_datetime(record.last_observed_at).timestamp(),
        ),
    )
    for record in ranked_records:
        content = _clean_handoff_text(_active_state_content_without_generic_title(record.content, record.title))
        if content and _is_meaningful_carry_forward_text(
            content,
            prior_summary_exists=previous_session is not None and bool(previous_session.summary),
        ):
            return f"Carry forward: {_sentence_start(content)}"

    open_commitments = sorted(
        commitments,
        key=lambda item: (
            -float(item.priority_score),
            -_sort_datetime(item.last_observed_at).timestamp(),
        ),
    )
    for commitment in open_commitments:
        content = _clean_handoff_text(commitment.statement)
        if content and _is_meaningful_carry_forward_text(
            content,
            prior_summary_exists=previous_session is not None and bool(previous_session.summary),
        ):
            return f"Carry forward: {_sentence_start(content)}"

    if previous_episodes:
        last_episode = previous_episodes[-1]
        if last_episode.role == EpisodeRole.USER and _is_meaningful_user_carry_forward(
            last_episode,
            prior_summary_exists=previous_session is not None and bool(previous_session.summary),
        ):
            content = _episode_handoff_text(last_episode)
            if content:
                label = "Open question" if content.endswith("?") else "Carry forward"
                return f"{label}: {_sentence_start(content)}"
    return None


def _continuity_assistant_line(previous_episodes: list[Episode]) -> str | None:
    last_assistant = _most_recent_episode_for_role(previous_episodes, EpisodeRole.ASSISTANT)
    if not _is_meaningful_assistant_handoff(last_assistant):
        return None
    cleaned = _episode_handoff_text(last_assistant)
    if not cleaned:
        return None
    return f"Assistant was helping with: {_sentence_start(cleaned)}"


def _continuity_tone_line(previous_session: Session | None, previous_episodes: list[Episode]) -> str | None:
    user_episodes = [episode for episode in previous_episodes if episode.role == EpisodeRole.USER and episode.dominant_emotion]
    strongest = None
    if user_episodes:
        strongest = max(user_episodes, key=lambda episode: float(episode.emotional_intensity or 0.0))
    if previous_session and previous_session.dominant_emotions and strongest is not None:
        if float(strongest.emotional_intensity or 0.0) >= 0.6:
            emotions = [emotion.strip() for emotion in previous_session.dominant_emotions if str(emotion or "").strip()]
            if emotions:
                return f"Last tone: {', '.join(emotions[:2])}"
    if strongest is None or float(strongest.emotional_intensity or 0.0) < 0.6 or not strongest.dominant_emotion:
        return None
    return f"Last tone: {strongest.dominant_emotion}"


def _build_continuity_handoff_lines(
    previous_session: Session | None,
    previous_episodes: list[Episode],
    *,
    active_state_records: list[ActiveState],
    commitments: list[Commitment],
) -> list[str]:
    lines: list[str] = []
    for line in (
        _continuity_summary_line(previous_session, previous_episodes),
        _continuity_carry_forward_line(previous_session, active_state_records, commitments, previous_episodes),
        _continuity_assistant_line(previous_episodes),
        _continuity_tone_line(previous_session, previous_episodes),
    ):
        if not line:
            continue
        normalized = " ".join(line.lower().split())
        if normalized in {"carry forward:", "last thread:", "assistant was helping with:", "last tone:"}:
            continue
        if any(" ".join(existing.lower().split()) == normalized for existing in lines):
            continue
        lines.append(line)
    return lines[:4]


def _format_recent_handoff(handoff_lines: list[str], fallback_episodes: list[Episode]) -> str:
    if not handoff_lines:
        return _format_recent_episodes(fallback_episodes)
    return "Recent cross-session continuity:\n" + "\n".join(f"- {_normalize_text(line)}" for line in handoff_lines)


def _format_active_state(lines: list[str]) -> str:
    if not lines:
        return "Active life snapshot:\n- No clear active state inferred yet."
    return "Active life snapshot:\n" + "\n".join(f"- {_normalize_text(line)}" for line in lines)


def _format_relevant_episodes(episodes: list[Episode]) -> str:
    if not episodes:
        return "Relevant prior conversations:\n- No semantically relevant episodes found."

    lines = []
    for episode in episodes:
        timestamp = episode.message_timestamp.isoformat()
        lines.append(
            f"- {timestamp} [{episode.role.value}, {episode.platform.value}] {_normalize_text(episode.content)}"
        )
    return "Relevant prior conversations:\n" + "\n".join(lines)


def _format_active_session(session: Session | None) -> str:
    if session is None:
        return "Active session summary:\n- No active session."

    lines = [
        f"- Platform: {session.platform.value}",
        f"- Started: {session.started_at.isoformat()}",
        f"- Messages: {session.message_count}",
        f"- User messages: {session.user_message_count}",
    ]
    if session.summary:
        lines.append(f"- Summary: {_normalize_text(session.summary)}")
    if session.dominant_emotions:
        lines.append(f"- Dominant emotions: {', '.join(session.dominant_emotions)}")
    return "Active session summary:\n" + "\n".join(lines)


def _derive_active_state_lines(
    facts: list[Fact],
    recent_episodes: list[Episode],
    active_session: Session | None,
    *,
    query_tokens: set[str],
    project_query: bool,
) -> list[str]:
    lines: list[str] = []

    focus_categories = {"project", "goal", "health", "finance", "relationship", "habit", "identity"}
    recent_facts = sorted(
        [fact for fact in facts if fact.category.value in focus_categories],
        key=lambda fact: (
            _sort_datetime(fact.updated_at or fact.created_at),
            fact.confidence,
        ),
        reverse=True,
    )
    for fact in recent_facts[:3]:
        if (
            not project_query
            and fact.category in {FactCategory.PROJECT, FactCategory.GOAL}
            and _looks_like_project_build_memory(fact.content, fact.tags)
            and _project_build_overlap(fact.content, fact.tags, query_tokens) == 0
        ):
            continue
        if not project_query and fact.category in {FactCategory.PROJECT, FactCategory.GOAL} and _fact_overlap(fact, query_tokens) == 0:
            continue
        lines.append(f"Likely active {fact.category.value}: {_clean_fact_content(fact)}")

    if active_session and active_session.summary and project_query:
        lines.append(f"Current session focus: {active_session.summary}")

    recent_user_episodes = sorted(
        [
            episode
            for episode in recent_episodes
            if episode.role.value == "user"
            and not _looks_like_reference_content(episode.content, episode.message_metadata)
            and not _looks_like_operational_content(episode.content, episode.message_metadata)
        ],
        key=lambda episode: _sort_datetime(episode.message_timestamp),
        reverse=True,
    )
    for episode in recent_user_episodes[:2]:
        lines.append(f"Recent user context: {episode.content}")

    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = " ".join(line.lower().split())
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(line)
    return deduped[:5]


def _active_state_content_without_generic_title(content: str, title: str | None) -> str:
    collapsed = _normalize_text(content, max_length=220).strip()
    generic_titles = {
        "current priority",
        "primary focus",
        "secondary focus",
        "current blocker",
        "open loop",
        "recent emotional tone",
        "emotional tone",
    }
    if title and title.strip().lower() in generic_titles:
        return collapsed
    return f"{title}: {collapsed}" if title else collapsed


def _sentence_start(value: str) -> str:
    trimmed = (value or "").strip()
    if not trimmed:
        return trimmed
    return trimmed[0].upper() + trimmed[1:]


def _render_active_state_record(record: ActiveState) -> str:
    content = _active_state_content_without_generic_title(record.content, record.title)
    lowered = content.lower()
    if record.kind.value == "project":
        prefix = "Also in focus" if record.state_key.endswith("secondary") else "In focus"
        if lowered.startswith(("building ", "focused on ", "working on ", "designing ", "trying to ")):
            return _sentence_start(content)
        return f"{prefix}: {_sentence_start(content)}"
    if record.kind.value == "priority":
        if lowered.startswith(("focused on ", "working on ", "building ", "designing ", "trying to ")):
            return _sentence_start(content)
        if lowered.startswith(("wants ", "want ", "needs ", "need to ")):
            return f"Priority: {_sentence_start(content)}"
        return f"Priority: {_sentence_start(content)}"
    if record.kind.value == "blocker":
        return f"Main blocker: {_sentence_start(content)}"
    if record.kind.value == "open_loop":
        if content.endswith("?"):
            return f"Open question: {_sentence_start(content)}"
        return f"Open loop: {_sentence_start(content)}"
    if record.kind.value == "emotion_state":
        if lowered.startswith("leaning toward "):
            return f"Emotional tone: {content}"
        return f"Emotional tone: {_sentence_start(content)}"
    label = record.kind.value.replace("_", " ")
    return f"{label.capitalize()}: {_sentence_start(content)}"


def _active_state_lines_from_records(
    records: list[ActiveState],
    *,
    query_tokens: set[str],
    project_query: bool,
) -> list[str]:
    lines: list[str] = []
    for record in records:
        line = _render_active_state_record(record)
        if _looks_like_low_quality_active_state_line(line):
            continue
        if (
            not project_query
            and record.kind.value in {"project", "blocker", "open_loop"}
            and _looks_like_project_build_memory(" ".join([record.title or "", record.content]), record.tags)
            and _project_build_overlap(" ".join([record.title or "", record.content]), record.tags, query_tokens) == 0
        ):
            continue
        if not project_query and record.kind.value in {"project", "blocker", "open_loop"}:
            overlap = _token_overlap_count(query_tokens, _tokenize(" ".join([record.title or "", record.content, " ".join(record.tags)])))
            if overlap == 0:
                continue
        lines.append(line)
    return lines


def _timeline_event_overlap(event: TimelineEvent, query_tokens: set[str]) -> int:
    combined_tokens = _tokenize(" ".join([event.title or "", event.summary, " ".join(event.tags)]))
    return _token_overlap_count(query_tokens, combined_tokens)


def _timeline_event_relevance_score(event: TimelineEvent, query_tokens: set[str], *, timeline_query: bool) -> float:
    kind_bonus = {
        "week_summary": 4.0 if timeline_query else 1.6,
        "day_summary": 3.0 if timeline_query else 1.2,
        "milestone": 2.0,
        "decision": 1.8,
        "session_summary": 1.0,
    }.get(event.kind.value, 1.0)
    return (
        (_timeline_event_overlap(event, query_tokens) * 4.0)
        + (float(event.importance_score) * 0.5)
        + kind_bonus
    )


async def collect_enrichment_payload(
    transport: MemoryTransport,
    user_message: str,
    *,
    platform: str | None = None,
    active_session_id: str | None = None,
    agent_namespace: str | None = None,
) -> EnrichmentPayload:
    _ = platform
    session_task = transport.get_session(active_session_id) if active_session_id is not None else None
    facts_task = transport.search_facts(limit=FACT_SEARCH_LIMIT, agent_namespace=agent_namespace)
    directives_task = transport.list_directives(limit=8, agent_namespace=agent_namespace, statuses=["active"])
    commitments_task = transport.list_commitments(limit=12, agent_namespace=agent_namespace, statuses=["open"])
    corrections_task = transport.list_corrections(limit=12, agent_namespace=agent_namespace, active_only=True)
    timeline_task = transport.list_timeline_events(limit=12, agent_namespace=agent_namespace)
    outcomes_task = transport.list_decision_outcomes(limit=24, agent_namespace=agent_namespace)
    patterns_task = transport.list_patterns(limit=24, agent_namespace=agent_namespace)
    active_state_task = transport.list_active_state(limit=5, agent_namespace=agent_namespace, statuses=["active", "cooling"])
    relevant_episodes_task = transport.search_episodes(
        query=user_message,
        limit=EPISODE_SEARCH_LIMIT,
        platform=None,
        days_back=EPISODE_SEARCH_DAYS_BACK,
        agent_namespace=agent_namespace,
    )
    recent_episodes_task = transport.list_recent_episodes(
        limit=MAX_RECENT_EPISODES,
        platform=None,
        exclude_session_id=active_session_id,
        agent_namespace=agent_namespace,
    )

    if session_task is None:
        facts, directives, commitments, corrections, timeline_events, decision_outcomes, patterns, active_state_records, relevant_episodes, recent_episodes = await asyncio.gather(
            facts_task,
            directives_task,
            commitments_task,
            corrections_task,
            timeline_task,
            outcomes_task,
            patterns_task,
            active_state_task,
            relevant_episodes_task,
            recent_episodes_task,
        )
        active_session = None
    else:
        facts, directives, commitments, corrections, timeline_events, decision_outcomes, patterns, active_state_records, relevant_episodes, recent_episodes, active_session = await asyncio.gather(
            facts_task,
            directives_task,
            commitments_task,
            corrections_task,
            timeline_task,
            outcomes_task,
            patterns_task,
            active_state_task,
            relevant_episodes_task,
            recent_episodes_task,
            session_task,
        )

    query_tokens = _tokenize(user_message)
    low_signal_query = _query_is_low_signal(user_message)
    advice_query = _query_targets_advice(user_message)
    memory_query = _query_targets_memory_context(user_message)
    delivery_memory_query = _query_targets_delivery_memory(user_message)
    continuity_query = _query_targets_continuity(user_message)
    project_query = _query_targets_project_context(user_message) or memory_query
    patterns_query = _query_targets_patterns(user_message)
    timeline_query = _query_targets_timeline(user_message)
    commitments_query = _query_targets_commitments(user_message) or advice_query
    ranked_facts = sorted(
        [
            fact
            for fact in facts
            if not _is_corrected_text(fact.content, corrections)
            if not _looks_like_low_quality_fact(fact)
            if project_query
            or not _looks_like_project_build_memory(fact.content, fact.tags)
            or _project_build_overlap(fact.content, fact.tags, query_tokens) > 0
            or fact.category
            not in {
                FactCategory.PROJECT,
                FactCategory.GOAL,
                FactCategory.FACT,
            }
        ],
        key=lambda fact: (
            _fact_relevance_score(fact, query_tokens),
            _sort_datetime(fact.updated_at or fact.created_at),
            _sort_datetime(fact.last_accessed_at or fact.updated_at),
        ),
        reverse=True,
    )[:MAX_FACTS_IN_CONTEXT]

    if ranked_facts:
        await asyncio.gather(*(transport.touch_fact(str(fact.id)) for fact in ranked_facts if fact.id is not None))

    ranked_relevant_episodes = sorted(
        [
            episode
            for episode in _dedupe_episodes(relevant_episodes)
            if active_session_id is None or str(episode.session_id) != active_session_id
            if not _is_corrected_text(episode.content, corrections)
            if not _looks_like_trivial_episode(episode.content)
        ],
        key=lambda episode: (
            _episode_relevance_score(episode, query_tokens),
            _sort_datetime(episode.message_timestamp),
        ),
        reverse=True,
    )
    include_operational_context = _query_targets_operational_context(user_message)
    if not include_operational_context:
        ranked_relevant_episodes = [
            episode
            for episode in ranked_relevant_episodes
            if episode.platform.value != "other"
            if not _looks_like_operational_content(
                episode.content,
                getattr(episode, "message_metadata", {}) or {},
            )
        ]
    if low_signal_query:
        ranked_relevant_episodes = []
    ranked_relevant_episodes = ranked_relevant_episodes[:MAX_RELEVANT_EPISODES]

    handoff_recent_candidates = _continuity_candidate_episodes(recent_episodes, corrections)
    handoff_recent_session_id = _recent_session_id_for_handoff(handoff_recent_candidates)
    previous_session: Session | None = None
    previous_session_episodes: list[Episode] = []
    if handoff_recent_session_id and _continuity_bootstrap_active(active_session, continuity_query=continuity_query):
        previous_session, prior_episodes = await asyncio.gather(
            transport.get_session(handoff_recent_session_id),
            transport.list_episodes_for_session(handoff_recent_session_id, limit=MAX_HANDOFF_EPISODES),
        )
        previous_session_episodes = _meaningful_session_episodes(prior_episodes, corrections)

    relevant_episode_ids = {str(episode.id) for episode in ranked_relevant_episodes if episode.id is not None}
    filtered_recent_episodes = [
        episode
        for episode in _dedupe_episodes(recent_episodes)
        if episode.id is None or str(episode.id) not in relevant_episode_ids
        if not _is_corrected_text(episode.content, corrections)
        if not _looks_like_reference_content(
            episode.content,
            getattr(episode, "message_metadata", {}) or {},
        )
        if episode.platform.value != "other"
        if not _looks_like_operational_content(
            episode.content,
            getattr(episode, "message_metadata", {}) or {},
        )
    ][:MAX_RECENT_EPISODES]
    if low_signal_query:
        filtered_recent_episodes = []

    continuity_handoff_lines = (
        _build_continuity_handoff_lines(
            previous_session,
            previous_session_episodes,
            active_state_records=active_state_records,
            commitments=commitments,
        )
        if _continuity_bootstrap_active(active_session, continuity_query=continuity_query)
        else []
    )

    filtered_timeline_events = [
        event
        for event in timeline_events
        if not _is_corrected_text(event.summary, corrections)
        if not _looks_like_reference_content(event.summary)
        if not _looks_like_operational_content(event.summary)
        if not _looks_like_low_quality_timeline_summary(event.summary)
        if not (
            advice_query
            and not project_query
            and _looks_like_project_build_memory(event.summary, event.tags)
            and _timeline_event_overlap(event, query_tokens) == 0
            and any(marker in event.summary.lower() for marker in ("memory", "active state", "retrieval layer", "supabase"))
        )
    ]
    ranked_timeline_events = sorted(
        filtered_timeline_events,
        key=lambda event: (
            _timeline_event_relevance_score(event, query_tokens, timeline_query=timeline_query),
            _sort_datetime(event.event_time),
        ),
        reverse=True,
    )
    if timeline_query:
        non_session_rollups = [
            event
            for event in ranked_timeline_events
            if event.kind.value in {"week_summary", "day_summary", "milestone", "decision"}
        ]
        if non_session_rollups:
            filtered_timeline_events = non_session_rollups[:MAX_TIMELINE_EVENTS_IN_CONTEXT]
        else:
            filtered_timeline_events = [
                event
                for event in ranked_timeline_events
                if event.kind.value == "session_summary"
            ][:MAX_TIMELINE_EVENTS_IN_CONTEXT]
    else:
        filtered_timeline_events = ranked_timeline_events[:MAX_TIMELINE_EVENTS_IN_CONTEXT]

    ranked_decision_outcomes = sorted(
        [
            outcome
            for outcome in _dedupe_decision_outcomes(decision_outcomes)
            if outcome.status != DecisionOutcomeStatus.OPEN
            if memory_query
            or outcome.kind.value != "memory"
            or (delivery_memory_query and _decision_outcome_overlap(outcome, query_tokens) > 0)
            if not _is_corrected_text(" ".join([outcome.decision, outcome.outcome, outcome.lesson or ""]), corrections)
            if not _looks_like_reference_content(outcome.decision)
            if not _looks_like_reference_content(outcome.outcome)
            if not _looks_like_operational_content(outcome.decision)
            if not _looks_like_operational_content(outcome.outcome)
        ],
        key=lambda outcome: (
            _decision_outcome_relevance_score(outcome, query_tokens),
            _sort_datetime(outcome.event_time),
        ),
        reverse=True,
    )
    if low_signal_query:
        filtered_decision_outcomes = []
    elif advice_query:
        filtered_decision_outcomes = [
            outcome
            for outcome in ranked_decision_outcomes
            if _decision_outcome_overlap(outcome, query_tokens) > 0
        ][:MAX_DECISION_OUTCOMES_IN_CONTEXT]
    else:
        filtered_decision_outcomes = [
            outcome
            for outcome in ranked_decision_outcomes
            if _decision_outcome_overlap(outcome, query_tokens) > 0
            and _decision_outcome_relevance_score(outcome, query_tokens) > 1.0
        ][:MAX_DECISION_OUTCOMES_IN_CONTEXT]

    ranked_patterns = sorted(
        [
            pattern
            for pattern in patterns
            if not _is_corrected_text(" ".join([pattern.statement, pattern.description or ""]), corrections)
        ],
        key=lambda pattern: (
            _pattern_relevance_score(pattern, query_tokens),
            _sort_datetime(pattern.last_observed_at),
        ),
        reverse=True,
    )
    if patterns_query:
        filtered_patterns = [
            pattern
            for pattern in ranked_patterns
            if _pattern_overlap(pattern, query_tokens) > 0
            or float(pattern.impact_score) >= 0.8
        ][:MAX_PATTERNS_IN_CONTEXT]
    else:
        filtered_patterns = [
            pattern
            for pattern in ranked_patterns
            if _pattern_overlap(pattern, query_tokens) > 0
            and _pattern_relevance_score(pattern, query_tokens) > 1.0
        ][:MAX_PATTERNS_IN_CONTEXT]

    if commitments_query:
        filtered_commitments = commitments[:MAX_COMMITMENTS_IN_CONTEXT]
    else:
        filtered_commitments = []

    return EnrichmentPayload(
        facts=ranked_facts,
        directives=directives,
        commitments=filtered_commitments,
        corrections=corrections,
        timeline_events=filtered_timeline_events,
        decision_outcomes=filtered_decision_outcomes,
        patterns=filtered_patterns,
        active_state_lines=(
            _active_state_lines_from_records(
                active_state_records,
                query_tokens=query_tokens,
                project_query=project_query,
            )
            if active_state_records
            else _derive_active_state_lines(
                ranked_facts,
                filtered_recent_episodes,
                active_session,
                query_tokens=query_tokens,
                project_query=project_query,
            )
        ),
        continuity_handoff_lines=continuity_handoff_lines,
        relevant_episodes=ranked_relevant_episodes,
        recent_episodes=filtered_recent_episodes,
        active_session=active_session,
    )


async def enrich_context(
    transport: MemoryTransport,
    user_message: str,
    *,
    platform: str | None = None,
    active_session_id: str | None = None,
    agent_namespace: str | None = None,
) -> str:
    payload = await collect_enrichment_payload(
        transport,
        user_message,
        platform=platform,
        active_session_id=active_session_id,
        agent_namespace=agent_namespace,
    )
    return payload.format()
