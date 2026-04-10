"""
Session management: active processes, activity history, session detail, and X-ray.

All data comes from ``~/.claude/`` -- ps aux for live processes, history.jsonl
for recent commands, and per-project JSONL files for session metadata / token usage.
"""
from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .types import ActivityResult, SessionDetailResult, SessionsResult, SessionXray

from .common import CLAUDE_DIR, decode_project_path, format_tokens, parse_timestamp_ms, read_json, read_text, ttl_cache


# ── Public API ────────────────────────────────────────────────────────────────

@ttl_cache(10)  # ps aux doesn't change faster than 10s
def get_sessions() -> SessionsResult:
    """Active claude processes for the current user (via ``ps aux``)."""
    sessions: List[Dict[str, Any]] = []
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError):
        user = os.environ.get("USER", "unknown")

    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"sessions": sessions}

    # Header: USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND
    for line in result.stdout.splitlines()[1:]:
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        ps_user, pid_str = parts[0], parts[1]
        tty, stat, start = parts[6], parts[7], parts[8]
        command = parts[10]

        if ps_user != user:
            continue

        cmd_lower = command.lower()
        if "claude" not in cmd_lower:
            continue
        # Exclude auxiliary processes
        skip_patterns = [
            "mcp-server", "server.py", "claude-glean", "node ",
            "bridge", "/bin/bash", "cwd",
        ]
        if any(s in cmd_lower for s in skip_patterns):
            continue

        pid = int(pid_str)

        # Map process state
        if stat.startswith("T"):
            state = "stopped"
        elif stat.startswith("R"):
            state = "running"
        else:
            state = "active"

        # Get cwd (Linux /proc; macOS lsof fallback)
        cwd = ""
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except (FileNotFoundError, PermissionError, OSError):
            if sys.platform == "darwin":
                try:
                    lsof = subprocess.run(
                        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                        capture_output=True, text=True, timeout=5,
                    )
                    for lsof_line in lsof.stdout.splitlines():
                        if lsof_line.startswith("n"):
                            cwd = lsof_line[1:]
                            break
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    pass

        sessions.append({
            "pid": pid,
            "state": state,
            "tty": tty,
            "started": start,
            "command": command.split("/")[-1] if "/" in command else command,
            "cwd": cwd,
        })

    return {"sessions": sessions}


