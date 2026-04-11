"""Base screen class with shared rendering utilities.

Provides: safe_addstr, progress bars, tables, color helpers,
auto-refresh logic, and content area calculation.
"""
from __future__ import annotations

import curses
import logging
import time
from abc import ABC, abstractmethod
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Shared executor for background data refreshes (2 workers to allow
# overlapping refreshes when switching screens quickly).
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tui-refresh")

# Color pair indices — initialized by tui.py via curses.init_pair().
COLOR_GREEN = 1
COLOR_YELLOW = 2
COLOR_RED = 3
COLOR_CYAN = 4
COLOR_BLUE = 5
COLOR_HEADER = 6
COLOR_SELECTED = 7
COLOR_ORANGE = 8  # yellow + bold as orange approximation


class BaseScreen(ABC):
    """Abstract base for all TUI screens.

    Subclasses must override ``render()``, ``handle_key()``,
    and ``refresh_data()``.
    """

    def __init__(self, stdscr: curses.window) -> None:
        self.stdscr = stdscr
        self.needs_refresh: bool = True
        self.last_refresh: float = 0.0
        self._refresh_future: Optional[Future] = None
        # True when the screen is capturing text input (e.g. search box).
        # While True, tui.py should NOT interpret number keys as screen switches.
        self.input_mode: bool = False

    # ── Abstract interface ────────────────────────────────────────────

    @abstractmethod
    def render(self) -> None:
        """Draw screen content.  Content area is rows 1 .. h-2."""
        ...

    @abstractmethod
    def handle_key(self, key: int) -> bool:
        """Handle a key press.  Return True if the key was consumed."""
        ...

    @abstractmethod
    def refresh_data(self) -> None:
        """Re-fetch data from the ``data/`` module."""
        ...

    # ── Refresh helpers ───────────────────────────────────────────────

    def check_auto_refresh(self, interval: float = 10.0) -> bool:
        """Submit background refresh when due; return False (always).

        Screens call ``self.check_auto_refresh(N)`` at the top of
        ``render()`` — no explicit ``refresh_data()`` call needed.

        * First load is **synchronous** so the screen has data on first paint.
        * Subsequent refreshes are submitted to a background thread pool.
        * While a refresh is in flight, further submissions are skipped.
        """
        # 1. Completed future → mark done
        if self._refresh_future is not None and self._refresh_future.done():
            try:
                self._refresh_future.result()
            except Exception:
                pass  # refresh_data handles its own errors
            self._refresh_future = None
            self.last_refresh = time.time()
            return False

        # 2. In-flight → skip
        if self._refresh_future is not None:
            return False

        # 3. First load → synchronous (no "Loading..." flicker)
        if self.last_refresh == 0.0:
            self.needs_refresh = False
            self._safe_refresh()
            self.last_refresh = time.time()
            return False

        # 4. Due → submit to background thread
        now = time.time()
        if self.needs_refresh or (now - self.last_refresh) >= interval:
            self.needs_refresh = False
            self._refresh_future = _executor.submit(self._safe_refresh)
            return False

        return False

    def _safe_refresh(self) -> None:
        """Wrap ``refresh_data()`` with exception logging (background-safe)."""
        try:
            self.refresh_data()
        except Exception:
            logger.debug("Background refresh failed for %s",
                         type(self).__name__, exc_info=True)

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

    @staticmethod
    def format_reset_datetime(iso_str: str) -> str:
        """Convert ISO UTC reset time to local date+time (e.g. '4/18 06:00')."""
        if not iso_str or len(iso_str) < 16:
            return ""
        try:
            cleaned = iso_str.replace("Z", "+00:00")
            dt_utc = datetime.fromisoformat(cleaned)
            dt_local = dt_utc.astimezone()
            now = datetime.now().astimezone()
            if dt_local.date() == now.date():
                return dt_local.strftime("%H:%M")
            return dt_local.strftime("%-m/%d %H:%M")
        except (ValueError, OSError):
            return iso_str[11:16]

    def status_keys(self) -> str:
        """Extra key hints for the status bar. Override in subclasses."""
        return ""

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
