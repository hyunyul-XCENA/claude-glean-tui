"""Usage detail screen — 5h/weekly breakdown with API or estimated data.

Screen 2: when authenticated via OAuth, shows exact utilization from
Anthropic API (same as /usage).  Otherwise falls back to JSONL
token aggregation with per-session breakdown.
"""
from __future__ import annotations

import curses
import time
from typing import Any, Dict, List

from data import get_usage_stats, is_authenticated
from data.common import CLAUDE_TIER, REFRESH_INTERVAL_SEC, format_tokens

from .base import BaseScreen, COLOR_GREEN, COLOR_CYAN, COLOR_YELLOW


class UsageScreen(BaseScreen):
    """Screen 2: detailed usage breakdown."""

    def __init__(self, stdscr: curses.window) -> None:
        super().__init__(stdscr)
        self.usage: Dict[str, Any] = {}

    def refresh_data(self) -> None:
        try:
            self.usage = get_usage_stats()
        except Exception as e:
            self.usage = {"source": "error", "api_error": str(e)}

    def render(self) -> None:
        if self.check_auto_refresh(REFRESH_INTERVAL_SEC):
            self.refresh_data()

        start_y, height, width = self.content_area()
        y = start_y

        source = self.usage.get("source", "estimated")
        if source == "api":
            self._render_api(y, width)
        else:
            self._render_estimated(y, width)

    # ── API mode (exact /usage data) ─────────────────────────────────

    def _render_api(self, y: int, width: int) -> None:
        y = self.draw_section(y, "API Usage (live from Anthropic)", width - 2)

        # 5-hour
        w5 = self.usage.get("window_5h", {})
        pct_5h = w5.get("usage_pct", 0.0)
        resets = w5.get("resets_at", "")
        local_time = self.format_reset_time(resets)
        reset_str = f"  resets {local_time}" if local_time else ""

        self.safe_addstr(y, 3, "5h Window:  ")
        self.draw_bar(y, 15, pct_5h, 25)
        self.safe_addstr(y, 45, f"used{reset_str}")
        y += 1

        # Weekly
        wk = self.usage.get("window_weekly", {})
        pct_wk = wk.get("usage_pct", 0.0)
        resets_wk = wk.get("resets_at", "")
        local_wk = self.format_reset_time(resets_wk)
        reset_wk_str = f"  resets {local_wk}" if local_wk else ""

        self.safe_addstr(y, 3, "Weekly:     ")
        self.draw_bar(y, 15, pct_wk, 25)
        self.safe_addstr(y, 45, f"used{reset_wk_str}")
        y += 2

        # Per-model breakdown
        for key, label in [("seven_day_opus", "Opus (7d)"), ("seven_day_sonnet", "Sonnet (7d)")]:
            bucket = self.usage.get(key, {})
            pct = bucket.get("usage_pct", 0.0)
            if pct > 0:
                self.safe_addstr(y, 3, f"{label:14s}")
                self.draw_bar(y, 17, pct, 20)
                self.safe_addstr(y, 42, "used")
                y += 1

        # Extra usage
        y += 1
        extra = self.usage.get("extra_usage", {})
        if extra.get("is_enabled"):
            y = self.draw_section(y, "Extra Usage", width - 2)
            used = extra.get("used_credits_usd", 0)
            limit = extra.get("monthly_limit_usd", 0)
            util = extra.get("utilization", 0.0)

            self.safe_addstr(y, 3, "Credits:    ")
            self.draw_bar(y, 15, util, 25)
            self.safe_addstr(y, 45, f"${used:.2f} / ${limit:.2f}")
            y += 1
        else:
            self.safe_addstr(y, 3, "Extra usage: disabled", curses.A_DIM)
            y += 1

        # Error or refresh info
        y += 1
        api_error = self.usage.get("api_error", "")
        if api_error:
            self.safe_addstr(y, 3, f"⚠ {api_error}",
                             curses.color_pair(COLOR_YELLOW))
        else:
            ts = self.usage.get("timestamp", "")
            self.safe_addstr(y, 3, f"Source: Anthropic API  |  {ts[:19]}", curses.A_DIM)

    # ── Estimated mode (JSONL fallback) ──────────────────────────────

    def _render_estimated(self, y: int, width: int) -> None:
        tier_label = CLAUDE_TIER.replace("max", "Max ")
        y = self.draw_section(y, f"API Quota ({tier_label}) [estimated from JSONL]", width - 2)

        # 5h window with per-session table
        y = self._render_window(y, width, "5h")
        y += 1

        # Weekly aggregate
        y = self._render_window(y, width, "weekly")
        y += 1

        # Cost
        y = self.draw_section(y, "Estimated Cost (Opus pricing)", width - 2)
        cost = self.usage.get("cost_estimate", {})
        self.safe_addstr(y, 5, f"5h window:  ${cost.get('window_5h_usd', 0):.2f}")
        y += 1
        self.safe_addstr(y, 5, f"This week:  ${cost.get('window_weekly_usd', 0):.2f}")
        y += 2

        self.safe_addstr(y, 3, "Press 'a' to sign in for exact usage data (same as /usage)", curses.A_DIM)

    def _render_window(self, y: int, width: int, window: str) -> int:
        if window == "5h":
            win = self.usage.get("window_5h", {})
            label = "5-Hour Window"
        else:
            win = self.usage.get("window_weekly", {})
            label = "Weekly"

        pct = win.get("usage_pct", 0.0)
        remaining = win.get("remaining_tokens", 0)

        self.safe_addstr(y, 3, f"{label}: ")
        self.draw_bar(y, 3 + len(label) + 2, pct, 20)
        offset = 3 + len(label) + 2 + 20 + 6
        self.safe_addstr(y, offset, f"  {format_tokens(remaining)} remaining")
        y += 1

        # Per-session table only for 5h
        if window == "5h":
            per_session: List[Dict[str, Any]] = self.usage.get("per_session", [])
            if per_session:
                y += 1
                headers = ["Session", "Input", "Output", "Cache-R", "Cache-W", "Total"]
                col_w = [22, 10, 10, 10, 10, 10]
                rows: List[List[str]] = []
                for s in per_session:
                    rows.append([
                        s.get("project_name", "?")[:20],
                        format_tokens(s.get("input_tokens", 0)),
                        format_tokens(s.get("output_tokens", 0)),
                        format_tokens(s.get("cache_read_tokens", 0)),
                        format_tokens(s.get("cache_creation_tokens", 0) if "cache_creation_tokens" in s else 0),
                        format_tokens(s.get("total_tokens", 0)),
                    ])
                y = self.draw_table(y, 3, headers, rows, col_w)

        return y

    def handle_key(self, key: int) -> bool:
        if key == ord("r"):
            self.needs_refresh = True
            return True
        return False
