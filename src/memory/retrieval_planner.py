from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Sequence, TypeVar


@dataclass(frozen=True, slots=True)
class RetrievalSignals:
    exact_recall_query: bool = False
    timeline_query: bool = False
    advice_query: bool = False
    patterns_query: bool = False
    reflections_query: bool = False
    continuity_query: bool = False
    graph_query: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalRoute:
    name: str
    enabled: bool
    weight: float


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    routes: dict[str, RetrievalRoute]
    relevant_episode_limit: int
    recent_episode_limit: int
    timeline_fetch_limit: int
    case_fetch_limit: int
    analogous_case_limit: int
    graph_path_limit: int
    graph_hop_limit: int
    proactive_trigger_threshold: float
    proactive_min_overlap: int

    def route_weight(self, route_name: str, *, default: float = 0.0) -> float:
        route = self.routes.get(route_name)
        if route is None or not route.enabled:
            return default
        return route.weight


def build_retrieval_plan(
    *,
    signals: RetrievalSignals,
    default_relevant_episode_limit: int,
    default_recent_episode_limit: int,
    default_timeline_fetch_limit: int,
    exact_relevant_episode_limit: int,
    exact_recent_episode_limit: int,
) -> RetrievalPlan:
    routes = {
        "semantic": RetrievalRoute(name="semantic", enabled=True, weight=1.0),
        "lexical": RetrievalRoute(name="lexical", enabled=True, weight=0.6),
        "temporal": RetrievalRoute(name="temporal", enabled=True, weight=0.35),
        "analogous_case": RetrievalRoute(name="analogous_case", enabled=False, weight=0.0),
        "outcome_aware": RetrievalRoute(name="outcome_aware", enabled=False, weight=0.0),
    }
    relevant_episode_limit = default_relevant_episode_limit
    recent_episode_limit = default_recent_episode_limit
    timeline_fetch_limit = default_timeline_fetch_limit
    case_fetch_limit = 24
    analogous_case_limit = 0
    graph_path_limit = 0
    graph_hop_limit = 2
    proactive_trigger_threshold = 0.72
    proactive_min_overlap = 1

    if signals.exact_recall_query:
        routes["semantic"] = RetrievalRoute(name="semantic", enabled=True, weight=0.5)
        routes["lexical"] = RetrievalRoute(name="lexical", enabled=True, weight=1.4)
        routes["temporal"] = RetrievalRoute(name="temporal", enabled=True, weight=1.2)
        routes["analogous_case"] = RetrievalRoute(name="analogous_case", enabled=False, weight=0.0)
        routes["outcome_aware"] = RetrievalRoute(name="outcome_aware", enabled=False, weight=0.0)
        routes["temporal_graph"] = RetrievalRoute(name="temporal_graph", enabled=False, weight=0.0)
        relevant_episode_limit = exact_relevant_episode_limit
        recent_episode_limit = exact_recent_episode_limit
    elif signals.timeline_query:
        routes["semantic"] = RetrievalRoute(name="semantic", enabled=True, weight=0.7)
        routes["lexical"] = RetrievalRoute(name="lexical", enabled=True, weight=0.8)
        routes["temporal"] = RetrievalRoute(name="temporal", enabled=True, weight=1.3)
        timeline_fetch_limit = max(default_timeline_fetch_limit, 20)

    if signals.advice_query or signals.patterns_query or signals.reflections_query:
        routes["analogous_case"] = RetrievalRoute(name="analogous_case", enabled=True, weight=0.95)
        routes["outcome_aware"] = RetrievalRoute(name="outcome_aware", enabled=True, weight=0.9)
        routes["semantic"] = RetrievalRoute(name="semantic", enabled=True, weight=max(routes["semantic"].weight, 0.9))
        routes["lexical"] = RetrievalRoute(name="lexical", enabled=True, weight=max(routes["lexical"].weight, 0.9))
        routes["temporal"] = RetrievalRoute(name="temporal", enabled=True, weight=max(routes["temporal"].weight, 0.45))
        case_fetch_limit = 36
        analogous_case_limit = 4
        proactive_trigger_threshold = 0.45
        proactive_min_overlap = 1

    if signals.graph_query:
        routes["temporal_graph"] = RetrievalRoute(name="temporal_graph", enabled=True, weight=1.1)
        routes["temporal"] = RetrievalRoute(name="temporal", enabled=True, weight=max(routes["temporal"].weight, 0.9))
        graph_path_limit = 6
        graph_hop_limit = 3
    elif signals.advice_query or signals.patterns_query or signals.reflections_query:
        routes["temporal_graph"] = RetrievalRoute(name="temporal_graph", enabled=True, weight=0.75)
        graph_path_limit = 4
        graph_hop_limit = 3

    if signals.continuity_query:
        routes["temporal"] = RetrievalRoute(
            name="temporal",
            enabled=True,
            weight=max(routes["temporal"].weight, 0.9),
        )
        recent_episode_limit = max(recent_episode_limit, 5)

    return RetrievalPlan(
        routes=routes,
        relevant_episode_limit=relevant_episode_limit,
        recent_episode_limit=recent_episode_limit,
        timeline_fetch_limit=timeline_fetch_limit,
        case_fetch_limit=case_fetch_limit,
        analogous_case_limit=analogous_case_limit,
        graph_path_limit=graph_path_limit,
        graph_hop_limit=graph_hop_limit,
        proactive_trigger_threshold=proactive_trigger_threshold,
        proactive_min_overlap=proactive_min_overlap,
    )


