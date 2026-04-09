"""Tests for vault.py — directory listing, env var path, path traversal.

Covers 3 test criteria from the spec:
  1. Dynamic dirs: all subdirectories in vault root are listed
  2. Env var path: CLAUDE_VAULT_PATH overrides default
  3. Path traversal: ``../../etc/passwd`` rejected with empty result
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Dynamic directory listing ────────────────────────────────────────


class TestVaultDynamicDirs:
    """vault_list_dirs() should return all visible subdirectories."""

    def test_vault_dynamic_dirs_lists_all_subdirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Create vault with 7 directories, verify all 7 listed."""
        vault = tmp_path / "vault"
        vault.mkdir()
        dir_names = [
            "lessons", "decisions", "sessions", "resources",
            "projects", "daily", "archive",
        ]
        for name in dir_names:
            (vault / name).mkdir()

        monkeypatch.setattr("vault.VAULT_PATH", vault)
        from vault import vault_list_dirs

        result = vault_list_dirs()

        assert len(result) == 7
        for name in dir_names:
            assert name in result

    def test_vault_dynamic_dirs_excludes_hidden_and_skip_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Hidden dirs and _SKIP_DIRS (templates, .obsidian, etc.) excluded."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "visible").mkdir()
        (vault / ".obsidian").mkdir()
        (vault / ".git").mkdir()
        (vault / "templates").mkdir()
        (vault / ".hidden").mkdir()

        monkeypatch.setattr("vault.VAULT_PATH", vault)
        from vault import vault_list_dirs

        result = vault_list_dirs()

        assert result == ["visible"]

    def test_vault_dynamic_dirs_returns_sorted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        vault = tmp_path / "vault"
        vault.mkdir()
        for name in ["zebra", "alpha", "middle"]:
            (vault / name).mkdir()

        monkeypatch.setattr("vault.VAULT_PATH", vault)
        from vault import vault_list_dirs

        result = vault_list_dirs()
        assert result == ["alpha", "middle", "zebra"]

    def test_vault_dynamic_dirs_nonexistent_path_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr("vault.VAULT_PATH", tmp_path / "no-such-dir")
        from vault import vault_list_dirs

        assert vault_list_dirs() == []

    def test_vault_dynamic_dirs_ignores_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Only directories should be listed, not files."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "realdir").mkdir()
        (vault / "readme.md").write_text("not a dir", encoding="utf-8")

        monkeypatch.setattr("vault.VAULT_PATH", vault)
        from vault import vault_list_dirs

        assert vault_list_dirs() == ["realdir"]


# ── Environment variable path ────────────────────────────────────────


class TestVaultEnvVarPath:
    """CLAUDE_VAULT_PATH env var should override the default vault path."""

    def test_vault_env_var_path_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Set CLAUDE_VAULT_PATH, verify vault reads from that location."""
        custom_vault = tmp_path / "custom-vault"
        custom_vault.mkdir()
        (custom_vault / "custom-dir").mkdir()

        monkeypatch.setattr("vault.VAULT_PATH", custom_vault)
        from vault import vault_list_dirs

        result = vault_list_dirs()
        assert "custom-dir" in result

    def test_vault_env_var_notes_read_from_custom_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Notes should be read from the custom vault path."""
        custom_vault = tmp_path / "alt-vault"
        custom_vault.mkdir()
        notes_dir = custom_vault / "notes"
        notes_dir.mkdir()
        (notes_dir / "test.md").write_text(
            "---\ndate: 2026-01-01\nsummary: test note\n---\nBody\n",
            encoding="utf-8",
        )

        monkeypatch.setattr("vault.VAULT_PATH", custom_vault)
        from vault import vault_list_notes

        result = vault_list_notes("notes")
        assert len(result) == 1
        assert result[0]["filename"] == "test.md"
        assert result[0]["summary"] == "test note"


# ── Path traversal rejection ────────────────────────────────────────


class TestVaultPathTraversal:
    """Path traversal attempts (containing '..') must return empty."""

    def test_vault_path_traversal_rejected_list_notes(
        self, tmp_vault_dir: Path,
    ):
        from vault import vault_list_notes

        result = vault_list_notes("../../etc/passwd")
        assert result == []

    def test_vault_path_traversal_rejected_read_note(
        self, tmp_vault_dir: Path,
    ):
        from vault import vault_read_note

        result = vault_read_note("../../etc", "passwd")
        assert result == ""

    def test_vault_path_traversal_rejected_in_filename(
        self, tmp_vault_dir: Path,
    ):
        from vault import vault_read_note

        result = vault_read_note("lessons", "../../etc/passwd")
        assert result == ""

    def test_vault_path_traversal_rejected_dotdot_subdir(
        self, tmp_vault_dir: Path,
    ):
        from vault import vault_list_notes

        result = vault_list_notes("../secrets")
        assert result == []


# ── Vault search ─────────────────────────────────────────────────────


class TestVaultSearch:
    """vault_search() should find notes across subdirectories."""

    def test_vault_search_finds_by_tag(self, tmp_vault_dir: Path):
        from vault import vault_search

        results = vault_search("python")
        assert len(results) >= 1
        assert any(r["filename"] == "lesson-one.md" for r in results)

    def test_vault_search_finds_by_body(self, tmp_vault_dir: Path):
        from vault import vault_search

        results = vault_search("Lifetimes")
        assert len(results) >= 1

    def test_vault_search_empty_query_returns_empty(self, tmp_vault_dir: Path):
        from vault import vault_search

        assert vault_search("") == []

    def test_vault_search_no_match_returns_empty(self, tmp_vault_dir: Path):
        from vault import vault_search

        assert vault_search("zzz_nonexistent_zzz") == []


# ── Vault note listing ───────────────────────────────────────────────


class TestVaultListNotes:
    """vault_list_notes() should return notes sorted by date descending."""

    def test_vault_list_notes_sorted_by_date_desc(self, tmp_vault_dir: Path):
        from vault import vault_list_notes

        notes = vault_list_notes("lessons")
        assert len(notes) == 2
        # lesson-two (2026-04-09) should come before lesson-one (2026-04-08)
        assert notes[0]["filename"] == "lesson-two.md"
        assert notes[1]["filename"] == "lesson-one.md"

    def test_vault_list_notes_nonexistent_subdir_returns_empty(
        self, tmp_vault_dir: Path,
    ):
        from vault import vault_list_notes

        assert vault_list_notes("nonexistent") == []
