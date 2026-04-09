"""Tests for data.delete — plugin deletion with full cleanup.

Covers 1 test criterion from the spec:
  - Plugin delete: all 4 artifacts removed
    (installed_plugins.json + cache dir + enabledPlugins + hooks)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _setup_plugin_structure(
    claude_dir: Path,
    plugin_key: str = "test-plugin@user",
    plugin_name: str = "test-plugin",
) -> None:
    """Create a realistic plugin structure under the temp claude dir.

    Creates all 4 artifacts that delete_plugin should clean up:
      1. Entry in installed_plugins.json
      2. Cache directory at plugins/cache/user/test-plugin/1.0.0/
      3. Entry in enabledPlugins in settings.json
      4. Plugin hook in settings.json hooks
    """
    # 1. installed_plugins.json
    plugins_file = claude_dir / "plugins" / "installed_plugins.json"
    plugins_data = {
        "plugins": {
            plugin_key: {
                "name": plugin_name,
                "version": "1.0.0",
                "enabled": True,
            }
        }
    }
    plugins_file.write_text(json.dumps(plugins_data), encoding="utf-8")

    # 2. Cache directory
    cache_dir = claude_dir / "plugins" / "cache" / "user" / plugin_name / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "index.js").write_text("// plugin code", encoding="utf-8")

    # 3 + 4. settings.json with enabledPlugins and hooks
    settings = {
        "enabledPlugins": {plugin_key: True},
        "hooks": {
            "PostToolUse": [
                {
                    "source": f"plugin:{plugin_name}",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"~/.claude/plugins/cache/user/{plugin_name}/1.0.0/run.sh",
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "type": "command",
                    "command": "echo user-hook",
                    "description": "user's own hook",
                }
            ],
        },
    }
    (claude_dir / "settings.json").write_text(
        json.dumps(settings), encoding="utf-8"
    )


class TestDeletePluginFullCleanup:
    """delete_plugin() should remove all 4 plugin artifacts."""

    def test_delete_plugin_full_cleanup(
        self, tmp_claude_dir: Path,
    ):
        """Create mock plugin structure, delete it, verify all 4 artifacts removed."""
        plugin_key = "test-plugin@user"
        plugin_name = "test-plugin"
        _setup_plugin_structure(tmp_claude_dir, plugin_key, plugin_name)

        # Verify setup: all artifacts exist
        plugins_data = json.loads(
            (tmp_claude_dir / "plugins" / "installed_plugins.json").read_text()
        )
        assert plugin_key in plugins_data["plugins"]
        assert (
            tmp_claude_dir / "plugins" / "cache" / "user" / plugin_name
        ).is_dir()
        settings = json.loads(
            (tmp_claude_dir / "settings.json").read_text()
        )
        assert plugin_key in settings["enabledPlugins"]
        assert len(settings["hooks"]["PostToolUse"]) == 1

        # Act
        from data.delete import delete_plugin
        result = delete_plugin(plugin_key)

        # Assert: success
        assert result.get("ok") is True

        # Assert 1: Removed from installed_plugins.json
        plugins_data = json.loads(
            (tmp_claude_dir / "plugins" / "installed_plugins.json").read_text()
        )
        assert plugin_key not in plugins_data["plugins"]

        # Assert 2: Cache directory deleted
        assert not (
            tmp_claude_dir / "plugins" / "cache" / "user" / plugin_name
        ).exists()

        # Assert 3: Removed from enabledPlugins in settings.json
        settings = json.loads(
            (tmp_claude_dir / "settings.json").read_text()
        )
        assert "enabledPlugins" not in settings or plugin_key not in settings.get(
            "enabledPlugins", {}
        )

        # Assert 4: Plugin hooks removed (user hook should remain)
        hooks = settings.get("hooks", {})
        # PostToolUse should be gone (only had the plugin hook)
        assert "PostToolUse" not in hooks
        # PreToolUse (user hook) should be untouched
        assert "PreToolUse" in hooks
        assert len(hooks["PreToolUse"]) == 1

    def test_delete_plugin_rejects_empty_key(self, tmp_claude_dir: Path):
        from data.delete import delete_plugin

        result = delete_plugin("")
        assert "error" in result

    def test_delete_plugin_rejects_path_traversal(self, tmp_claude_dir: Path):
        from data.delete import delete_plugin

        result = delete_plugin("../../evil")
        assert "error" in result
        assert "invalid" in result["error"].lower()

    def test_delete_plugin_missing_plugins_json_returns_error(
        self, tmp_claude_dir: Path,
    ):
        """Deleting a plugin when installed_plugins.json is empty/missing."""
        from data.delete import delete_plugin

        result = delete_plugin("nonexistent@user")
        # Should get an error about the plugins file
        assert "error" in result

    def test_delete_plugin_preserves_other_plugins(
        self, tmp_claude_dir: Path,
    ):
        """Deleting one plugin should not affect other plugins."""
        plugins_data = {
            "plugins": {
                "keep-me@user": {"name": "keep-me", "version": "1.0.0"},
                "delete-me@user": {"name": "delete-me", "version": "2.0.0"},
            }
        }
        (tmp_claude_dir / "plugins" / "installed_plugins.json").write_text(
            json.dumps(plugins_data), encoding="utf-8"
        )

        from data.delete import delete_plugin
        result = delete_plugin("delete-me@user")

        remaining = json.loads(
            (tmp_claude_dir / "plugins" / "installed_plugins.json").read_text()
        )
        assert "keep-me@user" in remaining["plugins"]
        assert "delete-me@user" not in remaining["plugins"]


# ── Skill/Agent/Hook deletion ────────────────────────────────────────


class TestDeleteSkill:
    """delete_skill() should remove user skill directories."""

    def test_delete_skill_removes_directory(self, tmp_claude_dir: Path):
        skill_dir = tmp_claude_dir / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill", encoding="utf-8")

        from data.delete import delete_skill
        result = delete_skill("my-skill")

        assert result.get("ok") is True
        assert not skill_dir.exists()

    def test_delete_skill_rejects_path_traversal(self, tmp_claude_dir: Path):
        from data.delete import delete_skill

        result = delete_skill("../../etc")
        assert "error" in result

    def test_delete_skill_not_found_returns_error(self, tmp_claude_dir: Path):
        from data.delete import delete_skill

        result = delete_skill("nonexistent")
        assert "error" in result
        assert "not found" in result["error"]


class TestDeleteAgent:
    """delete_agent() should remove agent markdown files."""

    def test_delete_agent_removes_file(self, tmp_claude_dir: Path):
        agents_dir = tmp_claude_dir / "agents"
        agents_dir.mkdir()
        agent_file = agents_dir / "my-agent.md"
        agent_file.write_text("# Agent", encoding="utf-8")

        from data.delete import delete_agent
        result = delete_agent("my-agent")

        assert result.get("ok") is True
        assert not agent_file.exists()


class TestDeleteHook:
    """delete_hook() should remove hooks by event + index."""

    def test_delete_hook_removes_by_index(self, tmp_claude_dir: Path):
        settings = {
            "hooks": {
                "PostToolUse": [
                    {"type": "command", "command": "echo first"},
                    {"type": "command", "command": "echo second"},
                ]
            }
        }
        (tmp_claude_dir / "settings.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )

        from data.delete import delete_hook
        result = delete_hook("PostToolUse", 0)

        assert result.get("ok") is True

        updated = json.loads(
            (tmp_claude_dir / "settings.json").read_text()
        )
        remaining = updated["hooks"]["PostToolUse"]
        assert len(remaining) == 1
        assert remaining[0]["command"] == "echo second"

    def test_delete_hook_rejects_invalid_index(self, tmp_claude_dir: Path):
        settings = {
            "hooks": {
                "PostToolUse": [
                    {"type": "command", "command": "echo only"},
                ]
            }
        }
        (tmp_claude_dir / "settings.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )

        from data.delete import delete_hook
        result = delete_hook("PostToolUse", 5)

        assert "error" in result
