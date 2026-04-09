---
date: 2026-04-09
spec-id: claude-glean-tui
version: 4
status: approved
tags: [spec]
---

# Spec: Claude Glean TUI

## Overview

A curses-based terminal dashboard for monitoring Claude Code workspace state — **especially rate-limit usage** — without opening a browser or typing `/usage` repeatedly. Standalone project at `~/Documents/claude-glean-tui/`, Python 3.9+, zero external dependencies. Data collection logic is inspired by claude-glean's `server.py` but self-contained (no cross-project imports).

### Primary Goals

Two distinct monitoring concerns, both critical for daily workflow:

1. **Rate-limit usage** (5h rolling window + weekly): "How much of my API quota is left?" — Track remaining tokens/capacity by subscription tier (Pro/Max 5x/Max 20x). Prevents surprise throttling mid-work.

2. **Session context health**: "Is this session's context window getting full?" — Per-session context window occupancy (% of 1M tokens), compact count, messages since last compact. This is what drives compact/handoff decisions — a session at 80% context degrades in quality regardless of how much API quota remains.

These are orthogonal: you can have plenty of API quota but a bloated context window (need /compact), or a fresh context but nearly exhausted quota (need to pace yourself).

### Secondary Goals

- Harness Score dashboard (workspace health)
- Component browser with delete capability (plugins, skills, agents, connectors, hooks)
- Vault note browser

## Project Structure

```
~/Documents/claude-glean-tui/
├── tui.py                  # Entry point + main event loop + top/status bar
├── data/
│   ├── __init__.py          # Re-exports all public functions
│   ├── common.py            # Shared utilities: read_json, read_text, parse_frontmatter,
│   │                        #   format_tokens, decode_project_path, constants (CLAUDE_DIR, etc.)
│   ├── health.py            # get_health() — Harness Score (7 items, 0-100)
│   ├── sessions.py          # get_sessions(), get_session_detail(), get_session_xray(),
│   │                        #   get_activity() — process list, context breakdown
│   ├── usage.py             # get_usage_stats() — 5h/weekly token aggregation, tier limits,
│   │                        #   cost estimation
│   ├── components.py        # get_plugins(), get_skills(), get_agents(), get_connectors(),
│   │                        #   get_hooks() — component scanning with source tagging
│   └── delete.py            # delete_plugin(), delete_skill(), delete_agent(), delete_hook()
│                            #   — all mutation operations isolated here
├── screens/
│   ├── __init__.py          # Screen registry
│   ├── base.py              # BaseScreen class — shared rendering (bars, tables, color),
│   │                        #   resize handling, refresh logic
│   ├── home.py              # Screen 1: session context table + usage bars + harness score
│   ├── usage.py             # Screen 2: detailed 5h/weekly breakdown + per-session + cost
│   ├── components.py        # Screen 3: tabbed browser (Plugins/Skills/Agents/Connectors/Hooks)
│   │                        #   + delete with confirmation
│   ├── xray.py              # Screen 4: session selector + context token deep-dive
│   └── vault.py             # Screen 5: directory/note browser + search
├── vault.py                 # Vault data: vault_list_dirs(), vault_list_notes(),
│                            #   vault_read_note(), vault_search()
└── .specs/
```

Each file targets 200-400 lines. Separation by concern:
- **`data/`**: all `~/.claude/` reads/writes. Pure functions returning dicts. No curses imports.
- **`screens/`**: all UI rendering. Each screen is a class inheriting `BaseScreen`. No file I/O directly — calls `data/` and `vault.py`.
- **`vault.py`**: vault-specific data (separate from `data/` because vault is not part of Claude Code workspace).
- **`tui.py`**: wires everything together — curses init, event dispatch, screen manager.

## Interfaces

### data/common.py — Shared Utilities

