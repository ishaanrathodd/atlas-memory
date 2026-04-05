from __future__ import annotations

import json
from pathlib import Path

import pytest

import memory.eval_harness as eval_harness
from memory.eval_harness import run_replay_eval


@pytest.mark.asyncio
async def test_run_replay_eval_reports_threshold_failure(tmp_path):
    scenarios_file = tmp_path / "scenarios.json"
    scenarios_file.write_text(
        json.dumps(
            [
                {
                    "id": "pass",
                    "user_message": "how should I approach this reliability issue?",
                    "agent_namespace": "main",
                    "expect_contains": ["root-cause debugging"],
                    "seed": {
                        "facts": [
                            {
                                "content": "User prefers root-cause debugging before broad patches.",
                                "category": "habit",
                                "tags": ["debugging"],
                            }
                        ]
                    },
                },
                {
                    "id": "fail",
                    "user_message": "what do you know about me?",
                    "agent_namespace": "main",
                    "expect_contains": ["this string should never appear"],
                    "seed": {
                        "facts": [
                            {
                                "content": "User prefers concise answers when under time pressure.",
                                "category": "preference",
                            }
                        ]
                    },
                },
            ]
        ),
        encoding="utf-8",
    )

    report = await run_replay_eval(scenarios_file=scenarios_file, min_pass_rate=0.75)

    assert report["total"] == 2
    assert report["passed"] == 1
    assert report["failed"] == 1
    assert report["pass_rate"] == 0.5
    assert report["meets_threshold"] is False
    assert report["failed_scenarios"][0]["scenario_id"] == "fail"
    scorecard = report["universal_outcome_scorecard"]
    assert scorecard["overall_score"]["all_metrics_green"] is False
    assert "regression_resilience" in scorecard
    assert "intervention_precision_recall" in scorecard
    assert "trust_calibration_rate" in scorecard
    assert scorecard["regression_resilience"]["pass_rate"] == 0.5


@pytest.mark.asyncio
async def test_run_replay_eval_fixture_passes_regression_gate():
    report = await run_replay_eval(
        scenarios_file="tests/fixtures/replay_eval_scenarios.json",
        min_pass_rate=1.0,
    )

    assert report["total"] >= 2
    assert report["failed"] == 0
    assert report["meets_threshold"] is True
    scorecard = report["universal_outcome_scorecard"]
    assert scorecard["overall_score"]["all_metrics_green"] is True
    assert scorecard["regression_resilience"]["pass_rate"] == 1.0


@pytest.mark.asyncio
async def test_run_replay_eval_identity_fixture_passes_regression_gate():
    report = await run_replay_eval(
        scenarios_file="tests/fixtures/replay_eval_identity_scenarios.json",
        min_pass_rate=1.0,
    )

    assert report["total"] >= 10
    assert report["failed"] == 0
    assert report["meets_threshold"] is True


@pytest.mark.asyncio
async def test_run_replay_eval_identity_edge_fixture_passes_regression_gate():
    report = await run_replay_eval(
        scenarios_file="tests/fixtures/replay_eval_identity_edge_scenarios.json",
        min_pass_rate=1.0,
    )

    assert report["total"] >= 12
    assert report["failed"] == 0
    assert report["meets_threshold"] is True
    assert report["identity_slot_scores"]
    for slot, score in report["identity_slot_scores"].items():
        assert score["required"] >= 1
        assert score["pass_rate"] == 1.0, f"slot={slot} score={score}"


@pytest.mark.asyncio
async def test_run_replay_eval_identity_adversarial_fixture_passes_regression_gate():
    report = await run_replay_eval(
        scenarios_file="tests/fixtures/replay_eval_identity_adversarial_scenarios.json",
        min_pass_rate=1.0,
    )

    assert report["total"] >= 10
    assert report["failed"] == 0
    assert report["meets_threshold"] is True
    assert report["identity_slot_scores"]
    for slot, score in report["identity_slot_scores"].items():
        assert score["required"] >= 1
        assert score["pass_rate"] == 1.0, f"slot={slot} score={score}"


@pytest.mark.asyncio
async def test_run_replay_eval_long_horizon_fixture_passes_regression_gate():
    report = await run_replay_eval(
        scenarios_file="tests/fixtures/replay_eval_long_horizon_scenarios.json",
        min_pass_rate=1.0,
    )

    assert report["total"] >= 6
    assert report["failed"] == 0
    assert report["meets_threshold"] is True
    slot_scores = report["identity_slot_scores"]
    if slot_scores:
        for slot, score in slot_scores.items():
            assert score["required"] >= 1
            assert score["pass_rate"] == 1.0, f"slot={slot} score={score}"


@pytest.mark.asyncio
async def test_run_replay_eval_trust_adversarial_fixture_passes_regression_gate():
    report = await run_replay_eval(
        scenarios_file="tests/fixtures/replay_eval_trust_adversarial_scenarios.json",
        min_pass_rate=1.0,
    )

    assert report["total"] >= 4
    assert report["failed"] == 0
    assert report["meets_threshold"] is True
    scorecard = report["universal_outcome_scorecard"]
    assert scorecard["trust_calibration_rate"]["required"] >= 1
    assert scorecard["trust_calibration_rate"]["pass_rate"] == 1.0


