from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from memory.backfill import backfill_memory_files
from memory.client import MemoryClient
from memory.consolidation import (
    consolidate_recent_sessions,
    extract_facts_from_recent_sessions,
    refresh_active_state,
    refresh_commitments,
    refresh_corrections,
    refresh_decision_outcomes,
    refresh_directives,
    refresh_patterns,
    refresh_reflections,
    refresh_timeline_events,
)
from memory.emotions import EmotionAnalyzer
from memory.embedding import OpenAIEmbeddingProvider
from memory.eval_harness import run_replay_eval
from memory.instance_identity import get_agent_namespace
from memory.observability import record_task_observability
from memory.transport import SupabaseTransport

DEFAULT_HERMES_HOME = Path.home() / ".hermes"
DEFAULT_GLM_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
_ENV_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _strip_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _resolve_env_value(raw_value: str, context: dict[str, str]) -> str:
    value = _strip_quotes(raw_value)
    for _ in range(8):
        resolved = _ENV_REFERENCE.sub(lambda match: context.get(match.group(1), os.environ.get(match.group(1), "")), value)
        if resolved == value:
            break
        value = resolved
    return value


def _apply_env_aliases() -> None:
    aliases = {
        "MEMORY_SUPABASE_URL": "MEMORY_SUPABASE_URL",
        "MEMORY_SUPABASE_KEY": "MEMORY_SUPABASE_KEY",
        "MEMORY_OPENAI_API_KEY": "MEMORY_OPENAI_API_KEY",
        "MEMORY_OPENAI_BASE_URL": "MEMORY_OPENAI_BASE_URL",
        "MEMORY_DEFAULT_PLATFORM": "MEMORY_DEFAULT_PLATFORM",
        "MEMORY_SUPABASE_URL": "SUPABASE_URL",
        "MEMORY_SUPABASE_KEY": "SUPABASE_SERVICE_KEY",
        "MEMORY_OPENAI_API_KEY": "OPENAI_API_KEY",
        "MEMORY_OPENAI_BASE_URL": "OPENAI_BASE_URL",
    }
    for target, source in aliases.items():
        if not os.getenv(target) and os.getenv(source):
            os.environ[target] = os.environ[source]

    os.environ.setdefault("HERMES_HOME", str(Path(os.getenv("HERMES_HOME", str(DEFAULT_HERMES_HOME))).expanduser()))
    os.environ.setdefault("MEMORY_SUPABASE_URL", os.getenv("MEMORY_SUPABASE_URL", "https://zopqdjmvbokconktqexf.supabase.co"))
    os.environ.setdefault("MEMORY_OPENAI_BASE_URL", os.getenv("MEMORY_OPENAI_BASE_URL", "https://api.openai.com/v1"))
    os.environ.setdefault("MEMORY_DEFAULT_PLATFORM", os.getenv("MEMORY_DEFAULT_PLATFORM", "telegram"))
    os.environ.setdefault("MEMORY_SUPABASE_URL", "https://zopqdjmvbokconktqexf.supabase.co")
    os.environ.setdefault("MEMORY_OPENAI_BASE_URL", "https://api.openai.com/v1")
    os.environ.setdefault("MEMORY_DEFAULT_PLATFORM", "telegram")


def load_hermes_env(hermes_home: str | Path | None = None) -> dict[str, str]:
    root = Path(hermes_home or os.getenv("HERMES_HOME") or DEFAULT_HERMES_HOME).expanduser()
    os.environ["HERMES_HOME"] = str(root)
    env_path = root / ".env"
    loaded: dict[str, str] = {}
    if not env_path.exists():
        _apply_env_aliases()
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_ASSIGNMENT.match(line)
        if not match:
            continue
        key, raw_value = match.groups()
        context = {**os.environ, **loaded}
        value = _resolve_env_value(raw_value, context)
        loaded[key] = value
        os.environ[key] = value

    _apply_env_aliases()
    return loaded