| Function/Constant | Description |
|--------------------|-------------|
| `CLAUDE_DIR` | `Path.home() / ".claude"` |
| `CLAUDE_JSON` | `Path.home() / ".claude.json"` |
| `read_json(path) -> Optional[Any]` | Safe JSON file read. Returns None on failure. |
| `read_text(path, max_chars) -> Optional[str]` | Safe text file read with optional truncation. |
| `parse_frontmatter(text) -> dict` | Minimal YAML frontmatter parser (key: value, key: [a,b,c]). |
| `format_tokens(n) -> str` | `318029` → `"318.0k"`, `1200000` → `"1.2M"` |
| `decode_project_path(folder) -> str` | Convert project folder name to actual path. |

### data/health.py

| Function | Returns |
|----------|---------|
| `get_health()` | `{"score": int, "total": 100, "items": {"claude_md": bool, ...}}` — 7 items, 0-100 score |

### data/sessions.py

| Function | Returns |
|----------|---------|
| `get_sessions()` | `{"sessions": [{"pid","state","tty","started","command","cwd"}]}` — active claude processes |
| `get_activity()` | `{"today_count": int, "recent": [{"text","project","ageSeconds"}]}` |
| `get_session_detail()` | `{"sessions": [{"session_id","project_name","message_count","is_active","context_tokens","context_pct"}]}` |
| `get_session_xray(sid)` | `{"context_tokens","context_max","context_pct","breakdown":[{"name","tokens","pct"}],"compacts_total","messages_since_compact","recommendation"}` |

### data/usage.py

| Function | Returns |
|----------|---------|
| `get_usage_stats()` | See Usage Monitor section below |

### data/components.py

| Function | Returns |
|----------|---------|
| `get_plugins()` | `{"plugins": [{"key","name","version","enabled","skills_count","agents_count","connectors_count"}]}` |
| `get_skills()` | `{"skills": [{"name","description","path","source","content"}]}` |
| `get_agents()` | `{"agents": [{"name","description","model","tools","source","content"}]}` |
| `get_connectors()` | `{"connectors": [{"name","command","type","source","tools","tool_count"}]}` |
| `get_hooks()` | `{"hooks": [{"event","matcher","type","command","description","source"}]}` |

### data/delete.py

All mutation operations isolated here. Each returns `{"ok": True}` or `{"error": str}`.

| Function | Description |
|----------|-------------|
| `delete_plugin(key)` | Removes from installed_plugins.json, cache dir, enabledPlugins, and plugin hooks from settings.json |
| `delete_skill(name)` | Removes `~/.claude/skills/{name}/`. User-only (rejects plugin paths). |
| `delete_agent(name)` | Removes `~/.claude/agents/{name}.md`. User-only. |
| `delete_hook(event, idx)` | Removes hook by event+index from settings.json. |

#### `get_usage_stats()` — Core Feature

Scans all JSONL session files to aggregate token usage across time windows.

```python
def get_usage_stats() -> dict:
    """Aggregate token usage across all sessions for rate limit monitoring."""
    return {
        "tier": {
            "name": str,              # "pro" | "max5x" | "max20x"
            "limit_5h": int,          # token limit per 5h window
            "limit_weekly": int,      # token limit per week
        },
        "window_5h": {
            "input_tokens": int,
            "output_tokens": int,
            "cache_read_tokens": int,
            "cache_creation_tokens": int,
            "total_tokens": int,
            "remaining_tokens": int,  # limit_5h - total_tokens
            "usage_pct": float,       # total / limit * 100
            "session_count": int,
        },
        "window_weekly": {
            "input_tokens": int,
            "output_tokens": int,
            "cache_read_tokens": int,
            "cache_creation_tokens": int,
            "total_tokens": int,
            "remaining_tokens": int,
            "usage_pct": float,
            "session_count": int,
        },
        "per_session": [
            {
                "session_id": str,
                "project_name": str,
                "input_tokens": int,
                "output_tokens": int,
                "cache_read_tokens": int,
                "total_tokens": int,
                "message_count": int,
                "last_activity": str,   # ISO timestamp
            }
        ],
        "cost_estimate": {
            "window_5h_usd": float,
            "window_weekly_usd": float,
        },
        "timestamp": str,
    }
```

