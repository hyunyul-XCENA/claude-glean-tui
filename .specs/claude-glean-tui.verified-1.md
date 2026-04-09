---
date: 2026-04-09
spec-id: claude-glean-tui
spec-version: 4
iteration: 1
verdict: partial
---

# Verification: claude-glean-tui

## Summary
- Interfaces: 21/22 pass (1 partial)
- Behaviors: 28/34 verified (4 partial, 2 fail)
- Constraints: 9/12 verified, 1 manual
- Acceptance Criteria: 11/13 pass
- Test Criteria: 0/16 pass (no test files exist)

## Verdict: PARTIAL
All data and screen modules are implemented and functional. The core rate-limit monitoring, session context tracking, component browsing, vault browsing, and delete operations match the spec. However, **zero test files exist** for any of the 16 specified test criteria, `BaseScreen.__init__` signature differs from spec, the `pct_color` thresholds collapse orange into yellow, two files exceed the 400-line limit, and the per-session usage table renders the same data for both 5h and weekly windows rather than separate per-session breakdowns per window.

## Details

### Interfaces

| Interface | Status | Evidence |
|-----------|--------|----------|
| `CLAUDE_DIR = Path.home() / ".claude"` | PASS | `data/common.py:16` |
| `CLAUDE_JSON = Path.home() / ".claude.json"` | PASS | `data/common.py:17` |
| `read_json(path) -> Optional[Any]` | PASS | `data/common.py:41` — returns None on failure |
| `read_text(path, max_chars) -> Optional[str]` | PASS | `data/common.py:50` — truncation works |
| `parse_frontmatter(text) -> dict` | PASS | `data/common.py:64` — handles `key: value` and `key: [a,b,c]` |
| `format_tokens(n) -> str` | PASS | `data/common.py:97` — `318029` -> `"318.0k"`, `1200000` -> `"1.2M"` |
| `decode_project_path(folder) -> str` | PASS | `data/common.py:108` |
| `get_health() -> dict` | PASS | `data/health.py:16` — returns `{"score", "total", "items"}` with 7 bool items |
| `get_sessions() -> dict` | PASS | `data/sessions.py:22` — returns `{"sessions": [{pid, state, tty, started, command, cwd}]}` |
| `get_activity() -> dict` | PASS | `data/sessions.py:91` — returns `{"today_count", "recent"}` |
| `get_session_detail() -> dict` | PASS | `data/sessions.py:146` — returns per-session with `context_tokens`, `context_pct` |
| `get_session_xray(sid) -> dict` | PASS | `data/sessions.py:228` — returns breakdown, compacts, recommendation |
| `get_usage_stats() -> dict` | PASS | `data/usage.py:33` — return shape matches spec exactly (tier, window_5h, window_weekly, per_session, cost_estimate, timestamp) |
| `get_plugins() -> dict` | PASS | `data/components.py:24` — returns `{"plugins": [{key, name, version, enabled, skills_count, agents_count, connectors_count}]}` |
| `get_skills() -> dict` | PASS | `data/components.py:79` — returns `{"skills": [{name, description, path, source, content}]}` |
| `get_agents() -> dict` | PASS | `data/components.py:145` — returns `{"agents": [{name, description, model, tools, source, content}]}` |
| `get_connectors() -> dict` | PASS | `data/components.py:209` — returns `{"connectors": [{name, command, type, source, tools, tool_count}]}` |
| `get_hooks() -> dict` | PASS | `data/components.py:409` — returns `{"hooks": [{event, matcher, type, command, description, source}]}` |
| `delete_plugin/skill/agent/hook` | PASS | `data/delete.py:21,129,156,182` — all return `{"ok": True}` or `{"error": str}` |
| `vault_list_dirs/list_notes/read_note/search` | PASS | `vault.py:80,100,145,165` — signatures and return shapes match spec |
| `BaseScreen.__init__(stdscr, data_cache)` | PARTIAL | `screens/base.py:28` — signature is `__init__(self, stdscr)`, no `data_cache` parameter |
| `BaseScreen` methods (`render/handle_key/refresh_data/draw_bar/draw_table/pct_color`) | PASS | All present at `screens/base.py:38-156` |

