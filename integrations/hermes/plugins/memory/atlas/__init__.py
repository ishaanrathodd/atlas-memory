from __future__ import annotations

import json
import logging
import os
import queue
import re
import select
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

_MEMORY_LIVE_SESSION_NAMESPACE = uuid5(NAMESPACE_URL, "memory://hermes-live-session")
_SESSION_TIMEOUT_SECONDS = 30.0
_PING_TIMEOUT_SECONDS = 10.0
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ENV_ALIAS_PAIRS: tuple[tuple[str, str], ...] = (
    ("MEMORY_SUPABASE_URL", "ATLAS_SUPABASE_URL"),
    ("MEMORY_SUPABASE_URL", "SUPABASE_URL"),
    ("MEMORY_SUPABASE_KEY", "ATLAS_SUPABASE_KEY"),
    ("MEMORY_SUPABASE_KEY", "SUPABASE_SERVICE_KEY"),
    ("MEMORY_OPENAI_API_KEY", "ATLAS_OPENAI_API_KEY"),
    ("MEMORY_OPENAI_API_KEY", "OPENAI_API_KEY"),
    ("MEMORY_OPENAI_BASE_URL", "ATLAS_OPENAI_BASE_URL"),
    ("MEMORY_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    ("MEMORY_OPENAI_EMBEDDING_MODEL", "ATLAS_OPENAI_EMBEDDING_MODEL"),
    ("MEMORY_EMBEDDING_DIMENSIONS", "ATLAS_EMBEDDING_DIMENSIONS"),
    ("MEMORY_DEFAULT_PLATFORM", "ATLAS_DEFAULT_PLATFORM"),
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_memory_session_id(session_id: str | None) -> str | None:
    if session_id is None:
        return None
    value = str(session_id).strip()
    if not value:
        return None
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return str(uuid5(_MEMORY_LIVE_SESSION_NAMESPACE, value))


def _normalize_agent_namespace(agent_identity: str | None) -> str:
    value = str(agent_identity or "").strip().lower()
    if value in {"", "primary"}:
        return "default"
    return value


def _hermes_home_path(hermes_home: str | None = None) -> Path:
    return Path(hermes_home or os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def _plugin_root() -> Path:
    return Path(__file__).resolve().parent


def _atlas_root() -> Path:
    override = os.environ.get("ATLAS_ROOT")
    if override:
        candidate = Path(override).expanduser().resolve()
        if (candidate / "src" / "memory" / "bridge_server.py").exists():
            return candidate

    plugin_root = _plugin_root()
    hermes_home = _hermes_home_path()
    candidates: list[Path] = []

    # atlas/integrations/hermes/plugins/memory/atlas/__init__.py -> atlas/
    if len(plugin_root.parents) > 5:
        candidates.append(plugin_root.parents[5])
    # hermes-agent/plugins/memory/atlas/__init__.py -> ~/.hermes/atlas
    if len(plugin_root.parents) > 4:
        candidates.append(plugin_root.parents[4] / "atlas")

    candidates.extend(
        [
            hermes_home / "atlas",
            Path.cwd() / "atlas",
        ]
    )

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "src" / "memory" / "bridge_server.py").exists():
            return resolved

    # Fallback keeps diagnostics deterministic for status checks and logs.
    return (hermes_home / "atlas").expanduser().resolve()


def _atlas_python() -> Path:
    atlas_root = _atlas_root()
    for candidate in (
        atlas_root / ".venv" / "bin" / "python",
        atlas_root / "venv" / "bin" / "python",
    ):
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def _atlas_config_path(hermes_home: str | None = None) -> Path:
    return _hermes_home_path(hermes_home) / "atlas.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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


def _read_env_file(hermes_home: str | None = None) -> dict[str, str]:
    env_path = _hermes_home_path(hermes_home) / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        context = {**os.environ, **values}
        values[normalized_key] = _resolve_env_value(value, context)
    return values


def _build_runtime_env(hermes_home: str | None = None) -> dict[str, str]:
    root = _hermes_home_path(hermes_home)
    config = _read_json(_atlas_config_path(root))
    file_env = _read_env_file(root)
    env = dict(os.environ)

    atlas_src = str(_atlas_root() / "src")
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = atlas_src if not existing_pythonpath else atlas_src + os.pathsep + existing_pythonpath
    env["HERMES_HOME"] = str(root)

    for key in (
        "MEMORY_SUPABASE_URL",
        "MEMORY_SUPABASE_SCHEMA",
        "MEMORY_OPENAI_BASE_URL",
        "MEMORY_OPENAI_EMBEDDING_MODEL",
        "MEMORY_EMBEDDING_DIMENSIONS",
        "MEMORY_SUPABASE_KEY",
        "MEMORY_OPENAI_API_KEY",
        "OPENAI_API_KEY",
    ):
        if key in file_env and key not in env:
            env[key] = file_env[key]

    for target, source in _ENV_ALIAS_PAIRS:
        if target not in env and source in file_env:
            env[target] = file_env[source]
        if not env.get(target) and env.get(source):
            env[target] = env[source]

    config_to_env = {
        "supabase_url": "MEMORY_SUPABASE_URL",
        "supabase_schema": "MEMORY_SUPABASE_SCHEMA",
        "openai_base_url": "MEMORY_OPENAI_BASE_URL",
        "embedding_model": "MEMORY_OPENAI_EMBEDDING_MODEL",
        "embedding_dimensions": "MEMORY_EMBEDDING_DIMENSIONS",
    }
    for config_key, env_key in config_to_env.items():
        if config.get(config_key):
            env.setdefault(env_key, str(config[config_key]))

    if not env.get("MEMORY_SUPABASE_KEY") and env.get("SUPABASE_SERVICE_KEY"):
        env["MEMORY_SUPABASE_KEY"] = env["SUPABASE_SERVICE_KEY"]
    if not env.get("MEMORY_SUPABASE_URL") and env.get("SUPABASE_URL"):
        env["MEMORY_SUPABASE_URL"] = env["SUPABASE_URL"]
    if not env.get("MEMORY_OPENAI_API_KEY") and env.get("OPENAI_API_KEY"):
        env["MEMORY_OPENAI_API_KEY"] = env["OPENAI_API_KEY"]

    return env


def _env_text(*keys: str) -> str | None:
    for key in keys:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return None


class _AtlasBridgeClient:
    def __init__(self, *, hermes_home: str) -> None:
        self._hermes_home = _hermes_home_path(hermes_home)
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._stderr_handle: Any = None

    def _ensure_process_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        log_dir = self._hermes_home / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if self._stderr_handle is None or getattr(self._stderr_handle, "closed", False):
            self._stderr_handle = open(log_dir / "atlas_memory_provider.log", "a", encoding="utf-8")

        self._process = subprocess.Popen(
            [str(_atlas_python()), "-m", "memory.bridge_server"],
            cwd=str(_atlas_root()),
            env=_build_runtime_env(str(self._hermes_home)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_handle,
            text=True,
            bufsize=1,
        )
        self._request_locked({"operation": "ping"}, timeout=_PING_TIMEOUT_SECONDS)

    def _shutdown_locked(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()
            except Exception:
                pass
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if self._stderr_handle is not None and not getattr(self._stderr_handle, "closed", False):
            self._stderr_handle.close()
            self._stderr_handle = None

    def _request_locked(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("Atlas bridge server is not available.")

        self._process.stdin.write(json.dumps(payload, ensure_ascii=False))
        self._process.stdin.write("\n")
        self._process.stdin.flush()

        ready, _, _ = select.select([self._process.stdout], [], [], timeout)
        if not ready:
            raise TimeoutError(f"Atlas bridge request timed out after {timeout:.1f}s")

        raw = self._process.stdout.readline()
        if not raw:
            raise RuntimeError("Atlas bridge server closed unexpectedly.")

        response = json.loads(raw)
        if not isinstance(response, dict):
            raise RuntimeError("Atlas bridge returned a malformed response.")
        if not response.get("success", False):
            raise RuntimeError(str(response.get("error") or "Atlas bridge request failed."))
        result = response.get("result", {})
        return result if isinstance(result, dict) else {"value": result}

    def request(self, payload: dict[str, Any], *, timeout: float = _SESSION_TIMEOUT_SECONDS) -> dict[str, Any]:
        with self._lock:
            self._ensure_process_locked()
            try:
                return self._request_locked(payload, timeout=timeout)
            except Exception:
                self._shutdown_locked()
                raise

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown_locked()


class AtlasMemoryProvider(MemoryProvider):
    """Atlas external memory provider for Hermes."""

    def __init__(self) -> None:
        self._bridge: _AtlasBridgeClient | None = None
        self._session_id = ""
        self._memory_session_id: str | None = None
        self._platform = "local"
        self._agent_namespace = "default"
        self._started_at = _utcnow_iso()
        self._model: str | None = None
        self._disabled = False
        self._session_synced = False
        self._sync_queue: queue.Queue[tuple[str, str] | None] | None = None
        self._sync_thread: threading.Thread | None = None

    @property
    def name(self) -> str:
        return "atlas"

    def system_prompt_block(self) -> str:
        return (
            "# Atlas Memory\n"
            "Atlas injects retrieved cross-session memory directly alongside the current user turn.\n"
            "When an Atlas memory block is present, treat it as the primary retrieved evidence for\n"
            "continuity, prior conversations, past work, active state, and long-term user context.\n"
            "Do not say memory is empty, unavailable, or unprocessed if the Atlas block already contains\n"
            "relevant evidence. Answer from that evidence first, and only express uncertainty when the\n"
            "Atlas block is actually empty or clearly inconclusive.\n"
        )

    def is_available(self) -> bool:
        atlas_root = _atlas_root()
        if not (atlas_root / "src" / "memory" / "bridge_server.py").exists():
            return False
        config = _read_json(_atlas_config_path())
        env_values = {**_read_env_file(), **os.environ}
        supabase_url = (
            config.get("supabase_url")
            or env_values.get("MEMORY_SUPABASE_URL")
            or env_values.get("SUPABASE_URL")
        )
        supabase_key = env_values.get("MEMORY_SUPABASE_KEY") or env_values.get("SUPABASE_SERVICE_KEY")
        return bool(supabase_url and supabase_key)

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "supabase_url",
                "description": "Supabase project URL",
                "required": True,
                "default": "https://YOUR_PROJECT.supabase.co",
            },
            {
                "key": "supabase_key",
                "description": "Supabase service key",
                "secret": True,
                "required": True,
                "env_var": "MEMORY_SUPABASE_KEY",
            },
            {
                "key": "openai_api_key",
                "description": "Embedding model API key",
                "secret": True,
                "required": True,
                "env_var": "MEMORY_OPENAI_API_KEY",
            },
            {
                "key": "llm_model",
                "description": "LLM model choice",
                "default": "gpt-5.3-codex",
            },
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        config_path = _atlas_config_path(hermes_home)
        existing = _read_json(config_path)
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def initialize(self, session_id: str, **kwargs) -> None:
        agent_context = str(kwargs.get("agent_context") or "primary").strip().lower()
        if agent_context not in {"", "primary"}:
            logger.debug("Atlas memory skipped for agent_context=%s", agent_context)
            self._disabled = True
            return

        hermes_home = str(kwargs.get("hermes_home") or _hermes_home_path())
        self._session_id = str(session_id or "").strip()
        self._memory_session_id = _normalize_memory_session_id(self._session_id)
        self._platform = str(kwargs.get("platform") or "local")
        self._agent_namespace = _normalize_agent_namespace(kwargs.get("agent_identity"))
        self._started_at = _utcnow_iso()
        self._model = kwargs.get("model")
        self._bridge = _AtlasBridgeClient(hermes_home=hermes_home)
        self._sync_queue = queue.Queue()
        self._sync_thread = threading.Thread(target=self._sync_worker, name="atlas-memory-sync", daemon=True)
        self._sync_thread.start()

        try:
            self._ensure_session_synced()
        except Exception as exc:
            logger.warning("Atlas memory initialize failed to prewarm session: %s", exc)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return []

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._disabled or not query.strip() or self._bridge is None:
            return ""
        active_session_id = _normalize_memory_session_id(session_id or self._session_id)
        try:
            result = self._bridge.request(
                {
                    "operation": "enrich",
                    "user_message": query,
                    "platform": self._platform,
                    "active_session_id": active_session_id,
                    "agent_namespace": self._agent_namespace,
                }
            )
        except Exception as exc:
            logger.warning("Atlas memory prefetch failed: %s", exc)
            return ""
        return self._format_prefetch_context(query, str(result.get("context") or "").strip())

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if self._disabled or self._sync_queue is None:
            return
        effective_session_id = str(session_id or self._session_id).strip()
        if effective_session_id and effective_session_id != self._session_id:
            self._session_id = effective_session_id
            self._memory_session_id = _normalize_memory_session_id(effective_session_id)
            self._session_synced = False
        self._sync_queue.put((user_content, assistant_content))

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        if self._disabled or self._bridge is None or not self._memory_session_id:
            return
        try:
            if self._sync_queue is not None:
                self._sync_queue.join()
            self._ensure_session_synced()
            self._bridge.request(
                {
                    "operation": "live-session-end",
                    "memory_session_id": self._memory_session_id,
                    "hermes_session_id": self._session_id,
                    "platform": self._platform,
                    "started_at": self._started_at,
                    "model": self._model,
                    "agent_namespace": self._agent_namespace,
                    "end_reason": "session_end",
                }
            )
        except Exception as exc:
            logger.warning("Atlas memory session end failed: %s", exc)

    def shutdown(self) -> None:
        if self._sync_queue is not None:
            self._sync_queue.put(None)
        if self._sync_thread is not None and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=2.0)
        self._sync_thread = None
        self._sync_queue = None
        if self._bridge is not None:
            self._bridge.shutdown()
            self._bridge = None

    def _ensure_session_synced(self) -> None:
        if self._session_synced or self._bridge is None or not self._memory_session_id:
            return
        updates: dict[str, Any] = {"legacy_session_id": self._session_id}
        routing = self._current_routing_metadata()
        if routing:
            updates["model_config"] = {"routing": routing}
        self._bridge.request(
            {
                "operation": "live-session-sync",
                "memory_session_id": self._memory_session_id,
                "hermes_session_id": self._session_id,
                "platform": self._platform,
                "started_at": self._started_at,
                "model": self._model,
                "agent_namespace": self._agent_namespace,
                "updates": updates,
            }
        )
        self._session_synced = True

    def _current_routing_metadata(self) -> dict[str, str] | None:
        session_key = _env_text("HERMES_SESSION_KEY")
        if not session_key:
            return None

        routing: dict[str, str] = {
            "session_key": session_key,
            "bound_at": self._started_at,
        }

        platform = _env_text("HERMES_SESSION_PLATFORM") or self._platform
        if platform:
            routing["platform"] = platform

        chat_id = _env_text("HERMES_SESSION_CHAT_ID")
        if chat_id:
            routing["chat_id"] = chat_id

        chat_name = _env_text("HERMES_SESSION_CHAT_NAME")
        if chat_name:
            routing["chat_name"] = chat_name

        thread_id = _env_text("HERMES_SESSION_THREAD_ID")
        if thread_id:
            routing["thread_id"] = thread_id

        user_id = _env_text("HERMES_SESSION_USER_ID")
        if user_id:
            routing["user_id"] = user_id

        user_id_alt = _env_text("HERMES_SESSION_USER_ID_ALT")
        if user_id_alt:
            routing["user_id_alt"] = user_id_alt

        return routing

    def _sync_worker(self) -> None:
        while self._sync_queue is not None:
            item = self._sync_queue.get()
            try:
                if item is None:
                    return
                user_content, assistant_content = item
                if self._bridge is None or not self._memory_session_id:
                    continue
                self._ensure_session_synced()
                self._bridge.request(
                    {
                        "operation": "live-session-append",
                        "memory_session_id": self._memory_session_id,
                        "hermes_session_id": self._session_id,
                        "platform": self._platform,
                        "started_at": self._started_at,
                        "model": self._model,
                        "agent_namespace": self._agent_namespace,
                        "messages": [
                            {"role": "user", "content": user_content},
                            {"role": "assistant", "content": assistant_content},
                        ],
                    }
                )
            except Exception as exc:
                logger.warning("Atlas memory sync failed: %s", exc)
            finally:
                if self._sync_queue is not None:
                    self._sync_queue.task_done()

    def _format_prefetch_context(self, query: str, context: str) -> str:
        if not context:
            return ""
        return (
            "## Atlas Memory Recall\n"
            "Use the retrieved memory below as primary evidence for this user message.\n"
            f"Current user message: {query.strip()}\n"
            "If this block already answers the question, answer directly from it and do not claim you\n"
            "cannot remember.\n\n"
            f"{context}"
        )


def register(ctx) -> None:
    ctx.register_memory_provider(AtlasMemoryProvider())
