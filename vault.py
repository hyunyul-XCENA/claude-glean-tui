"""Vault data module — browse and search Obsidian-style vault notes.

Self-contained: has its own frontmatter parser. No curses imports.
Reads from CLAUDE_VAULT_PATH (default ~/Documents/vault/).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

VAULT_PATH = Path(
    os.environ.get("CLAUDE_VAULT_PATH", str(Path.home() / "Documents" / "vault"))
)

# Directories to skip during recursive search
_SKIP_DIRS = {"templates", ".obsidian", ".trash", ".git"}


# ── Internal helpers ────────────────────────────────────────────────


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter delimited by ``---`` lines.

    Handles two value forms:
      key: value          -> str (stripped)
      key: [a, b, c]      -> list[str]

    Keys are lowercased.  Returns empty dict when no frontmatter is found.
    """
    # Frontmatter must start at the very beginning of the file
    if not text.startswith("---"):
        return {}

    # Find closing delimiter (second "---" line)
    end_match = re.search(r"\n---\s*\n", text[3:])
    if end_match is None:
        return {}

    block = text[3 : 3 + end_match.start()]
    result: dict = {}

    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        colon_idx = line.find(":")
        if colon_idx == -1:
            continue

        key = line[:colon_idx].strip().lower()
        value = line[colon_idx + 1 :].strip()

        # List syntax: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            items = value[1:-1]
            result[key] = [item.strip() for item in items.split(",") if item.strip()]
        else:
            result[key] = value

    return result


def _safe_subdir(subdir: str) -> bool:
    """Reject path-traversal and absolute path attempts."""
    return ".." not in subdir and not subdir.startswith("/")


def _safe_filename(filename: str) -> bool:
    """Reject path-traversal and absolute path attempts in filenames."""
    return ".." not in filename and not filename.startswith("/")


# ── Public API ──────────────────────────────────────────────────────


def vault_list_dirs() -> list[str]:
    """List subdirectories in the vault root.

    Returns a sorted list of directory names.
    Returns empty list if VAULT_PATH doesn't exist.
    """
    if not VAULT_PATH.is_dir():
        return []

    dirs: list[str] = []
    try:
        for entry in VAULT_PATH.iterdir():
            if entry.is_dir() and entry.name not in _SKIP_DIRS and not entry.name.startswith("."):
                dirs.append(entry.name)
    except OSError:
        return []

    return sorted(dirs)


def vault_list_notes(subdir: str) -> list[dict]:
    """List ``.md`` files in a vault subdirectory with parsed frontmatter.

    Args:
        subdir: Subdirectory name relative to vault root.
                Rejected if it contains ``..``.

    Returns:
        List of ``{"filename", "date", "summary", "tags"}`` dicts,
        sorted by date descending.  Unreadable files are silently skipped.
    """
    if not _safe_subdir(subdir):
        return []

    target = VAULT_PATH / subdir
    if not target.is_dir():
        return []

    notes: list[dict] = []
    try:
        for path in target.iterdir():
            if not path.is_file() or path.suffix.lower() != ".md":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue

            fm = _parse_frontmatter(text)
            notes.append(
                {
                    "filename": path.name,
                    "date": fm.get("date", ""),
                    "summary": fm.get("summary", ""),
                    "tags": fm.get("tags", []),
                }
            )
    except OSError:
        return []

    # Sort by date descending; notes without dates sink to the bottom
    notes.sort(key=lambda n: n["date"] or "", reverse=True)
    return notes


def vault_read_note(subdir: str, filename: str) -> str:
    """Read and return the full content of a vault note.

    Args:
        subdir: Subdirectory name (rejected if contains ``..``).
        filename: Note filename (rejected if contains ``..``).

    Returns:
        Full file content as string, or empty string on any error.
    """
    if not _safe_subdir(subdir) or not _safe_filename(filename):
        return ""

    target = VAULT_PATH / subdir / filename
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return ""


def vault_search(query: str) -> list[dict]:
    """Case-insensitive substring search across all vault notes.

    Searches frontmatter tags, summary, and full body text.
    Skips ``templates/`` and hidden directories.

    Args:
        query: Search string (case-insensitive substring match).

    Returns:
        Up to 50 results as ``[{"filename", "subdir", "date", "summary",
        "matched_line"}]``.  ``matched_line`` is the first line containing
        the query, stripped and truncated to 100 chars.
    """
    if not query or not VAULT_PATH.is_dir():
        return []

    query_lower = query.lower()
    results: list[dict] = []

    try:
        subdirs = sorted(VAULT_PATH.iterdir())
    except OSError:
        return []

    for subdir_path in subdirs:
        if not subdir_path.is_dir():
            continue
        if subdir_path.name in _SKIP_DIRS or subdir_path.name.startswith("."):
            continue

        # Walk recursively to cover nested directories
        try:
            md_files = sorted(subdir_path.rglob("*.md"))
        except OSError:
            continue

        for md_path in md_files:
            if not md_path.is_file():
                continue
            # Skip files inside templates/ anywhere in the path
            if "templates" in md_path.parts:
                continue

            try:
                text = md_path.read_text(encoding="utf-8")
            except OSError:
                continue

            fm = _parse_frontmatter(text)

            # Check tags (list or string)
            tags = fm.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            tags_match = any(query_lower in t.lower() for t in tags)

            # Check summary
            summary = fm.get("summary", "")
            summary_match = query_lower in summary.lower()

            # Check full body
            body_match = query_lower in text.lower()

            if not (tags_match or summary_match or body_match):
                continue

            # Find first matching line for context
            matched_line = ""
            for line in text.splitlines():
                if query_lower in line.lower():
                    matched_line = line.strip()[:100]
                    break

            # Compute subdir relative to vault root
            try:
                rel = md_path.parent.relative_to(VAULT_PATH)
                subdir_name = str(rel)
            except ValueError:
                subdir_name = subdir_path.name

            results.append(
                {
                    "filename": md_path.name,
                    "subdir": subdir_name,
                    "date": fm.get("date", ""),
                    "summary": summary,
                    "matched_line": matched_line,
                }
            )

            if len(results) >= 50:
                return results

    return results
