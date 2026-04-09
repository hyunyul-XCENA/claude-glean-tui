"""Components browser — plugins, skills, agents, connectors, hooks.

Screen 3: tabbed list browser with detail expansion and deletion.
The most complex screen due to sub-tab navigation, source badges,
and delete confirmation flow.
"""
from __future__ import annotations

import curses
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from data import (
    delete_agent,
    delete_hook,
    delete_plugin,
    delete_skill,
    get_agents,
    get_connectors,
    get_hooks,
    get_plugins,
    get_skills,
)

from .base import COLOR_BLUE, COLOR_GREEN, COLOR_RED, BaseScreen

# Sub-tab definitions: (label, data_fn_key)
_SUB_TABS: List[Tuple[str, str]] = [
    ("Plugins", "plugins"),
    ("Skills", "skills"),
    ("Agents", "agents"),
    ("Connectors", "connectors"),
    ("Hooks", "hooks"),
]

# Maps sub-tab key -> (fetch_function, list_key_in_result)
_FETCH_MAP: Dict[str, Tuple[Callable[[], Dict[str, Any]], str]] = {
    "plugins": (get_plugins, "plugins"),
    "skills": (get_skills, "skills"),
    "agents": (get_agents, "agents"),
    "connectors": (get_connectors, "connectors"),
    "hooks": (get_hooks, "hooks"),
}


