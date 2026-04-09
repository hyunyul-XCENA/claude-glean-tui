"""X-ray screen — per-session context token deep-dive.

Screen 4: two modes — a session list and a detailed context
breakdown for the selected session.
"""
from __future__ import annotations

import curses
import time
from typing import Any, Dict, List

from data import get_session_detail, get_session_xray
from data.common import REFRESH_INTERVAL_SEC, format_tokens
from .base import COLOR_GREEN, COLOR_RED, COLOR_YELLOW, BaseScreen


class XrayScreen(BaseScreen):
    """Screen 4: session list + context X-ray detail."""

    def __init__(self, stdscr: curses.window) -> None:
        super().__init__(stdscr)
        self.mode: str = "list"  # "list" or "detail"
        self.sessions: List[Dict[str, Any]] = []
        self.selected: int = 0
        self.scroll_offset: int = 0
        self.xray_data: Dict[str, Any] = {}
        self._active_session_id: str = ""
        self._is_active_session: bool = False
        self._confirm_delete: bool = False
        self._message: str = ""
        self._message_time: float = 0.0

    # ── Data ──────────────────────────────────────────────────────────

    def refresh_data(self) -> None:
        if self.mode == "list":
            self._refresh_list()
        else:
            self._refresh_detail()

    def _refresh_list(self) -> None:
        try:
            raw = get_session_detail()
            sessions = raw.get("sessions", [])
            # Sort: active first, then by context_pct descending
            sessions.sort(
                key=lambda s: (not s.get("is_active", False),
                               -s.get("context_pct", 0)),
            )
            self.sessions = sessions
        except Exception:
            self.sessions = []

    def _refresh_detail(self) -> None:
        if not self._active_session_id:
            return
        try:
            self.xray_data = get_session_xray(self._active_session_id)
        except Exception:
            self.xray_data = {}

    # ── Render ────────────────────────────────────────────────────────

    def render(self) -> None:
        if self.mode == "list":
            if self.check_auto_refresh(REFRESH_INTERVAL_SEC):
                self.refresh_data()
            self._render_list()
        else:
            # Auto-refresh only for active sessions
            interval = REFRESH_INTERVAL_SEC if self._is_active_session else 3600
            if self.check_auto_refresh(interval):
                self.refresh_data()
            self._render_detail()

    # ── List mode ─────────────────────────────────────────────────────

    def _render_list(self) -> None:
        start_y, height, width = self.content_area()
        y = start_y

        y = self.draw_section(y, "Sessions", width - 2)

        if not self.sessions:
            self.safe_addstr(y, 3, "No sessions found.", curses.A_DIM)
            return

        # Header
        self.safe_addstr(y, 3, "Status", curses.A_BOLD | curses.A_UNDERLINE)
        self.safe_addstr(y, 14, "Project", curses.A_BOLD | curses.A_UNDERLINE)
        self.safe_addstr(y, 36, "Messages", curses.A_BOLD | curses.A_UNDERLINE)
        self.safe_addstr(y, 48, "Context", curses.A_BOLD | curses.A_UNDERLINE)
        y += 1

        h_max, _ = self.stdscr.getmaxyx()
        for i in range(self.scroll_offset, len(self.sessions)):
            if y >= h_max - 2:
                break
            sess = self.sessions[i]
            is_selected = (i == self.selected)
            attr = curses.A_REVERSE if is_selected else 0

            # Status badge
            if sess.get("is_active", False):
                self.safe_addstr(y, 3, "[ACTIVE]",
                                 curses.color_pair(COLOR_GREEN) | curses.A_BOLD)
            else:
                self.safe_addstr(y, 3, "[idle]", curses.A_DIM)

            # Project name
            project = sess.get("project_name", "unknown")[:20]
            self.safe_addstr(y, 14, project, attr)

            # Message count
            msg_count = sess.get("message_count", 0)
            self.safe_addstr(y, 36, str(msg_count), attr)

            # Context percentage with bar
            pct = sess.get("context_pct", 0.0)
            self.draw_bar(y, 48, pct, 15)

            y += 1

        # Message or footer hint
        if self._message and time.time() - self._message_time < 5:
            color = curses.color_pair(COLOR_YELLOW) | curses.A_BOLD
            self.safe_addstr(h_max - 2, 3, self._message, color)
        elif y < h_max - 1:
            self.safe_addstr(y + 1, 3,
                             "Enter: X-ray  |  d: delete idle  |  r: refresh  |  j/k: nav",
                             curses.A_DIM)

    # ── Detail mode ───────────────────────────────────────────────────

    def _render_detail(self) -> None:
        start_y, height, width = self.content_area()
        y = start_y

        if not self.xray_data:
            self.safe_addstr(y, 3, "No X-ray data available.", curses.A_DIM)
            return

        pct = self.xray_data.get("context_pct", 0.0)
        ctx_tokens = self.xray_data.get("context_tokens", 0)
        ctx_max = self.xray_data.get("context_max", 1_000_000)

        # Context bar
        y = self.draw_section(y, "Context", width - 2)
        self.draw_bar(y, 3, pct, 25)
        self.safe_addstr(
            y, 33,
            f"({format_tokens(ctx_tokens)} / {format_tokens(ctx_max)} tokens)",
        )
        y += 2

        # Breakdown table
        breakdown = self.xray_data.get("breakdown", [])
        if breakdown:
            y = self.draw_section(y, "Breakdown", width - 2)

            headers = ["Category", "Tokens", "%"]
            col_widths = [32, 12, 8]
            rows: List[List[str]] = []
            for entry in breakdown:
                rows.append([
                    entry.get("name", "?"),
                    format_tokens(entry.get("tokens", 0)),
                    f"{entry.get('pct', 0.0):.1f}%",
                ])
            y = self.draw_table(y, 5, headers, rows, col_widths)
            y += 1

        h_max, _ = self.stdscr.getmaxyx()

        # Compact info
        compacts = self.xray_data.get("compacts_total", 0)
        since_compact = self.xray_data.get("messages_since_compact", 0)
        if y < h_max - 1:
            self.safe_addstr(
                y, 5,
                f"Compacts: {compacts} total | "
                f"{since_compact} messages since last compact",
            )
            y += 1

        # Recommendation
        rec = self.xray_data.get("recommendation", "")
        if rec and y < h_max - 1:
            y += 1
            color = self.pct_color(pct)
            self.safe_addstr(y, 5, f"Recommendation: {rec}",
                             curses.color_pair(color) | curses.A_BOLD)
            y += 1

        # Footer
        if y + 1 < h_max - 1:
            hint = "Esc: back to list"
            if not self._is_active_session:
                hint += "  |  r: refresh"
            self.safe_addstr(y + 1, 3, hint, curses.A_DIM)

    # ── Key handling ──────────────────────────────────────────────────

    def handle_key(self, key: int) -> bool:
        if self.mode == "detail":
            return self._handle_detail_key(key)
        return self._handle_list_key(key)

    def _handle_list_key(self, key: int) -> bool:
        # Confirm delete flow
        if self._confirm_delete:
            if key == ord("y"):
                self._do_delete()
            self._confirm_delete = False
            return True

        if key in (ord("j"), curses.KEY_DOWN):
            if self.sessions and self.selected < len(self.sessions) - 1:
                self.selected += 1
                self._adjust_scroll()
            return True

        if key in (ord("k"), curses.KEY_UP):
            if self.selected > 0:
                self.selected -= 1
                self._adjust_scroll()
            return True

        if key in (curses.KEY_ENTER, 10, 13):
            if self.sessions and self.selected < len(self.sessions):
                sess = self.sessions[self.selected]
                self._active_session_id = sess.get("session_id", "")
                self._is_active_session = sess.get("is_active", False)
                self.mode = "detail"
                self.needs_refresh = True
            return True

        if key == ord("d"):
            return self._initiate_delete()

        if key == ord("r"):
            self.needs_refresh = True
            return True

        return False

    def _initiate_delete(self) -> bool:
        if not self.sessions or self.selected >= len(self.sessions):
            return True
        sess = self.sessions[self.selected]
        if sess.get("is_active"):
            self._message = "Cannot delete active session"
            self._message_time = time.time()
            return True
        name = sess.get("project_name", "unknown")
        sid = sess.get("session_id", "")[:8]
        self._message = f"Delete session '{name}' ({sid}...)? (y/n)"
        self._message_time = time.time()
        self._confirm_delete = True
        return True

    def _do_delete(self) -> None:
        from data.delete import delete_session
        sess = self.sessions[self.selected]
        sid = sess.get("session_id", "")
        result = delete_session(sid)
        if result.get("ok"):
            self._message = "Deleted."
        else:
            self._message = result.get("error", "Failed")
        self._message_time = time.time()
        self.needs_refresh = True

    def _handle_detail_key(self, key: int) -> bool:
        if key in (27, curses.KEY_BACKSPACE, 127, 8):  # Esc or Backspace
            self.mode = "list"
            self.xray_data = {}
            self.needs_refresh = True
            return True

        if key == ord("r"):
            self.needs_refresh = True
            return True

        return False

    # ── Scroll ────────────────────────────────────────────────────────

    def _adjust_scroll(self) -> None:
        h_max, _ = self.stdscr.getmaxyx()
        visible = max(1, h_max - 6)
        if self.selected < self.scroll_offset:
            self.scroll_offset = self.selected
        elif self.selected >= self.scroll_offset + visible:
            self.scroll_offset = self.selected - visible + 1
