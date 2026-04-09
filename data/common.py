"""
Shared utilities and constants for claude-glean-tui data layer.

Provides: file I/O helpers, frontmatter parsing, token formatting,
project path decoding, and all configuration constants.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Paths ──────────────────────────────────────────────────────────────────────
CLAUDE_DIR: Path = Path.home() / ".claude"
CLAUDE_JSON: Path = Path.home() / ".claude.json"

# ── Subscription tier limits ───────────────────────────────────────────────────
# Set via CLAUDE_TIER env var: "pro" (default), "max5x", "max20x"
# Limits are approximate output-token equivalents for the rolling windows.
TIER_LIMITS: Dict[str, Dict[str, int]] = {
    "pro":    {"limit_5h": 800_000,    "limit_weekly": 4_000_000},
    "max5x":  {"limit_5h": 4_000_000,  "limit_weekly": 20_000_000},
    "max20x": {"limit_5h": 16_000_000, "limit_weekly": 80_000_000},
}
CLAUDE_TIER: str = os.environ.get("CLAUDE_TIER", "max5x").lower()

# ── Token pricing (USD per 1M tokens) -- Opus 4 defaults ──────────────────────
TOKEN_PRICE_INPUT: float = 15.0
TOKEN_PRICE_OUTPUT: float = 75.0
TOKEN_PRICE_CACHE_READ: float = 1.5
TOKEN_PRICE_CACHE_CREATION: float = 18.75

# ── Refresh ────────────────────────────────────────────────────────────────────
REFRESH_INTERVAL_SEC: int = 10


# ── File I/O helpers ──────────────────────────────────────────────────────────

def read_json(path: Path) -> Optional[Any]:
    """Safely read a JSON file. Returns None on any failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return None


def read_text(path: Path, max_chars: int = 0) -> Optional[str]:
    """Safely read a text file. Truncates if *max_chars* > 0."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if max_chars > 0 and len(content) > max_chars:
            return content[:max_chars]
        return content
    except (FileNotFoundError, PermissionError, UnicodeDecodeError, OSError):
        return None


# ── Frontmatter ───────────────────────────────────────────────────────────────

def parse_frontmatter(text: str) -> Dict[str, Any]:
    """Minimal YAML frontmatter parser (``---`` block).

    Supports ``key: value`` and ``key: [a, b, c]`` inline lists.
    Keys are lowercased; string values are unquoted.
    """
    result: Dict[str, Any] = {}
    if not text.startswith("---"):
        return result
    end = text.find("---", 3)
    if end == -1:
        return result
    block = text[3:end].strip()
    for line in block.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip().strip('"').strip("'")
        # Inline list: [a, b, c]
        if val.startswith("[") and val.endswith("]"):
            val = [
                v.strip().strip('"').strip("'")
                for v in val[1:-1].split(",")
                if v.strip()
            ]
        result[key] = val
    return result


# ── TTL cache ────────────────────────────────────────────────────────────────

def ttl_cache(seconds: float):
    """Simple TTL cache decorator. Caches the return value for *seconds*.

    Only works with no-arg functions (all our get_*() functions).
    Thread-unsafe but fine for single-threaded curses TUI.
    """
    def decorator(fn):
        _cache: Dict[str, Any] = {"value": None, "expires": 0.0}
        def wrapper():
            now = time.time()
            if _cache["value"] is not None and now < _cache["expires"]:
                return _cache["value"]
            result = fn()
            _cache["value"] = result
            _cache["expires"] = now + seconds
            return result
        wrapper.cache_clear = lambda: _cache.update(value=None, expires=0.0)
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


# ── Formatting ────────────────────────────────────────────────────────────────

def format_tokens(n: int) -> str:
    """Human-readable token count: ``318029`` -> ``"318.0k"``, ``1200000`` -> ``"1.2M"``."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


# ── Project path decoding ─────────────────────────────────────────────────────

def decode_project_path(folder_name: str) -> str:
    """Convert a project folder name to its actual filesystem path.

    Claude Code replaces ``/`` with ``-`` when creating folder names
    (e.g. ``-home-user-repo`` -> ``/home/user/repo``).
    We greedily reconstruct the real path by testing whether each
    candidate segment exists on disk.
    """
    parts = folder_name.lstrip("-").split("-")
    if not parts:
        return "/" + folder_name.lstrip("-")

    best_segments: List[str] = []
    i = 0
    while i < len(parts):
        found = False
        for j in range(len(parts), i, -1):
            sub = parts[i:j]
            for sep in ("-", "_"):
                candidate = sep.join(sub)
                test_path = "/" + "/".join(best_segments + [candidate])
                if os.path.exists(test_path):
                    best_segments.append(candidate)
                    i = j
                    found = True
                    break
            if found:
                break
        if not found:
            best_segments.append(parts[i])
            i += 1

    return "/" + "/".join(best_segments)


# ── Timestamp helpers ────────────────────────────────────────────────────────

def parse_timestamp_ms(ts: Any) -> int:
    """Convert a JSONL timestamp to milliseconds since epoch.

    Handles both legacy numeric timestamps (int/float, already in ms)
    and newer ISO 8601 strings (``"2026-04-09T13:04:55.929Z"``).
    Returns 0 on failure.
    """
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str) and ts:
        try:
            # Try ISO 8601 parsing (Python 3.11+ handles "Z" natively,
            # but for 3.9/3.10 we need to replace "Z" with "+00:00")
            cleaned = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return int(dt.timestamp() * 1000)
        except (ValueError, OSError):
            pass
    return 0
