from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    seed: dict[str, Any] = Field(default_factory=dict)


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
                kind=DirectiveKind(item.get("kind", DirectiveKind.OTHER.value)),
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

    count_values = {
        "facts": len(payload.facts),
        "directives": len(payload.directives),
        "decision_outcomes": len(payload.decision_outcomes),
        "patterns": len(payload.patterns),
        "reflections": len(payload.reflections),
        "timeline_events": len(payload.timeline_events),
        "commitments": len(payload.commitments),
        "active_state_lines": len(payload.active_state_lines),
        "core_profile_lines": len(payload.core_profile_lines),
        "life_trajectory_lines": len(payload.life_trajectory_lines),
        "proactive_coach_lines": len(payload.proactive_coach_lines),
        "verbatim_evidence_lines": len(payload.verbatim_evidence_lines),
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
) -> dict[str, Any]:
    scenarios = load_replay_scenarios(scenarios_file)
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        results.append(await evaluate_replay_scenario(scenario))

    total = len(results)
    passed = sum(1 for item in results if item.get("passed"))
    failed = total - passed
    pass_rate = (float(passed) / float(total)) if total else 0.0

    return {
        "task": "replay-eval",
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(pass_rate, 6),
        "min_pass_rate": float(min_pass_rate),
        "meets_threshold": pass_rate >= float(min_pass_rate),
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
