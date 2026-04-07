from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
import re


DEFAULT_HERMES_HOME = Path.home() / ".hermes"
_ENV_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
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


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return None


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
    for target, source in _ENV_ALIAS_PAIRS:
        if not os.getenv(target) and os.getenv(source):
            os.environ[target] = os.environ[source]
    os.environ.setdefault("HERMES_HOME", str(Path(os.getenv("HERMES_HOME", str(DEFAULT_HERMES_HOME))).expanduser()))


def load_memory_env(hermes_home: str | Path | None = None) -> dict[str, str]:
    raw_root = Path(hermes_home or os.getenv("HERMES_HOME") or DEFAULT_HERMES_HOME).expanduser()
    env_path = raw_root if raw_root.name == ".env" else raw_root / ".env"
    root = env_path.parent
    os.environ["HERMES_HOME"] = str(root)
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


@dataclass(slots=True)
class MemoryConfig:
    supabase_url: str | None = field(
        default_factory=lambda: _first_env("MEMORY_SUPABASE_URL", "ATLAS_SUPABASE_URL", "SUPABASE_URL")
    )
    supabase_key: str | None = field(
        default_factory=lambda: _first_env("MEMORY_SUPABASE_KEY", "ATLAS_SUPABASE_KEY", "SUPABASE_SERVICE_KEY")
    )
    supabase_schema: str = field(default_factory=lambda: os.getenv("MEMORY_SUPABASE_SCHEMA", "memory"))
    embedding_model: str = field(
        default_factory=lambda: _first_env("MEMORY_OPENAI_EMBEDDING_MODEL", "ATLAS_OPENAI_EMBEDDING_MODEL")
        or "text-embedding-3-small"
    )
    embedding_dimensions: int = field(
        default_factory=lambda: int(_first_env("MEMORY_EMBEDDING_DIMENSIONS", "ATLAS_EMBEDDING_DIMENSIONS") or "512")
    )
    default_platform: str = field(
        default_factory=lambda: _first_env("MEMORY_DEFAULT_PLATFORM", "ATLAS_DEFAULT_PLATFORM") or "local"
    )
    openai_api_key: str | None = field(
        default_factory=lambda: _first_env("MEMORY_OPENAI_API_KEY", "ATLAS_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    openai_base_url: str = field(
        default_factory=lambda: _first_env("MEMORY_OPENAI_BASE_URL", "ATLAS_OPENAI_BASE_URL", "OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )

    @classmethod
    def from_env(cls, hermes_home: str | Path | None = None) -> "MemoryConfig":
        load_memory_env(hermes_home)
        return cls()

    def require_supabase(self) -> tuple[str, str]:
        if not self.supabase_url or not self.supabase_key:
            raise ValueError("MEMORY_SUPABASE_URL and MEMORY_SUPABASE_KEY must be set.")
        return self.supabase_url, self.supabase_key
