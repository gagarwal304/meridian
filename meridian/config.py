"""
User-configurable thresholds stored in ~/.meridian/config.json.

Defaults reflect the Phase 1 spec values. Users can override any key.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path.home() / ".meridian" / "config.json"

DEFAULTS: dict[str, Any] = {
    # How many times the same Bash command must repeat before flagging a spiral
    "retry_spiral.min_repeats": 3,

    # Agent call duration (ms) above which a spawn is flagged
    "agent_spawn.threshold_ms": 30_000,

    # cache_read_tokens must grow by this ratio to flag context bloat
    "context_bloat.growth_threshold": 2.0,

    # cache_read_tokens above this absolute level always flags bloat
    "context_bloat.absolute_threshold": 80_000,

    # Minimum absolute token growth required to flag (suppresses noise when
    # a session starts already above the absolute threshold)
    "context_bloat.min_growth": 10_000,

    # WebFetch calls in a row to flag as a chain
    "webfetch_chain.min_length": 3,

    # How many ToolSearch calls per session before flagging overhead
    "tool_search.min_calls": 2,

    # Default window for `meridian analyse`
    "analyse.default_days": 30,

    # Default window for `meridian report`
    "report.default_days": 7,
}

_DESCRIPTIONS: dict[str, str] = {
    "retry_spiral.min_repeats":         "Bash command repeat count to flag a retry spiral",
    "agent_spawn.threshold_ms":         "Agent call duration (ms) to flag as expensive",
    "context_bloat.growth_threshold":   "cache_read_tokens growth ratio to flag bloat",
    "context_bloat.absolute_threshold": "cache_read_tokens level that always flags bloat",
    "context_bloat.min_growth":         "Minimum token growth required to report bloat",
    "webfetch_chain.min_length":        "Consecutive WebFetch calls to flag as a chain",
    "tool_search.min_calls":            "ToolSearch calls per session to flag overhead",
    "analyse.default_days":             "Default days window for `meridian analyse`",
    "report.default_days":              "Default days window for `meridian report`",
}


def _load_raw() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load() -> dict[str, Any]:
    """Return merged config: defaults overridden by user values."""
    return {**DEFAULTS, **_load_raw()}


def get(key: str) -> Any:
    return load().get(key, DEFAULTS.get(key))


def set_value(key: str, raw_value: str) -> Any:
    """Parse raw_value to the same type as the default, persist, and return parsed value."""
    if key not in DEFAULTS:
        raise KeyError(f"Unknown config key: {key!r}")

    default = DEFAULTS[key]
    try:
        if isinstance(default, bool):
            value = raw_value.lower() in ("1", "true", "yes")
        elif isinstance(default, int):
            value = int(raw_value)
        elif isinstance(default, float):
            value = float(raw_value)
        else:
            value = raw_value
    except ValueError:
        raise ValueError(f"Cannot convert {raw_value!r} to {type(default).__name__}")

    user = _load_raw()
    user[key] = value
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(user, indent=2), encoding="utf-8")
    return value


def reset(key: str | None = None) -> None:
    """Reset one key or all keys to defaults."""
    if key is None:
        if _CONFIG_PATH.exists():
            _CONFIG_PATH.unlink()
        return
    user = _load_raw()
    user.pop(key, None)
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(user, indent=2), encoding="utf-8")


def config_path() -> Path:
    return _CONFIG_PATH


def descriptions() -> dict[str, str]:
    return _DESCRIPTIONS
