#!/usr/bin/env python3
"""Claude Glean TUI — Terminal dashboard for Claude Code workspace monitoring.

Entry point: ``python3 tui.py``

Initializes curses, creates screen instances, and runs the main event loop.
Top bar shows screen tabs; bottom bar shows status/key hints.
"""
from __future__ import annotations

import curses
import importlib
import sys
import time
from pathlib import Path
from typing import List, Optional

# Ensure project root is importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).parent))

from screens.base import (
    COLOR_BLUE, COLOR_CYAN, COLOR_GREEN, COLOR_HEADER, COLOR_ORANGE,
    COLOR_RED, COLOR_SELECTED, COLOR_YELLOW, BaseScreen,
)

# ── Screen registry ─────────────────────────────────────────────────────────
SCREEN_NAMES = ["Home", "Usage", "Components", "X-ray", "Vault"]

# (module_path, class_name) — imported lazily so missing modules don't crash.
_SCREEN_SPEC = [
    ("screens.home", "HomeScreen"),
    ("screens.usage", "UsageScreen"),
    ("screens.components", "ComponentsScreen"),
    ("screens.xray", "XrayScreen"),
    ("screens.vault", "VaultScreen"),
]

_screen_classes: List[Optional[type]] = []
for _mod, _cls in _SCREEN_SPEC:
    try:
        _screen_classes.append(getattr(importlib.import_module(_mod), _cls))
    except (ImportError, AttributeError):
        _screen_classes.append(None)


