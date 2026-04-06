from __future__ import annotations

import json
import importlib.util
import os
import queue
import re
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


def test_current_routing_metadata_reads_live_gateway_env(monkeypatch) -> None:
    provider = AtlasMemoryProvider()
    provider._started_at = "2026-04-06T15:00:00+00:00"
    provider._platform = "signal"

    monkeypatch.setenv("HERMES_SESSION_KEY", "agent:main:signal:dm:+917977457870")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "signal")
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", "+917977457870")
    monkeypatch.setenv("HERMES_SESSION_CHAT_NAME", "Ishaan")
    monkeypatch.setenv("HERMES_SESSION_THREAD_ID", "thread-42")
    monkeypatch.setenv("HERMES_SESSION_USER_ID", "user-123")
    monkeypatch.setenv("HERMES_SESSION_USER_ID_ALT", "alt-456")

    routing = provider._current_routing_metadata()

    assert routing == {
        "session_key": "agent:main:signal:dm:+917977457870",
        "bound_at": "2026-04-06T15:00:00+00:00",
        "platform": "signal",
        "chat_id": "+917977457870",
        "chat_name": "Ishaan",
        "thread_id": "thread-42",
        "user_id": "user-123",
        "user_id_alt": "alt-456",
    }


def test_ensure_session_synced_includes_routing_metadata(monkeypatch) -> None:
    class DummyBridge:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def request(self, payload: dict[str, object], *, timeout: float = _MODULE._SESSION_TIMEOUT_SECONDS) -> dict[str, object]:
            self.payloads.append(payload)
            return {"success": True}

    provider = AtlasMemoryProvider()
    provider._bridge = DummyBridge()
    provider._session_id = "agent:main:signal:dm:+917977457870"
    provider._memory_session_id = "9b678df0-b337-55b8-b2b0-9b6dcb57d8b6"
    provider._platform = "signal"
    provider._agent_namespace = "default"
    provider._started_at = "2026-04-06T15:00:00+00:00"
    provider._model = "gpt-5.4-mini"

    monkeypatch.setenv("HERMES_SESSION_KEY", "agent:main:signal:dm:+917977457870")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "signal")
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", "+917977457870")

    provider._ensure_session_synced()

    payload = provider._bridge.payloads[0]
    assert payload["operation"] == "live-session-sync"
    assert payload["updates"]["legacy_session_id"] == "agent:main:signal:dm:+917977457870"
    assert payload["updates"]["model_config"]["routing"]["session_key"] == "agent:main:signal:dm:+917977457870"
    assert payload["updates"]["model_config"]["routing"]["chat_id"] == "+917977457870"


def test_sync_worker_preserves_enqueued_session_context(monkeypatch) -> None:
    class DummyBridge:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def request(self, payload: dict[str, object], *, timeout: float = _MODULE._SESSION_TIMEOUT_SECONDS) -> dict[str, object]:
            self.payloads.append(payload)
            return {"success": True}

    provider = AtlasMemoryProvider()
    provider._bridge = DummyBridge()
    provider._platform = "signal"
    provider._agent_namespace = "default"
    provider._started_at = "2026-04-06T15:00:00+00:00"
    provider._model = "gpt-5.3-codex"
    provider._sync_queue = queue.Queue()

    session_a = "agent:main:signal:dm:+15550000001"
    session_b = "agent:main:signal:dm:+15550000002"

    monkeypatch.setenv("HERMES_SESSION_KEY", session_a)
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "signal")
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", "+15550000001")
    provider.sync_turn("user-a", "assistant-a", session_id=session_a)

    monkeypatch.setenv("HERMES_SESSION_KEY", session_b)
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "signal")
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", "+15550000002")
    provider.sync_turn("user-b", "assistant-b", session_id=session_b)

    provider._sync_queue.put(None)
    provider._sync_worker()

    append_calls = [
        payload
        for payload in provider._bridge.payloads
        if payload.get("operation") == "live-session-append"
    ]

    assert len(append_calls) == 2
    assert append_calls[0]["memory_session_id"] == _MODULE._normalize_memory_session_id(session_a)
    assert append_calls[1]["memory_session_id"] == _MODULE._normalize_memory_session_id(session_b)


def test_hermes_provider_copies_remain_identical() -> None:
    atlas_copy = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    hermes_copy = (HERMES_AGENT_DIR / "plugins" / "memory" / "atlas" / "__init__.py").read_text(encoding="utf-8")

    def _method_source(source: str, method_name: str) -> str:
        pattern = re.compile(
            rf"^    def {re.escape(method_name)}\(.*?(?=^    def |^def register\(|\Z)",
            re.DOTALL | re.MULTILINE,
        )
        match = pattern.search(source)
        assert match is not None, f"Could not find method {method_name}"
        return match.group(0)

    for method_name in ("sync_turn", "_ensure_session_synced", "_sync_worker"):
        assert _method_source(atlas_copy, method_name) == _method_source(hermes_copy, method_name)


def test_atlas_bridge_shutdown_closes_stdout_handle(tmp_path: Path) -> None:
    bridge = _MODULE._AtlasBridgeClient(hermes_home=str(tmp_path))

    class DummyPipe:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class DummyProcess:
        def __init__(self) -> None:
            self.stdin = DummyPipe()
            self.stdout = DummyPipe()
            self.terminated = False
            self.waited = False

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float | None = None) -> None:
            self.waited = True

    process = DummyProcess()
    bridge._process = process

    bridge._shutdown_locked()

    assert process.stdin.closed is True
    assert process.stdout.closed is True
    assert process.terminated is True
    assert process.waited is True
