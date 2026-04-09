"""Base screen class with shared rendering utilities.

Provides: safe_addstr, progress bars, tables, color helpers,
auto-refresh logic, and content area calculation.
"""
from __future__ import annotations

import curses
import time
from datetime import datetime, timezone, timedelta

# Color pair indices — initialized by tui.py via curses.init_pair().
COLOR_GREEN = 1
COLOR_YELLOW = 2
COLOR_RED = 3
COLOR_CYAN = 4
COLOR_BLUE = 5
COLOR_HEADER = 6
COLOR_SELECTED = 7
COLOR_ORANGE = 8  # yellow + bold as orange approximation


class BaseScreen:
    """Abstract base for all TUI screens.

    Subclasses must override ``render()``, ``handle_key()``,
    and ``refresh_data()``.
    """

    def __init__(self, stdscr: curses.window) -> None:
        self.stdscr = stdscr
        self.needs_refresh: bool = True
        self.last_refresh: float = 0.0
        # True when the screen is capturing text input (e.g. search box).
        # While True, tui.py should NOT interpret number keys as screen switches.
        self.input_mode: bool = False

    # ── Abstract interface ────────────────────────────────────────────

    def render(self) -> None:
        """Draw screen content.  Content area is rows 1 .. h-2."""
        raise NotImplementedError

    def handle_key(self, key: int) -> bool:
        """Handle a key press.  Return True if the key was consumed."""
        raise NotImplementedError

    def refresh_data(self) -> None:
        """Re-fetch data from the ``data/`` module."""
        raise NotImplementedError

    # ── Refresh helpers ───────────────────────────────────────────────

    def check_auto_refresh(self, interval: float = 10.0) -> bool:
        """Return True and reset timer when auto-refresh is due.

        Screens call this at the top of ``render()``.  When it returns
        True the screen should call ``refresh_data()`` before drawing.
        """
        now = time.time()
        if self.needs_refresh or (now - self.last_refresh) >= interval:
            self.needs_refresh = False
            self.last_refresh = now
            return True
        return False

    # ── Layout helpers ────────────────────────────────────────────────

    def content_area(self) -> tuple[int, int, int]:
        """Return ``(start_y, height, width)`` for drawable content.

        Row 0 is the top bar; row h-1 is the status bar.
        """
        h, w = self.stdscr.getmaxyx()
        return 1, h - 2, w

    # ── Safe drawing ──────────────────────────────────────────────────

    def safe_addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        """``addstr`` wrapper that clips at screen edges instead of crashing."""
        h, w = self.stdscr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        max_len = w - x
        if max_len <= 0:
            return
        try:
            self.stdscr.addnstr(y, x, text, max_len, attr)
        except curses.error:
            pass

    # ── Progress bar ──────────────────────────────────────────────────

    def draw_bar(self, y: int, x: int, pct: float, width: int = 20) -> None:
        """Draw a colored progress bar: ``████████░░ 78%``."""
        filled = int(width * min(pct, 100) / 100)
        empty = width - filled
        color = self.pct_color(pct)
        bar = "\u2588" * filled + "\u2591" * empty
        self.safe_addstr(y, x, bar, curses.color_pair(color))
        self.safe_addstr(y, x + width + 1, f"{pct:.0f}%", curses.color_pair(color))

    def pct_color(self, pct: float) -> int:
        """Return curses color-pair index for a percentage value.

        <40 green, 40-60 yellow, 60-80 orange, >=80 red.
        """
        if pct < 40:
            return COLOR_GREEN
        if pct < 60:
            return COLOR_YELLOW
        if pct < 80:
            return COLOR_ORANGE
        return COLOR_RED

    @staticmethod
    def format_reset_time(iso_str: str) -> str:
        """Convert ISO UTC reset time to local time string (e.g. '23:45')."""
        if not iso_str or len(iso_str) < 16:
            return ""
        try:
            cleaned = iso_str.replace("Z", "+00:00")
            dt_utc = datetime.fromisoformat(cleaned)
            dt_local = dt_utc.astimezone()  # system timezone (KST)
            return dt_local.strftime("%H:%M")
        except (ValueError, OSError):
            return iso_str[11:16]  # fallback: raw UTC

    # ── Table drawing ─────────────────────────────────────────────────

    def draw_table(
        self,
        y: int,
        x: int,
        headers: list[str],
        rows: list[list[str]],
        col_widths: list[int],
    ) -> int:
        """Draw an aligned table with bold/underlined headers.

        Returns the y coordinate of the row *after* the last data row,
        so callers know where to continue drawing.
        """
        h_max, _ = self.stdscr.getmaxyx()

        # Header row
        hx = x
        for i, header in enumerate(headers):
            cw = col_widths[i] if i < len(col_widths) else 12
            self.safe_addstr(y, hx, header.ljust(cw),
                             curses.A_BOLD | curses.A_UNDERLINE)
            hx += cw

        # Data rows
        for ri, row in enumerate(rows):
            ry = y + 1 + ri
            if ry >= h_max - 1:
                break
            rx = x
            for ci, cell in enumerate(row):
                cw = col_widths[ci] if ci < len(col_widths) else 12
                self.safe_addstr(ry, rx, str(cell).ljust(cw))
                rx += cw

        return y + 1 + len(rows)

    # ── Section divider ───────────────────────────────────────────────

    def draw_section(self, y: int, title: str, width: int = 60) -> int:
        """Draw ``── Title ─────────`` and return y+1."""
        line = f"\u2500\u2500 {title} " + "\u2500" * max(0, width - len(title) - 4)
        self.safe_addstr(y, 1, line, curses.A_BOLD)
        return y + 1
