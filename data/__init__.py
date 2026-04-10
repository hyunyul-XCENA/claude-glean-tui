"""
data/ package — all ~/.claude/ reads/writes.

Pure functions returning dicts. No curses imports.
"""
from .health import get_health
from .sessions import get_sessions, get_activity, get_session_detail, get_session_xray
from .usage import get_usage_stats
from .components import get_plugins, get_skills, get_agents, get_hooks
from .connectors import get_connectors
from .delete import delete_plugin, delete_skill, delete_agent, delete_hook, delete_session

__all__ = [
    "get_health",
    "get_sessions",
    "get_activity",
    "get_session_detail",
    "get_session_xray",
    "get_usage_stats",
    "get_plugins",
    "get_skills",
    "get_agents",
    "get_connectors",
    "get_hooks",
    "delete_plugin",
    "delete_skill",
    "delete_agent",
    "delete_hook",
    "delete_session",
]
