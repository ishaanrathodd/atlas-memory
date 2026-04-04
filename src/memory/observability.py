from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _task_error_count(task: str, result: dict[str, Any] | None, error: str | None) -> int:
    if error:
        return 1
    if not isinstance(result, dict):
        return 0
    if task == "health" and not bool(result.get("ok", False)):
        return 1
    return _safe_int(result.get("errors"), 0)


def _task_success(task: str, result: dict[str, Any] | None, error: str | None) -> bool:
    if error:
        return False
    if not isinstance(result, dict):
        return True
    if task == "health":
        return bool(result.get("ok", False))
    if task == "replay-eval":
        if "meets_threshold" in result:
            return bool(result.get("meets_threshold"))
    return _task_error_count(task, result, error) == 0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_health(path: Path) -> dict[str, Any]:
    existing = _read_json(path)
    if not existing:
        return {
            "status": "unknown",
            "heartbeat_at": None,
            "last_successful_tasks": {},
            "degraded": {
                "memory_unavailable": False,
                "eval_regression": False,
            },
            "last_error": None,
        }
    existing.setdefault("last_successful_tasks", {})
    existing.setdefault("degraded", {})
    existing["degraded"].setdefault("memory_unavailable", False)
    existing["degraded"].setdefault("eval_regression", False)
    return existing


def _load_summary(path: Path) -> dict[str, Any]:
    existing = _read_json(path)
    if not existing:
        return {
            "total_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "tasks": {},
        }
    existing.setdefault("tasks", {})
    existing.setdefault("total_runs", 0)
    existing.setdefault("successful_runs", 0)
    existing.setdefault("failed_runs", 0)
    return existing


def record_task_observability(
    *,
    task: str,
    result: dict[str, Any] | None,
    started_at: datetime,
    finished_at: datetime | None = None,
    hermes_home: str | Path | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    root = Path(hermes_home or Path.home() / ".hermes").expanduser()
    base_dir = root / "logs" / "memory"
    base_dir.mkdir(parents=True, exist_ok=True)

    end = finished_at or _utcnow()
    duration_ms = max(0, int((end - started_at).total_seconds() * 1000))
    success = _task_success(task, result, error)
    error_count = _task_error_count(task, result, error)

    event = {
        "task": task,
        "started_at": _iso(started_at),
        "finished_at": _iso(end),
        "duration_ms": duration_ms,
        "success": success,
        "error_count": error_count,
        "agent_namespace": (result or {}).get("agent_namespace"),
        "sessions_summarized": _safe_int((result or {}).get("sessions_summarized"), 0),
        "facts_extracted": _safe_int((result or {}).get("facts_extracted"), 0),
        "replay_pass_rate": (result or {}).get("pass_rate") if task == "replay-eval" else None,
        "error": error,
    }

    events_path = base_dir / "curator_task_runs.jsonl"
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")

    health_path = base_dir / "curator_health.json"
    health = _load_health(health_path)
    health["heartbeat_at"] = _iso(end)
    health["status"] = "healthy" if success else "degraded"
    health["degraded"]["memory_unavailable"] = bool((task in {"process-memory", "extract-facts", "stats", "health"}) and not success)
    health["degraded"]["eval_regression"] = bool(task == "replay-eval" and not success)
    health["last_error"] = None if success else (error or "task returned errors")
    if success:
        health["last_successful_tasks"][task] = _iso(end)
    _write_json(health_path, health)

    summary_path = base_dir / "curator_summary.json"
    summary = _load_summary(summary_path)
    summary["total_runs"] = _safe_int(summary.get("total_runs"), 0) + 1
    if success:
        summary["successful_runs"] = _safe_int(summary.get("successful_runs"), 0) + 1
    else:
        summary["failed_runs"] = _safe_int(summary.get("failed_runs"), 0) + 1

    task_summary = summary["tasks"].get(task, {})
    task_runs = _safe_int(task_summary.get("runs"), 0)
    prev_avg = float(task_summary.get("avg_duration_ms", 0.0))
    new_avg = ((prev_avg * task_runs) + float(duration_ms)) / float(task_runs + 1)
    task_summary["runs"] = task_runs + 1
    task_summary["avg_duration_ms"] = round(new_avg, 3)
    task_summary["last_duration_ms"] = duration_ms
    task_summary["last_status"] = "success" if success else "failure"
    task_summary["last_run_at"] = _iso(end)
    if success:
        task_summary["last_success_at"] = _iso(end)
    else:
        task_summary["last_failure_at"] = _iso(end)
    if task == "replay-eval" and isinstance(result, dict):
        task_summary["last_pass_rate"] = result.get("pass_rate")
        task_summary["last_min_pass_rate"] = result.get("min_pass_rate")

    summary["tasks"][task] = task_summary
    _write_json(summary_path, summary)

    return {
        "events_path": str(events_path),
        "health_path": str(health_path),
        "summary_path": str(summary_path),
        "event": event,
    }
