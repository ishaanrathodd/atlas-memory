from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from memory.eval_harness import run_replay_eval


_TEMPORAL_KEYS = {
    "created_at",
    "updated_at",
    "event_time",
    "last_observed_at",
    "valid_from",
    "valid_until",
    "message_timestamp",
    "started_at",
    "ended_at",
    "first_observed_at",
    "transaction_time",
    "last_accessed_at",
}


def _parse_iso(value: str) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _shift_temporal(obj: Any, *, days: int) -> Any:
    if isinstance(obj, dict):
        shifted: dict[str, Any] = {}
        for key, value in obj.items():
            if key in _TEMPORAL_KEYS and isinstance(value, str):
                parsed = _parse_iso(value)
                if parsed is not None:
                    shifted[key] = _to_iso(parsed + timedelta(days=days))
                    continue
            shifted[key] = _shift_temporal(value, days=days)
        return shifted
    if isinstance(obj, list):
        return [_shift_temporal(item, days=days) for item in obj]
    return obj


def _load_fixture(path: str) -> list[dict[str, Any]]:
    fixture_path = Path(path)
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _fabricate_months_of_scenarios() -> list[dict[str, Any]]:
    base_fixtures = [
        "tests/fixtures/replay_eval_scenarios.json",
        "tests/fixtures/replay_eval_identity_scenarios.json",
        "tests/fixtures/replay_eval_identity_edge_scenarios.json",
        "tests/fixtures/replay_eval_identity_adversarial_scenarios.json",
        "tests/fixtures/replay_eval_trust_adversarial_scenarios.json",
        "tests/fixtures/replay_eval_long_horizon_scenarios.json",
    ]
    base_scenarios: list[dict[str, Any]] = []
    for fixture in base_fixtures:
        base_scenarios.extend(_load_fixture(fixture))

    month_offsets = [0, 30, 60, 90, 120, 150]
    synthetic: list[dict[str, Any]] = []
    for month_index, offset_days in enumerate(month_offsets, start=1):
        for index, scenario in enumerate(base_scenarios, start=1):
            cloned = _shift_temporal(scenario, days=offset_days)
            cloned["id"] = f"m{month_index:02d}_s{index:03d}_{scenario.get('id', 'scenario')}"
            cloned["description"] = (
                f"[synthetic month {month_index}] "
                f"{str(scenario.get('description') or '').strip()}"
            ).strip()
            synthetic.append(cloned)
    return synthetic


@pytest.mark.asyncio
async def test_run_replay_eval_synthetic_months_stays_green(tmp_path):
    synthetic_scenarios = _fabricate_months_of_scenarios()
    scenarios_file = tmp_path / "synthetic_months_scenarios.json"
    scenarios_file.write_text(json.dumps(synthetic_scenarios), encoding="utf-8")

    report = await run_replay_eval(
        scenarios_file=scenarios_file,
        min_pass_rate=1.0,
    )

    assert report["total"] == len(synthetic_scenarios)
    assert report["total"] >= 200
    assert report["failed"] == 0
    assert report["meets_threshold"] is True

    scorecard = report["universal_outcome_scorecard"]
    assert scorecard["overall_score"]["all_metrics_green"] is True
    assert scorecard["regression_resilience"]["pass_rate"] == 1.0
    assert scorecard["continuity_carry_forward_rate"]["required"] > 0
    assert scorecard["outcome_grounded_guidance_rate"]["required"] > 0
    assert scorecard["adaptation_latency"]["required"] > 0
