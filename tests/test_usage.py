"""Tests for data.usage — 5h/weekly token aggregation, tier limits, cost, per-session.

Covers 6 test criteria from the spec:
  1. 5h window aggregation: only tokens within last 5 hours counted
  2. Remaining capacity: limit - total = remaining
  3. Tier switching: different env var = different limits
  4. Weekly aggregation: only last 7 days included
  5. Cost calculation: matches pricing constants
  6. Per-session sorted by total_tokens descending
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.conftest import iso_now_minus, make_usage_entry


# ── 5h window aggregation ────────────────────────────────────────────


class TestWindow5hAggregation:
    """Given JSONL files with known timestamps, verify 5h total
    equals sum of tokens within the last 5 hours only."""

    def test_usage_5h_window_aggregation_correct_sum(
        self, tmp_claude_dir, mock_jsonl_file, monkeypatch,
    ):
        """Three sessions across time boundaries: only entries within 5h
        should contribute to window_5h totals.

        total_tokens = input + output + cache_read + cache_creation
        """
        # Arrange: 2 entries within 5h, 1 entry at 6h ago (outside window)
        recent_1h = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=1),
            input_tokens=100,
            output_tokens=200,
            cache_read_input_tokens=50,
            cache_creation_input_tokens=10,
        )
        recent_3h = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=3),
            input_tokens=300,
            output_tokens=400,
            cache_read_input_tokens=100,
            cache_creation_input_tokens=20,
        )
        old_6h = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=6),
            input_tokens=9999,
            output_tokens=9999,
            cache_read_input_tokens=9999,
            cache_creation_input_tokens=9999,
        )

        mock_jsonl_file("sess-a", [recent_1h])
        mock_jsonl_file("sess-b", [recent_3h])
        mock_jsonl_file("sess-c", [old_6h])

        # Act
        from data.usage import get_usage_stats
        result = get_usage_stats()

        # Assert: only recent entries in 5h window
        w5 = result["window_5h"]
        assert w5["input_tokens"] == 100 + 300
        assert w5["output_tokens"] == 200 + 400
        assert w5["cache_read_tokens"] == 50 + 100
        assert w5["cache_creation_tokens"] == 10 + 20
        expected_total = (100 + 300) + (200 + 400) + (50 + 100) + (10 + 20)
        assert w5["total_tokens"] == expected_total
        assert w5["session_count"] == 2

    def test_usage_5h_window_empty_no_files_returns_zero(self, tmp_claude_dir):
        """No JSONL files at all should produce zeroed 5h stats."""
        from data.usage import get_usage_stats
        result = get_usage_stats()
        w5 = result["window_5h"]
        assert w5["total_tokens"] == 0
        assert w5["session_count"] == 0

    def test_usage_5h_window_skips_non_assistant_entries(
        self, tmp_claude_dir, mock_jsonl_file,
    ):
        """Only entries with type=assistant should be counted."""
        assistant = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=1),
            input_tokens=500,
            output_tokens=500,
        )
        human_entry = {
            "type": "human",
            "timestamp": iso_now_minus(hours=1),
            "message": {"usage": {"input_tokens": 999, "output_tokens": 999}},
        }
        mock_jsonl_file("sess-mix", [assistant, human_entry])

        from data.usage import get_usage_stats
        result = get_usage_stats()
        assert result["window_5h"]["input_tokens"] == 500

    def test_usage_5h_window_skips_subagent_files(
        self, tmp_claude_dir,
    ):
        """Files containing 'subagent' in the name should be skipped."""
        from tests.conftest import write_jsonl
        proj_dir = tmp_claude_dir / "projects" / "-tmp-myproject"
        entry = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=1),
            input_tokens=1000,
            output_tokens=1000,
        )
        write_jsonl(proj_dir / "subagent-abc.jsonl", [entry])

        from data.usage import get_usage_stats
        result = get_usage_stats()
        assert result["window_5h"]["total_tokens"] == 0


# ── Remaining capacity ───────────────────────────────────────────────


class TestRemainingCapacity:
    """Given tier=max5x (limit_5h=4M) and known 5h usage,
    verify remaining_tokens and usage_pct."""

    def test_usage_remaining_capacity_correct_calculation(
        self, tmp_claude_dir, mock_jsonl_file, monkeypatch,
    ):
        monkeypatch.setattr("data.usage.CLAUDE_TIER", "max5x")
        monkeypatch.setattr("data.usage.TIER_LIMITS", {
            "max5x": {"limit_5h": 4_000_000, "limit_weekly": 20_000_000},
        })

        # 1M total tokens in 5h window
        entry = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=1),
            input_tokens=250_000,
            output_tokens=250_000,
            cache_read_input_tokens=250_000,
            cache_creation_input_tokens=250_000,
        )
        mock_jsonl_file("sess-cap", [entry])

        from data.usage import get_usage_stats
        result = get_usage_stats()
        w5 = result["window_5h"]

        assert w5["total_tokens"] == 1_000_000
        assert w5["remaining_tokens"] == 3_000_000
        assert w5["usage_pct"] == 25.0

    def test_usage_remaining_never_negative(
        self, tmp_claude_dir, mock_jsonl_file, monkeypatch,
    ):
        """remaining_tokens should be 0 when usage exceeds limit."""
        monkeypatch.setattr("data.usage.CLAUDE_TIER", "pro")
        monkeypatch.setattr("data.usage.TIER_LIMITS", {
            "pro": {"limit_5h": 100, "limit_weekly": 1_000},
        })

        entry = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=1),
            input_tokens=200,
            output_tokens=200,
        )
        mock_jsonl_file("sess-over", [entry])

        from data.usage import get_usage_stats
        result = get_usage_stats()
        assert result["window_5h"]["remaining_tokens"] == 0


# ── Tier switching ───────────────────────────────────────────────────


class TestTierSwitching:
    """Different CLAUDE_TIER values produce different limit values."""

    def test_usage_tier_switching_pro_limits(
        self, tmp_claude_dir, monkeypatch,
    ):
        monkeypatch.setattr("data.usage.CLAUDE_TIER", "pro")

        from data.usage import get_usage_stats
        result = get_usage_stats()
        assert result["tier"]["name"] == "pro"
        assert result["tier"]["limit_5h"] == 800_000
        assert result["tier"]["limit_weekly"] == 4_000_000

    def test_usage_tier_switching_max5x_limits(
        self, tmp_claude_dir, monkeypatch,
    ):
        monkeypatch.setattr("data.usage.CLAUDE_TIER", "max5x")

        from data.usage import get_usage_stats
        result = get_usage_stats()
        assert result["tier"]["name"] == "max5x"
        assert result["tier"]["limit_5h"] == 4_000_000
        assert result["tier"]["limit_weekly"] == 20_000_000

    def test_usage_tier_switching_max20x_limits(
        self, tmp_claude_dir, monkeypatch,
    ):
        monkeypatch.setattr("data.usage.CLAUDE_TIER", "max20x")

        from data.usage import get_usage_stats
        result = get_usage_stats()
        assert result["tier"]["name"] == "max20x"
        assert result["tier"]["limit_5h"] == 16_000_000
        assert result["tier"]["limit_weekly"] == 80_000_000

    def test_usage_tier_switching_unknown_falls_back_to_max5x(
        self, tmp_claude_dir, monkeypatch,
    ):
        monkeypatch.setattr("data.usage.CLAUDE_TIER", "nonexistent")

        from data.usage import get_usage_stats
        result = get_usage_stats()
        assert result["tier"]["name"] == "max5x"


# ── Weekly window ────────────────────────────────────────────────────


class TestWeeklyWindow:
    """Entries spanning 10 days: only last 7 days included in weekly."""

    def test_usage_weekly_excludes_old_entries(
        self, tmp_claude_dir, mock_jsonl_file,
    ):
        recent_1d = make_usage_entry(
            timestamp_iso=iso_now_minus(days=1),
            input_tokens=100,
            output_tokens=100,
        )
        recent_5d = make_usage_entry(
            timestamp_iso=iso_now_minus(days=5),
            input_tokens=200,
            output_tokens=200,
        )
        old_8d = make_usage_entry(
            timestamp_iso=iso_now_minus(days=8),
            input_tokens=5000,
            output_tokens=5000,
        )
        old_10d = make_usage_entry(
            timestamp_iso=iso_now_minus(days=10),
            input_tokens=9000,
            output_tokens=9000,
        )

        mock_jsonl_file("sess-recent", [recent_1d, recent_5d])
        mock_jsonl_file("sess-old", [old_8d, old_10d])

        from data.usage import get_usage_stats
        result = get_usage_stats()
        wk = result["window_weekly"]

        # Only recent entries (1d + 5d) counted
        assert wk["input_tokens"] == 100 + 200
        assert wk["output_tokens"] == 100 + 200
        # total = input + output + cache_read(0) + cache_creation(0)
        assert wk["total_tokens"] == (100 + 200) + (100 + 200)

    def test_usage_weekly_includes_boundary_entries(
        self, tmp_claude_dir, mock_jsonl_file,
    ):
        """Entry at exactly 6 days 23 hours should be included."""
        just_inside = make_usage_entry(
            timestamp_iso=iso_now_minus(days=6, hours=23),
            input_tokens=500,
            output_tokens=500,
        )
        mock_jsonl_file("sess-boundary", [just_inside])

        from data.usage import get_usage_stats
        result = get_usage_stats()
        assert result["window_weekly"]["input_tokens"] == 500


# ── Cost calculation ─────────────────────────────────────────────────


class TestCostCalculation:
    """Given known token counts, verify cost matches pricing constants.

    Pricing (per 1M tokens):
      input: $15.0, output: $75.0,
      cache_read: $1.5, cache_creation: $18.75
    """

    def test_usage_cost_calculation_matches_pricing(
        self, tmp_claude_dir, mock_jsonl_file,
    ):
        entry = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=1),
            input_tokens=100_000,
            output_tokens=50_000,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        mock_jsonl_file("sess-cost", [entry])

        from data.usage import get_usage_stats
        result = get_usage_stats()

        # Expected: 100k input @ $15/M = $1.50
        #           50k output @ $75/M = $3.75
        #           Total = $5.25
        cost = result["cost_estimate"]["window_5h_usd"]
        assert cost == pytest.approx(5.25, abs=0.01)

    def test_usage_cost_calculation_with_cache_tokens(
        self, tmp_claude_dir, mock_jsonl_file,
    ):
        entry = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=1),
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=1_000_000,
            cache_creation_input_tokens=1_000_000,
        )
        mock_jsonl_file("sess-cache-cost", [entry])

        from data.usage import get_usage_stats
        result = get_usage_stats()

        # Expected: 1M cache_read @ $1.5/M = $1.50
        #           1M cache_creation @ $18.75/M = $18.75
        #           Total = $20.25
        cost = result["cost_estimate"]["window_5h_usd"]
        assert cost == pytest.approx(20.25, abs=0.01)


# ── Per-session breakdown ────────────────────────────────────────────


class TestPerSessionBreakdown:
    """Per-session list should be sorted by total_tokens descending."""

    def test_usage_per_session_sorted_by_total_desc(
        self, tmp_claude_dir, mock_jsonl_file,
    ):
        small_entry = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=1),
            input_tokens=100,
            output_tokens=100,
        )
        large_entry = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=1),
            input_tokens=5000,
            output_tokens=5000,
        )

        mock_jsonl_file("sess-small", [small_entry])
        mock_jsonl_file("sess-large", [large_entry])

        from data.usage import get_usage_stats
        result = get_usage_stats()
        per_session = result["per_session"]

        assert len(per_session) == 2
        assert per_session[0]["total_tokens"] >= per_session[1]["total_tokens"]
        assert per_session[0]["session_id"] == "sess-large"
        assert per_session[1]["session_id"] == "sess-small"

    def test_usage_per_session_accumulates_multiple_entries(
        self, tmp_claude_dir, mock_jsonl_file,
    ):
        """Multiple entries in one session should sum up."""
        e1 = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=1),
            input_tokens=100,
            output_tokens=200,
        )
        e2 = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=2),
            input_tokens=300,
            output_tokens=400,
        )
        mock_jsonl_file("sess-multi", [e1, e2])

        from data.usage import get_usage_stats
        result = get_usage_stats()
        ps = result["per_session"]

        assert len(ps) == 1
        assert ps[0]["input_tokens"] == 400
        assert ps[0]["output_tokens"] == 600
        assert ps[0]["message_count"] == 2

    def test_usage_per_session_tracks_last_activity(
        self, tmp_claude_dir, mock_jsonl_file,
    ):
        """last_activity should reflect the most recent entry."""
        older = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=4),
            input_tokens=100,
            output_tokens=100,
        )
        newer = make_usage_entry(
            timestamp_iso=iso_now_minus(hours=1),
            input_tokens=100,
            output_tokens=100,
        )
        mock_jsonl_file("sess-activity", [older, newer])

        from data.usage import get_usage_stats
        result = get_usage_stats()
        ps = result["per_session"]
        assert len(ps) == 1
        # last_activity should be the newer timestamp (non-empty ISO string)
        assert ps[0]["last_activity"] != ""