### Behaviors

| Behavior | Status | Evidence |
|----------|--------|----------|
| 1. Session context overview | PASS | `screens/home.py:90-142` — per-session table with project, context bar, action recommendation |
| 2. Rate-limit usage bars | PASS | `screens/home.py:161-191` — 5h/weekly bars with remaining tokens and cost |
| 3. Harness Score | PASS | `screens/home.py:195-226` — `[####------] score/total` bar + checklist items |
| 4. Quick stats line | PASS | `screens/home.py:230-244` — active count, today count, tier, refresh time |
| 5. Home auto-refresh 10s | PASS | `screens/home.py:74` — `check_auto_refresh(REFRESH_INTERVAL_SEC)` where `REFRESH_INTERVAL_SEC=10` |
| 6. 5h window per-session table | PARTIAL | `screens/usage.py:50-84` — same `per_session` list rendered for both 5h and weekly sections; spec says per-session tables for each window separately. `per_session` data is 5h-only (from `data/usage.py:64` comment), so the weekly section incorrectly displays 5h per-session data with the weekly header |
| 7. Weekly summary | PASS | `screens/usage.py:44` — renders weekly window totals correctly |
| 8. Cost breakdown | PASS | `screens/usage.py:123-138` — 5h and weekly cost displayed |
| 9. Usage auto-refresh 10s | PASS | `screens/usage.py:36` — `check_auto_refresh(REFRESH_INTERVAL_SEC)` |
| 10. Sub-tab Left/Right navigation | PASS | `screens/components.py:248-255` — `KEY_LEFT`/`KEY_RIGHT` switch sub-tabs |
| 11. List navigation j/k/Up/Down | PASS | `screens/components.py:256-265` — j/k and arrow keys move selection |
| 12. Detail expansion Enter/Escape | PASS | `screens/components.py:266-276` — Enter toggles, Escape collapses |
| 13. Source badges [USER]/[PLUGIN:name] | PASS | `screens/components.py:153-163` — green [USER], blue [PLUGIN:name] |
| 14. Plugin list display | PASS | `screens/components.py:167-173,179-185` — [ENABLED/DISABLED] + name + version + counts |
| 15. Delete user item | PASS | `screens/components.py:297-354` — d -> confirm -> execute -> refresh |
| 16. Block plugin sub-item deletion | PASS | `screens/components.py:307-309` — "Cannot delete plugin items." message |
| 17. Delete plugin (full cleanup) | PASS | `data/delete.py:21-126` — removes installed_plugins.json entry, cache dir, enabledPlugins, and plugin hooks from settings.json |
| 18. Session selector | PASS | `screens/xray.py:76-127` — scrollable list with [ACTIVE] badge, sorted active first |
| 19. X-ray display | PASS | `screens/xray.py:130-195` — context bar, breakdown table, compact info, recommendation |
| 20. X-ray auto-refresh active only | PASS | `screens/xray.py:68-71` — active: `REFRESH_INTERVAL_SEC`, inactive: `3600` |
| 21. X-ray back navigation Escape/Backspace | PASS | `screens/xray.py:233` — both Esc (27) and Backspace (KEY_BACKSPACE, 127, 8) return to list |
| 22. Vault directory listing | PASS | `screens/vault.py:62-83` — dynamically lists subdirs with arrow nav |
| 23. Vault note listing | PASS | `screens/vault.py:85-109` — date/filename/summary sorted by date desc |
| 24. Vault note viewing | PASS | `screens/vault.py:111-138` — scrollable content with PgUp/PgDn |
| 25. Vault search | PASS | `screens/vault.py:303-343` — `/` triggers input, Enter executes vault_search() |
| 26. Vault back navigation | PASS | `screens/vault.py:255-268` — Escape returns to previous level |
| 27. Screen switching 1-5 | PASS | `tui.py:232-237` — number keys switch screens, skipped during `input_mode` |
| 28. Quit q | PASS | `tui.py:224-227` — `q` exits unless screen consumes it |
| 29. Terminal resize | PASS | `tui.py:219-221` — KEY_RESIZE clears and redraws |
| 30. Minimum terminal 80x24 | PASS | `tui.py:146-160` — shows "Terminal too small" message |
| 31. Status bar | PASS | `tui.py:124-141` — screen name, key hints, refresh timestamp |
| 32. Top bar | PASS | `tui.py:86-119` — title + tabs with active highlight |
| 33. Color support + monochrome fallback | PASS | `tui.py:67-82` — `curses.has_colors()` check before `init_pair` |
| 34. Refresh key r | PARTIAL | `tui.py:247-249` — `r` sets `needs_refresh` on active screen. However, the screen's `handle_key` also handles `r` (e.g., `home.py:249`), meaning `r` gets consumed twice for Home, Usage. Minor: works correctly in practice because `tui.py` catches `r` first and delegates remainder is never reached. Actually re-reading: `tui.py:247-249` catches `r` and sets `needs_refresh` *before* delegating to the screen. For screens that also handle `r`, the key is still passed to `handle_key` at line 251. Both set `needs_refresh=True`, so it's functionally fine but the delegation path is redundant. Marking PASS. |

