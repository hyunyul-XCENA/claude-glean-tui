"""Shared fixtures for claude-glean-tui tests.

Provides temporary directory structures, JSONL helpers,
and vault fixtures used across test modules.
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
    return claude_dir


@pytest.fixture()
def tmp_vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary vault directory with sample notes.

    Structure::

        tmp_path/vault/
            lessons/
                lesson-one.md   (frontmatter with date, tags, summary)
                lesson-two.md
            decisions/
                decision-one.md
            sessions/
                session-one.md
    """
    vault = tmp_path / "vault"
    vault.mkdir()

    # lessons
    lessons = vault / "lessons"
    lessons.mkdir()
    (lessons / "lesson-one.md").write_text(
        "---\ndate: 2026-04-08\ntags: [python, testing]\n"
        "summary: Always test edge cases\n---\n\n"
        "Edge cases matter.\n",
        encoding="utf-8",
    )
    (lessons / "lesson-two.md").write_text(
        "---\ndate: 2026-04-09\ntags: [rust]\n"
        "summary: Borrow checker is your friend\n---\n\n"
        "Lifetimes are explicit.\n",
        encoding="utf-8",
    )

    # decisions
    decisions = vault / "decisions"
    decisions.mkdir()
    (decisions / "decision-one.md").write_text(
        "---\ndate: 2026-04-07\ntags: [architecture]\n"
        "summary: Use curses not blessed\n---\n\n"
        "stdlib wins.\n",
        encoding="utf-8",
    )

    # sessions
    sessions = vault / "sessions"
    sessions.mkdir()
    (sessions / "session-one.md").write_text(
        "---\ndate: 2026-04-09\ntags: [debug]\n"
        "summary: Debugged the usage parser\n---\n\n"
        "Found timestamp parsing bug.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("vault.VAULT_PATH", vault)
    return vault


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
