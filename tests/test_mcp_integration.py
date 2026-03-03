"""
Integration tests for docker_log_analyzer.mcp_server tool functions.

Requires Docker daemon to be running.
Tests marked with @pytest.mark.integration are skipped automatically
when Docker is unavailable (handled by the docker_client fixture).

To run only these tests:
  uv run pytest tests/test_mcp_integration.py -v
"""

import pytest

from docker_log_analyzer.mcp_server import (
    tool_list_containers,
    tool_analyze_patterns,
    tool_detect_error_spikes,
    tool_correlate_containers,
    tool_start_test_containers,
    tool_stop_test_containers,
)


pytestmark = pytest.mark.integration


# ── list_containers ───────────────────────────────────────────────────────────

class TestListContainers:

    def test_returns_success_status(self, docker_client):
        result = tool_list_containers()
        assert result["status"] == "success"

    def test_contains_containers_key(self, docker_client):
        result = tool_list_containers()
        assert "containers" in result
        assert isinstance(result["containers"], list)

    def test_contains_count_key(self, docker_client):
        result = tool_list_containers()
        assert "count" in result
        assert result["count"] == len(result["containers"])

    def test_container_fields_present(self, docker_client):
        result = tool_list_containers()
        for c in result["containers"]:
            assert "name" in c
            assert "short_id" in c
            assert "image" in c
            assert "status" in c
            assert "labels" in c

    def test_status_values_are_strings(self, docker_client):
        result = tool_list_containers()
        for c in result["containers"]:
            assert isinstance(c["name"], str)
            assert isinstance(c["status"], str)


# ── analyze_patterns ──────────────────────────────────────────────────────────

class TestAnalyzePatterns:

    def test_returns_success_status(self, docker_client):
        result = tool_analyze_patterns(tail=50)
        assert result["status"] == "success"

    def test_results_is_dict(self, docker_client):
        result = tool_analyze_patterns(tail=50)
        assert isinstance(result["results"], dict)

    def test_per_container_fields(self, docker_client):
        result = tool_analyze_patterns(tail=50)
        for name, data in result["results"].items():
            assert isinstance(name, str)
            if data.get("status") == "no_logs":
                continue
            assert "total_lines" in data
            assert "timestamp_format" in data
            assert "language" in data
            assert "language_confidence" in data
            assert "log_levels" in data
            assert "health_check" in data
            assert "common_errors" in data

    def test_language_confidence_range(self, docker_client):
        result = tool_analyze_patterns(tail=100)
        for data in result["results"].values():
            if "language_confidence" in data:
                assert 0.0 <= data["language_confidence"] <= 1.0

    def test_health_check_structure(self, docker_client):
        result = tool_analyze_patterns(tail=100)
        for data in result["results"].values():
            if "health_check" in data:
                hc = data["health_check"]
                assert "detected" in hc
                assert isinstance(hc["detected"], bool)

    def test_invalid_container_returns_error(self, docker_client):
        result = tool_analyze_patterns(container_name="nonexistent-container-xyz")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_tail_parameter_respected(self, docker_client):
        small = tool_analyze_patterns(tail=10)
        large = tool_analyze_patterns(tail=200)
        assert small["status"] == "success"
        assert large["status"] == "success"
        # More lines fetched → total_lines should be >= for same containers
        for name in small["results"]:
            if name in large["results"]:
                s = small["results"][name].get("total_lines", 0)
                l = large["results"][name].get("total_lines", 0)
                assert l >= s


# ── detect_error_spikes ───────────────────────────────────────────────────────