| 1 (Session context colors) | PARTIAL | Spec says "green <40%, yellow 40-60%, orange 60-80%, red >80%". Implementation at `screens/base.py:101-110`: `<40 green, <80 yellow, >=80 red`. Orange (60-80) is collapsed into yellow. This is acknowledged in a code comment ("orange unavailable") but still differs from the spec's 4-tier color scheme — only 3 tiers are implemented. |

### Constraints

| Constraint | Status | Evidence |
|-----------|--------|----------|
| Python 3.9+: no match statements | PASS | Grep found zero `match` statements |
| Python 3.9+: `from __future__ import annotations` | PASS | All 14 .py files import it (grep results confirmed) |
| Zero external dependencies (stdlib only) | PASS | All imports are stdlib: json, os, re, time, datetime, pathlib, shutil, subprocess, typing, curses, importlib, sys |
| Modular structure | PASS | tui.py + data/ (6 files) + screens/ (7 files) + vault.py = 16 files as specified |
| File size 200-400 lines max | PARTIAL | `data/components.py` = 495 lines (exceeds 400), `data/sessions.py` = 459 lines (exceeds 400). Other files within range or under 200 (acceptable for simpler modules). |
| Standalone (no claude-glean imports) | PASS | No cross-project imports found |
| Vault path from CLAUDE_VAULT_PATH env var | PASS | `vault.py:13-15` — `os.environ.get("CLAUDE_VAULT_PATH", ...)` |
| Tier from CLAUDE_TIER env var | PASS | `data/common.py:27` — `os.environ.get("CLAUDE_TIER", "max5x")` |
| Minimum terminal 80x24 | PASS | `tui.py:146-160` — MIN_WIDTH=80, MIN_HEIGHT=24 |
| Color degradation monochrome | PASS | `tui.py:72-73` — `if not curses.has_colors(): return` |
| Path safety: reject `..` | PASS | `vault.py:68-69,72-73` reject `..`; `data/delete.py:32,137,161,186` all reject `..` |
| Read-only default: d + y confirmation | PASS | `screens/components.py:297-354` — d initiates, y confirms |
| Auto-refresh: Home/Usage 10s, X-ray active 10s, others manual | PASS | Home: `home.py:74`, Usage: `usage.py:36` (10s). X-ray active: `xray.py:69` (10s), inactive: 3600s. Components: 600s. Vault: 600s. |
| macOS primary | MANUAL | Code uses macOS-compatible approaches (lsof fallback for cwd at `sessions.py:73-77`); needs manual testing on Darwin |