def build_client() -> MemoryClient:
    embedding = OpenAIEmbeddingProvider()
    transport = SupabaseTransport(embedding_provider=embedding)
    return MemoryClient(
        transport=transport,
        embedding=embedding,
        emotions=EmotionAnalyzer(),
    )


async def close_client_resources(client: MemoryClient) -> None:
    closer = getattr(client.embedding, "aclose", None)
    if callable(closer):
        await closer()


async def _count_table_rows(client: MemoryClient, table: str) -> int:
    transport = client.transport
    custom_counter = getattr(transport, "count_rows", None)
    if callable(custom_counter):
        return int(await custom_counter(table))

    schema_client_factory = getattr(transport, "_schema_client", None)
    runner = getattr(transport, "_run", None)
    if callable(schema_client_factory) and callable(runner):
        def _query() -> Any:
            return schema_client_factory().table(table).select("id", count="exact").limit(0).execute()

        response = await runner(_query)
        count = getattr(response, "count", None)
        if count is None:
            data = getattr(response, "data", []) or []
            return len(data)
        return int(count)

    raise RuntimeError(f"Transport does not support counting {table}.")


async def collect_stats(client: MemoryClient) -> dict[str, int]:
    return {
        "session_count": await _count_table_rows(client, "sessions"),
        "episode_count": await _count_table_rows(client, "episodes"),
        "fact_count": await _count_table_rows(client, "facts"),
    }


