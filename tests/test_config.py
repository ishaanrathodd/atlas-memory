from __future__ import annotations

from pathlib import Path

from memory.config import MemoryConfig, load_memory_env


def test_load_memory_env_reads_top_level_hermes_env(tmp_path: Path, monkeypatch) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    env_path = hermes_home / ".env"
    env_path.write_text(
        "\n".join(
            [
                "SUPABASE_SERVICE_KEY=top-level-service-key",
                "MEMORY_SUPABASE_URL=http://127.0.0.1:54321",
                "OPENAI_API_KEY=test-openai-key",
            ]
        ),
        encoding="utf-8",
    )

    for key in (
        "HERMES_HOME",
        "MEMORY_SUPABASE_URL",
        "MEMORY_SUPABASE_KEY",
        "SUPABASE_SERVICE_KEY",
        "OPENAI_API_KEY",
        "MEMORY_OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    loaded = load_memory_env(hermes_home)

    assert loaded["MEMORY_SUPABASE_URL"] == "http://127.0.0.1:54321"
    assert loaded["SUPABASE_SERVICE_KEY"] == "top-level-service-key"
    assert loaded["OPENAI_API_KEY"] == "test-openai-key"

    config = MemoryConfig.from_env(hermes_home)
    assert config.supabase_url == "http://127.0.0.1:54321"
    assert config.supabase_key == "top-level-service-key"
    assert config.openai_api_key == "test-openai-key"
