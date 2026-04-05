from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from memory.retrieval_planner import (
    RetrievalSignals,
    build_retrieval_plan,
    rerank_items_first_pass,
    rerank_items_second_pass,
)


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


def test_build_retrieval_plan_advice_query_enables_outcome_aware_case_routes() -> None:
    plan = build_retrieval_plan(
        signals=RetrievalSignals(advice_query=True),
        default_relevant_episode_limit=12,
        default_recent_episode_limit=3,
        default_timeline_fetch_limit=12,
        exact_relevant_episode_limit=24,
        exact_recent_episode_limit=24,
    )

    assert plan.route_weight("analogous_case") > 0.0
    assert plan.route_weight("outcome_aware") > 0.0
    assert plan.case_fetch_limit == 36
    assert plan.analogous_case_limit == 4
    assert plan.proactive_trigger_threshold < 0.65
    assert plan.proactive_min_overlap == 1


def test_build_retrieval_plan_continuity_query_expands_recent_window() -> None:
    plan = build_retrieval_plan(
        signals=RetrievalSignals(continuity_query=True),
        default_relevant_episode_limit=12,
        default_recent_episode_limit=3,
        default_timeline_fetch_limit=12,
        exact_relevant_episode_limit=24,
        exact_recent_episode_limit=24,
    )

    assert plan.recent_episode_limit >= 5
    assert plan.route_weight("temporal") >= 0.9


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


def test_rerank_items_second_pass_promotes_outcome_and_evidence_rich_items() -> None:
    now = _utcnow()
    items = [
        _EpisodeCandidate(text="generic update", ts=now - timedelta(hours=1), semantic=0.9),
        _EpisodeCandidate(text="failure postmortem with corrective path", ts=now - timedelta(days=2), semantic=0.7),
    ]
    first_pass_order = {id(items[0]): 0, id(items[1]): 1}

    ranked = rerank_items_second_pass(
        items,
        first_pass_rank=lambda item: first_pass_order[id(item)],
        outcome_signal=lambda item: 1.0 if "failure" in item.text else 0.1,
        evidence_signal=lambda item: 0.9 if "corrective" in item.text else 0.1,
        confidence_signal=lambda item: item.semantic,
        first_pass_weight=0.4,
        outcome_weight=0.9,
        evidence_weight=0.7,
        confidence_weight=0.3,
    )

    assert ranked[0].text == "failure postmortem with corrective path"
