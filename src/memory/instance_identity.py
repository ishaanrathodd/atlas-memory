from __future__ import annotations

import os
import re
from pathlib import Path

_DEFAULT_HERMES_HOME = Path.home() / ".hermes"
_SAFE_NAMESPACE_RE = re.compile(r"[^a-z0-9._-]+")


def normalize_agent_namespace(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    cleaned = _SAFE_NAMESPACE_RE.sub("-", cleaned).strip("-.")
    return cleaned or "default"


def get_agent_namespace(*, hermes_home: str | Path | None = None) -> str:
    override = os.getenv("HERMES_AGENT_NAMESPACE")
    if override and override.strip():
        return normalize_agent_namespace(override)

    configured_home = Path(
        hermes_home or os.getenv("HERMES_HOME") or str(_DEFAULT_HERMES_HOME)
    ).expanduser()
    default_home = _DEFAULT_HERMES_HOME.expanduser()

    try:
        resolved_home = configured_home.resolve()
    except OSError:
        resolved_home = configured_home
    try:
        resolved_default = default_home.resolve()
    except OSError:
        resolved_default = default_home

    if resolved_home == resolved_default:
        return "default"

    profiles_root = resolved_default / "profiles"
    try:
        relative = resolved_home.relative_to(profiles_root)
    except ValueError:
        return "default"

    parts = relative.parts
    if len(parts) == 1 and parts[0]:
        return normalize_agent_namespace(parts[0])

    return "default"
