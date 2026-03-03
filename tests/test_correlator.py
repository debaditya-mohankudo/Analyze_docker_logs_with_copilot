"""
Unit tests for docker_log_analyzer.correlator.

All tests are self-contained (no Docker required).
"""

import pytest

from docker_log_analyzer.correlator import correlate, _extract_error_events


pytestmark = pytest.mark.unit


class TestCorrelate:

    def test_high_correlation_aligned_errors(self, corr_aligned_logs):
        results = correlate(corr_aligned_logs, time_window_seconds=30)
        assert len(results) == 1
        r = results[0]
        assert r["correlation_score"] > 0.5

    def test_zero_correlation_distant_errors(self, corr_distant_logs):
        results = correlate(corr_distant_logs, time_window_seconds=30)
        assert len(results) == 1
        assert results[0]["correlation_score"] == 0.0

    def test_single_container_returns_empty(self, corr_single_container):
        assert correlate(corr_single_container, time_window_seconds=30) == []

    def test_empty_input_returns_empty(self):
        assert correlate({}, time_window_seconds=30) == []

    def test_score_in_zero_one_range(self, corr_aligned_logs):
        results = correlate(corr_aligned_logs, time_window_seconds=30)
        for r in results:
            assert 0.0 <= r["correlation_score"] <= 1.0

    def test_result_fields_present(self, corr_aligned_logs):
        results = correlate(corr_aligned_logs, time_window_seconds=30)
        assert len(results) == 1
        r = results[0]
        expected_keys = {
            "container_a", "container_b", "correlation_score",
            "co_occurrences", "errors_a", "errors_b", "example_pairs",
        }
        assert set(r.keys()) == expected_keys

    def test_sorted_by_score_descending(self):
        logs = {
            "a": ["2024-03-02T21:10:00.000Z ERROR err"] * 5,
            "b": ["2024-03-02T21:10:01.000Z ERROR err"] * 5,  # very close → high score
            "c": ["2024-03-02T21:15:00.000Z ERROR err"] * 5,  # far away → low score
        }
        results = correlate(logs, time_window_seconds=10)
        scores = [r["correlation_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_example_pairs_limited_to_three(self):
        logs = {
            "a": [f"2024-03-02T21:10:{i:02d}.000Z ERROR err" for i in range(20)],
            "b": [f"2024-03-02T21:10:{i:02d}.000Z ERROR err" for i in range(20)],
        }
        results = correlate(logs, time_window_seconds=60)
        assert len(results) == 1
        assert len(results[0]["example_pairs"]) <= 3

    def test_example_pair_fields(self, corr_aligned_logs):
        results = correlate(corr_aligned_logs, time_window_seconds=30)
        for pair in results[0]["example_pairs"]:
            assert "a" in pair and "b" in pair and "delta_seconds" in pair
            assert pair["delta_seconds"] >= 0

    def test_narrow_window_reduces_correlation(self, corr_aligned_logs):
        wide  = correlate(corr_aligned_logs, time_window_seconds=60)
        narrow = correlate(corr_aligned_logs, time_window_seconds=1)
        assert wide[0]["correlation_score"] >= narrow[0]["correlation_score"]

    def test_no_error_lines_returns_empty(self):
        logs = {
            "a": ["2024-03-02T21:10:00.000Z INFO all fine"],
            "b": ["2024-03-02T21:10:01.000Z INFO all fine"],
        }
        # Both containers have no error events → correlate returns []
        assert correlate(logs, time_window_seconds=30) == []

    def test_one_empty_container_excluded(self):
        logs = {
            "a": ["2024-03-02T21:10:00.000Z ERROR err"],
            "b": [],  # no logs
        }
        assert correlate(logs, time_window_seconds=30) == []

    def test_three_containers_produces_three_pairs(self):
        def make_errors(minute_offset):
            return [f"2024-03-02T21:1{minute_offset}:00.000Z ERROR err"] * 3

        logs = {"a": make_errors(0), "b": make_errors(0), "c": make_errors(0)}
        results = correlate(logs, time_window_seconds=60)
        assert len(results) == 3
        pairs = {(r["container_a"], r["container_b"]) for r in results}
        assert len(pairs) == 3


class TestExtractErrorEvents:

    def test_extracts_errors_only(self):
        lines = [
            "2024-03-02T21:10:00.000Z ERROR something bad",
            "2024-03-02T21:10:01.000Z INFO all good",
            "2024-03-02T21:10:02.000Z CRITICAL meltdown",
        ]
        events = _extract_error_events(lines)
        assert len(events) == 2

    def test_skips_lines_without_timestamp(self):
        lines = [
            "ERROR no timestamp here",
            "2024-03-02T21:10:00.000Z ERROR with timestamp",
        ]
        events = _extract_error_events(lines)
        assert len(events) == 1

    def test_returns_unix_timestamps(self):
        lines = ["2024-03-02T21:10:00.000Z ERROR err"]
        events = _extract_error_events(lines)
        assert len(events) == 1
        ts, _ = events[0]
        assert isinstance(ts, float)
        assert ts > 0

    def test_empty_input(self):
        assert _extract_error_events([]) == []
