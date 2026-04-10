"""Home screen — session context overview, usage bars, harness score.

Screen 1: the primary decision surface showing which sessions need
attention and how much API quota remains.
"""
from __future__ import annotations

import curses
import time
from typing import Any, Dict, List

from data import (
    get_activity,
    get_health,
    get_session_detail,
    get_sessions,
    get_usage_stats,
)
from data.common import CLAUDE_TIER, REFRESH_INTERVAL_SEC, format_tokens

from .base import (
    COLOR_BLUE,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_YELLOW,
    BaseScreen,
)


class HomeScreen(BaseScreen):
    """Screen 1: active sessions + usage bars + harness score."""

    def __init__(self, stdscr: curses.window) -> None:
        super().__init__(stdscr)
        self.sessions: List[Dict[str, Any]] = []
        self.session_details: List[Dict[str, Any]] = []
        self.usage: Dict[str, Any] = {}
        self.health: Dict[str, Any] = {}
        self.activity: Dict[str, Any] = {}

    # ── Data ──────────────────────────────────────────────────────────

    def refresh_data(self) -> None:
        try:
            raw = get_sessions()
            self.sessions = raw.get("sessions", [])
        except Exception:
            self.sessions = []

        try:
            raw = get_session_detail()
            self.session_details = raw.get("sessions", [])
        except Exception:
            self.session_details = []

        try:
            self.usage = get_usage_stats()
        except Exception as e:
            self.usage = {"source": "error", "api_error": str(e)}

        try:
            self.health = get_health()
        except Exception:
            self.health = {}

        try:
            self.activity = get_activity()
        except Exception:
            self.activity = {}

    # ── Render ────────────────────────────────────────────────────────

    def render(self) -> None:
        if self.check_auto_refresh(REFRESH_INTERVAL_SEC):
            self.refresh_data()

        start_y, height, width = self.content_area()
        y = start_y

        try:
            y = self._render_sessions(y, width)
        except Exception as e:
            self.safe_addstr(y, 3, f"[sessions err: {e}]", curses.color_pair(COLOR_YELLOW))
            y += 1
        y += 1
        try:
            y = self._render_usage(y, width)
        except Exception as e:
            self.safe_addstr(y, 3, f"[usage err: {e}]", curses.color_pair(COLOR_YELLOW))
            y += 1
        y += 1
        try:
            y = self._render_harness(y, width)
        except Exception as e:
            self.safe_addstr(y, 3, f"[harness err: {e}]", curses.color_pair(COLOR_YELLOW))
            y += 1
        y += 1
        self._render_quick_stats(y, width)

    # ── Active sessions ───────────────────────────────────────────────

    def _render_sessions(self, y: int, width: int) -> int:
        y = self.draw_section(y, "Active Sessions", width - 2)

        if not self.session_details:
            self.safe_addstr(y, 3, "No active sessions detected.",
                             curses.A_DIM)
            return y + 1

        # Build a PID lookup from active processes
        active_pids: Dict[str, Dict[str, Any]] = {}
        for s in self.sessions:
            pid = str(s.get("pid", ""))
            if pid:
                active_pids[pid] = s

        # Header
        self.safe_addstr(y, 3, "Session", curses.A_BOLD | curses.A_UNDERLINE)
        self.safe_addstr(y, 33, "Context", curses.A_BOLD | curses.A_UNDERLINE)
        self.safe_addstr(y, 60, "Action", curses.A_BOLD | curses.A_UNDERLINE)
        y += 1

        h_max, _ = self.stdscr.getmaxyx()
        active_details = [d for d in self.session_details if d.get("is_active")]
        if not active_details:
            self.safe_addstr(y, 3, "No active sessions.", curses.A_DIM)
            return y + 1

        for detail in active_details:
            if y >= h_max - 1:
                break

            project = detail.get("project_name", "unknown")[:18]
            pct = detail.get("context_pct", 0.0)
            is_active = True

            # Build label: "project (PID xxxx)"
            # Try to find matching PID from active sessions
            pid_label = ""
            if is_active:
                for pid, sess in active_pids.items():
                    cwd = sess.get("cwd", "")
                    if project.lower() in cwd.lower():
                        pid_label = f"  (PID {pid})"
                        break

            label = f"{project}{pid_label}"
            self.safe_addstr(y, 3, label[:28])

            # Context bar
            self.draw_bar(y, 33, pct, 20)

            # Action recommendation
            action = self._recommend_action(pct)
            color = self.pct_color(pct)
            self.safe_addstr(y, 58, action, curses.color_pair(color))
            y += 1

        return y

    @staticmethod
    def _recommend_action(pct: float) -> str:
        """Action recommendation matching spec thresholds.

        <40% green OK, 40-60% yellow Monitor,
        60-80% yellow /compact soon, >=80% red /handoff now.
        """
        if pct >= 80:
            return "\u26a0 /handoff now"
        if pct >= 60:
            return "\u26a0 /compact soon"
        if pct >= 40:
            return "~ monitor"
        return "\u2713 OK"

    # ── API quota ─────────────────────────────────────────────────────

    def _render_usage(self, y: int, width: int) -> int:
        source = self.usage.get("source", "estimated")
        if source in ("api", "statusline"):
            return self._render_usage_api(y, width)
        if source == "error":
            y = self.draw_section(y, "API Usage", width - 2)
            err = self.usage.get("api_error", "unknown error")
            self.safe_addstr(y, 3, f"Error: {err}", curses.color_pair(COLOR_YELLOW))
            return y + 1
        return self._render_usage_estimated(y, width)

    def _render_usage_api(self, y: int, width: int) -> int:
        """Render usage from statusline or API (exact data)."""
        source = self.usage.get("source", "api")
        label = "Usage (live)" if source == "statusline" else "API Usage (live)"
        y = self.draw_section(y, label, width - 2)

        w5h = self.usage.get("window_5h", {})
        wk = self.usage.get("window_weekly", {})

        # 5-hour bar
        pct_5h = w5h.get("usage_pct", 0.0)
        resets_5h = w5h.get("resets_at", "")
        self.safe_addstr(y, 3, "5h Window: ")
        self.draw_bar(y, 14, pct_5h, 20)
        local_time = self.format_reset_time(resets_5h)
        reset_str = f"  resets {local_time}" if local_time else ""
        self.safe_addstr(y, 39, f"used{reset_str}")
        y += 1

        # Weekly bar
        pct_wk = wk.get("usage_pct", 0.0)
        resets_wk = wk.get("resets_at", "")
        self.safe_addstr(y, 3, "Weekly:    ")
        self.draw_bar(y, 14, pct_wk, 20)
        local_wk = self.format_reset_datetime(resets_wk)
        reset_wk_str = f"  resets {local_wk}" if local_wk else ""
        self.safe_addstr(y, 39, f"used{reset_wk_str}")
        y += 1

        # Extra usage
        extra = self.usage.get("extra_usage", {})
        if extra.get("is_enabled"):
            used = extra.get("used_credits_usd", 0)
            limit = extra.get("monthly_limit_usd", 0)
            self.safe_addstr(y, 3, f"Extra: ${used:.2f} / ${limit:.2f}", curses.A_DIM)
            y += 1

        # API error
        api_error = self.usage.get("api_error", "")
        if api_error:
            self.safe_addstr(y, 3, f"⚠ {api_error}", curses.color_pair(COLOR_YELLOW))
            y += 1

        return y

    def _render_usage_estimated(self, y: int, width: int) -> int:
        """Render usage from JSONL aggregation (estimated)."""
        tier_name = CLAUDE_TIER.replace("max", "Max ")
        y = self.draw_section(y, f"API Quota ({tier_name}) [estimated]", width - 2)

        w5h = self.usage.get("window_5h", {})
        wk = self.usage.get("window_weekly", {})
        cost = self.usage.get("cost_estimate", {})

        pct_5h = w5h.get("usage_pct", 0.0)
        rem_5h = w5h.get("remaining_tokens", 0)
        self.safe_addstr(y, 3, "5h Window: ")
        self.draw_bar(y, 14, pct_5h, 20)
        self.safe_addstr(y, 39, f"used  |  {format_tokens(rem_5h)} remaining")
        y += 1

        pct_wk = wk.get("usage_pct", 0.0)
        rem_wk = wk.get("remaining_tokens", 0)
        self.safe_addstr(y, 3, "Weekly:    ")
        self.draw_bar(y, 14, pct_wk, 20)
        self.safe_addstr(y, 39, f"used  |  {format_tokens(rem_wk)} remaining")
        y += 1

        cost_5h = cost.get("window_5h_usd", 0.0)
        cost_wk = cost.get("window_weekly_usd", 0.0)
        self.safe_addstr(y, 3,
                         f"Est. cost: 5h ${cost_5h:.2f} | Week ${cost_wk:.2f}",
                         curses.A_DIM)
        y += 1
        return y

    # ── Harness score ─────────────────────────────────────────────────

    def _render_harness(self, y: int, width: int) -> int:
        y = self.draw_section(y, "Harness Score", width - 2)

        score = self.health.get("score", 0)
        total = self.health.get("total", 100)
        items = self.health.get("items", {})

        # Score bar
        filled = score * 10 // total
        empty = 10 - filled
        bar = "[" + "#" * filled + "-" * empty + "]"
        self.safe_addstr(y, 3, f"{bar} {score}/{total}")
        y += 1

        # Item checklist
        parts: List[str] = []
        for name, ok in items.items():
            mark = "\u2713" if ok else "\u2717"
            color = COLOR_GREEN if ok else COLOR_RED
            parts.append((name, mark, color))

        x = 5
        for name, mark, color in parts:
            token = f"{mark} {name}"
            self.safe_addstr(y, x, token, curses.color_pair(color))
            x += len(token) + 2
            # Wrap to next line if near edge
            if x > width - 20:
                y += 1
                x = 5

        return y + 1

    # ── Quick stats ───────────────────────────────────────────────────

    def _render_quick_stats(self, y: int, width: int) -> None:
        active_count = sum(
            1 for s in self.session_details if s.get("is_active")
        )
        today_count = self.activity.get("today_count", 0)
        tier_label = CLAUDE_TIER
        refresh_ts = time.strftime("%H:%M:%S", time.localtime(self.last_refresh))

        line = (
            f"Active: {active_count}  |  "
            f"Today: {today_count} cmds  |  "
            f"Tier: {tier_label}  |  "
            f"Refreshed: {refresh_ts}"
        )
        self.safe_addstr(y, 3, line, curses.A_DIM)

    # ── Key handling ──────────────────────────────────────────────────

    def handle_key(self, key: int) -> bool:
        if key == ord("r"):
            self.needs_refresh = True
            return True
        return False