@pytest.mark.asyncio
async def test_run_replay_eval_judge_disabled_by_default():
    report = await run_replay_eval(
        scenarios_file="tests/fixtures/replay_eval_scenarios.json",
        min_pass_rate=1.0,
    )

    judge = report["judge_scorecard"]
    assert judge["enabled"] is False
    assert judge["status"] == "disabled"
    assert report["judge_enforce"] is False
    assert report["deterministic_meets_threshold"] is True
    assert report["meets_threshold"] is True


@pytest.mark.asyncio
async def test_run_replay_eval_optional_judge_reports_when_enabled(monkeypatch: pytest.MonkeyPatch):
    async def fake_judge(**kwargs):
        _ = kwargs
        return {
            "enabled": True,
            "status": "ok",
            "model": "test-judge",
            "sampled_scenarios": 2,
            "required": 2,
            "passed": 2,
            "pass_rate": 1.0,
            "threshold": 1.0,
            "meets_threshold": True,
            "notes": "Good continuity and grounding.",
            "scenario_scores": [],
        }

    monkeypatch.setattr(eval_harness, "_run_llm_judge", fake_judge)

    report = await run_replay_eval(
        scenarios_file="tests/fixtures/replay_eval_scenarios.json",
        min_pass_rate=1.0,
        enable_judge=True,
        judge_enforce=False,
    )

    judge = report["judge_scorecard"]
    assert judge["enabled"] is True
    assert judge["status"] == "ok"
    assert report["deterministic_meets_threshold"] is True
    assert report["meets_threshold"] is True


@pytest.mark.asyncio
async def test_run_replay_eval_judge_enforce_can_fail_even_when_deterministic_passes(monkeypatch: pytest.MonkeyPatch):
    async def fake_judge(**kwargs):
        _ = kwargs
        return {
            "enabled": True,
            "status": "ok",
            "model": "test-judge",
            "sampled_scenarios": 2,
            "required": 2,
            "passed": 1,
            "pass_rate": 0.5,
            "threshold": 1.0,
            "meets_threshold": False,
            "notes": "Judge found adaptation quality gaps.",
            "scenario_scores": [],
        }

    monkeypatch.setattr(eval_harness, "_run_llm_judge", fake_judge)

    report = await run_replay_eval(
        scenarios_file="tests/fixtures/replay_eval_scenarios.json",
        min_pass_rate=1.0,
        enable_judge=True,
        judge_enforce=True,
    )

    assert report["deterministic_meets_threshold"] is True
    assert report["judge_enforce"] is True
    assert report["meets_threshold"] is False


@pytest.mark.asyncio
async def test_run_replay_eval_judge_enforce_fails_when_judge_is_skipped(monkeypatch: pytest.MonkeyPatch):
    async def fake_judge(**kwargs):
        _ = kwargs
        return {
            "enabled": True,
            "status": "skipped",
            "reason": "Judge API key not configured.",
            "sampled_scenarios": 2,
        }

    monkeypatch.setattr(eval_harness, "_run_llm_judge", fake_judge)

    report = await run_replay_eval(
        scenarios_file="tests/fixtures/replay_eval_scenarios.json",
        min_pass_rate=1.0,
        enable_judge=True,
        judge_enforce=True,
    )

    assert report["deterministic_meets_threshold"] is True
    assert report["judge_enforce"] is True
    assert report["judge_scorecard"]["status"] == "skipped"
    assert report["meets_threshold"] is False


@pytest.mark.asyncio
async def test_universal_scorecard_flags_trust_calibration_regression(tmp_path: Path):
    scenarios_file = tmp_path / "trust_calibration_regression.json"
    scenarios_file.write_text(
        json.dumps(
            [
                {
                    "id": "trust_high_certainty_stale_regression",
                    "description": "Stale high-certainty evidence should fail trust calibration even when deterministic checks pass.",
                    "user_message": "what should i remember about my rollout approach?",
                    "agent_namespace": "main",
                    "expect_contains": [
                        "Trust ledger (source tags + freshness):",
                        "Trust operations:",
                        "this marker should not appear",
                    ],
                    "min_counts": {
                        "trust_ledger_lines": 1,
                        "quote_coverage_lines": 1,
                    },
                    "seed": {
                        "facts": [
                            {
                                "content": "Historical rollout policy used replay gates.",
                                "category": "project",
                                "confidence": 0.97,
                                "tags": ["rollout", "reliability"],
                                "updated_at": "2020-01-01T00:00:00Z",
                            }
                        ]
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    report = await run_replay_eval(scenarios_file=scenarios_file, min_pass_rate=1.0)

    assert report["deterministic_meets_threshold"] is False
    scorecard = report["universal_outcome_scorecard"]
    assert scorecard["trust_calibration_rate"]["required"] == 1
    assert scorecard["trust_calibration_rate"]["pass_rate"] == 0.0
    assert scorecard["overall_score"]["all_metrics_green"] is False
    assert "trust_calibration_rate" in scorecard["overall_score"]["metrics_below_threshold"]


def test_runtime_code_does_not_reference_retired_compatibility_views() -> None:
    retired_references = (
        "memory.active_facts",
        "memory.fact_timeline",
        "memory.recent_context",
    )
    src_root = Path(__file__).resolve().parent.parent / "src" / "memory"
    violations: list[str] = []

    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for reference in retired_references:
            if reference in text:
                violations.append(f"{path.relative_to(src_root.parent.parent)} -> {reference}")

    assert not violations, "Runtime code still references retired compatibility views: " + ", ".join(violations)