**Implementation**: Scan `~/.claude/projects/*/*.jsonl` files. For each assistant-type entry with a `usage` field and `timestamp`, accumulate tokens into the appropriate window. Skip subagent files. Cost estimation uses configurable model pricing constants.

### vault.py — Vault Browser Module

| Function | Signature | Returns |
|----------|-----------|---------|
| `vault_list_dirs()` | `() -> list[str]` | All subdirectories in vault root |
| `vault_list_notes(subdir)` | `(str) -> list[dict]` | `[{"filename","date","summary","tags"}]` sorted by date desc |
| `vault_read_note(subdir, fn)` | `(str, str) -> str` | Full note content. Rejects `..` in paths. |
| `vault_search(query)` | `(str) -> list[dict]` | `[{"filename","subdir","date","summary","matched_line"}]` max 50 results |

Vault root: `os.environ.get("CLAUDE_VAULT_PATH", os.path.expanduser("~/Documents/vault"))`.

### screens/base.py — Base Screen

| Method | Description |
|--------|-------------|
| `BaseScreen.__init__(stdscr, data_cache)` | Stores stdscr ref and shared data cache |
| `BaseScreen.render()` | Abstract. Each screen overrides to draw its content. |
| `BaseScreen.handle_key(key) -> bool` | Abstract. Returns True if key was consumed. |
| `BaseScreen.refresh_data()` | Re-fetches data via data/ functions. |
| `BaseScreen.draw_bar(y, x, pct, width)` | Shared: colored progress bar `████░░░░` |
| `BaseScreen.draw_table(y, x, headers, rows)` | Shared: aligned table rendering |
| `BaseScreen.pct_color(pct) -> int` | Returns curses color pair for percentage thresholds |

### screens/ — Screen Implementations

Each screen extends `BaseScreen`:

| File | Class | Screen # |
|------|-------|----------|
| `home.py` | `HomeScreen` | 1 |
| `usage.py` | `UsageScreen` | 2 |
| `components.py` | `ComponentsScreen` | 3 |
| `xray.py` | `XrayScreen` | 4 |
| `vault.py` | `VaultScreen` | 5 |

### tui.py — Entry Point + Event Loop

| Function | Signature | Description |
|----------|-----------|-------------|
| `main()` | `() -> None` | `curses.wrapper(app)` |
| `app(stdscr)` | `(curses.window) -> None` | Init colors, create screen instances, run event dispatch loop |
| `draw_top_bar(stdscr, active)` | Renders screen tabs |
| `draw_status_bar(stdscr, screen_name, last_refresh)` | Renders bottom bar |

## Screens

5 top-level screens, switched via number keys `1`-`5`:

| # | Screen | Description |
|---|--------|-------------|
| 1 | **Home** | Usage overview + Harness Score + session token summaries |
| 2 | **Usage** | Detailed 5h/weekly usage breakdown + per-session table + cost |
| 3 | **Components** | Plugins/Skills/Agents/Connectors/Hooks browser + delete |
| 4 | **X-ray** | Per-session context token deep-dive |
| 5 | **Vault** | Browse and search vault notes |

## Behaviors

### Screen 1: Home

1. **Session context overview** (top priority): Given active sessions exist / When the Home screen renders / Then display a per-session table showing context window health:
   ```
   ── Active Sessions ──────────────────────────────────────────────
   Session                       Context          Action
   claude-glean   (PID 1234)  78% ████████░░  ⚠ /compact soon
   pimp-my-claude (PID 5678)  23% ██░░░░░░░░  ✓ OK
   vault-work     (PID 9012)  91% █████████░  🔴 /handoff now
   ```
   Color: green <40%, yellow 40-60%, orange 60-80%, red >80%.
   This is the primary decision surface: "which session needs attention right now?"