class _PlaceholderScreen(BaseScreen):
    """Stub for screens whose modules aren't implemented yet."""

    def __init__(self, stdscr: curses.window, name: str) -> None:
        super().__init__(stdscr)
        self._name = name

    def render(self) -> None:
        y, _, w = self.content_area()
        msg = f"{self._name} screen not yet implemented."
        self.safe_addstr(y + 2, max(0, (w - len(msg)) // 2), msg, curses.A_DIM)

    def handle_key(self, key: int) -> bool:
        return False

    def refresh_data(self) -> None:
        pass


# ── Color initialization ────────────────────────────────────────────────────

def init_colors() -> None:
    """Set up the 7 color pairs used throughout the TUI.

    Falls back gracefully when the terminal does not support color.
    """
    if not curses.has_colors():
        return
    curses.use_default_colors()
    curses.init_pair(COLOR_GREEN, curses.COLOR_GREEN, -1)
    curses.init_pair(COLOR_YELLOW, curses.COLOR_YELLOW, -1)
    curses.init_pair(COLOR_RED, curses.COLOR_RED, -1)
    curses.init_pair(COLOR_CYAN, curses.COLOR_CYAN, -1)
    curses.init_pair(COLOR_BLUE, curses.COLOR_BLUE, -1)
    curses.init_pair(COLOR_HEADER, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(COLOR_SELECTED, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(COLOR_ORANGE, curses.COLOR_YELLOW, -1)  # bold yellow = orange approx


# ── Top bar ──────────────────────────────────────────────────────────────────

def draw_top_bar(stdscr: curses.window, active_idx: int) -> None:
    """Render the tab bar at row 0.

    Format: `` Claude Glean TUI  [1:Home] [2:Usage] ...``
    Active tab is highlighted with COLOR_SELECTED.
    """
    h, w = stdscr.getmaxyx()
    if h < 1 or w < 10:
        return

    header_attr = curses.color_pair(COLOR_HEADER) | curses.A_BOLD
    try:
        stdscr.addnstr(0, 0, " " * w, w, header_attr)
    except curses.error:
        pass

    title = " Claude Glean TUI "
    try:
        stdscr.addnstr(0, 0, title, min(len(title), w), header_attr)
    except curses.error:
        pass

    x = len(title) + 1
    for i, name in enumerate(SCREEN_NAMES):
        label = f" [{i + 1}:{name}] "
        if x + len(label) >= w:
            break
        attr = (curses.color_pair(COLOR_SELECTED) | curses.A_BOLD
                if i == active_idx else header_attr)
        try:
            stdscr.addnstr(0, x, label, min(len(label), w - x), attr)
        except curses.error:
            pass
        x += len(label)


# ── Status bar ───────────────────────────────────────────────────────────────

def draw_status_bar(stdscr: curses.window, screen_name: str,
                    last_refresh: float) -> None:
    """Render the bottom status bar at row h-1."""
    h, w = stdscr.getmaxyx()
    if h < 2 or w < 10:
        return

    y = h - 1
    refresh_ts = time.strftime("%H:%M:%S", time.localtime(last_refresh))
    from data.oauth import is_authenticated
    auth_tag = " [API]" if is_authenticated() else " a:SignIn"
    left = f" [{screen_name}]  q:Quit  r:Refresh  /:Search  d:Delete{auth_tag}"
    right = f"Last refresh: {refresh_ts} "
    gap = max(0, w - len(left) - len(right))
    line = left + " " * gap + right

    try:
        stdscr.addnstr(y, 0, line, w, curses.color_pair(COLOR_HEADER))
    except curses.error:
        pass


# ── Terminal size guard ──────────────────────────────────────────────────────

MIN_WIDTH, MIN_HEIGHT = 80, 24


def check_terminal_size(stdscr: curses.window) -> bool:
    """Return True if terminal >= 80x24, otherwise display a warning."""
    h, w = stdscr.getmaxyx()
    if h >= MIN_HEIGHT and w >= MIN_WIDTH:
        return True
    msg = f"Terminal too small ({w}x{h}) — minimum {MIN_WIDTH}x{MIN_HEIGHT}"
    try:
        stdscr.addnstr(h // 2, max(0, (w - len(msg)) // 2),
                       msg, max(0, w), curses.A_BOLD)
    except curses.error:
        pass
    return False


# ── Main application loop ───────────────────────────────────────────────────

def app(stdscr: curses.window) -> None:
    """Initialize the TUI and run the event dispatch loop."""
    curses.curs_set(0)
    init_colors()
    stdscr.timeout(100)
    stdscr.keypad(True)

    # Build screen instances — placeholder for any missing modules.
    screens: List[BaseScreen] = [
        cls(stdscr) if cls is not None
        else _PlaceholderScreen(stdscr, SCREEN_NAMES[i])
        for i, cls in enumerate(_screen_classes)
    ]
    active_idx = 0

    while True:
        stdscr.erase()

        # Terminal size guard
        if not check_terminal_size(stdscr):
            stdscr.refresh()
            key = stdscr.getch()
            if key == ord("q"):
                break
            if key == curses.KEY_RESIZE:
                stdscr.clear()
            continue

        active_screen = screens[active_idx]

        draw_top_bar(stdscr, active_idx)
        draw_status_bar(stdscr, SCREEN_NAMES[active_idx],
                        active_screen.last_refresh or time.time())

        # Render content (rows 1 .. h-2).
        try:
            active_screen.render()
        except Exception:
            start_y, _, width = active_screen.content_area()
            try:
                stdscr.addnstr(start_y + 1, 3,
                               "Error rendering screen — press r to retry.",
                               max(0, width - 4),
                               curses.color_pair(COLOR_RED) | curses.A_BOLD)
            except curses.error:
                pass

        stdscr.refresh()

        # ── Input handling ───────────────────────────────────────────
        key = stdscr.getch()
        if key == -1:
            continue

        if key == curses.KEY_RESIZE:
            stdscr.clear()
            continue

        # 'q' — let screen consume first; quit only if not consumed.
        if key == ord("q"):
            if not active_screen.handle_key(key):
                break
            continue

        in_input = getattr(active_screen, "input_mode", False)

        # Number keys 1-5: switch screen (skip during input mode).
        if not in_input and ord("1") <= key <= ord("5"):
            new_idx = key - ord("1")
            if new_idx < len(screens):
                active_idx = new_idx
                screens[active_idx].needs_refresh = True
            continue

        # Tab: cycle to next screen.
        if not in_input and key == ord("\t"):
            active_idx = (active_idx + 1) % len(screens)
            screens[active_idx].needs_refresh = True
            continue

        # 'r': force-refresh active screen.
        if key == ord("r"):
            active_screen.needs_refresh = True
            continue

        # 'a': OAuth sign-in flow.
        if not in_input and key == ord("a"):
            _handle_oauth(stdscr, screens)
            continue

        # Delegate everything else to the active screen.
        active_screen.handle_key(key)


# ── OAuth authentication flow ────────────────────────────────────────────────

def _handle_oauth(stdscr: curses.window, screens: list) -> None:
    """Handle 'a' key — OAuth sign-in or sign-out."""
    from data.oauth import is_authenticated, start_oauth_flow, complete_oauth_flow, delete_credentials

    h, w = stdscr.getmaxyx()
    prompt_y = h - 2

    if is_authenticated():
        # Already signed in — offer sign out
        try:
            stdscr.addnstr(prompt_y, 1, "Already signed in. Sign out? (y/n): ",
                           w - 2, curses.color_pair(COLOR_YELLOW))
        except curses.error:
            pass
        stdscr.refresh()
        stdscr.timeout(-1)  # blocking
        key = stdscr.getch()
        stdscr.timeout(100)
        if key == ord("y"):
            delete_credentials()
            for s in screens:
                s.needs_refresh = True
        return

    # Start OAuth flow — open browser
    try:
        stdscr.addnstr(prompt_y, 1, "Opening browser for sign-in...",
                       w - 2, curses.color_pair(COLOR_CYAN))
    except curses.error:
        pass
    stdscr.refresh()

    start_oauth_flow()

    # Collect code from user
    try:
        stdscr.addnstr(prompt_y, 1, " " * (w - 2), w - 2)  # clear line
        stdscr.addnstr(prompt_y, 1, "Paste code here (or Esc to cancel): ",
                       w - 2, curses.color_pair(COLOR_CYAN))
    except curses.error:
        pass
    stdscr.refresh()

    curses.echo()
    curses.curs_set(1)
    stdscr.timeout(-1)  # blocking for input

    code_buf = ""
    input_x = 36
    while True:
        key = stdscr.getch()
        if key == 27:  # Escape
            break
        if key in (10, 13):  # Enter
            if code_buf.strip():
                result = complete_oauth_flow(code_buf.strip())
                if result.get("ok"):
                    try:
                        stdscr.addnstr(prompt_y - 1, 1, " " * (w - 2), w - 2)
                        stdscr.addnstr(prompt_y - 1, 1,
                                       "Signed in! Token saved to ~/.config/claude-glean-tui/token",
                                       w - 2, curses.color_pair(COLOR_GREEN) | curses.A_BOLD)
                        stdscr.addnstr(prompt_y, 1, " " * (w - 2), w - 2)
                        stdscr.addnstr(prompt_y, 1, "Press any key to continue...",
                                       w - 2, curses.A_DIM)
                    except curses.error:
                        pass
                    stdscr.refresh()
                    stdscr.timeout(-1)
                    stdscr.getch()
                    for s in screens:
                        s.needs_refresh = True
                else:
                    msg = result.get("error", "Failed")
                    try:
                        stdscr.addnstr(prompt_y, 1, " " * (w - 2), w - 2)
                        stdscr.addnstr(prompt_y, 1, msg, w - 2,
                                       curses.color_pair(COLOR_RED) | curses.A_BOLD)
                    except curses.error:
                        pass
                    stdscr.refresh()
                    stdscr.timeout(-1)
                    stdscr.getch()
            break
        if key in (curses.KEY_BACKSPACE, 127, 8):
            if code_buf:
                code_buf = code_buf[:-1]
                input_x -= 1
                try:
                    stdscr.addch(prompt_y, input_x, " ")
                    stdscr.move(prompt_y, input_x)
                except curses.error:
                    pass
        elif 32 <= key < 127:
            code_buf += chr(key)
            try:
                stdscr.addch(prompt_y, input_x, key)
            except curses.error:
                pass
            input_x += 1

    curses.noecho()
    curses.curs_set(0)
    stdscr.timeout(100)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    """Launch the TUI via ``curses.wrapper``."""
    curses.wrapper(app)


if __name__ == "__main__":
    main()
