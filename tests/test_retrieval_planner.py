from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from memory.retrieval_planner import RetrievalSignals, build_retrieval_plan, rerank_items_first_pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def test_build_retrieval_plan_exact_recall_prefers_lexical_temporal_routes() -> None:
    plan = build_retrieval_plan(
        signals=RetrievalSignals(exact_recall_query=True),
        default_relevant_episode_limit=12,
        default_recent_episode_limit=3,
        default_timeline_fetch_limit=12,
        exact_relevant_episode_limit=24,
        exact_recent_episode_limit=24,
    )

    assert plan.relevant_episode_limit == 24
    assert plan.recent_episode_limit == 24
    assert plan.timeline_fetch_limit == 12
    assert plan.route_weight("lexical") > plan.route_weight("semantic")
    assert plan.route_weight("temporal") > plan.route_weight("semantic")


def test_build_retrieval_plan_timeline_query_expands_temporal_route() -> None:
    plan = build_retrieval_plan(
        signals=RetrievalSignals(timeline_query=True),
        default_relevant_episode_limit=12,
        default_recent_episode_limit=3,
        default_timeline_fetch_limit=12,
        exact_relevant_episode_limit=24,
        exact_recent_episode_limit=24,
    )

    assert plan.timeline_fetch_limit == 20
    assert plan.route_weight("temporal") > plan.route_weight("semantic")


@dataclass(frozen=True)
class _EpisodeCandidate:
    text: str
    ts: datetime
    semantic: float


def test_rerank_items_first_pass_blends_semantic_lexical_and_temporal() -> None:
    now = _utcnow()
    items = [
        _EpisodeCandidate(text="generic update", ts=now - timedelta(hours=1), semantic=0.9),
        _EpisodeCandidate(text="continuity regression after restart", ts=now - timedelta(days=2), semantic=0.7),
    ]
    query_tokens = {"continuity", "restart"}

    ranked = rerank_items_first_pass(
        items,
        semantic_score=lambda item: item.semantic,
        lexical_overlap=lambda item: len(query_tokens.intersection(set(item.text.split()))),
        event_time=lambda item: item.ts,
        semantic_weight=0.5,
        lexical_weight=1.2,
        temporal_weight=0.2,
        now=now,
    )

    assert ranked[0].text == "continuity regression after restart"
