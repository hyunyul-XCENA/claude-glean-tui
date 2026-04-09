"""
Mutation operations: delete plugins, skills, agents, hooks.

All functions return ``{"ok": True}`` on success or ``{"error": str}``
on failure.  Every input is validated against path-traversal attacks
(``..`` is rejected).  Plugin sub-items cannot be deleted individually --
the caller must delete the entire plugin instead.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

from .common import CLAUDE_DIR, read_json


# ── Public API ────────────────────────────────────────────────────────────────

def delete_plugin(plugin_key: str) -> Dict[str, Any]:
    """Remove a plugin completely.

    1. Remove entry from ``installed_plugins.json``
    2. Delete cache directory under ``~/.claude/plugins/cache/``
    3. Remove from ``enabledPlugins`` in ``settings.json``
    4. Remove plugin hooks from ``settings.json``
    """
    if not plugin_key:
        return {"error": "plugin_key required"}
    if ".." in plugin_key:
        return {"error": "invalid plugin_key"}

    name = plugin_key.split("@")[0] if "@" in plugin_key else plugin_key
    errors: List[str] = []

    # ── 1. Remove from installed_plugins.json ──────────────────────────
    installed_path = CLAUDE_DIR / "plugins" / "installed_plugins.json"
    plugins_data = read_json(installed_path)
    if plugins_data and isinstance(plugins_data, dict):
        plugins_map = plugins_data.get("plugins", {})
        if isinstance(plugins_map, dict) and plugin_key in plugins_map:
            del plugins_map[plugin_key]
            try:
                installed_path.write_text(
                    json.dumps(plugins_data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as e:
                errors.append(f"installed_plugins.json: {e}")
    else:
        errors.append("installed_plugins.json not found or invalid")

    # ── 2. Delete cache directory ──────────────────────────────────────
    #   Cache structure: ~/.claude/plugins/cache/<org>/<name>/<version>/
    #   We find and delete any directory matching the plugin name.
    cache_dir = CLAUDE_DIR / "plugins" / "cache"
    if cache_dir.is_dir():
        deleted_cache = False
        try:
            for org_dir in cache_dir.iterdir():
                if not org_dir.is_dir():
                    continue
                for pkg_dir in org_dir.iterdir():
                    if pkg_dir.is_dir() and pkg_dir.name == name:
                        shutil.rmtree(pkg_dir)
                        deleted_cache = True
        except Exception as e:
            errors.append(f"cache cleanup: {e}")
        if not deleted_cache:
            # Not fatal -- cache may already be gone
            pass

    # ── 3. Remove from enabledPlugins in settings.json ────────────────
    settings_path = CLAUDE_DIR / "settings.json"
    try:
        settings = _read_settings(settings_path)
        if settings is not None:
            changed = False
            ep = settings.get("enabledPlugins")
            if isinstance(ep, dict) and plugin_key in ep:
                del ep[plugin_key]
                if not ep:
                    del settings["enabledPlugins"]
                changed = True
            elif isinstance(ep, list) and plugin_key in ep:
                ep.remove(plugin_key)
                if not ep:
                    del settings["enabledPlugins"]
                changed = True

            # ── 4. Remove plugin hooks ────────────────────────────────
            hooks = settings.get("hooks")
            if isinstance(hooks, dict):
                plugin_source = f"plugin:{name}"
                events_to_delete: List[str] = []
                for event, handlers in hooks.items():
                    if not isinstance(handlers, list):
                        continue
                    # Filter out handlers that belong to this plugin
                    # Hooks registered by plugins via settings have a "source" or
                    # were added by the plugin installer with a recognizable pattern.
                    # Since user hooks in settings.json don't normally have "source",
                    # we check for the plugin name in the command string as a heuristic.
                    filtered = [
                        h for h in handlers
                        if not _hook_belongs_to_plugin(h, name)
                    ]
                    if len(filtered) != len(handlers):
                        hooks[event] = filtered
                        changed = True
                    if not filtered:
                        events_to_delete.append(event)
                for event in events_to_delete:
                    del hooks[event]
                if not hooks:
                    settings.pop("hooks", None)

            if changed:
                _write_settings(settings_path, settings)
    except Exception as e:
        errors.append(f"settings.json: {e}")

    if errors:
        return {"error": "; ".join(errors)}
    return {"ok": True}


def delete_skill(name: str) -> Dict[str, Any]:
    """Delete a user skill directory: ``~/.claude/skills/{name}/``.

    Rejects if the skill is inside the plugin cache.
    """
    if not name:
        return {"error": "name required"}
    if ".." in name:
        return {"error": "invalid name"}

    target = CLAUDE_DIR / "skills" / name
    plugin_cache = CLAUDE_DIR / "plugins" / "cache"

    # Block plugin items
    if _is_inside(target, plugin_cache):
        return {"error": "cannot delete plugin skills -- uninstall the plugin instead"}

    if not target.is_dir():
        return {"error": "skill not found"}

    try:
        shutil.rmtree(target)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


def delete_agent(name: str) -> Dict[str, Any]:
    """Delete a user agent: ``~/.claude/agents/{name}.md``.

    Rejects if the agent is inside the plugin cache.
    """
    if not name:
        return {"error": "name required"}
    if ".." in name:
        return {"error": "invalid name"}

    target = CLAUDE_DIR / "agents" / f"{name}.md"
    plugin_cache = CLAUDE_DIR / "plugins" / "cache"

    if _is_inside(target, plugin_cache):
        return {"error": "cannot delete plugin agents -- uninstall the plugin instead"}

    if not target.is_file():
        return {"error": "agent not found"}

    try:
        target.unlink()
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


def delete_session(session_id: str) -> Dict[str, Any]:
    """Delete an idle session's JSONL files and session metadata.

    Refuses to delete active sessions (running claude processes).
    """
    if not session_id or ".." in session_id or "/" in session_id:
        return {"error": "invalid session_id"}

    # Determine active session IDs
    import os
    import subprocess
    active_session_ids: set = set()
    sessions_dir = CLAUDE_DIR / "sessions"
    if sessions_dir.is_dir():
        running_pids: set = set()
        try:
            ps = subprocess.run(
                ["ps", "aux"], capture_output=True, text=True, timeout=5,
                env={**os.environ, "LC_ALL": "C"},
            )
            for line in ps.stdout.splitlines()[1:]:
                parts = line.split(None, 10)
                if len(parts) >= 11 and "claude" in parts[10].lower():
                    try:
                        running_pids.add(int(parts[1]))
                    except ValueError:
                        pass
        except Exception:
            pass
        for sf in sessions_dir.glob("*.json"):
            try:
                pid = int(sf.stem)
                if pid in running_pids:
                    sess_data = read_json(sf)
                    if sess_data:
                        sid = sess_data.get("sessionId", "")
                        if sid:
                            active_session_ids.add(sid)
            except ValueError:
                pass

    if session_id in active_session_ids:
        return {"error": "cannot delete active session"}

    deleted = False
    errors: List[str] = []

    # Delete JSONL files across all project dirs
    projects_dir = CLAUDE_DIR / "projects"
    if projects_dir.is_dir():
        for jsonl_file in projects_dir.rglob(f"{session_id}.jsonl"):
            try:
                jsonl_file.unlink()
                deleted = True
            except Exception as e:
                errors.append(str(e))
        # Delete subagent directory if exists
        for subdir in projects_dir.rglob(session_id):
            if subdir.is_dir():
                try:
                    shutil.rmtree(subdir)
                    deleted = True
                except Exception as e:
                    errors.append(str(e))

    # Remove session/*.json where sessionId matches
    if sessions_dir.is_dir():
        for sf in sessions_dir.glob("*.json"):
            try:
                sess_data = read_json(sf)
                if sess_data and sess_data.get("sessionId", "") == session_id:
                    sf.unlink()
                    deleted = True
            except Exception as e:
                errors.append(str(e))

    if errors:
        return {"error": "; ".join(errors)}
    if not deleted:
        return {"error": "session not found"}
    return {"ok": True}


def delete_hook(event: str, index: int) -> Dict[str, Any]:
    """Delete a hook by event name and handler index from ``settings.json``."""
    if not event:
        return {"error": "event required"}
    if ".." in event:
        return {"error": "invalid event"}

    settings_path = CLAUDE_DIR / "settings.json"
    try:
        settings = _read_settings(settings_path)
        if settings is None:
            return {"error": "settings.json not found or invalid"}

        hooks = settings.get("hooks", {})
        if not isinstance(hooks, dict) or event not in hooks:
            return {"error": f"event '{event}' not found"}

        handlers = hooks[event]
        if not isinstance(handlers, list) or index >= len(handlers) or index < 0:
            return {"error": "invalid index"}

        handlers.pop(index)

        # Clean up empty event / hooks key
        if not handlers:
            del hooks[event]
        if not hooks:
            settings.pop("hooks", None)

        _write_settings(settings_path, settings)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


# ── Private helpers ───────────────────────────────────────────────────────────

def _read_settings(path: Path) -> Any:
    """Read settings.json, returning the parsed dict or None."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return None


def _write_settings(path: Path, data: Any) -> None:
    """Write settings.json with pretty formatting."""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _is_inside(target: Path, container: Path) -> bool:
    """Return True if *target* resolves to somewhere inside *container*."""
    try:
        target.resolve().relative_to(container.resolve())
        return True
    except ValueError:
        return False


def _hook_belongs_to_plugin(handler: Any, plugin_name: str) -> bool:
    """Heuristic: does this hook handler belong to the given plugin?

    Checks for a ``source`` field, or failing that, whether the command
    string contains the plugin cache path or plugin name.
    """
    if not isinstance(handler, dict):
        return False

    # Explicit source tag
    source = handler.get("source", "")
    if source == f"plugin:{plugin_name}":
        return True

    # Check sub-hooks
    sub_hooks = handler.get("hooks", [])
    if isinstance(sub_hooks, list):
        for sh in sub_hooks:
            if isinstance(sh, dict):
                cmd = sh.get("command", "")
                if f"plugins/cache" in cmd and plugin_name in cmd:
                    return True

    # Direct command reference
    cmd = handler.get("command", "")
    if f"plugins/cache" in cmd and plugin_name in cmd:
        return True

    return False
