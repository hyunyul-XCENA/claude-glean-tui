"""
Rate-limit usage monitoring: 5h rolling window + weekly aggregation.

Scans all JSONL session files under ``~/.claude/projects/`` to accumulate
token usage. Skips subagent files.  Cost estimation uses Opus 4 pricing
from ``common.py``.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import UsageStats, UsageStatsEstimated, UsageStatsLive

from .common import (
    CLAUDE_DIR,
    CLAUDE_TIER,
    TIER_LIMITS,
    TOKEN_PRICE_CACHE_CREATION,
    TOKEN_PRICE_CACHE_READ,
    TOKEN_PRICE_INPUT,
    TOKEN_PRICE_OUTPUT,
    decode_project_path,
    parse_timestamp_ms,
    ttl_cache,
)

# ── Time window thresholds (milliseconds) ─────────────────────────────────────
_5H_MS = 5 * 3600 * 1000
_7D_MS = 7 * 24 * 3600 * 1000






_STATUSLINE_FILE = Path(os.environ.get(
    "XDG_CONFIG_HOME", str(Path.home() / ".config"),
)) / "claude-glean-tui" / "statusline.json"


@ttl_cache(10)
def get_usage_stats() -> UsageStats:
    """Get usage stats. Priority: statusline file > JSONL estimated.

    The statusline file is written by Claude Code's statusLine command
    every few seconds — no API calls needed.
    """
    # 1. Statusline file (written by Claude Code, always fresh)
    sl = _read_statusline()
    if sl is not None:
        return sl

    # 2. JSONL estimated (fallback when Claude Code not running)
    return _aggregate_jsonl_usage()


def _read_statusline() -> Optional[UsageStatsLive]:
    """Read usage data from Claude Code's statusline dump file.

    Returns parsed data in the same format as API, or None if stale/missing.
    """
    try:
        if not _STATUSLINE_FILE.is_file():
            return None
        raw = _STATUSLINE_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        data = json.loads(raw)

        ts = data.get("timestamp", 0)
        rl = data.get("rate_limits", {})
        five_hour = rl.get("five_hour", {})
        seven_day = rl.get("seven_day", {})
        ctx = data.get("context_window", {})
        cost = data.get("cost", {})

        return {
            "source": "statusline",
            "window_5h": {
                "usage_pct": five_hour.get("used_percentage", 0.0),
                "resets_at": _epoch_to_iso(five_hour.get("resets_at", 0)),
            },
            "window_weekly": {
                "usage_pct": seven_day.get("used_percentage", 0.0),
                "resets_at": _epoch_to_iso(seven_day.get("resets_at", 0)),
            },
            "context_window": {
                "used_pct": ctx.get("used_percentage", 0),
                "remaining_pct": ctx.get("remaining_percentage", 0),
            },
            "cost": {
                "total_usd": cost.get("total_cost_usd", 0.0),
            },
            "model": data.get("model", {}).get("display_name", ""),
            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else "",
        }
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _epoch_to_iso(epoch: int) -> str:
    if not epoch:
        return ""
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except (OSError, ValueError):
        return ""


def _aggregate_jsonl_usage() -> UsageStatsEstimated:
    """Fallback: aggregate token usage from JSONL files (estimated)."""
    now_ms = int(time.time() * 1000)
    cutoff_5h = now_ms - _5H_MS
    cutoff_weekly = now_ms - _7D_MS

    # Tier info
    tier_name = CLAUDE_TIER if CLAUDE_TIER in TIER_LIMITS else "max5x"
    limits = TIER_LIMITS[tier_name]
    limit_5h = limits["limit_5h"]
    limit_weekly = limits["limit_weekly"]

    # 5h window accumulators
    w5_input = 0
    w5_output = 0
    w5_cache_read = 0
    w5_cache_create = 0
    w5_sessions: set = set()

    # Weekly window accumulators
    wk_input = 0
    wk_output = 0
    wk_cache_read = 0
    wk_cache_create = 0
    wk_sessions: set = set()

    # Per-session breakdown (5h window only)
    per_session_map: Dict[str, Dict[str, Any]] = {}

    projects_dir = CLAUDE_DIR / "projects"
    if projects_dir.is_dir():
        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            decoded_path = decode_project_path(proj_dir.name)
            project_name = Path(decoded_path).name or proj_dir.name

            for jsonl_file in proj_dir.glob("*.jsonl"):
                if "subagent" in jsonl_file.name:
                    continue
                # Skip files not modified within weekly window
                try:
                    if jsonl_file.stat().st_mtime < (time.time() - _7D_MS / 1000):
                        continue
                except OSError:
                    continue
                session_id = jsonl_file.stem

                try:
                    with open(jsonl_file, "r", encoding="utf-8", errors="replace") as f:
                        for raw_line in f:
                            try:
                                entry = json.loads(raw_line)
                            except Exception:
                                continue
                            if entry.get("type") != "assistant":
                                continue
                            ts = parse_timestamp_ms(entry.get("timestamp"))
                            if ts == 0:
                                continue

                            usage = _get_usage_dict(entry)
                            if usage is None:
                                continue

                            inp = usage.get("input_tokens", 0)
                            out = usage.get("output_tokens", 0)
                            cr = usage.get("cache_read_input_tokens", 0)
                            cc = usage.get("cache_creation_input_tokens", 0)

                            # Weekly window
                            if ts >= cutoff_weekly:
                                wk_input += inp
                                wk_output += out
                                wk_cache_read += cr
                                wk_cache_create += cc
                                wk_sessions.add(session_id)

                            # 5h window (subset of weekly)
                            if ts >= cutoff_5h:
                                w5_input += inp
                                w5_output += out
                                w5_cache_read += cr
                                w5_cache_create += cc
                                w5_sessions.add(session_id)

                                # Per-session accumulator
                                slot = per_session_map.setdefault(session_id, {
                                    "session_id": session_id,
                                    "project_name": project_name,
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                    "cache_read_tokens": 0,
                                    "total_tokens": 0,
                                    "message_count": 0,
                                    "last_activity": "",
                                })
                                slot["input_tokens"] += inp
                                slot["output_tokens"] += out
                                slot["cache_read_tokens"] += cr
                                slot["total_tokens"] += inp + out  # rate limit = input+output only
                                slot["message_count"] += 1
                                if not slot["last_activity"] or ts > _ts_to_num(slot["last_activity"]):
                                    slot["last_activity"] = _ms_to_iso(ts)

                except (FileNotFoundError, PermissionError, OSError):
                    continue

    # Compute totals — rate limits are based on input+output only (cache doesn't count)
    w5_total = w5_input + w5_output
    wk_total = wk_input + wk_output
    w5_remaining = max(0, limit_5h - w5_total)
    wk_remaining = max(0, limit_weekly - wk_total)
    w5_pct = round(w5_total / limit_5h * 100, 1) if limit_5h > 0 else 0.0
    wk_pct = round(wk_total / limit_weekly * 100, 1) if limit_weekly > 0 else 0.0

    # Per-session list sorted by total_tokens descending
    per_session: List[Dict[str, Any]] = sorted(
        per_session_map.values(),
        key=lambda x: x["total_tokens"],
        reverse=True,
    )

    # Cost estimation
    cost_5h = _estimate_cost(w5_input, w5_output, w5_cache_read, w5_cache_create)
    cost_weekly = _estimate_cost(wk_input, wk_output, wk_cache_read, wk_cache_create)

    return {
        "source": "estimated",
        "tier": {
            "name": tier_name,
            "limit_5h": limit_5h,
            "limit_weekly": limit_weekly,
        },
        "window_5h": {
            "input_tokens": w5_input,
            "output_tokens": w5_output,
            "cache_read_tokens": w5_cache_read,
            "cache_creation_tokens": w5_cache_create,
            "total_tokens": w5_total,
            "remaining_tokens": w5_remaining,
            "usage_pct": w5_pct,
            "session_count": len(w5_sessions),
        },
        "window_weekly": {
            "input_tokens": wk_input,
            "output_tokens": wk_output,
            "cache_read_tokens": wk_cache_read,
            "cache_creation_tokens": wk_cache_create,
            "total_tokens": wk_total,
            "remaining_tokens": wk_remaining,
            "usage_pct": wk_pct,
            "session_count": len(wk_sessions),
        },
        "per_session": per_session,
        "cost_estimate": {
            "window_5h_usd": round(cost_5h, 2),
            "window_weekly_usd": round(cost_weekly, 2),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Private helpers ───────────────────────────────────────────────────────────

def _get_usage_dict(entry: Dict[str, Any]) -> Any:
    """Extract the ``usage`` dict from a JSONL entry (top-level or inside message)."""
    usage = entry.get("usage")
    if isinstance(usage, dict):
        return usage
    msg = entry.get("message")
    if isinstance(msg, dict):
        usage = msg.get("usage")
        if isinstance(usage, dict):
            return usage
    return None


def _estimate_cost(
    inp: int, out: int, cache_read: int, cache_create: int,
) -> float:
    """Estimate USD cost based on per-1M-token pricing."""
    return (
        inp * TOKEN_PRICE_INPUT
        + out * TOKEN_PRICE_OUTPUT
        + cache_read * TOKEN_PRICE_CACHE_READ
        + cache_create * TOKEN_PRICE_CACHE_CREATION
    ) / 1_000_000


def _ms_to_iso(ms: int) -> str:
    """Convert millisecond timestamp to ISO 8601 string."""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (OSError, ValueError, OverflowError):
        return ""


def _ts_to_num(iso: str) -> float:
    """Best-effort parse of ISO timestamp back to a comparable number."""
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return 0.0