@ttl_cache(30)  # activity doesn't change every second
def get_activity() -> ActivityResult:
    """Today's command count + recent activity summary from ``history.jsonl``."""
    history_path = CLAUDE_DIR / "history.jsonl"
    if not history_path.is_file():
        return {"today_count": 0, "recent": []}

    now_ms = int(time.time() * 1000)
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_ms = int(today_start.timestamp() * 1000)

    # Stream for today count (avoids loading entire file into memory)
    today_count = 0
    try:
        with open(history_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    ts = entry.get("timestamp", 0)
                    if ts >= today_start_ms:
                        today_count += 1
                except Exception:
                    continue
    except (FileNotFoundError, PermissionError, OSError):
        pass

    # Recent entries from tail
    recent: List[Dict[str, Any]] = []
    tail = _read_last_n_lines(history_path, 50)
    for line in reversed(tail):
        if len(recent) >= 10:
            break
        try:
            entry = json.loads(line)
            ts = entry.get("timestamp", 0)
            display = entry.get("display", "").strip()
            if not display:
                continue
            project = entry.get("project", "")
            project_name = Path(project).name if project else ""
            age_seconds = max(0, (now_ms - ts) // 1000) if ts else 0
            recent.append({
                "text": display[:100] + ("..." if len(display) > 100 else ""),
                "project": project_name,
                "ageSeconds": int(age_seconds),
            })
        except Exception:
            continue

    return {"today_count": today_count, "recent": recent}


@ttl_cache(15)  # session detail is expensive (~110ms), cache 15s
def get_session_detail() -> SessionDetailResult:
    """Detailed info for all sessions (active + history).

    Returns per-session: session_id, project_name, message_count,
    is_active, context_tokens, context_pct, etc.
    """
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.is_dir():
        return {"sessions": []}

    # Collect active session IDs via running PIDs
    active_session_ids = _get_active_session_ids()
    sessions: List[Dict[str, Any]] = []

    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        for jsonl_file in proj_dir.glob("*.jsonl"):
            if "subagent" in str(jsonl_file):
                continue

            try:
                session_id = jsonl_file.stem
                decoded_path = decode_project_path(proj_dir.name)
                project_name = Path(decoded_path).name or proj_dir.name

                # Single read — slice for slug/timestamp and token usage
                last_500 = _read_last_n_lines(jsonl_file, 500)
                last_5 = last_500[-5:]

                # Slug / custom title from tail
                slug = ""
                last_timestamp: Any = ""
                for line in reversed(last_5):
                    try:
                        entry = json.loads(line)
                        if not slug and entry.get("slug"):
                            slug = entry["slug"]
                        if not last_timestamp and entry.get("timestamp"):
                            last_timestamp = entry["timestamp"]
                    except Exception:
                        continue

                # Message count (sample last 500 lines)
                message_count = 0
                for line in last_500:
                    try:
                        entry = json.loads(line)
                        if entry.get("type") in ("user", "assistant"):
                            message_count += 1
                    except Exception:
                        continue

                # Token usage from last assistant message
                last_20 = last_500[-20:]
                token_data = _extract_token_usage(last_20)
                context_tokens = token_data.get("context_tokens", 0)
                context_max = 1_000_000
                context_pct = round(context_tokens / context_max * 100) if context_max > 0 else 0

                is_active = session_id in active_session_ids

                sessions.append({
                    "session_id": session_id,
                    "slug": slug,
                    "project_name": project_name,
                    "message_count": message_count,
                    "is_active": is_active,
                    "context_tokens": context_tokens,
                    "context_pct": context_pct,
                    "last_timestamp": last_timestamp,
                })
            except Exception:
                continue

    # Sort: active first, then by last_timestamp descending
    sessions.sort(key=lambda x: (
        not x["is_active"],
        -parse_timestamp_ms(x["last_timestamp"]),
    ))

    return {"sessions": sessions}


def get_session_xray(session_id: str) -> SessionXray:
    """Context breakdown for a single session.

    Reads actual token usage (``cache_read_input_tokens``) from the JSONL,
    estimates breakdown by component, and produces a recommendation.
    """
    if not session_id:
        return {"error": "session_id required"}

    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.is_dir():
        return {"error": "projects dir not found"}

    # Find the JSONL file
    jsonl_path: Optional[Path] = None
    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        candidate = proj_dir / f"{session_id}.jsonl"
        if candidate.is_file():
            jsonl_path = candidate
            break

    if not jsonl_path:
        return {"error": f"session {session_id} not found"}

    # Real token usage from tail
    last_20 = _read_last_n_lines(jsonl_path, 20)
    token_data = _extract_token_usage(last_20)

    context_max = 1_000_000
    context_tokens = token_data.get("context_tokens", 0)
    context_pct = round(context_tokens / context_max * 100) if context_max > 0 else 0

    # Compact counting (stream line-by-line to avoid loading full file into memory)
    compacts_total = 0
    last_compact_ts = None
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                try:
                    entry = json.loads(raw_line)
                except Exception:
                    continue
                if entry.get("type") == "summary":
                    compacts_total += 1
                    last_compact_ts = entry.get("timestamp")
    except (FileNotFoundError, PermissionError, OSError):
        pass

    # Messages since last compact (last 500 lines)
    last_500 = _read_last_n_lines(jsonl_path, 500)
    messages_since_compact = 0
    for line in reversed(last_500):
        try:
            entry = json.loads(line)
        except Exception:
            continue
        entry_type = entry.get("type", "")
        if entry_type == "summary":
            break
        if entry_type in ("user", "assistant"):
            messages_since_compact += 1

    # ── Breakdown estimate ────────────────────────────────────────────
    autocompact_buffer = 33_000
    system_tokens = 15_000

    # CLAUDE.md + memory files
    memory_tokens = 0
    claude_md = CLAUDE_DIR / "CLAUDE.md"
    if claude_md.is_file():
        try:
            memory_tokens += claude_md.stat().st_size // 4
        except OSError:
            pass
    memory_dir = CLAUDE_DIR / "projects"
    if memory_dir.is_dir():
        try:
            for md in memory_dir.rglob("memory/*.md"):
                memory_tokens += md.stat().st_size // 4
        except OSError:
            pass

    # Agents (~35 tokens per definition)
    agent_tokens = 0
    agents_dir = CLAUDE_DIR / "agents"
    if agents_dir.is_dir():
        try:
            agent_tokens = sum(35 for _ in agents_dir.glob("*.md"))
        except OSError:
            pass

    # Skills (~22 tokens per definition)
    skill_tokens = 0
    skills_dir = CLAUDE_DIR / "skills"
    if skills_dir.is_dir():
        try:
            skill_tokens = sum(
                22 for d in skills_dir.iterdir()
                if d.is_dir() and (d / "SKILL.md").is_file()
            )
        except OSError:
            pass

    overhead = system_tokens + memory_tokens + agent_tokens + skill_tokens
    message_tokens = max(0, context_tokens - overhead)
    free_tokens = max(0, context_max - context_tokens - autocompact_buffer)

    breakdown = [
        {"name": "System (prompt + tools)", "tokens": system_tokens},
        {"name": "Memory files", "tokens": memory_tokens},
        {"name": "Custom agents", "tokens": agent_tokens},
        {"name": "Skills", "tokens": skill_tokens},
        {"name": "Messages", "tokens": message_tokens},
        {"name": "Free space", "tokens": free_tokens},
        {"name": "Autocompact buffer", "tokens": autocompact_buffer},
    ]
    for b in breakdown:
        b["pct"] = round(b["tokens"] / context_max * 100, 1)
        b["display"] = format_tokens(b["tokens"])

    # Recommendation
    if context_pct > 80:
        recommendation = f"Context nearly full ({context_pct}%). Use /compact or /handoff immediately"
    elif context_pct > 60:
        recommendation = f"Context getting large ({context_pct}%). Consider /compact soon"
    elif context_pct > 40:
        recommendation = f"Context moderate ({context_pct}%). Healthy for now"
    else:
        recommendation = f"Context healthy ({context_pct}%)"

    return {
        "session_id": session_id,
        "context_tokens": context_tokens,
        "context_max": context_max,
        "context_pct": context_pct,
        "breakdown": breakdown,
        "compacts_total": compacts_total,
        "messages_since_compact": messages_since_compact,
        "recommendation": recommendation,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _read_last_n_lines(path: Path, n: int) -> List[str]:
    """Efficiently read the last *n* lines of a file (binary seek from end)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            fsize = f.tell()
            if fsize == 0:
                return []
            # JSONL lines can be several KB each
            read_size = min(fsize, n * 5000)
            f.seek(max(0, fsize - read_size))
            data = f.read().decode("utf-8", errors="replace")
            lines = data.splitlines()
            return lines[-n:] if len(lines) > n else lines
    except (FileNotFoundError, PermissionError, OSError):
        return []


def _extract_token_usage(lines: List[str]) -> Dict[str, int]:
    """Extract ``cache_read_input_tokens`` from the last assistant entry.

    Returns dict with ``context_tokens`` (and related counters) or empty dict.
    """
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if entry.get("type") != "assistant":
            continue
        usage = entry.get("usage")
        if not isinstance(usage, dict):
            msg = entry.get("message")
            if isinstance(msg, dict):
                usage = msg.get("usage")
        if isinstance(usage, dict) and "cache_read_input_tokens" in usage:
            return {
                "context_tokens": usage["cache_read_input_tokens"],
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            }
    return {}


def _get_active_session_ids() -> Set[str]:
    """Return set of session IDs that belong to currently running claude processes."""
    active_pids: Set[int] = set()
    for s in get_sessions().get("sessions", []):
        if s["state"] != "stopped":
            active_pids.add(s["pid"])

    active_ids: Set[str] = set()
    sessions_dir = CLAUDE_DIR / "sessions"
    if not sessions_dir.is_dir():
        return active_ids

    for sf in sessions_dir.glob("*.json"):
        try:
            text = read_text(sf)
            if not text:
                continue
            sd = json.loads(text)
            try:
                pid = int(sd.get("pid", 0))
            except (TypeError, ValueError):
                continue
            sid = sd.get("sessionId")
            if pid in active_pids and sid:
                active_ids.add(sid)
        except Exception:
            continue

    return active_ids
