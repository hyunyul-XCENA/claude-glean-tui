"""Tests for data.sessions — _read_last_n_lines, _extract_token_usage, get_activity.

Covers:
  - _read_last_n_lines: normal tail, file smaller than n, empty file, nonexistent file
  - _extract_token_usage: normal assistant entry, no assistant entries, multiple entries
  - get_activity: empty history, history with today entries
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sessions import _read_last_n_lines, _extract_token_usage, get_activity
from tests.conftest import iso_now_minus, make_usage_entry, write_jsonl


# ── _read_last_n_lines ──────────────────────────────────────────────


class TestReadLastNLines:
    """Binary-seek tail reader for JSONL files."""

    def test_read_last_n_lines_normal_returns_last_n(self, tmp_path: Path) -> None:
        """10-line file, request last 5 -> exactly lines 6..10."""
        path = tmp_path / "ten.txt"
        lines = [f"line-{i}" for i in range(1, 11)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = _read_last_n_lines(path, 5)

        assert result == ["line-6", "line-7", "line-8", "line-9", "line-10"]

    def test_read_last_n_lines_file_smaller_than_n_returns_all(self, tmp_path: Path) -> None:
        """3-line file, request last 10 -> all 3 lines returned."""
        path = tmp_path / "small.txt"
        path.write_text("a\nb\nc\n", encoding="utf-8")

        result = _read_last_n_lines(path, 10)

        assert result == ["a", "b", "c"]

    def test_read_last_n_lines_empty_file_returns_empty(self, tmp_path: Path) -> None:
        """Empty file -> empty list."""
        path = tmp_path / "empty.txt"
        path.write_text("", encoding="utf-8")

        result = _read_last_n_lines(path, 5)

        assert result == []

    def test_read_last_n_lines_nonexistent_returns_empty(self, tmp_path: Path) -> None:
        """Missing file -> empty list (no exception raised)."""
        path = tmp_path / "does_not_exist.txt"

        result = _read_last_n_lines(path, 5)

        assert result == []


# ── _extract_token_usage ────────────────────────────────────────────


class TestExtractTokenUsage:
    """Extracts cache_read_input_tokens from the last assistant entry."""

    def test_extract_token_usage_normal_assistant_returns_tokens(self) -> None:
        """Single assistant entry with usage dict -> correct extraction."""
        entry = {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 50_000,
                    "cache_creation_input_tokens": 1_000,
                }
            },
        }
        lines = [json.dumps(entry)]

        result = _extract_token_usage(lines)

        assert result["context_tokens"] == 50_000
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 200
        assert result["cache_creation_input_tokens"] == 1_000

    def test_extract_token_usage_no_assistant_returns_empty(self) -> None:
        """Lines with no assistant entries -> empty dict."""
        user_entry = {"type": "user", "message": {"text": "hello"}}
        system_entry = {"type": "system", "message": {"text": "init"}}
        lines = [json.dumps(user_entry), json.dumps(system_entry)]

        result = _extract_token_usage(lines)

        assert result == {}

    def test_extract_token_usage_multiple_entries_uses_last(self) -> None:
        """Multiple assistant entries -> uses the last one (most recent)."""
        older = {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 1_000,
                    "cache_creation_input_tokens": 0,
                }
            },
        }
        newer = {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 600,
                    "cache_read_input_tokens": 99_000,
                    "cache_creation_input_tokens": 2_000,
                }
            },
        }
        lines = [json.dumps(older), json.dumps(newer)]

        result = _extract_token_usage(lines)

        # _extract_token_usage iterates in reverse, so it finds `newer` first
        assert result["context_tokens"] == 99_000
        assert result["input_tokens"] == 500
        assert result["output_tokens"] == 600

    def test_extract_token_usage_toplevel_usage_fallback(self) -> None:
        """Assistant entry with usage at top level (not nested in message)."""
        entry = {
            "type": "assistant",
            "usage": {
                "input_tokens": 42,
                "output_tokens": 84,
                "cache_read_input_tokens": 10_000,
                "cache_creation_input_tokens": 500,
            },
        }
        lines = [json.dumps(entry)]

        result = _extract_token_usage(lines)

        assert result["context_tokens"] == 10_000

    def test_extract_token_usage_assistant_without_usage_returns_empty(self) -> None:
        """Assistant entry that has no usage dict at all -> empty."""
        entry = {"type": "assistant", "message": {"text": "response"}}
        lines = [json.dumps(entry)]

        result = _extract_token_usage(lines)

        assert result == {}


# ── get_activity ────────────────────────────────────────────────────


class TestGetActivity:
    """Today's command count + recent activity from history.jsonl."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        """Clear the TTL cache before each test."""
        get_activity.cache_clear()
        yield
        get_activity.cache_clear()

    def test_get_activity_empty_history_returns_zero(self, tmp_claude_dir: Path) -> None:
        """No history.jsonl -> today_count=0, recent=[]."""
        # Patch sessions.CLAUDE_DIR since it imports from common
        import data.sessions as sessions_mod
        original = sessions_mod.CLAUDE_DIR
        sessions_mod.CLAUDE_DIR = tmp_claude_dir
        try:
            result = get_activity()

            assert result["today_count"] == 0
            assert result["recent"] == []
        finally:
            sessions_mod.CLAUDE_DIR = original

    def test_get_activity_with_today_entries_counts_correctly(
        self, tmp_claude_dir: Path,
    ) -> None:
        """History with entries timestamped today -> counts them."""
        import data.sessions as sessions_mod
        original = sessions_mod.CLAUDE_DIR
        sessions_mod.CLAUDE_DIR = tmp_claude_dir

        try:
            now_ms = int(time.time() * 1000)
            yesterday_ms = now_ms - 86_400_000  # 24h ago

            today_entries = [
                {"timestamp": now_ms, "display": "ask about tests", "project": "/tmp/myproj"},
                {"timestamp": now_ms - 60_000, "display": "write code", "project": "/tmp/myproj"},
            ]
            yesterday_entry = {"timestamp": yesterday_ms, "display": "old command", "project": ""}

            history_path = tmp_claude_dir / "history.jsonl"
            write_jsonl(history_path, [yesterday_entry] + today_entries)

            result = get_activity()

            assert result["today_count"] == 2
            assert len(result["recent"]) >= 2
            # Recent entries should contain the display text (most recent first)
            texts = [r["text"] for r in result["recent"]]
            assert "write code" in texts
            assert "ask about tests" in texts
        finally:
            sessions_mod.CLAUDE_DIR = original

    def test_get_activity_recent_truncates_long_display(
        self, tmp_claude_dir: Path,
    ) -> None:
        """Display text longer than 100 chars gets truncated with '...'."""
        import data.sessions as sessions_mod
        original = sessions_mod.CLAUDE_DIR
        sessions_mod.CLAUDE_DIR = tmp_claude_dir

        try:
            now_ms = int(time.time() * 1000)
            long_text = "x" * 150
            entry = {"timestamp": now_ms, "display": long_text, "project": ""}

            history_path = tmp_claude_dir / "history.jsonl"
            write_jsonl(history_path, [entry])

            result = get_activity()

            assert len(result["recent"]) == 1
            assert result["recent"][0]["text"].endswith("...")
            assert len(result["recent"][0]["text"]) == 103  # 100 + "..."
        finally:
            sessions_mod.CLAUDE_DIR = original

    def test_get_activity_skips_entries_without_display(
        self, tmp_claude_dir: Path,
    ) -> None:
        """Entries without a 'display' field are excluded from recent."""
        import data.sessions as sessions_mod
        original = sessions_mod.CLAUDE_DIR
        sessions_mod.CLAUDE_DIR = tmp_claude_dir

        try:
            now_ms = int(time.time() * 1000)
            no_display = {"timestamp": now_ms, "project": "/tmp/proj"}
            with_display = {"timestamp": now_ms, "display": "visible", "project": ""}

            history_path = tmp_claude_dir / "history.jsonl"
            write_jsonl(history_path, [no_display, with_display])

            result = get_activity()

            texts = [r["text"] for r in result["recent"]]
            assert texts == ["visible"]
        finally:
            sessions_mod.CLAUDE_DIR = original