async def process_memory(
    client: MemoryClient,
    *,
    lookback_hours: int,
    min_message_count: int,
) -> dict[str, Any]:
    agent_namespace = get_agent_namespace()
    llm_api_key = os.getenv("GLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    llm_base_url = os.getenv("GLM_BASE_URL") or os.getenv("MEMORY_OPENAI_BASE_URL")

    consolidation = await consolidate_recent_sessions(
        client,
        lookback_hours=lookback_hours,
        min_message_count=min_message_count,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        agent_namespace=agent_namespace,
    )
    active_state = await refresh_active_state(
        client,
        lookback_hours=max(lookback_hours, 72),
        min_message_count=min_message_count,
        agent_namespace=agent_namespace,
    )
    directives = await refresh_directives(
        client,
        lookback_days=90,
        agent_namespace=agent_namespace,
    )
    commitments = await refresh_commitments(
        client,
        lookback_days=365,
        agent_namespace=agent_namespace,
    )
    corrections = await refresh_corrections(
        client,
        lookback_days=365,
        agent_namespace=agent_namespace,
    )
    timeline_events = await refresh_timeline_events(
        client,
        lookback_days=3650,
        min_message_count=min_message_count,
        agent_namespace=agent_namespace,
    )
    decision_outcomes = await refresh_decision_outcomes(
        client,
        lookback_days=3650,
        min_message_count=min_message_count,
        agent_namespace=agent_namespace,
    )
    patterns = await refresh_patterns(
        client,
        lookback_days=3650,
        min_message_count=min_message_count,
        agent_namespace=agent_namespace,
    )
    reflections = await refresh_reflections(
        client,
        lookback_days=3650,
        min_message_count=min_message_count,
        agent_namespace=agent_namespace,
    )
    stats = await collect_stats(client)

    return {
        "task": "process-memory",
        "agent_namespace": agent_namespace,
        "memory_processor": True,
        "sessions_summarized": int(consolidation.get("sessions_processed") or 0),
        "facts_extracted": int(consolidation.get("facts_extracted") or 0),
        "active_states_updated": int(active_state.get("states_upserted") or 0),
        "active_states_staled": int(active_state.get("states_staled") or 0),
        "directives_updated": int(directives.get("directives_upserted") or 0),
        "directive_count": int(directives.get("directive_count") or 0),
        "commitments_updated": int(commitments.get("commitments_upserted") or 0),
        "commitment_count": int(commitments.get("commitment_count") or 0),
        "corrections_updated": int(corrections.get("corrections_upserted") or 0),
        "correction_count": int(corrections.get("correction_count") or 0),
        "timeline_events_updated": int(timeline_events.get("timeline_events_upserted") or 0),
        "timeline_event_count": int(timeline_events.get("timeline_event_count") or 0),
        "decision_outcomes_updated": int(decision_outcomes.get("decision_outcomes_upserted") or 0),
        "decision_outcome_count": int(decision_outcomes.get("decision_outcome_count") or 0),
        "patterns_updated": int(patterns.get("patterns_upserted") or 0),
        "pattern_count": int(patterns.get("pattern_count") or 0),
        "reflections_updated": int(reflections.get("reflections_upserted") or 0),
        "reflection_count": int(reflections.get("reflection_count") or 0),
        "errors": int(consolidation.get("errors") or 0),
        "error_details": list(consolidation.get("error_details") or []),
        "stats": stats,
        "message": (
            "Memory processor completed."
            if int(consolidation.get("sessions_processed") or 0) > 0
            else "No new sessions needed memory processing."
        ),
    }


async def run_task(
    task: str,
    *,
    client: MemoryClient | None = None,
    hermes_home: str | Path | None = None,
    lookback_hours: int | None = None,
    min_message_count: int | None = None,
    scenarios_file: str | Path | None = None,
    min_pass_rate: float | None = None,
) -> dict[str, Any]:
    if task == "replay-eval":
        resolved_min_pass_rate = min_pass_rate if min_pass_rate is not None else float(os.getenv("MEMORY_EVAL_MIN_PASS_RATE", "1.0"))
        return await run_replay_eval(
            scenarios_file=scenarios_file,
            min_pass_rate=resolved_min_pass_rate,
        )

    owns_client = client is None
    active_client = client or build_client()
    try:
        resolved_lookback = lookback_hours or int(os.getenv("MEMORY_CONSOLIDATE_LOOKBACK_HOURS", "6"))
        resolved_min_messages = min_message_count or int(os.getenv("MEMORY_CONSOLIDATE_MIN_MESSAGES", "3"))
        if task == "process-memory":
            return await process_memory(
                active_client,
                lookback_hours=resolved_lookback,
                min_message_count=resolved_min_messages,
            )
        if task == "extract-facts":
            return await extract_facts_from_recent_sessions(
                active_client,
                lookback_hours=resolved_lookback,
                min_message_count=resolved_min_messages,
                agent_namespace=get_agent_namespace(),
            )
        if task == "backfill":
            return await backfill_memory_files(active_client, hermes_home=hermes_home)
        if task == "stats":
            return await collect_stats(active_client)
        if task == "health":
            return {"ok": await active_client.health_check()}
        raise ValueError(f"Unknown task: {task}")
    finally:
        if owns_client:
            await close_client_resources(active_client)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m memory.curator_runtime")
    parser.add_argument(
        "task",
        choices=("process-memory", "extract-facts", "backfill", "stats", "health", "replay-eval"),
    )
    parser.add_argument("--lookback-hours", type=int)
    parser.add_argument("--min-message-count", type=int)
    parser.add_argument("--scenarios-file")
    parser.add_argument("--min-pass-rate", type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    hermes_home = os.getenv("HERMES_HOME") or str(DEFAULT_HERMES_HOME)
    load_hermes_env(hermes_home)
    started_at = datetime.now(timezone.utc)
    try:
        result = asyncio.run(
            run_task(
                args.task,
                hermes_home=hermes_home,
                lookback_hours=args.lookback_hours,
                min_message_count=args.min_message_count,
                scenarios_file=args.scenarios_file,
                min_pass_rate=args.min_pass_rate,
            )
        )
        record_task_observability(
            task=args.task,
            result=result if isinstance(result, dict) else None,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            hermes_home=hermes_home,
            error=None,
        )
    except Exception as exc:
        try:
            record_task_observability(
                task=args.task,
                result=None,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                hermes_home=hermes_home,
                error=str(exc),
            )
        except Exception as obs_exc:
            _log(f"Failed writing observability for {args.task}: {obs_exc}")
        _log(f"Curator task {args.task} failed: {exc}")
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
