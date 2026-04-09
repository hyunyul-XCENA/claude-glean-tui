"""Vault browser — directory / note / content navigation + search.

Screen 5: four-level drill-down (dirs -> notes -> content, plus search
results) with keyboard-driven search input capture.
"""
from __future__ import annotations

import curses
from typing import Any, Dict, List

import vault

from .base import COLOR_CYAN, COLOR_GREEN, COLOR_YELLOW, BaseScreen


class VaultScreen(BaseScreen):
    """Screen 5: vault note browser with search."""

    def __init__(self, stdscr: curses.window) -> None:
        super().__init__(stdscr)
        # Navigation levels: 0=dirs, 1=notes, 2=content, 3=search results
        self.level: int = 0
        self.dirs: List[str] = []
        self.notes: List[Dict[str, Any]] = []
        self.content_lines: List[str] = []
        self.search_query: str = ""
        self.search_results: List[Dict[str, Any]] = []
        self.selected: int = 0
        self.scroll_offset: int = 0
        self.current_dir: str = ""
        self.current_note: str = ""
        self._content_origin: int = 1  # level to return to from content

    # ── Data ──────────────────────────────────────────────────────────

    def refresh_data(self) -> None:
        if self.level == 0:
            try:
                self.dirs = vault.vault_list_dirs()
            except Exception:
                self.dirs = []
        elif self.level == 1:
            try:
                self.notes = vault.vault_list_notes(self.current_dir)
            except Exception:
                self.notes = []

    # ── Render ────────────────────────────────────────────────────────

    def render(self) -> None:
        if self.check_auto_refresh(600):  # vault: manual refresh only
            self.refresh_data()
        renderers = [
            self._render_dirs, self._render_notes,
            self._render_content, self._render_search_results,
        ]
        if 0 <= self.level < len(renderers):
            renderers[self.level]()
        if self.input_mode:
            self._render_search_input()

    def _render_dirs(self) -> None:
        start_y, height, width = self.content_area()
        y = self.draw_section(start_y, "Vault Directories", width - 2)
        if not self.dirs:
            self.safe_addstr(y, 3, "No vault directories found.", curses.A_DIM)
            self.safe_addstr(y + 1, 3,
                             "Set CLAUDE_VAULT_PATH or create ~/Documents/vault/",
                             curses.A_DIM)
            return
        h_max, _ = self.stdscr.getmaxyx()
        for i in range(self.scroll_offset, len(self.dirs)):
            if y >= h_max - 2:
                break
            is_sel = i == self.selected
            icon = "\u25b6 " if is_sel else "  "
            self.safe_addstr(y, 3, f"{icon}{self.dirs[i]}/",
                             curses.A_REVERSE if is_sel else 0)
            y += 1
        if y < h_max - 1:
            self.safe_addstr(y + 1, 3,
                             "Enter: open  |  /: search  |  r: refresh",
                             curses.A_DIM)

    def _render_notes(self) -> None:
        start_y, height, width = self.content_area()
        y = self.draw_section(start_y, f"Vault / {self.current_dir}", width - 2)
        if not self.notes:
            self.safe_addstr(y, 3, "No notes in this directory.", curses.A_DIM)
            return
        self.safe_addstr(y, 3, "Date", curses.A_BOLD | curses.A_UNDERLINE)
        self.safe_addstr(y, 16, "Filename", curses.A_BOLD | curses.A_UNDERLINE)
        self.safe_addstr(y, 42, "Summary", curses.A_BOLD | curses.A_UNDERLINE)
        y += 1
        h_max, _ = self.stdscr.getmaxyx()
        for i in range(self.scroll_offset, len(self.notes)):
            if y >= h_max - 2:
                break
            note = self.notes[i]
            attr = curses.A_REVERSE if i == self.selected else 0
            self.safe_addstr(y, 3, str(note.get("date", ""))[:10], attr)
            self.safe_addstr(y, 16, note.get("filename", "?")[:24], attr)
            max_sum = max(1, width - 44)
            self.safe_addstr(y, 42, note.get("summary", "")[:max_sum], attr)
            y += 1
        if y < h_max - 1:
            self.safe_addstr(y + 1, 3,
                             "Enter: view  |  Esc: back  |  /: search",
                             curses.A_DIM)

    def _render_content(self) -> None:
        start_y, height, width = self.content_area()
        title = f"Vault / {self.current_dir} / {self.current_note}"
        y = self.draw_section(start_y, title, width - 2)
        h_max, _ = self.stdscr.getmaxyx()
        visible = h_max - y - 2
        for i in range(self.scroll_offset, len(self.content_lines)):
            if y >= h_max - 1:
                break
            line = self.content_lines[i]
            if line.startswith("# "):
                self.safe_addstr(y, 3, line, curses.A_BOLD)
            elif line.startswith("## "):
                self.safe_addstr(y, 3, line,
                                 curses.A_BOLD | curses.color_pair(COLOR_CYAN))
            elif line.startswith("---"):
                self.safe_addstr(y, 3, line, curses.A_DIM)
            elif line.startswith("> "):
                self.safe_addstr(y, 3, line, curses.color_pair(COLOR_GREEN))
            else:
                self.safe_addstr(y, 3, line)
            y += 1
        total = len(self.content_lines)
        if total > visible:
            end = min(self.scroll_offset + visible, total)
            indicator = f"[{self.scroll_offset + 1}-{end} of {total}]"
            self.safe_addstr(h_max - 2, width - len(indicator) - 2,
                             indicator, curses.A_DIM)
        # Key hints
        self.safe_addstr(h_max - 2, 3, "b:Back  j/k:Scroll  /:Search", curses.A_DIM)

    def _render_search_results(self) -> None:
        start_y, height, width = self.content_area()
        qd = self.search_query[:30]
        y = self.draw_section(
            start_y,
            f"Search: \"{qd}\" ({len(self.search_results)} results)",
            width - 2,
        )
        if not self.search_results:
            self.safe_addstr(y, 3, "No results found.", curses.A_DIM)
            return
        h_max, _ = self.stdscr.getmaxyx()
        for i in range(self.scroll_offset, len(self.search_results)):
            if y >= h_max - 2:
                break
            r = self.search_results[i]
            attr = curses.A_REVERSE if i == self.selected else 0
            path_str = f"{r.get('subdir', '')}/{r.get('filename', '?')}"[:35]
            self.safe_addstr(y, 3, path_str, attr)
            self.safe_addstr(y, 40, str(r.get("date", ""))[:10], attr)
            y += 1
            if y < h_max - 2:
                max_m = max(1, width - 8)
                self.safe_addstr(y, 6, r.get("matched_line", "")[:max_m],
                                 curses.A_DIM | attr)
                y += 1
        if y < h_max - 1:
            self.safe_addstr(y + 1, 3,
                             "Enter: open note  |  Esc: back  |  /: new search",
                             curses.A_DIM)

    def _render_search_input(self) -> None:
        h_max, w_max = self.stdscr.getmaxyx()
        prompt = "Search: "
        y = h_max - 2
        self.safe_addstr(y, 1, prompt, curses.A_BOLD)
        self.safe_addstr(y, len(prompt) + 1, self.search_query)
        cx = len(prompt) + 1 + len(self.search_query)
        if cx < w_max:
            try:
                self.stdscr.move(y, cx)
                curses.curs_set(1)
            except curses.error:
                pass

    # ── Key handling ──────────────────────────────────────────────────

    def handle_key(self, key: int) -> bool:
        if self.input_mode:
            return self._handle_search_input(key)
        if self.level == 2:
            return self._handle_content(key)
        # Levels 0, 1, 3 share list navigation
        return self._handle_list(key)

    def _handle_list(self, key: int) -> bool:
        """Shared handler for levels 0 (dirs), 1 (notes), 3 (search)."""
        items = self._current_items()

        if key in (ord("j"), curses.KEY_DOWN):
            if items and self.selected < len(items) - 1:
                self.selected += 1
                self._adjust_scroll()
            return True
        if key in (ord("k"), curses.KEY_UP):
            if self.selected > 0:
                self.selected -= 1
                self._adjust_scroll()
            return True
        if key in (curses.KEY_ENTER, 10, 13):
            return self._drill_down(items)
        if key in (27, curses.KEY_BACKSPACE, 127, 8, ord("b")):  # Escape / Backspace / b
            return self._go_back()
        if key == ord("/"):
            self._enter_search()
            return True
        if key == ord("r"):
            self.needs_refresh = True
            return True
        return False

    def _current_items(self) -> list:
        if self.level == 0:
            return self.dirs
        if self.level == 1:
            return self.notes
        if self.level == 3:
            return self.search_results
        return []

    def _drill_down(self, items: list) -> bool:
        if not items or self.selected >= len(items):
            return True
        if self.level == 0:
            self.current_dir = self.dirs[self.selected]
            self.level = 1
            self.selected = 0
            self.scroll_offset = 0
            self.needs_refresh = True
        elif self.level == 1:
            self.current_note = self.notes[self.selected].get("filename", "")
            self._load_content()
            self._content_origin = 1
            self.level = 2
            self.scroll_offset = 0
        elif self.level == 3:
            r = self.search_results[self.selected]
            self.current_dir = r.get("subdir", "")
            self.current_note = r.get("filename", "")
            self._load_content()
            self._content_origin = 3
            self.level = 2
            self.scroll_offset = 0
        return True

    def _go_back(self) -> bool:
        if self.level == 1:
            self.level = 0
            self.selected = 0
            self.scroll_offset = 0
            self.needs_refresh = True
            return True
        if self.level == 3:
            self.level = 0
            self.selected = 0
            self.scroll_offset = 0
            self.needs_refresh = True
            return True
        return False  # level 0: let tui.py handle Escape

    def _handle_content(self, key: int) -> bool:
        # Back: Escape, Backspace, or 'b'
        if key in (27, curses.KEY_BACKSPACE, 127, 8, ord("b")):
            self.level = self._content_origin
            self.scroll_offset = 0
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            return True
        # 'q' in content view goes back, not quit app
        if key == ord("q"):
            self.level = self._content_origin
            self.scroll_offset = 0
            return True
        if key in (ord("j"), curses.KEY_DOWN):
            if self.scroll_offset < len(self.content_lines) - 1:
                self.scroll_offset += 1
            return True
        if key in (ord("k"), curses.KEY_UP):
            if self.scroll_offset > 0:
                self.scroll_offset -= 1
            return True
        if key == curses.KEY_NPAGE:
            page = max(1, self.stdscr.getmaxyx()[0] - 4)
            self.scroll_offset = min(
                self.scroll_offset + page,
                max(0, len(self.content_lines) - 1),
            )
            return True
        if key == curses.KEY_PPAGE:
            page = max(1, self.stdscr.getmaxyx()[0] - 4)
            self.scroll_offset = max(0, self.scroll_offset - page)
            return True
        if key == ord("/"):
            self._enter_search()
            return True
        return False

    # ── Search input ─────────────────────────────────────────────────

    def _enter_search(self) -> None:
        self.input_mode = True
        self.search_query = ""
        try:
            curses.curs_set(1)
        except curses.error:
            pass

    def _handle_search_input(self, key: int) -> bool:
        if key == 27:  # Escape -> cancel
            self.input_mode = False
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            return True
        if key in (curses.KEY_ENTER, 10, 13):
            self.input_mode = False
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            if self.search_query.strip():
                try:
                    self.search_results = vault.vault_search(self.search_query)
                except Exception:
                    self.search_results = []
                self.level = 3
                self.selected = 0
                self.scroll_offset = 0
            return True
        if key in (curses.KEY_BACKSPACE, 127, 8):
            if self.search_query:
                self.search_query = self.search_query[:-1]
            return True
        if 32 <= key <= 126:
            self.search_query += chr(key)
            return True
        return True  # swallow unrecognised keys during input

    # ── Helpers ───────────────────────────────────────────────────────

    def _load_content(self) -> None:
        try:
            text = vault.vault_read_note(self.current_dir, self.current_note)
        except Exception:
            text = "(Error reading note)"
        self.content_lines = text.splitlines()

    def _adjust_scroll(self) -> None:
        h_max, _ = self.stdscr.getmaxyx()
        visible = max(1, h_max - 6)
        if self.selected < self.scroll_offset:
            self.scroll_offset = self.selected
        elif self.selected >= self.scroll_offset + visible:
            self.scroll_offset = self.selected - visible + 1
