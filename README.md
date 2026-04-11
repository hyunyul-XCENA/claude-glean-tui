# claude-glean-tui

A terminal dashboard for monitoring Claude Code — usage, context, components, and vault — without leaving the terminal.

```
┌─ Claude Glean TUI ──────────────[1:Home] [2:Components] [3:X-ray]────────────┐
│                                                                                │
│  ── Active Sessions ───────────────────────────────────────────────────         │
│  Session                       Context          Action                         │
│  claude-glean-tui (PID 1234) 42% ████░░░░░░  ~ monitor                        │
│  pimp-my-claude   (PID 5678) 78% ████████░░  ⚠ /compact soon                  │
│                                                                                │
│  ── Usage (live) ──────────────────────────────────────────────────────         │
│  5h Window:  ██████░░░░░░░░░░░░░░ 31%  used  resets 04:00                      │
│  Weekly:     ███░░░░░░░░░░░░░░░░░ 13%  used                                   │
│                                                                                │
│  ── Harness Score ─────────────────────────────────────────────────────         │
│  [██████████] 100/100                                                          │
│  ✓ claude_md  ✓ permissions  ✓ hooks  ✓ agents  ✓ skills  ✓ connectors         │
│                                                                                │
│ [Home]  q:Quit  r:Refresh  /:Search  d:Delete          Last refresh: 21:45:30  │
└────────────────────────────────────────────────────────────────────────────────┘
```

## Why

- **`/usage` doesn't auto-refresh.** You have to type it every time to check your quota.
- **Context window is invisible** until you type `/context` or it auto-compacts.
- **Scattered workspace data** — plugins, skills, agents, hooks spread across `~/.claude/`.
- **No terminal-native dashboard** — the web version (claude-glean) requires a browser.

## Quick Start

```bash
git clone https://github.com/hyunyul-XCENA/claude-glean-tui.git
cd claude-glean-tui
bash setup.sh        # configures Claude Code statusline integration
python3 tui.py       # launch the dashboard
```

- Python 3.9+ (stdlib only, zero dependencies)
- `jq` required (for statusline data parsing)

## How It Works

Claude Code's `statusLine` feature pipes live usage data (rate limits, context window, cost) into a shell script every few seconds. Our `setup.sh` adds a one-liner to that script that dumps the data to a JSON file. The TUI reads that file — **no API calls, no authentication, no tokens.**

```
Claude Code (running) → statusline stdin → statusline.json → TUI reads
```

When Claude Code isn't running, the TUI falls back to estimated data from JSONL session files.

## Screens

### 1. Home

The at-a-glance decision screen:

- **Active sessions** with context window usage bars and recommendations (`OK` / `monitor` / `/compact soon` / `/handoff now`)
- **Rate limit bars** — 5-hour and weekly usage percentage (same data as `/usage`)
- **Harness Score** — workspace health check (7 items, 0-100)

### 2. Components

Browse and manage Claude Code components:

- **Plugins** — list, view details, delete (removes plugin + cache + hooks)
- **Skills** — USER vs PLUGIN badges, expand to view content
- **Agents** — model, tools, full definition
- **Connectors** — MCP servers (local + cloud)
- **Hooks** — event, command, matcher

Sub-tab navigation with Left/Right arrows. `d` to delete user items (with confirmation).

### 3. X-ray

Per-session context window deep dive:

- Session list with `[ACTIVE]` / `[idle]` badges
- Context breakdown: system prompt, CLAUDE.md, agents/skills, conversation
- Compact count and messages since last compact
- Delete idle sessions with `d`

## Keybindings

| Key | Action |
|-----|--------|
| `1`-`3` | Switch screens |
| `Tab` | Cycle screens |
| `j`/`k` or `↑`/`↓` | Navigate lists |
| `Enter` | Select / expand |
| `b` / `Backspace` / `Esc` | Go back |
| `d` | Delete (with confirmation) |
| `/` | Search |
| `r` | Refresh |
| `q` | Quit |

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `CLAUDE_TIER` | `max5x` | Subscription tier for estimated mode (`pro`, `max5x`, `max20x`) |

## Project Structure

```
claude-glean-tui/
├── tui.py              # Entry point + event loop
├── setup.sh            # Statusline integration setup
├── data/
│   ├── common.py       # Utilities, constants, TTL cache
│   ├── health.py       # Harness Score (7 items)
│   ├── sessions.py     # Process list, session detail, X-ray
│   ├── usage.py        # Statusline reader + JSONL fallback
│   ├── components.py   # Plugins, skills, agents, hooks
│   ├── connectors.py   # MCP server scanning
│   └── delete.py       # Delete operations (plugins, skills, agents, hooks, sessions)
└── screens/
    ├── base.py         # BaseScreen + shared rendering
    ├── home.py         # Screen 1
    ├── components.py   # Screen 2
    └── xray.py         # Screen 3
```

## Requirements

- Python 3.9+
- macOS or Linux
- Claude Code installed and running (for live usage data)
- `jq` (for statusline JSON parsing)

## License

MIT
