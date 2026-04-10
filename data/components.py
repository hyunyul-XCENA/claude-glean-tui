"""
Component scanning: plugins, skills, agents, connectors, and hooks.

Each ``get_*()`` function scans user directories (``~/.claude/``) and
the plugin cache, tagging every item with ``"source": "user"`` or
``"source": "plugin:<name>"``.  No mutations -- see ``delete.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .types import AgentsResult, HooksResult, PluginsResult, SkillsResult

from .common import CLAUDE_DIR, CLAUDE_JSON, parse_frontmatter, read_json, read_text, ttl_cache
from .connectors import get_connectors

# ── Plugins ──────────────────────────────────────────────────────────────────

@ttl_cache(60)  # plugins don't change often
def get_plugins() -> PluginsResult:
    """Return installed plugins with component counts and enabled status."""
    plugins: List[Dict[str, Any]] = []

    plugins_data = read_json(CLAUDE_DIR / "plugins" / "installed_plugins.json")
    if not plugins_data or not isinstance(plugins_data, dict):
        return {"plugins": plugins}

    plugins_map = plugins_data.get("plugins", {})
    if not isinstance(plugins_map, dict):
        return {"plugins": plugins}

    # Enabled plugins from settings.json
    settings = read_json(CLAUDE_DIR / "settings.json")
    enabled_plugins: Dict[str, bool] = {}
    if settings and "enabledPlugins" in settings:
        ep = settings["enabledPlugins"]
        if isinstance(ep, dict):
            enabled_plugins = ep
        elif isinstance(ep, list):
            enabled_plugins = {name: True for name in ep}

    # Pre-compute component data once (avoid repeated scans)
    all_skills = get_skills()["skills"]
    all_agents = get_agents()["agents"]
    all_connectors = get_connectors()["connectors"]

    for plugin_key, entries in plugins_map.items():
        if not (isinstance(entries, list) and entries):
            continue
        entry = entries[0]
        if not isinstance(entry, dict):
            continue

        name = plugin_key.split("@")[0] if "@" in plugin_key else plugin_key
        plugin_source = f"plugin:{name}"
        n_skills = sum(1 for s in all_skills if s.get("source") == plugin_source)
        n_agents = sum(1 for a in all_agents if a.get("source") == plugin_source)
        n_connectors = sum(1 for c in all_connectors if c.get("source") == plugin_source)

        plugins.append({
            "key": plugin_key,
            "name": name,
            "version": entry.get("version", "unknown"),
            "enabled": bool(enabled_plugins.get(plugin_key, False)),
            "skills_count": n_skills,
            "agents_count": n_agents,
            "connectors_count": n_connectors,
        })

    return {"plugins": plugins}


# ── Skills ───────────────────────────────────────────────────────────────────

def get_skills() -> SkillsResult:
    """Scan ``~/.claude/skills/`` + plugin cache for skill definitions."""
    skills: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    # User skills
    _scan_skills_dir(CLAUDE_DIR / "skills", "user", skills, seen)

    # Plugin cache skills
    plugins_cache = CLAUDE_DIR / "plugins" / "cache"
    if plugins_cache.is_dir():
        try:
            for skills_subdir in plugins_cache.rglob("skills"):
                if skills_subdir.is_dir():
                    parts = skills_subdir.relative_to(plugins_cache).parts
                    plugin_name = parts[1] if len(parts) >= 2 else "plugin"
                    _scan_skills_dir(skills_subdir, f"plugin:{plugin_name}", skills, seen)
        except OSError:
            pass

    return {"skills": skills}


def _scan_skills_dir(
    skills_dir: Path,
    source: str,
    skills: List[Dict[str, Any]],
    seen: Set[str],
) -> None:
    """Scan a directory for skill subdirectories containing ``SKILL.md``."""
    if not skills_dir.is_dir():
        return
    try:
        for d in sorted(skills_dir.iterdir()):
            if not d.is_dir():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.is_file():
                continue
            content = read_text(skill_md)
            if content is None:
                continue
            fm = parse_frontmatter(content)
            name = fm.get("name", d.name)
            if name in seen:
                continue
            seen.add(name)
            # Extract body after frontmatter
            body = content
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    body = content[end + 3:].strip()
            skills.append({
                "name": name,
                "description": fm.get("description", ""),
                "path": str(skill_md),
                "source": source,
                "content": body[:2000],
            })
    except OSError:
        pass


# ── Agents ───────────────────────────────────────────────────────────────────

def get_agents() -> AgentsResult:
    """Scan ``~/.claude/agents/`` + plugin cache for agent definitions."""
    agents: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    # User agents
    _scan_agents_dir(CLAUDE_DIR / "agents", "user", agents, seen)

    # Plugin cache agents
    plugins_cache = CLAUDE_DIR / "plugins" / "cache"
    if plugins_cache.is_dir():
        try:
            for d in plugins_cache.rglob("agents"):
                if d.is_dir():
                    parts = d.relative_to(plugins_cache).parts
                    plugin_name = parts[1] if len(parts) >= 2 else "plugin"
                    _scan_agents_dir(d, f"plugin:{plugin_name}", agents, seen)
        except OSError:
            pass

    return {"agents": agents}


def _scan_agents_dir(
    agents_dir: Path,
    source: str,
    agents: List[Dict[str, Any]],
    seen: Set[str],
) -> None:
    """Scan a directory for ``.md`` agent files."""
    if not agents_dir.is_dir():
        return
    try:
        for f in sorted(agents_dir.glob("*.md")):
            content = read_text(f)
            if content is None:
                continue
            fm = parse_frontmatter(content)
            name = fm.get("name", f.stem)
            if name in seen:
                continue
            seen.add(name)
            tools = fm.get("tools", [])
            if isinstance(tools, str):
                tools = [t.strip() for t in tools.split(",") if t.strip()]
            body = content
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    body = content[end + 3:].strip()
            agents.append({
                "name": name,
                "description": fm.get("description", ""),
                "model": fm.get("model", ""),
                "tools": tools,
                "source": source,
                "content": body[:2000],
            })
    except OSError:
        pass


# ── Hooks ────────────────────────────────────────────────────────────────────

def get_hooks() -> HooksResult:
    """Scan ``settings.json`` hooks + plugin ``hooks.json`` files."""
    hooks_list: List[Dict[str, Any]] = []

    # User hooks from settings.json
    settings = read_json(CLAUDE_DIR / "settings.json")
    if settings and "hooks" in settings:
        _parse_hooks_block(settings["hooks"], "user", hooks_list)

    # Plugin hooks
    plugins_dir = CLAUDE_DIR / "plugins"
    if plugins_dir.is_dir():
        try:
            for hooks_json in plugins_dir.rglob("hooks/hooks.json"):
                plugin_name = _extract_plugin_name(hooks_json)
                try:
                    data = json.loads(hooks_json.read_text(encoding="utf-8"))
                    plugin_hooks = data.get("hooks", {})
                    if isinstance(plugin_hooks, dict):
                        _parse_hooks_block(plugin_hooks, f"plugin:{plugin_name}", hooks_list)
                except Exception:
                    continue
        except OSError:
            pass

    return {"hooks": hooks_list}


def _parse_hooks_block(
    hooks: Any,
    source: str,
    hooks_list: List[Dict[str, Any]],
) -> None:
    """Parse a hooks dict (``event -> [handler, ...]``) into flat entries."""
    if not isinstance(hooks, dict):
        return
    for event, handlers in hooks.items():
        if not isinstance(handlers, list):
            handlers = [handlers]
        for event_idx, h in enumerate(handlers):
            if not isinstance(h, dict):
                continue
            matcher = h.get("matcher", "")

            # New-style: {"matcher": "...", "hooks": [{...}]}
            sub_hooks = h.get("hooks", [])
            if isinstance(sub_hooks, list) and sub_hooks:
                for sh in sub_hooks:
                    if not isinstance(sh, dict):
                        continue
                    entry = {
                        "event": event,
                        "matcher": matcher,
                        "type": sh.get("type", "command"),
                        "command": sh.get("command", ""),
                        "description": sh.get("description", sh.get("statusMessage", "")),
                        "source": source,
                    }
                    # Keep only non-empty values (but always keep event/type/source)
                    entry = {
                        k: v for k, v in entry.items()
                        if v or k in ("event", "type", "source")
                    }
                    entry["_event_index"] = event_idx
                    hooks_list.append(entry)
            # Old-style: {"command": "...", "description": "..."}
            elif "command" in h:
                hooks_list.append({
                    "event": event,
                    "matcher": "",
                    "type": "command",
                    "command": h["command"],
                    "description": h.get("description", ""),
                    "source": source,
                    "_event_index": event_idx,
                })


def _extract_plugin_name(hooks_json_path: Path) -> str:
    """Derive plugin name from a ``hooks/hooks.json`` file path inside the cache."""
    parts = str(hooks_json_path).split("/")
    for i, p in enumerate(parts):
        if p == "cache" and i + 3 < len(parts):
            return parts[i + 2]
        if p == "plugins" and i + 2 < len(parts) and parts[i + 1] != "cache":
            if parts[i + 1] == "marketplaces":
                return parts[i + 2] if i + 2 < len(parts) else "plugin"
            return parts[i + 1]
    return "plugin"
