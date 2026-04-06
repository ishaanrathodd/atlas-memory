from __future__ import annotations

import json
import importlib.util
import os
import sys
import types
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1] / "integrations" / "hermes" / "plugins" / "memory" / "atlas"
HERMES_AGENT_DIR = Path(__file__).resolve().parents[2] / "hermes-agent"

if str(HERMES_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(HERMES_AGENT_DIR))

if "agent.memory_provider" not in sys.modules:
    agent_module = types.ModuleType("agent")
    memory_provider_module = types.ModuleType("agent.memory_provider")

    class _MemoryProvider:  # pragma: no cover - simple CI fallback shim
        pass

    memory_provider_module.MemoryProvider = _MemoryProvider
    agent_module.memory_provider = memory_provider_module
    sys.modules["agent"] = agent_module
    sys.modules["agent.memory_provider"] = memory_provider_module

_SPEC = importlib.util.spec_from_file_location("atlas_test_plugin", PLUGIN_DIR / "__init__.py")
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
AtlasMemoryProvider = _MODULE.AtlasMemoryProvider
normalize_agent_namespace = _MODULE._normalize_agent_namespace


def test_atlas_provider_config_schema_contains_setup_fields() -> None:
    provider = AtlasMemoryProvider()

    schema = provider.get_config_schema()
    keys = {field["key"] for field in schema}

    assert "supabase_url" in keys
    assert "supabase_key" in keys
    assert "openai_api_key" in keys
    assert "llm_model" in keys
    assert "supabase_schema" not in keys


def test_atlas_provider_save_config_writes_profile_scoped_json(tmp_path: Path) -> None:
    provider = AtlasMemoryProvider()

    provider.save_config(
        {
            "supabase_url": "https://example.supabase.co",
            "llm_model": "gpt-5.3-codex",
        },
        str(tmp_path),
    )

    payload = json.loads((tmp_path / "atlas.json").read_text(encoding="utf-8"))
    assert payload["supabase_url"] == "https://example.supabase.co"
    assert payload["llm_model"] == "gpt-5.3-codex"


def test_atlas_provider_system_prompt_guides_model_to_use_recalled_memory() -> None:
    provider = AtlasMemoryProvider()

    block = provider.system_prompt_block()

    assert "# Atlas Memory" in block
    assert "Do not say memory is empty" in block
    assert "primary retrieved evidence" in block


def test_atlas_provider_prefetch_format_makes_recall_hard_to_ignore() -> None:
    provider = AtlasMemoryProvider()

    formatted = provider._format_prefetch_context(
        "what did we do yesterday?",
        "Recent major events:\n- We migrated the memory schema.",
    )

    assert "## Atlas Memory Recall" in formatted
    assert "Current user message: what did we do yesterday?" in formatted
    assert "do not claim you" in formatted.lower()
    assert "We migrated the memory schema." in formatted


def test_atlas_provider_prefetch_format_skips_empty_context() -> None:
    provider = AtlasMemoryProvider()

    assert provider._format_prefetch_context("hey", "") == ""


def test_default_profile_maps_to_default_namespace() -> None:
    assert normalize_agent_namespace("") == "default"
    assert normalize_agent_namespace("default") == "default"
    assert normalize_agent_namespace("primary") == "default"
    assert normalize_agent_namespace("main") == "main"
    assert normalize_agent_namespace("atlas") == "atlas"


def test_build_runtime_env_resolves_references_and_atlas_aliases(tmp_path: Path, monkeypatch) -> None:
    for key in (
        "MEMORY_SUPABASE_URL",
        "MEMORY_SUPABASE_KEY",
        "MEMORY_OPENAI_BASE_URL",
        "SUPABASE_SERVICE_KEY",
        "ATLAS_SUPABASE_URL",
        "ATLAS_OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    (tmp_path / ".env").write_text(
        "SUPABASE_SERVICE_KEY=service-role-secret\n"
        "ATLAS_SUPABASE_URL=https://example.supabase.co\n"
        "MEMORY_SUPABASE_KEY=${SUPABASE_SERVICE_KEY}\n"
        "ATLAS_OPENAI_BASE_URL=https://api.z.ai/v1\n",
        encoding="utf-8",
    )

    env = _MODULE._build_runtime_env(str(tmp_path))

    assert env["MEMORY_SUPABASE_URL"] == "https://example.supabase.co"
    assert env["MEMORY_SUPABASE_KEY"] == "service-role-secret"
    assert env["MEMORY_OPENAI_BASE_URL"] == "https://api.z.ai/v1"
    assert env["HERMES_HOME"] == str(tmp_path)