2. **Rate-limit usage bars**: Given usage and tier data / Then display below sessions:
   ```
   ── API Quota (Max 5x) ──────────────────────────────────────────
   5h Window:  ███████░░░  70% used  |  1.2M remaining
   Weekly:     █████░░░░░  48% used  |  10.4M remaining
   Est. cost: 5h $0.47 | Week $3.21
   ```
   Same color coding. Shows remaining capacity and cost.

3. **Harness Score**: Given health data is loaded / Then display `[####------] 57/100` bar with 7 checklist items (green check / red cross).

4. **Quick stats line**: Active sessions count, today's command count, tier name, last refresh time.

5. **Auto-refresh**: Home screen refreshes every **10 seconds** automatically.

### Screen 2: Usage Detail

6. **5-hour window breakdown**: Per-session token usage table within the 5h window, sorted by total tokens descending:
   ```
   ── 5-Hour Window (70% of Max 5x limit) ─── Remaining: 234k tokens ──
   Session          Input    Output   Cache-R  Cache-W  Total
   claude-glean     12.3k   45.6k    234k     8.2k     300.1k
   pimp-my-claude    5.1k   12.4k    89k      3.1k     109.6k
   ─────────────────────────────────────────────────────────────
   5h Total         17.4k   58.0k    323k     11.3k    409.7k
   ```

7. **Weekly summary**: Below the 5h table, same format with weekly header showing remaining capacity.

8. **Cost breakdown**: Estimated cost per window with model pricing:
   ```
   Estimated cost (Opus pricing):
     5h window:  $0.47
     This week:  $3.21
   ```

9. **Auto-refresh**: Every **10 seconds** (same as Home).

### Screen 3: Components

10. **Sub-tab navigation**: Left/Right arrow keys switch between sub-tabs: `[Plugins] [Skills] [Agents] [Connectors] [Hooks]`. Active tab highlighted. Number keys reserved for screen switching.

11. **List navigation**: `j`/Down and `k`/Up to move selection. Scrolls when list exceeds visible area.

12. **Detail expansion**: Enter to expand selected item showing full content. Enter or Escape to collapse.

13. **Source badges**: `[USER]` in green, `[PLUGIN:name]` in blue before each item name.

14. **Plugin list display**: Each plugin shows: `[ENABLED/DISABLED] name vX.Y.Z | skills:N agents:N connectors:N`.

15. **Delete user item**: `d` on a user-sourced item → confirmation `"Delete {type} '{name}'? (y/n)"` → on `y`, execute delete. Refresh list after.

16. **Block plugin sub-item deletion**: `d` on a plugin-sourced skill/agent/hook → message: `"Cannot delete plugin items. Uninstall the plugin instead."`

17. **Delete plugin**: `d` on a plugin → confirmation `"Delete plugin '{name}'? Removes plugin + all components + hooks. (y/n)"` → on `y`, call `delete_plugin(key)` which:
    - Removes entry from `installed_plugins.json`
    - Deletes cache directory under `~/.claude/plugins/cache/`
    - Removes from `enabledPlugins` in `settings.json`
    - Removes any hooks in `settings.json` that were registered by this plugin

### Screen 4: X-ray

18. **Session selector**: Scrollable list from `get_session_detail()`. Active sessions `[ACTIVE]` sorted to top. Shows project name, message count, context %.

19. **X-ray display**: Enter → full breakdown:
    ```
    Context: [########░░] 78% (780k / 1M tokens)

    Breakdown:
      System (prompt + tools)    15.0k   1.5%
      CLAUDE.md + memory          8.2k   0.8%
      Agents/Skills definitions  12.1k   1.2%
      Conversation messages     744.7k  74.5%

    Compacts: 3 total | 47 messages since last compact
    Recommendation: ⚠ Consider running /compact soon
    ```
    Color: green <40%, yellow 40-60%, orange 60-80%, red >80%.

20. **X-ray auto-refresh**: Active session X-ray auto-refreshes every **10 seconds**. Inactive sessions: manual `r` only.

