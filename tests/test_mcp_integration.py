"""
Integration tests for docker_log_analyzer.mcp_server tool functions.

Requires Docker daemon to be running.
Tests marked with @pytest.mark.integration are skipped automatically
when Docker is unavailable (handled by the docker_client fixture).

To run only these tests:
  uv run pytest tests/test_mcp_integration.py -v
"""

import pytest

from docker_log_analyzer.tools import (
    tool_list_containers,
    tool_analyze_patterns,
    tool_detect_error_spikes,
    tool_correlate_containers,
    tool_detect_data_leaks,
    tool_map_service_dependencies,
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
        """Test that start_test_containers returns success when containers already exist."""
        result = tool_start_test_containers(rebuild=False)
        assert result["status"] == "success"
        assert "message" in result
        assert "compose_file" in result

    def test_stop_returns_success(self, docker_client):
        """Test that stop_test_containers returns success."""
        # Don't actually stop containers during tests since they're needed by other tests
        # Just verify the function exists and can be called
        result = tool_stop_test_containers()
        assert result["status"] == "success"
        assert "message" in result
        
        # Restart containers so other tests can use them
        tool_start_test_containers(rebuild=False)


# ── detect_data_leaks ──────────────────────────────────────────────────────────

class TestDetectDataLeaks:

    def test_returns_success_status(self, docker_client, setup_integration_containers):
        import asyncio
        result = asyncio.run(tool_detect_data_leaks(duration_seconds=1))
        assert result["status"] == "success"

    def test_contains_required_fields(self, docker_client, setup_integration_containers):
        import asyncio
        result = asyncio.run(tool_detect_data_leaks(duration_seconds=1))
        assert "scan_window" in result
        assert "findings" in result
        assert "summary" in result
        assert "recommendations" in result

    def test_scan_window_has_timestamps(self, docker_client, setup_integration_containers):
        import asyncio
        result = asyncio.run(tool_detect_data_leaks(duration_seconds=1))
        scan_window = result["scan_window"]
        assert "start" in scan_window
        assert "end" in scan_window
        assert "duration_seconds" in scan_window

    def test_findings_is_list(self, docker_client, setup_integration_containers):
        import asyncio
        result = asyncio.run(tool_detect_data_leaks(duration_seconds=1))
        assert isinstance(result["findings"], list)

    def test_finding_fields_present(self, docker_client, setup_integration_containers):
        import asyncio
        result = asyncio.run(tool_detect_data_leaks(duration_seconds=5))
        if result["findings"]:
            for finding in result["findings"]:
                assert "container" in finding
                assert "severity" in finding
                assert "pattern_name" in finding
                assert "matched_text" in finding
                assert "line_number" in finding
                assert "context_before" in finding
                assert "context_after" in finding
                # Secrets should be redacted
                assert "*" in finding["matched_text"] or finding["severity"] == "low"

    def test_summary_counts_findings(self, docker_client, setup_integration_containers):
        import asyncio
        result = asyncio.run(tool_detect_data_leaks(duration_seconds=1))
        summary = result["summary"]
        assert "total_findings" in summary
        assert "by_severity" in summary
        assert all(k in summary["by_severity"] for k in ["critical", "high", "medium", "low"])

    def test_filters_by_severity_critical(self, docker_client, setup_integration_containers):
        import asyncio
        result = asyncio.run(tool_detect_data_leaks(
            duration_seconds=5,
            severity_filter="critical"
        ))
        # If there are findings, they should all be critical
        for finding in result["findings"]:
            assert finding["severity"] == "critical"

    def test_filters_by_severity_high(self, docker_client, setup_integration_containers):
        import asyncio
        result = asyncio.run(tool_detect_data_leaks(
            duration_seconds=5,
            severity_filter="high"
        ))
        # If there are findings, they should be critical or high
        for finding in result["findings"]:
            assert finding["severity"] in ("critical", "high")

    def test_specific_container_filtering(self, docker_client, setup_integration_containers):
        import asyncio
        result = asyncio.run(tool_detect_data_leaks(
            duration_seconds=1,
            container_names=["test-web-app"]
        ))
        assert result["status"] == "success"
        # All findings should be from the specified container
        for finding in result["findings"]:
            assert "test-web-app" in finding["container"]

    def test_recommendations_present(self, docker_client, setup_integration_containers):
        import asyncio
        result = asyncio.run(tool_detect_data_leaks(duration_seconds=5))
        assert isinstance(result["recommendations"], list)
        # If critical findings, should recommend action
        if result["summary"]["by_severity"]["critical"] > 0:
            assert len(result["recommendations"]) > 0
            assert any("crit" in r.lower() for r in result["recommendations"])

    def test_invalid_container_name_error(self, docker_client):
        import asyncio
        result = asyncio.run(tool_detect_data_leaks(
            duration_seconds=1,
            container_names=["nonexistent-container-xyz"]
        ))
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()


# ── map_service_dependencies ───────────────────────────────────────────────────

class TestMapServiceDependencies:

    def test_returns_success_status(self, docker_client):
        result = tool_map_service_dependencies(tail=100)
        assert result["status"] == "success"

    def test_required_top_level_keys(self, docker_client):
        result = tool_map_service_dependencies(tail=100)
        assert "dependencies" in result
        assert "cascade_candidates" in result
        assert "cache_hits" in result
        assert "parameters" in result

    def test_dependencies_is_dict(self, docker_client):
        result = tool_map_service_dependencies(tail=100)
        assert isinstance(result["dependencies"], dict)

    def test_cascade_candidates_is_list(self, docker_client):
        result = tool_map_service_dependencies(tail=100)
        assert isinstance(result["cascade_candidates"], list)

    def test_cache_hits_is_dict(self, docker_client):
        result = tool_map_service_dependencies(tail=100)
        assert isinstance(result["cache_hits"], dict)

    def test_parameters_echoed_back(self, docker_client):
        result = tool_map_service_dependencies(tail=200, include_transitive=True)
        assert result["parameters"]["tail"] == 200
        assert result["parameters"]["include_transitive"] is True

    def test_dependency_edge_fields(self, docker_client):
        result = tool_map_service_dependencies(tail=200)
        for _src, edges in result["dependencies"].items():
            assert isinstance(edges, list)
            for edge in edges:
                assert "target" in edge
                assert "inferred_from" in edge
                assert "confidence" in edge
                assert "hit_count" in edge
                assert edge["confidence"] in ("high", "medium", "low")

    def test_cascade_candidate_fields(self, docker_client):
        result = tool_map_service_dependencies(tail=500)
        for c in result["cascade_candidates"]:
            assert "from" in c
            assert "to" in c
            assert "dependency_type" in c
            assert "correlation_score" in c
            assert "confidence" in c
            assert "evidence" in c
            assert c["confidence"] in ("high", "medium", "low")
            assert 0.0 <= c["correlation_score"] <= 1.0

    def test_specific_containers_filter(self, docker_client):
        result = tool_map_service_dependencies(
            containers=["test-web-app"], tail=100
        )
        assert result["status"] == "success"
        # cache_hits should only include the requested container
        assert set(result["cache_hits"].keys()).issubset({"test-web-app"})

    def test_include_transitive_flag_accepted(self, docker_client):
        result = tool_map_service_dependencies(tail=100, include_transitive=True)
        assert result["status"] == "success"
        # Verify any transitive edge is properly labelled
        for _src, edges in result["dependencies"].items():
            for edge in edges:
                if edge["inferred_from"] == "transitive":
                    assert edge["confidence"] == "low"
                    assert edge["hit_count"] == 0

    def test_invalid_container_returns_error(self, docker_client):
        result = tool_map_service_dependencies(containers=["nonexistent-xyz"])
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()
