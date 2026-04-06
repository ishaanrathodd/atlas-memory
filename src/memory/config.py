from __future__ import annotations

import os
from dataclasses import dataclass, field


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return None


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
    def from_env(cls) -> "MemoryConfig":
        return cls()

    def require_supabase(self) -> tuple[str, str]:
        if not self.supabase_url or not self.supabase_key:
            raise ValueError("MEMORY_SUPABASE_URL and MEMORY_SUPABASE_KEY must be set.")
        return self.supabase_url, self.supabase_key