class ComponentsScreen(BaseScreen):
    """Screen 3: tabbed component browser with delete support."""

    def __init__(self, stdscr: curses.window) -> None:
        super().__init__(stdscr)
        self.sub_tab: int = 0
        self.items: List[Dict[str, Any]] = []
        self.selected: int = 0
        self.scroll_offset: int = 0
        self.expanded: int = -1          # index of expanded item, or -1
        self.confirm_delete: bool = False
        self.message: str = ""
        self.message_time: float = 0.0

    # ── Data ──────────────────────────────────────────────────────────

    def refresh_data(self) -> None:
        tab_key = _SUB_TABS[self.sub_tab][1]
        fetch_fn, list_key = _FETCH_MAP[tab_key]
        try:
            result = fetch_fn()
            self.items = result.get(list_key, [])
        except Exception:
            self.items = []

    def _reload_tab(self) -> None:
        """Reset list state and fetch data for the current sub-tab."""
        self.selected = 0
        self.scroll_offset = 0
        self.expanded = -1
        self.confirm_delete = False
        self.needs_refresh = True

    # ── Render ────────────────────────────────────────────────────────

    def render(self) -> None:
        if self.check_auto_refresh(600):  # components: manual refresh only
            self.refresh_data()

        start_y, height, width = self.content_area()
        y = start_y

        # Auto-clear temporary messages after 3 seconds
        if self.message and (time.time() - self.message_time > 3):
            self.message = ""

        y = self._render_tabs(y, width)
        y += 1
        y = self._render_list(y, width, height)

        # Delete confirmation or message at bottom
        if self.confirm_delete:
            self._render_confirm(width)
        elif self.message:
            self._render_message(width)

    def _render_tabs(self, y: int, width: int) -> int:
        """Draw sub-tab bar: ``[Plugins] [Skills] ...``."""
        x = 2
        for i, (label, _) in enumerate(_SUB_TABS):
            token = f"[{label}]"
            if i == self.sub_tab:
                attr = curses.A_BOLD | curses.A_REVERSE
            else:
                attr = curses.A_DIM
            self.safe_addstr(y, x, token, attr)
            x += len(token) + 1
        return y + 1

    def _render_list(self, y: int, width: int, height: int) -> int:
        """Draw the scrollable item list for the active sub-tab."""
        h_max, _ = self.stdscr.getmaxyx()
        visible = h_max - y - 3  # leave room for status bar + message

        if not self.items:
            self.safe_addstr(y, 3, "No items found.", curses.A_DIM)
            return y + 1

        tab_key = _SUB_TABS[self.sub_tab][1]

        for i in range(self.scroll_offset, len(self.items)):
            if y >= h_max - 3:
                break
            item = self.items[i]
            is_selected = (i == self.selected)

            y = self._render_item(y, width, item, tab_key, is_selected)

            # If this item is expanded, show detail below it
            if i == self.expanded:
                y = self._render_detail(y, width, item, tab_key)

        return y

    def _render_item(
        self,
        y: int,
        width: int,
        item: Dict[str, Any],
        tab_key: str,
        is_selected: bool,
    ) -> int:
        """Draw a single list item with source badge."""
        x = 3
        attr = curses.A_REVERSE if is_selected else 0

        # Source badge
        source = item.get("source", "user")
        if source == "user":
            self.safe_addstr(y, x, "[USER]", curses.color_pair(COLOR_GREEN))
            x += 7
        elif source.startswith("plugin:"):
            plugin_name = source.split(":", 1)[1] if ":" in source else source
            badge = f"[PLUGIN:{plugin_name}]"
            self.safe_addstr(y, x, badge, curses.color_pair(COLOR_BLUE))
            x += len(badge) + 1
        else:
            x += 1

        # Item-specific rendering
        line = self._format_item_line(tab_key, item)
        if tab_key == "plugins":
            enabled = item.get("enabled", False)
            tag = "[ENABLED]" if enabled else "[DISABLED]"
            tag_attr = curses.color_pair(COLOR_GREEN) if enabled else curses.A_DIM
            self.safe_addstr(y, x, tag, tag_attr)
            x += len(tag) + 1
        self.safe_addstr(y, x, line, attr)
        return y + 1

    @staticmethod
    def _format_item_line(tab_key: str, item: Dict[str, Any]) -> str:
        """Build the display string for an item."""
        if tab_key == "plugins":
            name = item.get("name", item.get("key", "unnamed"))
            ver = f" v{item['version']}" if item.get("version") else ""
            counts = (f"skills:{item.get('skills_count', 0)} "
                      f"agents:{item.get('agents_count', 0)} "
                      f"connectors:{item.get('connectors_count', 0)}")
            return f"{name}{ver} | {counts}"
        if tab_key == "hooks":
            ev = item.get("event", "?")
            ht = item.get("type", "?")
            desc = item.get("description", item.get("command", ""))[:40]
            return f"[{ev}] {ht}: {desc}"
        name = item.get("name", "unnamed")
        desc = item.get("description", "")[:40]
        return f"{name}  -- {desc}" if desc else name

    def _render_detail(
        self, y: int, width: int, item: Dict[str, Any], tab_key: str,
    ) -> int:
        """Show expanded detail for the selected item."""
        h_max, _ = self.stdscr.getmaxyx()

        # Gather detail lines
        detail_lines: List[str] = []
        for key in ("path", "model", "type", "command", "tools", "matcher"):
            val = item.get(key)
            if val is not None:
                detail_lines.append(f"  {key}: {val}")

        content = item.get("content", "")
        if content:
            detail_lines.append("  --- content ---")
            for line in content.splitlines()[:15]:
                detail_lines.append(f"  {line}")
            if content.count("\n") > 15:
                detail_lines.append("  ... (truncated)")

        for dl in detail_lines:
            if y >= h_max - 3:
                break
            self.safe_addstr(y, 5, dl, curses.A_DIM)
            y += 1

        return y

    def _render_confirm(self, width: int) -> None:
        """Show delete confirmation prompt at the bottom."""
        h_max, _ = self.stdscr.getmaxyx()
        if not self.items or self.selected >= len(self.items):
            return
        item = self.items[self.selected]
        tab_key = _SUB_TABS[self.sub_tab][1]
        name = item.get("name", item.get("key", "unnamed"))
        if tab_key == "plugins":
            msg = f"Delete plugin '{name}'? Removes plugin + all components + hooks. (y/n)"
        else:
            msg = f"Delete {tab_key.rstrip('s')} '{name}'? (y/n)"
        self.safe_addstr(h_max - 2, 2, msg,
                         curses.A_BOLD | curses.color_pair(COLOR_RED))

    def _render_message(self, width: int) -> None:
        h_max, _ = self.stdscr.getmaxyx()
        self.safe_addstr(h_max - 2, 2, self.message, curses.A_BOLD)

    # ── Key handling ──────────────────────────────────────────────────

    def handle_key(self, key: int) -> bool:
        if self.confirm_delete:
            return self._handle_confirm(key)
        if key == curses.KEY_LEFT:
            self.sub_tab = (self.sub_tab - 1) % len(_SUB_TABS)
            self._reload_tab()
            return True
        if key == curses.KEY_RIGHT:
            self.sub_tab = (self.sub_tab + 1) % len(_SUB_TABS)
            self._reload_tab()
            return True
        if key in (ord("j"), curses.KEY_DOWN):
            if self.items and self.selected < len(self.items) - 1:
                self.selected += 1
                self._adjust_scroll()
            return True
        if key in (ord("k"), curses.KEY_UP):
            if self.selected > 0:
                self.selected -= 1
                self._adjust_scroll()
            return True
        if key in (curses.KEY_ENTER, 10, 13):
            if self.expanded == self.selected:
                self.expanded = -1
            elif self.items:
                self.expanded = self.selected
            return True
        if key == 27:  # Escape
            if self.expanded >= 0:
                self.expanded = -1
                return True
            return False
        if key == ord("d"):
            self._initiate_delete()
            return True
        if key == ord("r"):
            self.needs_refresh = True
            return True
        return False

    def _handle_confirm(self, key: int) -> bool:
        if key == ord("y"):
            self._execute_delete()
            self.confirm_delete = False
            return True
        if key in (ord("n"), 27):
            self.confirm_delete = False
            return True
        return True  # swallow other keys during confirmation

    # ── Delete logic ──────────────────────────────────────────────────

    def _initiate_delete(self) -> None:
        """Start delete flow: check source, show confirmation or error."""
        if not self.items or self.selected >= len(self.items):
            return

        item = self.items[self.selected]
        tab_key = _SUB_TABS[self.sub_tab][1]
        source = item.get("source", "user")

        # Plugin sub-items cannot be deleted individually
        if tab_key != "plugins" and source.startswith("plugin:"):
            self.message = "Cannot delete plugin items. Uninstall the plugin instead."
            self.message_time = time.time()
            return

        # Connectors cannot be deleted through this UI
        if tab_key == "connectors":
            self.message = "Connectors cannot be deleted from this screen."
            self.message_time = time.time()
            return

        self.confirm_delete = True

    def _execute_delete(self) -> None:
        """Perform the actual deletion and refresh."""
        if not self.items or self.selected >= len(self.items):
            return

        item = self.items[self.selected]
        tab_key = _SUB_TABS[self.sub_tab][1]

        try:
            if tab_key == "plugins":
                result = delete_plugin(item.get("key", ""))
            elif tab_key == "skills":
                result = delete_skill(item.get("name", ""))
            elif tab_key == "agents":
                result = delete_agent(item.get("name", ""))
            elif tab_key == "hooks":
                event = item.get("event", "")
                # Hooks use event + list index for identification
                idx = self.selected
                result = delete_hook(event, idx)
            else:
                result = {"error": "Unsupported"}
        except Exception as exc:
            result = {"error": str(exc)}

        if result.get("ok"):
            self.message = "Deleted successfully."
        else:
            self.message = f"Error: {result.get('error', 'unknown')}"
        self.message_time = time.time()

        # Reset selection and refresh
        self.selected = max(0, self.selected - 1)
        self.expanded = -1
        self.needs_refresh = True

    # ── Scroll ────────────────────────────────────────────────────────

    def _adjust_scroll(self) -> None:
        """Keep selected item visible in the viewport."""
        h_max, _ = self.stdscr.getmaxyx()
        visible = max(1, h_max - 6)

        if self.selected < self.scroll_offset:
            self.scroll_offset = self.selected
        elif self.selected >= self.scroll_offset + visible:
            self.scroll_offset = self.selected - visible + 1
