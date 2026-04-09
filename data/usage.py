"""
Rate-limit usage monitoring: 5h rolling window + weekly aggregation.

Scans all JSONL session files under ``~/.claude/projects/`` to accumulate
token usage. Skips subagent files.  Cost estimation uses Opus 4 pricing
from ``common.py``.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
)

# ── Time window thresholds (milliseconds) ─────────────────────────────────────
_5H_MS = 5 * 3600 * 1000
_7D_MS = 7 * 24 * 3600 * 1000


_last_api_result: Optional[Dict[str, Any]] = None
_api_fail_count: int = 0
_last_api_call: float = 0.0
_API_POLL_SEC: float = 60.0  # API call interval — don't hammer the server


def get_usage_stats() -> Dict[str, Any]:
    """Get usage stats — API when authenticated, JSONL when not.

    API is called at most once per 60 seconds.  Between calls, cached
    data is returned.
    """
    global _last_api_result, _api_fail_count, _last_api_call

    try:
        from .oauth import is_authenticated
        authenticated = is_authenticated()
    except ImportError:
        authenticated = False

    if authenticated:
        now = time.time()
        # Only call API every 60 seconds
        if now - _last_api_call >= _API_POLL_SEC:
            _last_api_call = now
            api_data = _fetch_api_usage()
            if api_data is not None:
                _last_api_result = api_data
                _api_fail_count = 0
                return api_data
            # API failed
            _api_fail_count += 1

        # Between calls or after failure — return cached
        if _last_api_result is not None:
            cached = dict(_last_api_result)
            if _api_fail_count > 0:
                cached["api_error"] = f"API unreachable (retry {_api_fail_count}), showing cached data"
            return cached

    # Not authenticated — JSONL fallback
    return _aggregate_jsonl_usage()


def _fetch_api_usage() -> Optional[Dict[str, Any]]:
    """Fetch real usage data from Anthropic OAuth API."""
    try:
        from .oauth import fetch_usage, is_authenticated
    except ImportError:
        return None

    if not is_authenticated():
        return None

    raw = fetch_usage()
    if raw is None:
        return None

    def _parse_bucket(data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"usage_pct": 0.0, "resets_at": ""}
        return {
            "usage_pct": data.get("utilization", 0.0),
            "resets_at": data.get("resets_at", ""),
        }

    five_hour = _parse_bucket(raw.get("five_hour"))
    seven_day = _parse_bucket(raw.get("seven_day"))

    extra = raw.get("extra_usage", {}) or {}

    return {
        "source": "api",
        "window_5h": {
            "usage_pct": five_hour["usage_pct"],
            "resets_at": five_hour["resets_at"],
        },
        "window_weekly": {
            "usage_pct": seven_day["usage_pct"],
            "resets_at": seven_day["resets_at"],
        },
        "seven_day_opus": _parse_bucket(raw.get("seven_day_opus")),
        "seven_day_sonnet": _parse_bucket(raw.get("seven_day_sonnet")),
        "extra_usage": {
            "is_enabled": extra.get("is_enabled", False),
            "used_credits_usd": (extra.get("used_credits", 0) or 0) / 100.0,
            "monthly_limit_usd": (extra.get("monthly_limit", 0) or 0) / 100.0,
            "utilization": extra.get("utilization", 0.0),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _aggregate_jsonl_usage() -> Dict[str, Any]:
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