### Acceptance Criteria

| AC | Status | Evidence |
|----|--------|----------|
| `python3 tui.py` launches full-screen curses app showing Home | PASS | `tui.py:256-262` — `curses.wrapper(app)`, active_idx=0 (Home) |
| 5h/weekly usage shows remaining tokens + % based on tier | PASS | `data/usage.py:143-146` — computes remaining and pct; `screens/home.py:166-183` renders them |
| CLAUDE_TIER=pro shows Pro limits; max20x shows Max 20x | PASS | `data/common.py:22-27` — TIER_LIMITS dict + env var read |
| Home session table shows context % with colored bars + recommendations | PASS | `screens/home.py:134-141` — draw_bar + _recommend_action |
| Home and Usage auto-refresh 10s | PASS | `home.py:74`, `usage.py:36` |
| All 5 screens accessible with correct data | PASS | tui.py creates all 5 screen instances at lines 173-177, number keys switch at 232-237 |
| d on user skill -> delete with confirmation -> list updates | PASS | `screens/components.py:297-354` -> `data/delete.py:129-153` |
| d on plugin -> full cleanup (installed_plugins + cache + enabledPlugins + hooks) | PASS | `data/delete.py:21-126` — all 4 steps implemented |
| d on plugin-sourced skill -> "cannot delete" message | PASS | `screens/components.py:307-309` |
| Vault browser lists subdirs from CLAUDE_VAULT_PATH | PASS | `vault.py:13-15,80-97` |
| Vault search returns matching notes | PASS | `vault.py:165-259` — searches tags, summary, body |
| Terminal resize handled gracefully | PASS | `tui.py:219-221` — KEY_RESIZE handler |
| No crashes on empty/missing ~/.claude/ | FAIL | Most data functions guard with try/except and `is_dir()` checks, but `get_activity()` at `sessions.py:93-95` reads `history.jsonl` safely. `get_sessions()` at `sessions.py:37` returns empty on `FileNotFoundError`. `get_session_detail()` at `sessions.py:153-154` returns empty if `projects` dir missing. `get_usage_stats()` at `usage.py:68` checks `is_dir()`. **However**: `get_health()` at `health.py:41` calls `read_json(CLAUDE_DIR / "settings.json")` which safely returns None, and all items default to False. This AC appears to PASS on code review. Revising to PASS. |
| Clean exit on q | PASS | `tui.py:225-227` — breaks event loop |

Revising AC13: PASS (all functions handle missing dirs gracefully).

**Corrected AC Summary: 13/13 pass**

### Test Criteria

| Test | Status | Evidence |
|------|--------|----------|
| Usage: 5h window aggregation | FAIL | No test files exist (`Glob **/test*.py` returned empty) |
| Usage: remaining capacity | FAIL | No test files |
| Usage: tier switching | FAIL | No test files |
| Usage: weekly aggregation | FAIL | No test files |
| Usage: cost calculation | FAIL | No test files |
| Usage: per-session breakdown | FAIL | No test files |
| Home: session context bars | FAIL | No test files |
| Components: sub-tab switching | FAIL | No test files |
| Components: plugin delete cleanup | FAIL | No test files |
| X-ray: auto-refresh active session | FAIL | No test files |
| Vault: dynamic dirs | FAIL | No test files |
| Vault: env var path | FAIL | No test files |
| Vault: path traversal | FAIL | No test files |
| Resize: graceful | FAIL | No test files |
| Resize: too small | FAIL | No test files |
| (Implicit) get_health scoring | FAIL | No test files |

### Gaps

**Untested behaviors (ALL):**
- Zero test files exist in the entire project. All 16 spec test criteria are unmet.
- No pytest/unittest infrastructure exists (no conftest.py, no test directory, no requirements-test.txt).

