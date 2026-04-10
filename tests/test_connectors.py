"""Tests for data.connectors — _parse_mcp_prefix, get_connectors.

Covers:
  - _parse_mcp_prefix: cloud prefix, plugin prefix with hyphen, multi-part prefix
  - get_connectors: empty state, with mcpServers in .claude.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.connectors import _parse_mcp_prefix, get_connectors


# ── _parse_mcp_prefix ──────────────────────────────────────────────


class TestParseMcpPrefix:
    """Decompose MCP server prefixes into (provider, server_name, type, source)."""

    def test_parse_mcp_prefix_cloud_returns_cloud_type(self) -> None:
        """Single-word cloud prefix like 'anthropic' -> cloud type."""
        provider, server_name, mcp_type, source = _parse_mcp_prefix("anthropic")

        assert mcp_type == "cloud"
        assert source == "claude.ai"
        assert server_name == "anthropic"

    def test_parse_mcp_prefix_hyphen_plugin_returns_plugin_type(self) -> None:
        """Prefix containing a hyphen like 'my-plugin' -> plugin type."""
        provider, server_name, mcp_type, source = _parse_mcp_prefix("my-plugin")

        assert mcp_type == "local"
        assert source == "plugin:my-plugin"
        assert server_name == "my-plugin"
        assert provider == "plugin"

    def test_parse_mcp_prefix_multi_part_cloud_correct_decomposition(self) -> None:
        """Multi-part prefix like 'github_tools' -> splits provider/server."""
        provider, server_name, mcp_type, source = _parse_mcp_prefix("github_tools")

        assert provider == "github"
        assert server_name == "tools"
        assert mcp_type == "cloud"
        assert source == "claude.ai"

    def test_parse_mcp_prefix_hyphen_after_underscore_returns_plugin(self) -> None:
        """Prefix like 'some_my-server' -> hyphen triggers plugin detection."""
        provider, server_name, mcp_type, source = _parse_mcp_prefix("some_my-server")

        assert mcp_type == "local"
        assert server_name == "my-server"
        assert source == "plugin:my-server"
        assert provider == "some"

    def test_parse_mcp_prefix_single_part_returns_cloud(self) -> None:
        """Single part with no underscore or hyphen -> cloud, name=prefix."""
        provider, server_name, mcp_type, source = _parse_mcp_prefix("valtown")

        assert provider == "valtown"
        assert server_name == "valtown"
        assert mcp_type == "cloud"
        assert source == "claude.ai"


# ── get_connectors ──────────────────────────────────────────────────


class TestGetConnectors:
    """Scan .claude.json and session files for MCP connectors."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        """Clear the TTL cache before each test."""
        get_connectors.cache_clear()
        yield
        get_connectors.cache_clear()

    def test_get_connectors_empty_state_returns_empty_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No .claude.json, no sessions -> empty connectors list."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        monkeypatch.setattr("data.connectors.CLAUDE_DIR", claude_dir)
        monkeypatch.setattr("data.connectors.CLAUDE_JSON", tmp_path / ".claude.json")

        result = get_connectors()

        assert result["connectors"] == []

    def test_get_connectors_with_mcp_servers_returns_servers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A .claude.json with mcpServers -> connectors list populated."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(
            json.dumps({
                "mcpServers": {
                    "context7": {
                        "command": "npx",
                        "args": ["-y", "@upstash/context7-mcp@latest"],
                        "type": "local",
                    },
                    "filesystem": {
                        "command": "node",
                        "args": ["server.js"],
                    },
                }
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr("data.connectors.CLAUDE_DIR", claude_dir)
        monkeypatch.setattr("data.connectors.CLAUDE_JSON", claude_json)

        result = get_connectors()

        names = [c["name"] for c in result["connectors"]]
        assert "context7" in names
        assert "filesystem" in names
        assert len(result["connectors"]) == 2

    def test_get_connectors_mcp_server_has_expected_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each connector entry should have name, command, type, source, tools."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(
            json.dumps({
                "mcpServers": {
                    "myserver": {
                        "command": "python",
                        "args": ["-m", "myserver"],
                        "type": "local",
                    },
                }
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr("data.connectors.CLAUDE_DIR", claude_dir)
        monkeypatch.setattr("data.connectors.CLAUDE_JSON", claude_json)

        result = get_connectors()

        connector = result["connectors"][0]
        assert connector["name"] == "myserver"
        assert connector["command"] == "python"
        assert connector["type"] == "local"
        assert connector["source"] == "user"
        assert connector["tools"] == []
        assert connector["tool_count"] == 0

    def test_get_connectors_no_mcp_servers_key_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """.claude.json exists but has no mcpServers key -> empty list."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({"someOtherKey": True}), encoding="utf-8")

        monkeypatch.setattr("data.connectors.CLAUDE_DIR", claude_dir)
        monkeypatch.setattr("data.connectors.CLAUDE_JSON", claude_json)

        result = get_connectors()

        assert result["connectors"] == []
