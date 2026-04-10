"""
Harness health score (0-100) for the Claude Code workspace.

Checks 7 items worth 14 points each (all passing = 100).
Uses minimal file-existence / key-existence checks to avoid
circular imports -- does NOT call get_agents/get_skills/etc.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from .common import CLAUDE_DIR, read_json, ttl_cache


@ttl_cache(30)
def get_health() -> Dict[str, object]:
    """Return harness health score and per-item check results.

    Returns::

        {
            "score": int,       # 0-100
            "total": 100,
            "items": {
                "claude_md": bool,
                "permissions": bool,
                "hooks": bool,
                "agents": bool,
                "skills": bool,
                "connectors": bool,
                "plugins": bool,
            },
        }
    """
    items: Dict[str, bool] = {}

    # 1) claude_md -- global CLAUDE.md exists
    items["claude_md"] = (CLAUDE_DIR / "CLAUDE.md").is_file()

    # 2) permissions -- settings.json contains a "permissions" key
    settings = read_json(CLAUDE_DIR / "settings.json")
    items["permissions"] = bool(settings and "permissions" in settings)

    # 3) hooks -- settings.json contains a "hooks" key
    items["hooks"] = bool(settings and "hooks" in settings)

    # 4) agents -- any .md files under ~/.claude/agents/ or plugin cache agents/
    items["agents"] = _has_md_files(CLAUDE_DIR / "agents") or _has_agents_in_cache()

    # 5) skills -- any subdirectory with SKILL.md under ~/.claude/skills/ or cache
    items["skills"] = _has_skill_dirs(CLAUDE_DIR / "skills") or _has_skills_in_cache()

    # 6) connectors -- any MCP servers found (uses cached get_connectors)
    from .connectors import get_connectors
    items["connectors"] = len(get_connectors().get("connectors", [])) > 0

    # 7) plugins -- installed_plugins.json has entries
    plugins_data = read_json(CLAUDE_DIR / "plugins" / "installed_plugins.json")
    if plugins_data and isinstance(plugins_data, dict):
        plugins_map = plugins_data.get("plugins", {})
        items["plugins"] = isinstance(plugins_map, dict) and len(plugins_map) > 0
    else:
        items["plugins"] = False

    # Score: 14 points each, all 7 true = 100
    per_item = 100 // len(items)  # 14
    score = sum(per_item for v in items.values() if v)
    if all(items.values()):
        score = 100

    return {"score": score, "total": 100, "items": items}


# ── Private helpers (lightweight file-existence probes) ───────────────────────

def _has_md_files(directory: Path) -> bool:
    """True if *directory* contains at least one ``.md`` file."""
    if not directory.is_dir():
        return False
    try:
        return any(True for f in directory.glob("*.md") if f.is_file())
    except OSError:
        return False


def _has_skill_dirs(directory: Path) -> bool:
    """True if *directory* has any subdirectory containing ``SKILL.md``."""
    if not directory.is_dir():
        return False
    try:
        for d in directory.iterdir():
            if d.is_dir() and (d / "SKILL.md").is_file():
                return True
    except OSError:
        pass
    return False


def _has_agents_in_cache() -> bool:
    """Check plugin cache for agent .md files."""
    cache = CLAUDE_DIR / "plugins" / "cache"
    if not cache.is_dir():
        return False
    try:
        for agents_dir in cache.rglob("agents"):
            if agents_dir.is_dir() and any(agents_dir.glob("*.md")):
                return True
    except OSError:
        pass
    return False


def _has_skills_in_cache() -> bool:
    """Check plugin cache for skill directories with SKILL.md."""
    cache = CLAUDE_DIR / "plugins" / "cache"
    if not cache.is_dir():
        return False
    try:
        for skills_dir in cache.rglob("skills"):
            if skills_dir.is_dir():
                for d in skills_dir.iterdir():
                    if d.is_dir() and (d / "SKILL.md").is_file():
                        return True
    except OSError:
        pass
    return False