class TestDetectErrorSpikes:

    def test_returns_success_status(self, docker_client):
        result = tool_detect_error_spikes(tail=200, spike_threshold=1.5)
        assert result["status"] == "success"

    def test_spikes_is_list(self, docker_client):
        result = tool_detect_error_spikes(tail=200, spike_threshold=1.5)
        assert isinstance(result["spikes"], list)

    def test_spike_count_matches_list(self, docker_client):
        result = tool_detect_error_spikes(tail=200, spike_threshold=1.5)
        assert result["spike_count"] == len(result["spikes"])

    def test_spike_fields_present(self, docker_client):
        result = tool_detect_error_spikes(tail=500, spike_threshold=1.5)
        for s in result["spikes"]:
            assert "container" in s
            assert "bucket_minute" in s
            assert "error_count" in s
            assert "baseline" in s
            assert "ratio" in s

    def test_spike_ratio_exceeds_threshold(self, docker_client):
        threshold = 1.5
        result = tool_detect_error_spikes(tail=500, spike_threshold=threshold)
        for s in result["spikes"]:
            assert s["ratio"] > threshold

    def test_parameters_echoed_back(self, docker_client):
        result = tool_detect_error_spikes(tail=300, window_minutes=3, spike_threshold=2.5)
        assert result["parameters"]["tail"] == 300
        assert result["parameters"]["window_minutes"] == 3
        assert result["parameters"]["spike_threshold"] == 2.5

    def test_warnings_is_list(self, docker_client):
        result = tool_detect_error_spikes(tail=100)
        assert isinstance(result["warnings"], list)

    def test_invalid_container_returns_error(self, docker_client):
        result = tool_detect_error_spikes(container_name="nonexistent-xyz")
        assert result["status"] == "error"

    def test_high_threshold_reduces_spikes(self, docker_client):
        low  = tool_detect_error_spikes(tail=500, spike_threshold=1.5)
        high = tool_detect_error_spikes(tail=500, spike_threshold=100.0)
        assert low["spike_count"] >= high["spike_count"]


# ── correlate_containers ──────────────────────────────────────────────────────

class TestCorrelateContainers:

    def test_returns_success_status(self, docker_client):
        result = tool_correlate_containers(time_window_seconds=30, tail=200)
        assert result["status"] == "success"

    def test_correlations_is_list(self, docker_client):
        result = tool_correlate_containers(time_window_seconds=30, tail=200)
        assert isinstance(result["correlations"], list)

    def test_correlation_fields_present(self, docker_client):
        result = tool_correlate_containers(time_window_seconds=30, tail=200)
        for r in result["correlations"]:
            assert "container_a" in r
            assert "container_b" in r
            assert "correlation_score" in r
            assert "errors_a" in r
            assert "errors_b" in r
            assert "example_pairs" in r

    def test_score_in_range(self, docker_client):
        result = tool_correlate_containers(time_window_seconds=30, tail=300)
        for r in result["correlations"]:
            assert 0.0 <= r["correlation_score"] <= 1.0

    def test_sorted_by_score_descending(self, docker_client):
        result = tool_correlate_containers(time_window_seconds=30, tail=300)
        scores = [r["correlation_score"] for r in result["correlations"]]
        assert scores == sorted(scores, reverse=True)

    def test_parameters_echoed_back(self, docker_client):
        result = tool_correlate_containers(time_window_seconds=45, tail=150)
        assert result["parameters"]["time_window_seconds"] == 45
        assert result["parameters"]["tail"] == 150

    def test_example_pairs_at_most_three(self, docker_client):
        result = tool_correlate_containers(time_window_seconds=60, tail=500)
        for r in result["correlations"]:
            assert len(r["example_pairs"]) <= 3

    def test_wider_window_same_or_higher_score(self, docker_client):
        narrow = tool_correlate_containers(time_window_seconds=5,  tail=300)
        wide   = tool_correlate_containers(time_window_seconds=60, tail=300)
        # At least one pair should have a higher or equal score with the wider window
        if narrow["correlations"] and wide["correlations"]:
            max_narrow = max(r["correlation_score"] for r in narrow["correlations"])
            max_wide   = max(r["correlation_score"] for r in wide["correlations"])
            assert max_wide >= max_narrow


# ── start/stop test containers ────────────────────────────────────────────────

class TestTestContainerLifecycle:

    def test_start_returns_success_or_already_running(self, docker_client):
        result = tool_start_test_containers(rebuild=False)
        assert result["status"] == "success"
        assert "message" in result
        assert "compose_file" in result

    def test_stop_returns_success(self, docker_client):
        # Start first to ensure containers exist
        tool_start_test_containers(rebuild=False)
        result = tool_stop_test_containers()
        assert result["status"] == "success"
        assert "message" in result

    def test_start_after_stop(self, docker_client):
        tool_stop_test_containers()
        result = tool_start_test_containers(rebuild=False)
        assert result["status"] == "success"
