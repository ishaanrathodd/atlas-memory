from __future__ import annotations

import json

import pytest

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


@pytest.mark.asyncio
async def test_run_replay_eval_fixture_passes_regression_gate():
    report = await run_replay_eval(
        scenarios_file="tests/fixtures/replay_eval_scenarios.json",
        min_pass_rate=1.0,
    )

    assert report["total"] >= 2
    assert report["failed"] == 0
    assert report["meets_threshold"] is True