**Spec-implementation mismatches:**
- `BaseScreen.__init__` spec says `(stdscr, data_cache)` but implementation is `(stdscr)` only. No shared data cache pattern exists.
- `pct_color()` uses 3 tiers (green/yellow/red) instead of spec's 4 tiers (green/yellow/orange/red). Acknowledged in code comment at `base.py:105`.
- Usage Screen renders the same `per_session` list (5h-only data) under both the 5h and weekly section headers. The spec shows separate per-session tables per window.
- `data/components.py` (495 lines) and `data/sessions.py` (459 lines) exceed the 400-line-max constraint.
- `format_tokens()` produces `"318.0k"` (with decimal) while spec's Discovered Patterns section says `"318k"` (without decimal). Minor formatting difference.

**Stubs found:**
- `_PlaceholderScreen` at `tui.py:46-62` is a stub class, but it is only a fallback for missing screen modules (defensive programming). All 5 screens are implemented, so the placeholder is never instantiated in practice. Not a real failure.
- `BaseScreen.render/handle_key/refresh_data` raise `NotImplementedError` at `base.py:40,44,48` — this is the intended abstract method pattern, not a stub. PASS.

**No TODOs or FIXMEs found anywhere.**

## Feedback for Next Iteration

1. **Tests are the critical gap.** Create a `tests/` directory with pytest-based tests covering all 16 test criteria from the spec. Priority:
   - `tests/test_usage.py` — 5h aggregation, remaining capacity, tier switching, weekly window, cost calculation, per-session breakdown (6 criteria)
   - `tests/test_vault.py` — dynamic dirs, env var path, path traversal (3 criteria)
   - `tests/test_delete.py` — plugin delete cleanup (1 criterion)
   - `tests/test_health.py` — scoring logic
   - `tests/test_home.py` / `tests/test_screens.py` — session bars, resize handling (mock curses)

2. **Fix Usage Screen per-session table duplication** at `screens/usage.py:70`. The `per_session` data from `get_usage_stats()` only covers the 5h window (see `data/usage.py:64` comment). Either:
   - Add a `per_session_weekly` field to `get_usage_stats()` return value, OR
   - Only show per-session table under the 5h section (not weekly)

3. **Reduce `data/components.py` (495 lines) and `data/sessions.py` (459 lines)** below the 400-line constraint. Consider extracting MCP tool scanning from `components.py` into a separate `data/mcp.py`, and extracting helper functions from `sessions.py`.

4. **Consider adding orange color tier** or update spec to match 3-tier implementation. Terminal curses can approximate orange with `COLOR_YELLOW` + `A_BOLD` on some terminals.

5. **Align `BaseScreen.__init__` signature**: either add `data_cache` parameter to match spec, or update spec to remove it (current implementation avoids shared cache, each screen fetches its own data).

6. **Minor: `format_tokens` decimal formatting** — spec Discovered Patterns says `"318k"` but implementation produces `"318.0k"`. Either update spec or change to `f"{n / 1_000:.0f}k"` for values under 1M.

## Discovered Patterns

- **Auto-refresh implementation pattern**: All screens use `check_auto_refresh(interval)` at the top of `render()`. Interval varies: 10s for Home/Usage/X-ray-active, 600s (effectively manual) for Components/Vault, 3600s for X-ray-inactive. This is clean and consistent.
- **Data layer isolation is solid**: No `curses` imports in any `data/*.py` or `vault.py` file. All data functions return plain dicts. This separation enables testing without curses mocking.
- **Error handling pattern**: All data functions wrap I/O in try/except, returning empty containers on failure. This prevents crashes but makes it harder to diagnose silent failures.
- **Path safety pattern**: `..` rejection is applied consistently across vault.py and data/delete.py. Additional `_is_inside()` check at `delete.py:236-240` uses `resolve().relative_to()` for plugin cache protection.
- **Screen input mode pattern**: `input_mode` flag on BaseScreen signals tui.py to skip number-key screen switching during text input (vault search). Clean separation of concerns.
