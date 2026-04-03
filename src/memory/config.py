from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(slots=True)
class MemoryConfig:
    supabase_url: str | None = field(default_factory=lambda: os.getenv("MEMORY_SUPABASE_URL"))
    supabase_key: str | None = field(default_factory=lambda: os.getenv("MEMORY_SUPABASE_KEY"))
    supabase_schema: str = field(default_factory=lambda: os.getenv("MEMORY_SUPABASE_SCHEMA", "memory"))
    embedding_model: str = field(default_factory=lambda: os.getenv("MEMORY_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"))
    embedding_dimensions: int = field(default_factory=lambda: int(os.getenv("MEMORY_EMBEDDING_DIMENSIONS", "512")))
    default_platform: str = field(default_factory=lambda: os.getenv("MEMORY_DEFAULT_PLATFORM", "local"))
    openai_api_key: str | None = field(
        default_factory=lambda: os.getenv("MEMORY_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    )
    openai_base_url: str = field(
        default_factory=lambda: os.getenv("MEMORY_OPENAI_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )

    @classmethod
    def from_env(cls) -> "MemoryConfig":
        return cls()

    def require_supabase(self) -> tuple[str, str]:
        if not self.supabase_url or not self.supabase_key:
            raise ValueError("MEMORY_SUPABASE_URL and MEMORY_SUPABASE_KEY must be set.")
        return self.supabase_url, self.supabase_key