_ItemT = TypeVar("_ItemT")


def rerank_items_first_pass(
    items: Sequence[_ItemT],
    *,
    semantic_score: Callable[[_ItemT], float],
    lexical_overlap: Callable[[_ItemT], int],
    event_time: Callable[[_ItemT], datetime | None],
    semantic_weight: float,
    lexical_weight: float,
    temporal_weight: float,
    temporal_horizon_hours: float = 24.0 * 30.0,
    now: datetime | None = None,
) -> list[_ItemT]:
    if not items:
        return []

    current = now or datetime.now(timezone.utc)

    def _temporal_freshness(value: datetime | None) -> float:
        if value is None:
            return 0.0
        age_hours = max((current - value).total_seconds() / 3600.0, 0.0)
        if temporal_horizon_hours <= 0:
            return 0.0
        return max(0.0, 1.0 - min(age_hours, temporal_horizon_hours) / temporal_horizon_hours)

    scored: list[tuple[float, datetime, _ItemT]] = []
    for item in items:
        item_time = event_time(item) or datetime.min.replace(tzinfo=timezone.utc)
        combined_score = (
            float(semantic_score(item)) * semantic_weight
            + float(lexical_overlap(item)) * lexical_weight
            + _temporal_freshness(item_time) * temporal_weight
        )
        scored.append((combined_score, item_time, item))

    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [item for _, _, item in scored]


def rerank_items_second_pass(
    items: Sequence[_ItemT],
    *,
    first_pass_rank: Callable[[_ItemT], int],
    outcome_signal: Callable[[_ItemT], float],
    evidence_signal: Callable[[_ItemT], float],
    confidence_signal: Callable[[_ItemT], float],
    first_pass_weight: float = 1.0,
    outcome_weight: float = 0.7,
    evidence_weight: float = 0.5,
    confidence_weight: float = 0.4,
) -> list[_ItemT]:
    """Refine first-pass ordering using outcome/evidence confidence signals.

    Inputs are intentionally generic so the same scorer can rank episodes, outcomes,
    or analogous cases without coupling planner logic to domain models.
    """
    if not items:
        return []

    max_rank = max(1, len(items) - 1)
    scored: list[tuple[float, int, _ItemT]] = []
    for item in items:
        rank = max(0, int(first_pass_rank(item)))
        first_pass_score = 1.0 - (min(rank, max_rank) / float(max_rank))
        combined_score = (
            first_pass_score * float(first_pass_weight)
            + max(0.0, float(outcome_signal(item))) * float(outcome_weight)
            + max(0.0, float(evidence_signal(item))) * float(evidence_weight)
            + max(0.0, float(confidence_signal(item))) * float(confidence_weight)
        )
        scored.append((combined_score, -rank, item))

    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [item for _, _, item in scored]