21. **Back navigation**: Escape or Backspace → return to session list.

### Screen 5: Vault

22. **Directory listing**: Show all subdirectories in vault root dynamically. Navigate with arrow keys, Enter to open.

23. **Note listing**: Inside a directory, list `.md` files with frontmatter date, filename, summary. Sorted by date descending.

24. **Note viewing**: Enter → scrollable full content. Up/Down/PgUp/PgDn to scroll. Escape to return.

25. **Vault search**: `/` → enter query → substring match across tags, summary, body in all subdirectories. Results show `subdir/filename`, date, matched line.

26. **Back navigation**: Escape returns to previous level.

### Global

27. **Screen switching**: Number keys `1`-`5` switch screens (except during text input).

28. **Quit**: `q` exits cleanly, restoring terminal state.

29. **Terminal resize**: `curses.KEY_RESIZE` → recalculate dimensions, redraw. No crash.

30. **Minimum terminal size**: 80x24. If smaller, show `"Terminal too small (min 80x24)"`.

31. **Status bar (bottom)**: Current screen name, key hints, last refresh timestamp.

32. **Top bar**: `Claude Glean TUI` + screen tabs with active one highlighted.

33. **Color support**: `curses.has_colors()` check. Graceful monochrome fallback.

34. **Refresh key**: `r` manually refreshes current screen data on any screen.

## Constants (data/common.py)

```python
CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_JSON = Path.home() / ".claude.json"

# ── Subscription tier limits ──
# Set via CLAUDE_TIER env var: "pro" (default), "max5x", "max20x"
# Limits are approximate output-token equivalents for the rolling windows.
# Users should adjust these if Anthropic changes the actual limits.
TIER_LIMITS = {
    "pro":    {"limit_5h": 800_000,   "limit_weekly": 4_000_000},
    "max5x":  {"limit_5h": 4_000_000, "limit_weekly": 20_000_000},
    "max20x": {"limit_5h": 16_000_000, "limit_weekly": 80_000_000},
}
CLAUDE_TIER = os.environ.get("CLAUDE_TIER", "max5x").lower()

# ── Token pricing (USD per 1M tokens) — Opus 4 defaults ──
TOKEN_PRICE_INPUT = 15.0
TOKEN_PRICE_OUTPUT = 75.0
TOKEN_PRICE_CACHE_READ = 1.5
TOKEN_PRICE_CACHE_CREATION = 18.75

# ── Refresh ──
REFRESH_INTERVAL_SEC = 10
```

## Constraints

- [ ] Python 3.9+: no `match` statements, no `X | Y` union syntax without `from __future__ import annotations`.
- [ ] Zero external dependencies: stdlib only (`curses`, `json`, `pathlib`, `os`, `re`, `time`, `datetime`, `shutil`, `typing`, `subprocess`).
- [ ] Modular structure: `tui.py` + `data/` (6 files) + `screens/` (7 files) + `vault.py`. Each file 200-400 lines max.
- [ ] Standalone: no imports from claude-glean's `server.py`.
- [ ] Vault path from environment: `CLAUDE_VAULT_PATH` env var, default `~/Documents/vault/`.
- [ ] Tier from environment: `CLAUDE_TIER` env var (`pro`, `max5x`, `max20x`), default `max5x`.
- [ ] Minimum terminal: 80x24.
- [ ] Color degradation: works in monochrome terminals.
- [ ] Path safety: reject `..` in all user-facing path inputs.
- [ ] Read-only default: all deletes require `d` + `y` confirmation.
- [ ] Auto-refresh: Home and Usage screens every 10 seconds. X-ray for active sessions every 10 seconds. Other screens on `r` key only.
- [ ] macOS (Darwin) primary target. Linux compatible.

## Acceptance Criteria

