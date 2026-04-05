from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import getpass
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    refresh_memory_cases,
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

_DEFAULT_ATLAS_CONFIG = {
    "supabase_schema": "memory",
    "openai_base_url": "https://api.openai.com/v1",
    "embedding_model": "text-embedding-3-small",
    "embedding_dimensions": 512,
}

_DEFAULT_ENV_TEMPLATE = """# Atlas memory setup defaults (non-secret)
MEMORY_DEFAULT_PLATFORM=telegram
MEMORY_OPENAI_BASE_URL=https://api.openai.com/v1

# Required secrets/config (set these values):
# MEMORY_SUPABASE_URL=https://<your-project>.supabase.co
# MEMORY_SUPABASE_KEY=<supabase-service-role-key>

# Optional (needed for embeddings):
# MEMORY_OPENAI_API_KEY=<openai-api-key>
# MEMORY_LLM_MODEL=<default-llm-model-from-hermes-config>
"""


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
    root = Path(os.getenv("HERMES_HOME") or DEFAULT_HERMES_HOME).expanduser()
    atlas_config = _read_atlas_config(root)
    summary_generation_enabled = str(os.getenv("MEMORY_ENABLE_SESSION_SUMMARIES", "0")).strip().lower() in {"1", "true", "yes", "on"}
    if summary_generation_enabled:
        llm_api_key = os.getenv("GLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        llm_base_url = os.getenv("GLM_BASE_URL") or os.getenv("MEMORY_OPENAI_BASE_URL")
        llm_model = (
            os.getenv("MEMORY_SUMMARY_MODEL")
            or os.getenv("MEMORY_LLM_MODEL")
            or atlas_config.get("llm_model")
            or os.getenv("LLM_MODEL")
        )
        fact_pipeline = await consolidate_recent_sessions(
            client,
            lookback_hours=lookback_hours,
            min_message_count=min_message_count,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            agent_namespace=agent_namespace,
        )
    else:
        fact_pipeline = await extract_facts_from_recent_sessions(
            client,
            lookback_hours=lookback_hours,
            min_message_count=min_message_count,
            agent_namespace=agent_namespace,
        )

    sessions_processed = int(fact_pipeline.get("sessions_processed") or 0)
    facts_extracted = int(fact_pipeline.get("facts_extracted") or 0)
    sessions_summarized = sessions_processed if summary_generation_enabled else 0

    active_state = await refresh_active_state(
        client,
        lookback_hours=max(lookback_hours, 72),
        min_message_count=min_message_count,
        include_unsummarized=True,
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
    memory_cases = await refresh_memory_cases(
        client,
        lookback_days=3650,
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
        "summary_generation_enabled": summary_generation_enabled,
        "sessions_processed": sessions_processed,
        "sessions_summarized": sessions_summarized,
        "facts_extracted": facts_extracted,
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
        "memory_cases_updated": int(memory_cases.get("memory_cases_upserted") or 0),
        "memory_case_count": int(memory_cases.get("memory_case_count") or 0),
        "patterns_updated": int(patterns.get("patterns_upserted") or 0),
        "pattern_count": int(patterns.get("pattern_count") or 0),
        "reflections_updated": int(reflections.get("reflections_upserted") or 0),
        "reflection_count": int(reflections.get("reflection_count") or 0),
        "errors": int(fact_pipeline.get("errors") or 0),
        "error_details": list(fact_pipeline.get("error_details") or []),
        "stats": stats,
        "message": (
            "Memory processor completed."
            if sessions_processed > 0
            else "No new sessions needed memory processing."
        ),
    }


def _diagnostic_entry(*, name: str, status: str, detail: str, recommendation: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name,
        "status": status,
        "detail": detail,
    }
    if recommendation:
        item["recommendation"] = recommendation
    return item


def _is_valid_http_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(str(value).strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _read_atlas_config(root: Path) -> dict[str, Any]:
    path = root / "atlas.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_hermes_default_llm(root: Path) -> str | None:
    config_path = root / "config.yaml"
    if not config_path.exists():
        return None
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None

    in_model_block = False
    model_indent = 0
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))

        if not in_model_block:
            if stripped == "model:":
                in_model_block = True
                model_indent = indent
                continue
            if stripped.startswith("model:"):
                inline_value = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                if inline_value:
                    return inline_value
                continue

        if in_model_block:
            if indent <= model_indent and stripped.endswith(":"):
                in_model_block = False
                continue
            if stripped.startswith("default:"):
                value = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value

    return None


def _prompt_text(label: str, *, default: str | None = None) -> str | None:
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{label}{suffix}: ")
    except EOFError:
        return default
    value = str(raw or "").strip()
    return value or default


def _prompt_secret(label: str, *, current_present: bool = False, default: str | None = None) -> str | None:
    suffix = " [press Enter to keep current]" if current_present else ""
    try:
        raw = getpass.getpass(f"{label}{suffix}: ")
    except EOFError:
        return default
    value = str(raw or "").strip()
    return value or default


def _upsert_env_values(path: Path, values: dict[str, str]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    for key, value in values.items():
        if value is None or str(value).strip() == "":
            continue
        replacement = f"{key}={value}"
        replaced = False
        for idx, line in enumerate(existing_lines):
            if line.strip().startswith("#"):
                continue
            if re.match(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=", line):
                if existing_lines[idx] != replacement:
                    existing_lines[idx] = replacement
                    actions.append({"action": "updated", "path": str(path), "key": key})
                replaced = True
                break
        if not replaced:
            existing_lines.append(replacement)
            actions.append({"action": "added", "path": str(path), "key": key})

    path.write_text("\n".join(existing_lines).rstrip() + "\n", encoding="utf-8")
    return actions


def _upsert_atlas_config(path: Path, updates: dict[str, Any]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    current: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
        except Exception:
            current = {}

    changed = False
    for key, value in updates.items():
        if value is None or str(value).strip() == "":
            continue
        if current.get(key) != value:
            current[key] = value
            actions.append({"action": "updated", "path": str(path), "key": str(key)})
            changed = True

    if changed:
        path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return actions


def _read_env_file(root: Path) -> dict[str, str]:
    env_path = root / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        values[key.strip()] = _strip_quotes(raw_value)
    return values


async def run_setup_diagnostics(
    *,
    client: MemoryClient | None,
    hermes_home: str | Path | None = None,
    client_build_error: str | None = None,
) -> dict[str, Any]:
    root = Path(hermes_home or os.getenv("HERMES_HOME") or DEFAULT_HERMES_HOME).expanduser()
    env_path = root / ".env"
    atlas_config = _read_atlas_config(root)

    checks: list[dict[str, Any]] = []
    checks.append(
        _diagnostic_entry(
            name="hermes_home",
            status="pass" if root.exists() else "fail",
            detail=f"HERMES_HOME={root}",
            recommendation=None if root.exists() else "Create the Hermes home directory or set HERMES_HOME.",
        )
    )
    checks.append(
        _diagnostic_entry(
            name="env_file",
            status="pass" if env_path.exists() else "warn",
            detail=f"Env file path: {env_path}",
            recommendation=None if env_path.exists() else "Add a .env file under HERMES_HOME for consistent local setup.",
        )
    )

    supabase_url = os.getenv("MEMORY_SUPABASE_URL")
    checks.append(
        _diagnostic_entry(
            name="supabase_url",
            status="pass" if _is_valid_http_url(supabase_url) else "fail",
            detail="MEMORY_SUPABASE_URL configured." if _is_valid_http_url(supabase_url) else "MEMORY_SUPABASE_URL is missing or invalid.",
            recommendation=None if _is_valid_http_url(supabase_url) else "Set MEMORY_SUPABASE_URL to your project HTTPS endpoint.",
        )
    )

    supabase_key = os.getenv("MEMORY_SUPABASE_KEY")
    key_ok = bool((supabase_key or "").strip()) and len((supabase_key or "").strip()) >= 20
    checks.append(
        _diagnostic_entry(
            name="supabase_key",
            status="pass" if key_ok else "fail",
            detail="MEMORY_SUPABASE_KEY configured." if key_ok else "MEMORY_SUPABASE_KEY is missing or too short.",
            recommendation=None if key_ok else "Set MEMORY_SUPABASE_KEY (or SUPABASE_SERVICE_KEY) in your environment.",
        )
    )

    openai_key = os.getenv("MEMORY_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    checks.append(
        _diagnostic_entry(
            name="embedding_key",
            status="pass" if openai_key else "warn",
            detail="Embedding API key configured." if openai_key else "Embedding API key not configured.",
            recommendation=None if openai_key else "Set MEMORY_OPENAI_API_KEY (or OPENAI_API_KEY) for embedding refresh tasks.",
        )
    )

    openai_base_url = os.getenv("MEMORY_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    checks.append(
        _diagnostic_entry(
            name="embedding_base_url",
            status="pass" if _is_valid_http_url(openai_base_url) else "warn",
            detail=f"Embedding base URL: {openai_base_url or 'missing'}",
            recommendation=None if _is_valid_http_url(openai_base_url) else "Set MEMORY_OPENAI_BASE_URL to a valid HTTP(S) endpoint.",
        )
    )

    llm_model = (
        os.getenv("MEMORY_SUMMARY_MODEL")
        or os.getenv("MEMORY_LLM_MODEL")
        or atlas_config.get("llm_model")
        or _read_hermes_default_llm(root)
        or os.getenv("LLM_MODEL")
    )
    checks.append(
        _diagnostic_entry(
            name="llm_model",
            status="pass" if llm_model else "warn",
            detail=f"LLM model: {llm_model}" if llm_model else "No LLM model configured.",
            recommendation=None if llm_model else "Set MEMORY_LLM_MODEL (or configure model.default in ~/.hermes/config.yaml).",
        )
    )

    bridge_available = importlib.util.find_spec("memory.bridge_server") is not None
    checks.append(
        _diagnostic_entry(
            name="atlas_runtime_import",
            status="pass" if bridge_available else "fail",
            detail="memory.bridge_server import path is available." if bridge_available else "memory.bridge_server import path not found.",
            recommendation=None if bridge_available else "Install atlas package in the current environment (e.g. pip install -e .).",
        )
    )

    if client_build_error:
        checks.append(
            _diagnostic_entry(
                name="client_build",
                status="fail",
                detail=f"Failed to create memory client: {client_build_error}",
                recommendation="Fix the environment errors above and rerun setup-diagnostics.",
            )
        )
    elif client is None:
        checks.append(
            _diagnostic_entry(
                name="client_build",
                status="fail",
                detail="No memory client was available for diagnostics.",
                recommendation="Fix configuration and rerun setup-diagnostics.",
            )
        )
    else:
        try:
            is_healthy = bool(await client.health_check())
            checks.append(
                _diagnostic_entry(
                    name="supabase_health",
                    status="pass" if is_healthy else "fail",
                    detail="Supabase transport health check passed." if is_healthy else "Supabase transport health check failed.",
                    recommendation=None if is_healthy else "Verify Supabase URL/key and network connectivity.",
                )
            )
        except Exception as exc:
            checks.append(
                _diagnostic_entry(
                    name="supabase_health",
                    status="fail",
                    detail=f"Supabase health check error: {exc}",
                    recommendation="Verify Supabase credentials and that the memory schema/tables are provisioned.",
                )
            )

    failing = [entry for entry in checks if entry["status"] == "fail"]
    warnings = [entry for entry in checks if entry["status"] == "warn"]
    return {
        "task": "setup-diagnostics",
        "ok": not failing,
        "failed": len(failing),
        "warnings": len(warnings),
        "checks": checks,
    }


def _ensure_setup_files(root: Path) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    root.mkdir(parents=True, exist_ok=True)

    env_path = root / ".env"
    if not env_path.exists():
        env_path.write_text(_DEFAULT_ENV_TEMPLATE, encoding="utf-8")
        actions.append({"action": "created", "path": str(env_path)})

    config_path = root / "atlas.json"
    if not config_path.exists():
        config_path.write_text(json.dumps(_DEFAULT_ATLAS_CONFIG, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        actions.append({"action": "created", "path": str(config_path)})

    return actions


def _collect_setup_inputs(root: Path) -> dict[str, str]:
    file_env = _read_env_file(root)
    default_llm = (
        file_env.get("MEMORY_LLM_MODEL")
        or os.getenv("MEMORY_LLM_MODEL")
        or _read_hermes_default_llm(root)
        or os.getenv("LLM_MODEL")
        or "gpt-5.3-codex"
    )

    # Keep setup intentionally minimal and focused on required inputs.
    supabase_url = _prompt_text(
        "Supabase project URL",
        default=file_env.get("MEMORY_SUPABASE_URL") or os.getenv("MEMORY_SUPABASE_URL"),
    )
    supabase_key = _prompt_secret(
        "Supabase service role key",
        current_present=bool(file_env.get("MEMORY_SUPABASE_KEY") or os.getenv("MEMORY_SUPABASE_KEY")),
        default=file_env.get("MEMORY_SUPABASE_KEY") or os.getenv("MEMORY_SUPABASE_KEY"),
    )
    embedding_api_key = _prompt_secret(
        "Embedding model API key",
        current_present=bool(file_env.get("MEMORY_OPENAI_API_KEY") or os.getenv("MEMORY_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")),
        default=file_env.get("MEMORY_OPENAI_API_KEY") or os.getenv("MEMORY_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
    )
    llm_model = _prompt_text("LLM model choice", default=default_llm)

    values: dict[str, str] = {}
    if supabase_url:
        values["MEMORY_SUPABASE_URL"] = supabase_url
    if supabase_key:
        values["MEMORY_SUPABASE_KEY"] = supabase_key
    if embedding_api_key:
        values["MEMORY_OPENAI_API_KEY"] = embedding_api_key
    if llm_model:
        values["MEMORY_LLM_MODEL"] = llm_model
    return values


async def run_setup_workflow(
    *,
    client: MemoryClient | None,
    hermes_home: str | Path | None = None,
    client_build_error: str | None = None,
    auto_fix: bool = True,
) -> dict[str, Any]:
    root = Path(hermes_home or os.getenv("HERMES_HOME") or DEFAULT_HERMES_HOME).expanduser()
    actions = _ensure_setup_files(root) if auto_fix else []

    selected_llm_model: str | None = None
    if auto_fix and sys.stdin.isatty():
        setup_values = _collect_setup_inputs(root)
        if setup_values:
            env_path = root / ".env"
            actions.extend(_upsert_env_values(env_path, setup_values))
            selected_llm_model = setup_values.get("MEMORY_LLM_MODEL")
            actions.extend(_upsert_atlas_config(root / "atlas.json", {"llm_model": selected_llm_model}))

    # Reload aliases/env after creating defaults so diagnostics reflect current files.
    load_hermes_env(root)
    diagnostics = await run_setup_diagnostics(
        client=client,
        hermes_home=root,
        client_build_error=client_build_error,
    )

    unresolved = [
        {
            "name": check.get("name"),
            "status": check.get("status"),
            "recommendation": check.get("recommendation"),
        }
        for check in diagnostics.get("checks", [])
        if check.get("status") in {"fail", "warn"}
    ]
    return {
        "task": "setup",
        "auto_fix": auto_fix,
        "interactive": bool(auto_fix and sys.stdin.isatty()),
        "actions": actions,
        "selected_llm_model": (
            selected_llm_model
            or os.getenv("MEMORY_LLM_MODEL")
            or _read_atlas_config(root).get("llm_model")
            or _read_hermes_default_llm(root)
        ),
        "ready": bool(diagnostics.get("ok")),
        "diagnostics": diagnostics,
        "next_steps": unresolved,
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
    enable_judge: bool | None = None,
    judge_enforce: bool | None = None,
    judge_model: str | None = None,
    judge_sample_limit: int | None = None,
    auto_fix: bool = True,
) -> dict[str, Any]:
    if task == "replay-eval":
        resolved_min_pass_rate = min_pass_rate if min_pass_rate is not None else float(os.getenv("MEMORY_EVAL_MIN_PASS_RATE", "1.0"))
        return await run_replay_eval(
            scenarios_file=scenarios_file,
            min_pass_rate=resolved_min_pass_rate,
            enable_judge=enable_judge,
            judge_enforce=judge_enforce,
            judge_model=judge_model,
            judge_sample_limit=judge_sample_limit,
        )

    build_error: str | None = None
    owns_client = client is None
    active_client = client
    if active_client is None:
        try:
            active_client = build_client()
        except Exception as exc:
            build_error = str(exc)

    if task == "setup-diagnostics":
        result = await run_setup_diagnostics(
            client=active_client,
            hermes_home=hermes_home,
            client_build_error=build_error,
        )
        if owns_client and active_client is not None:
            await close_client_resources(active_client)
        return result

    if task == "setup":
        result = await run_setup_workflow(
            client=active_client,
            hermes_home=hermes_home,
            client_build_error=build_error,
            auto_fix=auto_fix,
        )
        if owns_client and active_client is not None:
            await close_client_resources(active_client)
        return result

    if active_client is None:
        raise RuntimeError(f"Failed to initialize memory client: {build_error or 'unknown error'}")

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
        choices=("process-memory", "extract-facts", "backfill", "stats", "health", "replay-eval", "setup-diagnostics", "setup"),
    )
    parser.add_argument("--lookback-hours", type=int)
    parser.add_argument("--min-message-count", type=int)
    parser.add_argument("--scenarios-file")
    parser.add_argument("--min-pass-rate", type=float)
    parser.add_argument("--enable-judge", action="store_true")
    parser.add_argument("--judge-enforce", action="store_true")
    parser.add_argument("--judge-model")
    parser.add_argument("--judge-sample-limit", type=int)
    parser.add_argument("--no-auto-fix", action="store_true")
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
                enable_judge=args.enable_judge,
                judge_enforce=args.judge_enforce,
                judge_model=args.judge_model,
                judge_sample_limit=args.judge_sample_limit,
                auto_fix=not args.no_auto_fix,
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
