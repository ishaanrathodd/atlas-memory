from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1] / "integrations" / "hermes" / "plugins" / "memory" / "atlas"
HERMES_AGENT_DIR = Path(__file__).resolve().parents[2] / "hermes-agent"

if str(HERMES_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(HERMES_AGENT_DIR))

_SPEC = importlib.util.spec_from_file_location("atlas_test_plugin", PLUGIN_DIR / "__init__.py")
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
AtlasMemoryProvider = _MODULE.AtlasMemoryProvider


def test_atlas_provider_config_schema_contains_setup_fields() -> None:
    provider = AtlasMemoryProvider()

    schema = provider.get_config_schema()
    keys = {field["key"] for field in schema}

    assert "supabase_url" in keys
    assert "supabase_key" in keys
    assert "supabase_schema" in keys


def test_atlas_provider_save_config_writes_profile_scoped_json(tmp_path: Path) -> None:
    provider = AtlasMemoryProvider()

    provider.save_config(
        {
            "supabase_url": "https://example.supabase.co",
            "supabase_schema": "memory",
            "embedding_model": "text-embedding-3-small",
        },
        str(tmp_path),
    )

    payload = json.loads((tmp_path / "atlas.json").read_text(encoding="utf-8"))
    assert payload["supabase_url"] == "https://example.supabase.co"
    assert payload["supabase_schema"] == "memory"
