from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from memory.observability import record_task_observability


def test_record_task_observability_writes_health_and_summary(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(seconds=2)
    result = {
        "task": "process-memory",
        "agent_namespace": "main",
        "sessions_summarized": 3,
        "facts_extracted": 7,
        "errors": 0,
    }

    report = record_task_observability(
        task="process-memory",
        result=result,
        started_at=start,
        hermes_home=tmp_path,
    )

    events_path = tmp_path / "logs" / "memory" / "curator_task_runs.jsonl"
    health_path = tmp_path / "logs" / "memory" / "curator_health.json"
    summary_path = tmp_path / "logs" / "memory" / "curator_summary.json"

    assert events_path.exists()
    assert health_path.exists()
    assert summary_path.exists()
    assert report["event"]["success"] is True

    health = json.loads(health_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert health["status"] == "healthy"
    assert health["degraded"]["memory_unavailable"] is False
    assert "process-memory" in health["last_successful_tasks"]

    assert summary["total_runs"] == 1
    assert summary["successful_runs"] == 1
    assert summary["failed_runs"] == 0
    assert summary["tasks"]["process-memory"]["last_status"] == "success"


def test_record_task_observability_marks_eval_regression(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(seconds=1)

    record_task_observability(
        task="replay-eval",
        result={
            "task": "replay-eval",
            "pass_rate": 1.0,
            "min_pass_rate": 1.0,
            "meets_threshold": True,
        },
        started_at=start,
        hermes_home=tmp_path,
    )

    record_task_observability(
        task="replay-eval",
        result={
            "task": "replay-eval",
            "pass_rate": 0.5,
            "min_pass_rate": 1.0,
            "meets_threshold": False,
        },
        started_at=start,
        hermes_home=tmp_path,
    )

    health_path = tmp_path / "logs" / "memory" / "curator_health.json"
    summary_path = tmp_path / "logs" / "memory" / "curator_summary.json"

    health = json.loads(health_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert health["status"] == "degraded"
    assert health["degraded"]["eval_regression"] is True
    assert summary["total_runs"] == 2
    assert summary["failed_runs"] == 1
    assert summary["tasks"]["replay-eval"]["last_status"] == "failure"
    assert summary["tasks"]["replay-eval"]["last_pass_rate"] == 0.5
