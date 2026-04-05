from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from pydantic import Field

from memory.enrichment import collect_enrichment_payload
from memory.models import (
    ActiveState,
    ActiveStateKind,
    ActiveStateStatus,
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    Correction,
    CorrectionKind,
    DecisionOutcome,
    DecisionOutcomeKind,
    DecisionOutcomeStatus,
    Directive,
    DirectiveKind,
    DirectiveScope,
    DirectiveStatus,
    Episode,
    EpisodeRole,
    Fact,
    FactCategory,
    MemoryBaseModel,
    Pattern,
    PatternType,
    Platform,
    Reflection,
    ReflectionKind,
    ReflectionStatus,
    Session,
    SessionHandoff,
    TimelineEvent,
    TimelineEventKind,
)


DEFAULT_SCENARIOS_PATH = Path("tests/fixtures/replay_eval_scenarios.json")
_IDENTITY_LINE_RE = re.compile(r"^\[(name|religion|origin|location|role|employer|identity|communication)\]\s+(active|confirmed|revoked|uncertain|directive):", re.IGNORECASE)
_IDENTITY_EXPECT_CONTAINS_RE = re.compile(r"\[(name|religion|origin|location|role|employer|identity)\]\s+(active|confirmed|revoked|uncertain):", re.IGNORECASE)
_CONTINUITY_MIN_COUNT_KEYS = {
    "always_on_identity_lines",
    "core_profile_lines",
    "continuity_handoff_lines",
    "decision_outcomes",
    "exact_recall_mode",
    "patterns",
    "proactive_coach_lines",
    "quote_coverage_lines",
    "relevant_episodes",
    "reflections",
    "timeline_events",
    "verbatim_evidence_lines",
}
_OUTCOME_GROUNDED_MIN_COUNT_KEYS = {
    "decision_outcomes",
    "patterns",
    "proactive_coach_lines",
}
_TEMPORAL_SIGNAL_KEYS = {
    "created_at",
    "updated_at",
    "event_time",
    "last_observed_at",
    "valid_from",
    "message_timestamp",
    "first_observed_at",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_base_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "https://api.openai.com/v1"
    return text.rstrip("/")


def _judge_sample_rows(
    scenarios: list[ReplayScenario],
    results: list[dict[str, Any]],
    *,
    sample_limit: int,
) -> list[dict[str, Any]]:
    scenario_map = {scenario.id: scenario for scenario in scenarios}
    failed = [item for item in results if not bool(item.get("passed"))]
    passed = [item for item in results if bool(item.get("passed"))]
    ordered = [*failed, *passed]
    sampled: list[dict[str, Any]] = []
    for item in ordered[: max(1, sample_limit)]:
        scenario_id = str(item.get("scenario_id") or "")
        scenario = scenario_map.get(scenario_id)
        sampled.append(
            {
                "scenario_id": scenario_id,
                "description": item.get("description") or (scenario.description if scenario else None),
                "query": scenario.user_message if scenario else None,
                "passed": bool(item.get("passed")),
                "checks_failed": item.get("checks_failed", []),
                "counts": item.get("counts", {}),
            }
        )
    return sampled


async def _run_llm_judge(
    *,
    scenarios: list[ReplayScenario],
    results: list[dict[str, Any]],
    min_pass_rate: float,
    model: str,
    base_url: str,
    api_key: str | None,
    sample_limit: int,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    sampled_rows = _judge_sample_rows(
        scenarios,
        results,
        sample_limit=sample_limit,
    )
    if not sampled_rows:
        return {
            "enabled": True,
            "status": "skipped",
            "reason": "No sampled rows available for judging.",
            "sampled_scenarios": 0,
        }
    if not api_key:
        return {
            "enabled": True,
            "status": "skipped",
            "reason": "Judge API key not configured.",
            "sampled_scenarios": len(sampled_rows),
        }

    system_prompt = (
        "You are a strict memory quality judge. Evaluate whether responses show continuity,"
        " adaptation, and outcome grounding. Return JSON only."
    )
    user_prompt = {
        "task": "score_replay_eval_samples",
        "instructions": {
            "score_range": "0.0-1.0",
            "threshold": float(min_pass_rate),
            "pass_rule": "A scenario is pass if score >= threshold.",
        },
        "samples": sampled_rows,
        "output_schema": {
            "overall": {
                "mean_score": "float",
                "notes": "string",
            },
            "scenario_scores": [
                {
                    "scenario_id": "string",
                    "score": "float",
                    "reason": "string",
                }
            ],
        },
    }

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.post(
            f"{_normalize_base_url(base_url)}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_prompt, sort_keys=True)},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        payload = response.json()
        content = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        parsed = json.loads(content) if isinstance(content, str) else {}
    except Exception as exc:
        return {
            "enabled": True,
            "status": "error",
            "model": model,
            "sampled_scenarios": len(sampled_rows),
            "error": str(exc),
        }
    finally:
        if owns_client:
            await client.aclose()

    scenario_rows = parsed.get("scenario_scores", []) if isinstance(parsed, dict) else []
    normalized_rows: list[dict[str, Any]] = []
    pass_count = 0
    for row in scenario_rows:
        if not isinstance(row, dict):
            continue
        scenario_id = str(row.get("scenario_id") or "").strip()
        if not scenario_id:
            continue
        raw_score = row.get("score")
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        score = max(0.0, min(1.0, score))
        passed = score >= float(min_pass_rate)
        if passed:
            pass_count += 1
        normalized_rows.append(
            {
                "scenario_id": scenario_id,
                "score": round(score, 6),
                "passed": passed,
                "reason": str(row.get("reason") or "").strip(),
            }
        )

    required = len(normalized_rows)
    pass_rate = round((float(pass_count) / float(required)) if required else 0.0, 6)
    overall = parsed.get("overall", {}) if isinstance(parsed, dict) else {}
    notes = ""
    if isinstance(overall, dict):
        notes = str(overall.get("notes") or "").strip()

    return {
        "enabled": True,
        "status": "ok",
        "model": model,
        "sampled_scenarios": len(sampled_rows),
        "required": required,
        "passed": pass_count,
        "pass_rate": pass_rate,
        "threshold": float(min_pass_rate),
        "meets_threshold": pass_rate >= float(min_pass_rate),
        "notes": notes,
        "scenario_scores": normalized_rows,
    }


def _parse_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _parse_optional_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    return _parse_uuid(value)


def _parse_dt(value: Any, *, default: datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
        cleaned = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return default


def _platform(value: Any) -> str:
    if value is None:
        return Platform.LOCAL.value
    return str(value).strip() or Platform.LOCAL.value


class ReplayScenario(MemoryBaseModel):
    id: str
    description: str | None = None
    user_message: str
    platform: str = Platform.LOCAL.value
    agent_namespace: str = "main"
    active_session_id: str | None = None
    expect_contains: list[str] = Field(default_factory=list)
    expect_not_contains: list[str] = Field(default_factory=list)
    min_counts: dict[str, int] = Field(default_factory=dict)
    identity_slot_expectations: dict[str, str] = Field(default_factory=dict)
    seed: dict[str, Any] = Field(default_factory=dict)


def _identity_expectations_for_scenario(scenario: ReplayScenario) -> dict[str, str]:
    inferred_identity_expectations: dict[str, str] = {}
    for expected in scenario.expect_contains:
        match = _IDENTITY_EXPECT_CONTAINS_RE.search(str(expected))
        if not match:
            continue
        inferred_identity_expectations[match.group(1).lower()] = match.group(2).lower()

    explicit_identity_expectations = {
        str(slot).strip().lower(): str(state).strip().lower()
        for slot, state in scenario.identity_slot_expectations.items()
    }
    return {**inferred_identity_expectations, **explicit_identity_expectations}


def _scenario_requires_min_count(scenario: ReplayScenario, keys: set[str]) -> bool:
    for key in keys:
        if int(scenario.min_counts.get(key, 0)) > 0:
            return True
    return False


def _scenario_has_any_expectation(scenario: ReplayScenario) -> bool:
    return bool(
        scenario.expect_contains
        or scenario.expect_not_contains
        or scenario.min_counts
        or _identity_expectations_for_scenario(scenario)
    )


def _scenario_has_temporal_signal(scenario: ReplayScenario) -> bool:
    seed = scenario.seed if isinstance(scenario.seed, dict) else {}

    def _items(name: str) -> list[dict[str, Any]]:
        raw = seed.get(name, [])
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    def _has_temporal(items: list[dict[str, Any]]) -> bool:
        return any(any(item.get(key) for key in _TEMPORAL_SIGNAL_KEYS) for item in items)

    fact_items = _items("facts")
    directive_items = _items("directives")
    outcome_items = _items("decision_outcomes")
    pattern_items = _items("patterns")

    temporal = (
        _has_temporal(fact_items)
        or _has_temporal(directive_items)
        or _has_temporal(outcome_items)
        or _has_temporal(pattern_items)
    )
    if not temporal:
        return False

    has_sequence = len(fact_items) >= 2 or len(directive_items) >= 2 or len(outcome_items) >= 2
    has_lifecycle_expectation = bool(_identity_expectations_for_scenario(scenario))
    return has_sequence or has_lifecycle_expectation


def _build_universal_outcome_scorecard(
    scenarios: list[ReplayScenario],
    results: list[dict[str, Any]],
    *,
    threshold: float,
) -> dict[str, Any]:
    result_by_id = {str(item.get("scenario_id")): item for item in results}

    def _bucket(required_ids: set[str]) -> tuple[int, int]:
        required = len(required_ids)
        passed = 0
        for scenario_id in required_ids:
            if bool((result_by_id.get(scenario_id) or {}).get("passed")):
                passed += 1
        return required, passed

    continuity_required = {
        scenario.id
        for scenario in scenarios
        if _scenario_requires_min_count(scenario, _CONTINUITY_MIN_COUNT_KEYS)
        or bool(_identity_expectations_for_scenario(scenario))
    }
    alignment_required = {
        scenario.id
        for scenario in scenarios
        if _scenario_has_any_expectation(scenario)
    }
    outcome_required = {
        scenario.id
        for scenario in scenarios
        if _scenario_requires_min_count(scenario, _OUTCOME_GROUNDED_MIN_COUNT_KEYS)
    }
    adaptation_required = {
        scenario.id
        for scenario in scenarios
        if _scenario_has_temporal_signal(scenario)
    }

    proactive_required = {
        scenario.id
        for scenario in scenarios
        if int(scenario.min_counts.get("proactive_coach_lines", 0)) > 0
    }
    proactive_emitted = {
        str(item.get("scenario_id"))
        for item in results
        if int((item.get("counts") or {}).get("proactive_coach_lines", 0)) > 0
    }
    proactive_true_positive = proactive_required.intersection(proactive_emitted)

    continuity_required_count, continuity_passed = _bucket(continuity_required)
    alignment_required_count, alignment_passed = _bucket(alignment_required)
    outcome_required_count, outcome_passed = _bucket(outcome_required)
    adaptation_required_count, adaptation_passed = _bucket(adaptation_required)

    def _rate(passed_count: int, required_count: int) -> float:
        if required_count <= 0:
            return 1.0
        return round(float(passed_count) / float(required_count), 6)

    continuity_rate = _rate(continuity_passed, continuity_required_count)
    alignment_rate = _rate(alignment_passed, alignment_required_count)
    outcome_rate = _rate(outcome_passed, outcome_required_count)
    adaptation_rate = _rate(adaptation_passed, adaptation_required_count)

    total = len(results)
    passed = sum(1 for item in results if item.get("passed"))
    regression_rate = _rate(passed, total)

    metrics = {
        "continuity_carry_forward_rate": {
            "required": continuity_required_count,
            "passed": continuity_passed,
            "pass_rate": continuity_rate,
        },
        "restatement_burden": {
            "required": alignment_required_count,
            "aligned_without_restatement": alignment_passed,
            "pass_rate": alignment_rate,
            "burden_rate": round(1.0 - alignment_rate, 6),
        },
        "outcome_grounded_guidance_rate": {
            "required": outcome_required_count,
            "passed": outcome_passed,
            "pass_rate": outcome_rate,
        },
        "adaptation_latency": {
            "required": adaptation_required_count,
            "adopted_on_first_turn": adaptation_passed,
            "pass_rate": adaptation_rate,
            "estimated_extra_turns_per_case": round(1.0 - adaptation_rate, 6),
        },
        "intervention_precision_recall": {
            "required_proactive": len(proactive_required),
            "emitted_proactive": len(proactive_emitted),
            "true_positive": len(proactive_true_positive),
            "precision": round(
                float(len(proactive_true_positive)) / float(len(proactive_emitted))
                if proactive_emitted
                else 1.0,
                6,
            ),
            "recall": round(
                float(len(proactive_true_positive)) / float(len(proactive_required))
                if proactive_required
                else 1.0,
                6,
            ),
        },
        "regression_resilience": {
            "required": total,
            "passed": passed,
            "pass_rate": regression_rate,
            "threshold": float(threshold),
            "meets_threshold": regression_rate >= float(threshold),
        },
    }

    metric_rates = [
        continuity_rate,
        alignment_rate,
        outcome_rate,
        adaptation_rate,
        regression_rate,
    ]
    metrics_below_threshold = {
        name: metric
        for name, metric in metrics.items()
        if float(metric.get("pass_rate", 1.0)) < float(threshold)
    }
    metrics["overall_score"] = {
        "threshold": float(threshold),
        "metrics_considered": len(metric_rates),
        "mean_pass_rate": round(sum(metric_rates) / float(len(metric_rates)), 6) if metric_rates else 1.0,
        "all_metrics_green": not metrics_below_threshold,
        "metrics_below_threshold": sorted(metrics_below_threshold.keys()),
    }
    return metrics


class _EvalTransport:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.episodes: list[Episode] = []
        self.facts: list[Fact] = []
        self.active_state: list[ActiveState] = []
        self.directives: list[Directive] = []
        self.timeline_events: list[TimelineEvent] = []
        self.decision_outcomes: list[DecisionOutcome] = []
        self.patterns: list[Pattern] = []
        self.reflections: list[Reflection] = []
        self.commitments: list[Commitment] = []
        self.corrections: list[Correction] = []
        self.session_handoffs: list[SessionHandoff] = []

    @classmethod
    def from_seed(cls, seed: dict[str, Any], *, default_namespace: str = "main") -> "_EvalTransport":
        now = _utcnow()
        transport = cls()

        for item in seed.get("sessions", []):
            session = Session(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                agent_namespace=item.get("agent_namespace", default_namespace),
                platform=_platform(item.get("platform")),
                started_at=_parse_dt(item.get("started_at"), default=now),
                ended_at=_parse_dt(item.get("ended_at"), default=now) if item.get("ended_at") else None,
                message_count=int(item.get("message_count", 0)),
                user_message_count=int(item.get("user_message_count", 0)),
                summary=item.get("summary"),
                topics=list(item.get("topics", [])),
                dominant_emotions=list(item.get("dominant_emotions", [])),
                dominant_emotion_counts=dict(item.get("dominant_emotion_counts", {})),
            )
            transport.sessions[str(session.id)] = session

        for item in seed.get("episodes", []):
            session_id = _parse_optional_uuid(item.get("session_id")) or next(iter(transport.sessions.keys()), None)
            if isinstance(session_id, str):
                session_uuid = _parse_uuid(session_id)
            elif session_id is None:
                session_uuid = uuid4()
            else:
                session_uuid = session_id
            episode = Episode(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                session_id=session_uuid,
                agent_namespace=item.get("agent_namespace", default_namespace),
                role=EpisodeRole(item.get("role", EpisodeRole.USER.value)),
                content=str(item.get("content", "")),
                content_hash=str(item.get("content_hash") or uuid4().hex),
                platform=_platform(item.get("platform")),
                message_metadata=dict(item.get("message_metadata", {})),
                emotions=dict(item.get("emotions", {})),
                dominant_emotion=item.get("dominant_emotion"),
                emotional_intensity=float(item.get("emotional_intensity", 0.0)),
                message_timestamp=_parse_dt(item.get("message_timestamp"), default=now),
            )
            transport.episodes.append(episode)

        for item in seed.get("facts", []):
            event_time = _parse_dt(item.get("event_time"), default=now)
            created_at = _parse_dt(item.get("created_at"), default=event_time)
            fact = Fact(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                agent_namespace=item.get("agent_namespace", default_namespace),
                content=str(item.get("content", "")),
                category=FactCategory(item.get("category", FactCategory.FACT.value)),
                confidence=float(item.get("confidence", 0.85)),
                event_time=event_time,
                transaction_time=_parse_dt(item.get("transaction_time"), default=event_time),
                is_active=bool(item.get("is_active", True)),
                source_episode_ids=[_parse_uuid(value) for value in item.get("source_episode_ids", [])],
                access_count=int(item.get("access_count", 0)),
                last_accessed_at=_parse_dt(item.get("last_accessed_at"), default=event_time),
                tags=list(item.get("tags", [])),
                created_at=created_at,
                updated_at=_parse_dt(item.get("updated_at"), default=created_at),
            )
            transport.facts.append(fact)

        for item in seed.get("active_state", []):
            valid_from = _parse_dt(item.get("valid_from"), default=now)
            state = ActiveState(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                agent_namespace=item.get("agent_namespace", default_namespace),
                kind=ActiveStateKind(item.get("kind", ActiveStateKind.PROJECT.value)),
                title=item.get("title"),
                content=str(item.get("content", "")),
                content_hash=item.get("content_hash"),
                state_key=str(item.get("state_key") or f"eval:state:{uuid4().hex}"),
                status=ActiveStateStatus(item.get("status", ActiveStateStatus.ACTIVE.value)),
                confidence=float(item.get("confidence", 0.8)),
                priority_score=float(item.get("priority_score", 1.0)),
                valid_from=valid_from,
                valid_until=_parse_dt(item.get("valid_until"), default=valid_from) if item.get("valid_until") else None,
                last_observed_at=_parse_dt(item.get("last_observed_at"), default=valid_from),
                tags=list(item.get("tags", [])),
            )
            transport.active_state.append(state)

        for item in seed.get("directives", []):
            directive = Directive(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                agent_namespace=item.get("agent_namespace", default_namespace),
                kind=DirectiveKind(item.get("kind", DirectiveKind.COMMUNICATION.value)),
                scope=DirectiveScope(item.get("scope", DirectiveScope.GLOBAL.value)),
                title=item.get("title"),
                content=str(item.get("content", "")),
                directive_key=str(item.get("directive_key") or f"eval:directive:{uuid4().hex}"),
                status=DirectiveStatus(item.get("status", DirectiveStatus.ACTIVE.value)),
                confidence=float(item.get("confidence", 0.9)),
                priority_score=float(item.get("priority_score", 1.0)),
                tags=list(item.get("tags", [])),
                last_observed_at=_parse_dt(item.get("last_observed_at"), default=now),
            )
            transport.directives.append(directive)

        for item in seed.get("timeline_events", []):
            event = TimelineEvent(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                agent_namespace=item.get("agent_namespace", default_namespace),
                kind=TimelineEventKind(item.get("kind", TimelineEventKind.SESSION_SUMMARY.value)),
                title=item.get("title"),
                summary=str(item.get("summary", "")),
                event_key=str(item.get("event_key") or f"eval:timeline:{uuid4().hex}"),
                event_time=_parse_dt(item.get("event_time"), default=now),
                session_id=_parse_optional_uuid(item.get("session_id")),
                tags=list(item.get("tags", [])),
                importance_score=float(item.get("importance_score", 0.7)),
            )
            transport.timeline_events.append(event)

        for item in seed.get("decision_outcomes", []):
            event_time = _parse_dt(item.get("event_time"), default=now)
            outcome = DecisionOutcome(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                agent_namespace=item.get("agent_namespace", default_namespace),
                kind=DecisionOutcomeKind(item.get("kind", DecisionOutcomeKind.MEMORY.value)),
                title=item.get("title"),
                decision=str(item.get("decision", "")),
                outcome=str(item.get("outcome", "")),
                lesson=item.get("lesson"),
                outcome_key=str(item.get("outcome_key") or f"eval:outcome:{uuid4().hex}"),
                status=DecisionOutcomeStatus(item.get("status", DecisionOutcomeStatus.SUCCESS.value)),
                confidence=float(item.get("confidence", 0.82)),
                importance_score=float(item.get("importance_score", 0.8)),
                event_time=event_time,
                session_id=_parse_optional_uuid(item.get("session_id")),
                tags=list(item.get("tags", [])),
            )
            transport.decision_outcomes.append(outcome)

        for item in seed.get("patterns", []):
            first_observed = _parse_dt(item.get("first_observed_at"), default=now)
            pattern = Pattern(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                agent_namespace=item.get("agent_namespace", default_namespace),
                pattern_type=PatternType(item.get("pattern_type", PatternType.WORK_PATTERN.value)),
                statement=str(item.get("statement", "")),
                description=item.get("description"),
                pattern_key=str(item.get("pattern_key") or f"eval:pattern:{uuid4().hex}"),
                confidence=float(item.get("confidence", 0.8)),
                frequency_score=float(item.get("frequency_score", 0.7)),
                impact_score=float(item.get("impact_score", 0.7)),
                first_observed_at=first_observed,
                last_observed_at=_parse_dt(item.get("last_observed_at"), default=first_observed),
                tags=list(item.get("tags", [])),
            )
            transport.patterns.append(pattern)

        for item in seed.get("reflections", []):
            first_observed = _parse_dt(item.get("first_observed_at"), default=now)
            reflection = Reflection(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                agent_namespace=item.get("agent_namespace", default_namespace),
                kind=ReflectionKind(item.get("kind", ReflectionKind.WORKFLOW_HYPOTHESIS.value)),
                statement=str(item.get("statement", "")),
                evidence_summary=item.get("evidence_summary"),
                reflection_key=str(item.get("reflection_key") or f"eval:reflection:{uuid4().hex}"),
                status=ReflectionStatus(item.get("status", ReflectionStatus.TENTATIVE.value)),
                confidence=float(item.get("confidence", 0.75)),
                first_observed_at=first_observed,
                last_observed_at=_parse_dt(item.get("last_observed_at"), default=first_observed),
                tags=list(item.get("tags", [])),
            )
            transport.reflections.append(reflection)

        for item in seed.get("commitments", []):
            first_observed = _parse_dt(item.get("first_committed_at"), default=now)
            commitment = Commitment(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                agent_namespace=item.get("agent_namespace", default_namespace),
                kind=CommitmentKind(item.get("kind", CommitmentKind.OTHER.value)),
                statement=str(item.get("statement", "")),
                commitment_key=str(item.get("commitment_key") or f"eval:commitment:{uuid4().hex}"),
                status=CommitmentStatus(item.get("status", CommitmentStatus.OPEN.value)),
                confidence=float(item.get("confidence", 0.8)),
                priority_score=float(item.get("priority_score", 0.7)),
                first_committed_at=first_observed,
                last_observed_at=_parse_dt(item.get("last_observed_at"), default=first_observed),
                tags=list(item.get("tags", [])),
            )
            transport.commitments.append(commitment)

        for item in seed.get("corrections", []):
            first_observed = _parse_dt(item.get("first_observed_at"), default=now)
            correction = Correction(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                agent_namespace=item.get("agent_namespace", default_namespace),
                kind=CorrectionKind(item.get("kind", CorrectionKind.MEMORY_DISPUTE.value)),
                statement=str(item.get("statement", "")),
                target_text=item.get("target_text"),
                correction_key=str(item.get("correction_key") or f"eval:correction:{uuid4().hex}"),
                active=bool(item.get("active", True)),
                confidence=float(item.get("confidence", 0.9)),
                first_observed_at=first_observed,
                last_observed_at=_parse_dt(item.get("last_observed_at"), default=first_observed),
                tags=list(item.get("tags", [])),
            )
            transport.corrections.append(correction)

        for item in seed.get("session_handoffs", []):
            session_id = _parse_optional_uuid(item.get("session_id")) or uuid4()
            handoff = SessionHandoff(
                id=_parse_optional_uuid(item.get("id")) or uuid4(),
                agent_namespace=item.get("agent_namespace", default_namespace),
                session_id=session_id,
                handoff_key=str(item.get("handoff_key") or f"eval:handoff:{session_id}"),
                last_thread=str(item.get("last_thread", "")),
                carry_forward=item.get("carry_forward"),
                assistant_context=item.get("assistant_context"),
                emotional_tone=item.get("emotional_tone"),
                confidence=float(item.get("confidence", 0.8)),
                source_episode_ids=[_parse_uuid(value) for value in item.get("source_episode_ids", [])],
                source_session_ids=[_parse_uuid(value) for value in item.get("source_session_ids", [])],
                last_observed_at=_parse_dt(item.get("last_observed_at"), default=now),
            )
            transport.session_handoffs.append(handoff)

        return transport

    async def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    async def list_episodes_for_session(self, session_id: str, limit: int | None = None) -> list[Episode]:
        episodes = [episode for episode in self.episodes if str(episode.session_id) == str(session_id)]
        episodes.sort(key=lambda episode: episode.message_timestamp)
        if limit is not None:
            episodes = episodes[:limit]
        return episodes

    async def search_facts(self, category: str | None = None, tags: list[str] | None = None, limit: int = 50, agent_namespace: str | None = None) -> list[Fact]:
        facts = [fact for fact in self.facts if fact.is_active]
        if agent_namespace is not None:
            facts = [fact for fact in facts if fact.agent_namespace == agent_namespace]
        if category is not None:
            facts = [fact for fact in facts if fact.category.value == category]
        if tags:
            facts = [fact for fact in facts if all(tag in fact.tags for tag in tags)]
        return facts[:limit]

    async def touch_fact(self, fact_id: str) -> None:
        for fact in self.facts:
            if str(fact.id) == str(fact_id):
                fact.access_count += 1
                fact.last_accessed_at = _utcnow()

    async def search_episodes(
        self,
        query: str,
        limit: int = 20,
        platform: str | None = None,
        days_back: int = 30,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        _ = (query, days_back)
        episodes = list(self.episodes)
        if platform is not None:
            episodes = [episode for episode in episodes if episode.platform.value == platform]
        if agent_namespace is not None:
            episodes = [episode for episode in episodes if episode.agent_namespace == agent_namespace]
        episodes.sort(key=lambda episode: episode.message_timestamp, reverse=True)
        return episodes[:limit]

    async def list_recent_episodes(
        self,
        limit: int = 5,
        platform: str | None = None,
        exclude_session_id: str | None = None,
        agent_namespace: str | None = None,
    ) -> list[Episode]:
        episodes = list(self.episodes)
        if platform is not None:
            episodes = [episode for episode in episodes if episode.platform.value == platform]
        if exclude_session_id is not None:
            episodes = [episode for episode in episodes if str(episode.session_id) != str(exclude_session_id)]
        if agent_namespace is not None:
            episodes = [episode for episode in episodes if episode.agent_namespace == agent_namespace]
        episodes.sort(key=lambda episode: episode.message_timestamp, reverse=True)
        return episodes[:limit]

    async def list_directives(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None) -> list[Directive]:
        directives = list(self.directives)
        if agent_namespace is not None:
            directives = [item for item in directives if item.agent_namespace == agent_namespace]
        if statuses:
            allowed = set(statuses)
            directives = [item for item in directives if item.status.value in allowed]
        directives.sort(key=lambda item: (item.priority_score, item.last_observed_at or _utcnow()), reverse=True)
        return directives[:limit]

    async def list_commitments(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None) -> list[Commitment]:
        commitments = list(self.commitments)
        if agent_namespace is not None:
            commitments = [item for item in commitments if item.agent_namespace == agent_namespace]
        if statuses:
            allowed = set(statuses)
            commitments = [item for item in commitments if item.status.value in allowed]
        commitments.sort(key=lambda item: (item.priority_score, item.last_observed_at), reverse=True)
        return commitments[:limit]

    async def list_corrections(self, limit: int = 10, agent_namespace: str | None = None, active_only: bool = True) -> list[Correction]:
        corrections = list(self.corrections)
        if agent_namespace is not None:
            corrections = [item for item in corrections if item.agent_namespace == agent_namespace]
        if active_only:
            corrections = [item for item in corrections if item.active]
        corrections.sort(key=lambda item: item.last_observed_at, reverse=True)
        return corrections[:limit]

    async def list_timeline_events(self, limit: int = 10, agent_namespace: str | None = None) -> list[TimelineEvent]:
        events = list(self.timeline_events)
        if agent_namespace is not None:
            events = [event for event in events if event.agent_namespace == agent_namespace]
        events.sort(key=lambda event: event.event_time, reverse=True)
        return events[:limit]

    async def list_decision_outcomes(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None) -> list[DecisionOutcome]:
        outcomes = list(self.decision_outcomes)
        if agent_namespace is not None:
            outcomes = [outcome for outcome in outcomes if outcome.agent_namespace == agent_namespace]
        if statuses:
            allowed = set(statuses)
            outcomes = [outcome for outcome in outcomes if outcome.status.value in allowed]
        outcomes.sort(key=lambda outcome: (outcome.importance_score, outcome.event_time), reverse=True)
        return outcomes[:limit]

    async def list_patterns(self, limit: int = 10, agent_namespace: str | None = None, pattern_types: list[str] | None = None) -> list[Pattern]:
        patterns = list(self.patterns)
        if agent_namespace is not None:
            patterns = [pattern for pattern in patterns if pattern.agent_namespace == agent_namespace]
        if pattern_types:
            allowed = set(pattern_types)
            patterns = [pattern for pattern in patterns if pattern.pattern_type.value in allowed]
        patterns.sort(key=lambda pattern: (pattern.impact_score, pattern.last_observed_at), reverse=True)
        return patterns[:limit]

    async def list_reflections(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None) -> list[Reflection]:
        reflections = list(self.reflections)
        if agent_namespace is not None:
            reflections = [reflection for reflection in reflections if reflection.agent_namespace == agent_namespace]
        if statuses:
            allowed = set(statuses)
            reflections = [reflection for reflection in reflections if reflection.status.value in allowed]
        reflections.sort(key=lambda reflection: (reflection.confidence, reflection.last_observed_at), reverse=True)
        return reflections[:limit]

    async def list_active_state(self, limit: int = 10, agent_namespace: str | None = None, statuses: list[str] | None = None) -> list[ActiveState]:
        states = list(self.active_state)
        if agent_namespace is not None:
            states = [state for state in states if state.agent_namespace == agent_namespace]
        if statuses:
            allowed = set(statuses)
            states = [state for state in states if state.status.value in allowed]
        states.sort(key=lambda state: (state.priority_score, state.last_observed_at), reverse=True)
        return states[:limit]

    async def list_session_handoffs(
        self,
        limit: int = 10,
        agent_namespace: str | None = None,
        exclude_session_id: str | None = None,
    ) -> list[SessionHandoff]:
        handoffs = list(self.session_handoffs)
        if agent_namespace is not None:
            handoffs = [handoff for handoff in handoffs if handoff.agent_namespace == agent_namespace]
        if exclude_session_id is not None:
            handoffs = [handoff for handoff in handoffs if str(handoff.session_id) != str(exclude_session_id)]
        handoffs.sort(key=lambda handoff: handoff.last_observed_at, reverse=True)
        return handoffs[:limit]


async def evaluate_replay_scenario(scenario: ReplayScenario) -> dict[str, Any]:
    transport = _EvalTransport.from_seed(scenario.seed, default_namespace=scenario.agent_namespace)
    payload = await collect_enrichment_payload(
        transport,
        scenario.user_message,
        platform=scenario.platform,
        active_session_id=scenario.active_session_id,
        agent_namespace=scenario.agent_namespace,
    )
    rendered = payload.format()
    lowered = rendered.lower()

    checks: list[dict[str, Any]] = []
    for expected in scenario.expect_contains:
        matched = str(expected).lower() in lowered
        checks.append({"kind": "contains", "value": expected, "passed": matched})
    for blocked in scenario.expect_not_contains:
        matched = str(blocked).lower() not in lowered
        checks.append({"kind": "not_contains", "value": blocked, "passed": matched})

    observed_identity_slot_states: dict[str, set[str]] = {}
    for line in payload.always_on_identity_lines:
        match = _IDENTITY_LINE_RE.match(str(line).strip())
        if not match:
            continue
        slot = match.group(1).lower()
        state = match.group(2).lower()
        observed_identity_slot_states.setdefault(slot, set()).add(state)

    identity_expectations = _identity_expectations_for_scenario(scenario)
    for slot, expected_state in identity_expectations.items():
        observed_states = sorted(observed_identity_slot_states.get(slot, set()))
        passed = expected_state in observed_states
        checks.append(
            {
                "kind": "identity_slot",
                "slot": slot,
                "expected_state": expected_state,
                "observed_states": observed_states,
                "passed": passed,
            }
        )

    count_values = {
        "facts": len(payload.facts),
        "directives": len(payload.directives),
        "decision_outcomes": len(payload.decision_outcomes),
        "patterns": len(payload.patterns),
        "reflections": len(payload.reflections),
        "timeline_events": len(payload.timeline_events),
        "commitments": len(payload.commitments),
        "active_state_lines": len(payload.active_state_lines),
        "always_on_identity_lines": len(payload.always_on_identity_lines),
        "core_profile_lines": len(payload.core_profile_lines),
        "life_trajectory_lines": len(payload.life_trajectory_lines),
        "proactive_coach_lines": len(payload.proactive_coach_lines),
        "verbatim_evidence_lines": len(payload.verbatim_evidence_lines),
        "quote_coverage_lines": len(payload.quote_coverage_lines),
        "continuity_handoff_lines": len(payload.continuity_handoff_lines),
        "relevant_episodes": len(payload.relevant_episodes),
        "recent_episodes": len(payload.recent_episodes),
        "exact_recall_mode": 1 if payload.exact_recall_mode else 0,
    }
    for key, minimum in scenario.min_counts.items():
        observed = int(count_values.get(key, 0))
        passed = observed >= int(minimum)
        checks.append(
            {
                "kind": "min_count",
                "value": key,
                "minimum": int(minimum),
                "observed": observed,
                "passed": passed,
            }
        )

    failed = [check for check in checks if not check.get("passed")]
    return {
        "scenario_id": scenario.id,
        "description": scenario.description,
        "passed": not failed,
        "checks_total": len(checks),
        "checks_passed": len(checks) - len(failed),
        "checks_failed": failed,
        "counts": count_values,
        "identity_slot_observed": {
            slot: sorted(states)
            for slot, states in observed_identity_slot_states.items()
        },
    }


def load_replay_scenarios(scenarios_file: str | Path | None = None) -> list[ReplayScenario]:
    path = Path(scenarios_file) if scenarios_file else DEFAULT_SCENARIOS_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Replay scenarios file must be a list of scenario objects.")
    return [ReplayScenario.model_validate(item) for item in raw]


async def run_replay_eval(
    *,
    scenarios_file: str | Path | None = None,
    min_pass_rate: float = 1.0,
    enable_judge: bool | None = None,
    judge_enforce: bool | None = None,
    judge_model: str | None = None,
    judge_sample_limit: int | None = None,
    judge_base_url: str | None = None,
    judge_api_key: str | None = None,
    judge_http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    scenarios = load_replay_scenarios(scenarios_file)
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        results.append(await evaluate_replay_scenario(scenario))

    total = len(results)
    passed = sum(1 for item in results if item.get("passed"))
    failed = total - passed
    pass_rate = (float(passed) / float(total)) if total else 0.0

    scenario_by_id = {scenario.id: scenario for scenario in scenarios}
    result_by_id = {str(result.get("scenario_id")): result for result in results}
    slot_scores = {}
    for scenario_id, scenario in scenario_by_id.items():
        expected = _identity_expectations_for_scenario(scenario)
        if not expected:
            continue
        result = result_by_id.get(scenario_id) or {}
        failed_identity = {
            (str(check.get("slot") or "").strip().lower(), str(check.get("expected_state") or "").strip().lower())
            for check in result.get("checks_failed", [])
            if check.get("kind") == "identity_slot"
        }
        for slot, state in expected.items():
            bucket = slot_scores.setdefault(slot, {"required": 0, "passed": 0})
            bucket["required"] += 1
            if (slot, state) not in failed_identity:
                bucket["passed"] += 1

    identity_slot_scores = {
        slot: {
            "required": int(values["required"]),
            "passed": int(values["passed"]),
            "pass_rate": round((float(values["passed"]) / float(values["required"])) if int(values["required"]) else 0.0, 6),
        }
        for slot, values in sorted(slot_scores.items())
    }
    universal_outcome_scorecard = _build_universal_outcome_scorecard(
        scenarios,
        results,
        threshold=float(min_pass_rate),
    )

    deterministic_meets_threshold = pass_rate >= float(min_pass_rate)
    resolved_enable_judge = enable_judge if enable_judge is not None else _env_flag("MEMORY_EVAL_ENABLE_JUDGE", default=False)
    resolved_judge_enforce = judge_enforce if judge_enforce is not None else _env_flag("MEMORY_EVAL_JUDGE_ENFORCE", default=False)

    if resolved_enable_judge:
        resolved_judge_model = str(
            judge_model
            or os.getenv("MEMORY_EVAL_JUDGE_MODEL")
            or "gpt-4o-mini"
        )
        resolved_judge_base_url = str(
            judge_base_url
            or os.getenv("MEMORY_EVAL_JUDGE_BASE_URL")
            or os.getenv("MEMORY_OPENAI_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("GLM_BASE_URL")
            or "https://api.openai.com/v1"
        )
        resolved_judge_api_key = (
            judge_api_key
            or os.getenv("MEMORY_EVAL_JUDGE_API_KEY")
            or os.getenv("MEMORY_OPENAI_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("GLM_API_KEY")
        )
        resolved_judge_sample_limit = int(
            judge_sample_limit
            if judge_sample_limit is not None
            else int(os.getenv("MEMORY_EVAL_JUDGE_SAMPLE_LIMIT", "12"))
        )
        judge_scorecard = await _run_llm_judge(
            scenarios=scenarios,
            results=results,
            min_pass_rate=float(min_pass_rate),
            model=resolved_judge_model,
            base_url=resolved_judge_base_url,
            api_key=resolved_judge_api_key,
            sample_limit=max(1, resolved_judge_sample_limit),
            http_client=judge_http_client,
        )
    else:
        judge_scorecard = {
            "enabled": False,
            "status": "disabled",
            "reason": "Judge layer is disabled.",
        }

    meets_threshold = deterministic_meets_threshold
    if resolved_judge_enforce:
        if judge_scorecard.get("status") != "ok":
            meets_threshold = False
        else:
            meets_threshold = meets_threshold and bool(judge_scorecard.get("meets_threshold", False))

    return {
        "task": "replay-eval",
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(pass_rate, 6),
        "min_pass_rate": float(min_pass_rate),
        "deterministic_meets_threshold": deterministic_meets_threshold,
        "judge_enforce": resolved_judge_enforce,
        "meets_threshold": meets_threshold,
        "identity_slot_scores": identity_slot_scores,
        "universal_outcome_scorecard": universal_outcome_scorecard,
        "judge_scorecard": judge_scorecard,
        "failed_scenarios": [
            {
                "scenario_id": item.get("scenario_id"),
                "checks_failed": item.get("checks_failed", []),
            }
            for item in results
            if not item.get("passed")
        ],
        "results": results,
    }
