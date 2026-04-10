"""MCP connector scanning: local servers, plugin servers, and cloud discovery.

Scans ``~/.claude.json``, plugin ``.mcp.json`` files, and session JSONL
files for ``mcp__`` tool patterns to discover cloud/plugin MCP servers.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .types import ConnectorsResult

from .common import CLAUDE_DIR, CLAUDE_JSON, UUID_RE, read_json, ttl_cache

# Valid MCP tool name: mcp__<server>__<tool>
_VALID_MCP_RE = re.compile(r"^mcp__[A-Za-z0-9][A-Za-z0-9_-]*__[A-Za-z][A-Za-z0-9_-]*$")
_MCP_GREP_RE = re.compile(rb'"(mcp__[^"]*)"')


@ttl_cache(60)
def get_connectors() -> ConnectorsResult:
    """Scan ``~/.claude.json``, plugin ``.mcp.json``, and session JSONL files.

    The JSONL pass discovers cloud MCP servers and plugin servers
    that only appear in usage data (``mcp__`` tool patterns).
    """
    connectors: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    # Global MCP servers from ~/.claude.json
    _add_mcp_servers(read_json(CLAUDE_JSON), "user", "", connectors, seen)

    # Plugin cache .mcp.json files
    plugins_cache = CLAUDE_DIR / "plugins" / "cache"
    if plugins_cache.is_dir():
        try:
            for mcp_file in plugins_cache.rglob(".mcp.json"):
                data = read_json(mcp_file)
                parts = mcp_file.relative_to(plugins_cache).parts
                plugin_name = parts[1] if len(parts) >= 2 else mcp_file.parent.name
                _add_mcp_servers(
                    data, f"plugin:{plugin_name}", plugin_name, connectors, seen,
                )
        except OSError:
            pass

    # Second pass: mcp__ patterns from session JSONL files
    server_tools = _extract_mcp_tools_from_sessions()
    for server_name, info in sorted(server_tools.items()):
        tools_list = sorted(info["tools"])
        tool_count = len(tools_list)

        existing = next(
            (c for c in connectors if c.get("_mcp_name") == server_name), None,
        )
        if existing is None and info["source"].startswith("plugin:"):
            existing = next(
                (c for c in connectors if c.get("source") == info["source"]),
                None,
            )
        if existing:
            existing["tools"] = tools_list
            existing["tool_count"] = tool_count
        else:
            key = f"{info['source']}:{server_name}"
            if key in seen:
                continue
            seen.add(key)
            connectors.append({
                "name": server_name,
                "_mcp_name": server_name,
                "_plugin_name": "",
                "command": info["command"],
                "type": info["type"],
                "args": [],
                "source": info["source"],
                "tools": tools_list,
                "tool_count": tool_count,
            })

    # Strip internal matching keys
    for c in connectors:
        c.pop("_mcp_name", None)
        c.pop("_plugin_name", None)

    return {"connectors": connectors}


def _add_mcp_servers(
    data: Optional[Any],
    source: str,
    plugin_name: str,
    connectors: List[Dict[str, Any]],
    seen: Set[str],
) -> None:
    """Extract ``mcpServers`` from a JSON dict and append to *connectors*."""
    if not data or not isinstance(data, dict):
        return
    mcp_servers = data.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        return
    for name, config in sorted(mcp_servers.items()):
        if not isinstance(config, dict):
            continue
        key = f"{source}:{name}"
        if key in seen:
            continue
        seen.add(key)
        display_name = name
        if plugin_name and name != plugin_name:
            display_name = f"{plugin_name} ({name})"
        connectors.append({
            "name": display_name,
            "_mcp_name": name,
            "_plugin_name": plugin_name,
            "command": config.get("command", ""),
            "type": config.get("type", "local"),
            "args": config.get("args", []),
            "source": source,
            "tools": [],
            "tool_count": 0,
        })


def _parse_mcp_prefix(prefix: str):
    """Decompose an MCP server prefix into (provider, server_name, mcp_type, source)."""
    parts = prefix.split("_")
    for i, p in enumerate(parts):
        if "-" in p:
            provider = "_".join(parts[:i]) if i > 0 else "plugin"
            return provider, p, "local", f"plugin:{p}"
    if len(parts) >= 2:
        server = parts[-1]
        provider = "_".join(parts[:-1])
        return provider, server, "cloud", "claude.ai"
    return prefix, prefix, "cloud", "claude.ai"


def _extract_mcp_tools_from_sessions() -> Dict[str, Dict[str, Any]]:
    """Grep session JSONL files for ``mcp__`` tool patterns."""
    jsonl_paths: Set[Path] = set()
    sessions_dir = CLAUDE_DIR / "sessions"
    projects_dir = CLAUDE_DIR / "projects"

    if sessions_dir.is_dir() and projects_dir.is_dir():
        for sess_file in sessions_dir.glob("*.json"):
            sess_data = read_json(sess_file)
            if not sess_data or not isinstance(sess_data, dict):
                continue
            session_id = sess_data.get("sessionId", "")
            if not session_id:
                continue
            if not UUID_RE.match(session_id):
                continue
            try:
                for jsonl_file in projects_dir.rglob(f"{session_id}.jsonl"):
                    jsonl_paths.add(jsonl_file)
            except OSError:
                pass

    if not jsonl_paths and projects_dir.is_dir():
        try:
            for proj_dir in projects_dir.iterdir():
                if not proj_dir.is_dir():
                    continue
                for jsonl_file in proj_dir.glob("*.jsonl"):
                    if "subagent" not in jsonl_file.name:
                        jsonl_paths.add(jsonl_file)
        except OSError:
            pass

    server_tools: Dict[str, Dict[str, Any]] = {}
    for jsonl_path in jsonl_paths:
        try:
            with open(jsonl_path, "rb") as f:
                for line in f:
                    for match in _MCP_GREP_RE.finditer(line):
                        tool_name = match.group(1).decode("utf-8", errors="replace")
                        if not _VALID_MCP_RE.match(tool_name):
                            continue
                        inner = tool_name[5:]
                        sep_idx = inner.find("__")
                        if sep_idx == -1:
                            continue
                        prefix = inner[:sep_idx]
                        tool = inner[sep_idx + 2:]
                        if not prefix or not tool:
                            continue
                        _, server_name, mcp_type, source = _parse_mcp_prefix(prefix)
                        if server_name not in server_tools:
                            server_tools[server_name] = {
                                "tools": set(),
                                "type": mcp_type,
                                "source": source,
                                "command": "cloud" if mcp_type == "cloud" else "node",
                            }
                        server_tools[server_name]["tools"].add(tool)
        except (OSError, UnicodeDecodeError):
            continue

    return server_tools