- [ ] `python3 tui.py` launches full-screen curses app showing Home screen with usage bars and remaining capacity.
- [ ] 5h/weekly usage shows remaining tokens and % based on configured tier.
- [ ] `CLAUDE_TIER=pro python3 tui.py` shows Pro tier limits; `CLAUDE_TIER=max20x` shows Max 20x limits.
- [ ] Home screen session table shows context % with color-coded bars and compact/handoff recommendations.
- [ ] Home and Usage screens auto-refresh every 10 seconds.
- [ ] All 5 screens accessible and display correct data.
- [ ] `d` on a user skill → delete with confirmation → list updates.
- [ ] `d` on a plugin → deletes plugin + cache + enabledPlugins + plugin hooks from settings.json.
- [ ] `d` on a plugin-sourced skill → shows "cannot delete" message.
- [ ] Vault browser lists all subdirectories dynamically from `CLAUDE_VAULT_PATH`.
- [ ] Vault search returns matching notes across subdirectories.
- [ ] Terminal resize handled gracefully.
- [ ] No crashes on empty/missing `~/.claude/` directory.
- [ ] Clean exit on `q`.

## Test Criteria

- [ ] **Usage: 5h window aggregation**: Given 3 JSONL files with known timestamps / When `get_usage_stats()` / Then `window_5h.total_tokens` equals sum of tokens within last 5 hours only.
- [ ] **Usage: remaining capacity**: Given tier=max5x (limit_5h=4M) and 5h total=1M / Then `remaining_tokens`=3M and `usage_pct`=25.0.
- [ ] **Usage: tier switching**: Given `CLAUDE_TIER=pro` / Then limits are 800k/4M. Given `max20x` / Then 16M/80M.
- [ ] **Usage: weekly aggregation**: Given entries spanning 10 days / Then weekly only includes last 7 days.
- [ ] **Usage: cost calculation**: Given 100k input + 50k output / Then cost ≈ $1.50 + $3.75 = $5.25.
- [ ] **Usage: per-session breakdown**: Given 2 sessions in last 5h / Then `per_session` has 2 entries sorted by total desc.
- [ ] **Home: session context bars**: Given session at 78% / Then bar ~78% filled, orange.
- [ ] **Components: sub-tab switching**: Given Skills active / When right arrow / Then Agents active.
- [ ] **Components: plugin delete cleanup**: Given plugin "test" / When deleted / Then installed_plugins.json + cache + enabledPlugins + hooks all cleaned.
- [ ] **X-ray: auto-refresh active session**: Given active session X-ray / When 10s pass / Then display updates.
- [ ] **Vault: dynamic dirs**: Given 7 subdirectories / Then all 7 listed.
- [ ] **Vault: env var path**: Given `CLAUDE_VAULT_PATH=/tmp/test` / Then reads from `/tmp/test/`.
- [ ] **Vault: path traversal**: Given `../../etc/passwd` / Then rejected.
- [ ] **Resize: graceful**: 120x40 → 80x24 redraws correctly.
- [ ] **Resize: too small**: 60x15 → shows "Terminal too small".

## Out of Scope (v1)

- Web server / SSE streaming
- Session JSONL raw viewer
- Vault note editing
- Projects/Instructions/Forks tabs
- Mouse support
- Configuration file (env vars + constants only)
- Windows support

## Discovered Patterns (from server.py)

- **JSONL entry structure**: `{"type":"assistant", "timestamp": ms, "usage": {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"}}`.
- **Source tagging**: `"source": "user"` vs `"source": "plugin:{name}"` — consistent across skills, agents, hooks, connectors.
- **Delete safety**: Check `..` in paths, verify not in plugin cache, block active session deletion.
- **Frontmatter parsing**: `key: value` and `key: [a, b, c]` YAML subset. Lowercases keys.
- **Token formatting**: `318029` → `"318k"`, `1200000` → `"1.2M"`.
- **Plugin key format**: `"name@scope"` in `installed_plugins.json`.

## Separate Work: server.py (claude-glean)

`delete_plugin()` should also be added to `claude-glean/server.py` as `/api/delete-plugin` POST handler. Not part of this spec's deliverables — tracked separately.
