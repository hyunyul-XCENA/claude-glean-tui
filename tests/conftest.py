"""Shared fixtures for claude-glean-tui tests.

Provides temporary directory structures and JSONL helpers
used across test modules.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pytest

# Ensure the project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── JSONL helpers ────────────────────────────────────────────────────


def make_usage_entry(
    *,
    timestamp_iso: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> dict[str, Any]:
    """Build a JSONL assistant entry with usage data.

    Matches the real JSONL format:
    ``{"type":"assistant", "timestamp":"...", "message":{"usage":{...}}}``
    """
    return {
        "type": "assistant",
        "timestamp": timestamp_iso,
        "message": {
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
            }
        },
    }


def write_jsonl(path: Path, entries: list[dict[str, Any]]) -> None:
    """Write a list of dicts as JSONL lines to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def iso_now_minus(hours: float = 0, days: float = 0) -> str:
    """Return an ISO 8601 timestamp offset from now (UTC)."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours, days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_claude_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary ``~/.claude/`` structure and patch ``data.common.CLAUDE_DIR``.

    Directory layout::

        tmp_path/.claude/
            settings.json
            plugins/
                installed_plugins.json
            projects/
                -tmp-myproject/
    """
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{}", encoding="utf-8")
    (claude_dir / "plugins").mkdir()
    (claude_dir / "plugins" / "installed_plugins.json").write_text(
        '{"plugins":{}}', encoding="utf-8"
    )
    (claude_dir / "projects").mkdir()
    (claude_dir / "projects" / "-tmp-myproject").mkdir()

    monkeypatch.setattr("data.common.CLAUDE_DIR", claude_dir)
    # Patch the re-exported binding in modules that do `from .common import CLAUDE_DIR`
    monkeypatch.setattr("data.usage.CLAUDE_DIR", claude_dir)
    monkeypatch.setattr("data.delete.CLAUDE_DIR", claude_dir)
    monkeypatch.setattr("data.sessions.CLAUDE_DIR", claude_dir)
    # Prevent statusline from returning real system data in tests
    monkeypatch.setattr(
        "data.usage._STATUSLINE_FILE", tmp_path / "nonexistent" / "statusline.json",
    )

    # Clear TTL caches before and after each test to prevent stale data
    from data.usage import get_usage_stats
    if hasattr(get_usage_stats, "cache_clear"):
        get_usage_stats.cache_clear()

    yield claude_dir

    if hasattr(get_usage_stats, "cache_clear"):
        get_usage_stats.cache_clear()


@pytest.fixture()
def mock_jsonl_file(tmp_claude_dir: Path):
    """Factory fixture: create JSONL files under the temp claude dir.

    Usage::

        mock_jsonl_file("session-abc", [entry1, entry2], project="-tmp-myproject")
    """

    def _create(
        session_id: str,
        entries: list[dict[str, Any]],
        project: str = "-tmp-myproject",
    ) -> Path:
        proj_dir = tmp_claude_dir / "projects" / project
        proj_dir.mkdir(parents=True, exist_ok=True)
        path = proj_dir / f"{session_id}.jsonl"
        write_jsonl(path, entries)
        return path

    return _create
