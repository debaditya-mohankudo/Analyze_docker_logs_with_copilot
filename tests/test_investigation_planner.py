"""
Unit tests for docker_log_analyzer.investigation_planner.

All tests are self-contained (no Docker required).
"""

from __future__ import annotations

import pytest
from pathlib import Path

from docker_log_analyzer.investigation_planner import (
    classify_symptoms,
    generate_plan,
    VALID_FOCUSES,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# classify_symptoms
# --------------------------------------------------------------------------- #

class TestClassifySymptoms:
    def test_crash_keywords(self):
        signals = classify_symptoms(["payment-service throwing 500 errors"])
        assert "crash" in signals

    def test_spike_keywords(self):
        signals = classify_symptoms(["high latency in checkout service"])
        assert "spike" in signals

    def test_cascade_keywords(self):
        signals = classify_symptoms(["connection refused from database", "downstream failure"])
        assert "cascade" in signals

    def test_security_keywords(self):
        signals = classify_symptoms(["API token exposed in logs"])
        assert "security" in signals

    def test_pattern_keywords(self):
        signals = classify_symptoms(["detecting log level distribution"])
        assert "pattern" in signals

    def test_multiple_signals(self):
        signals = classify_symptoms([
            "payment-service 500 errors",
            "high latency",
            "connection refused",
        ])
        assert {"crash", "spike", "cascade"}.issubset(signals)

    def test_no_match_defaults_empty(self):
        signals = classify_symptoms(["everything is fine"])
        assert signals == set()

    def test_case_insensitive(self):
        signals = classify_symptoms(["PANIC in goroutine", "TIMEOUT on DB"])
        assert "crash" in signals
        assert "spike" in signals


# --------------------------------------------------------------------------- #
# generate_plan – return structure
# --------------------------------------------------------------------------- #

class TestGeneratePlanStructure:
    def test_returns_success_status(self, tmp_path):
        result = generate_plan(["payment-service 500 errors"], plans_dir=tmp_path)
        assert result["status"] == "success"

    def test_contains_required_keys(self, tmp_path):
        result = generate_plan(["some error"], plans_dir=tmp_path)
        for key in ("signals_detected", "focus", "containers_in_scope", "step_count", "plan", "plan_file"):
            assert key in result, f"missing key: {key}"

    def test_plan_is_list_of_dicts(self, tmp_path):
        result = generate_plan(["error in gateway"], plans_dir=tmp_path)
        assert isinstance(result["plan"], list)
        for step in result["plan"]:
            assert "step" in step
            assert "action" in step
            assert "reason" in step

    def test_step_numbers_sequential(self, tmp_path):
        result = generate_plan(["high latency"], plans_dir=tmp_path)
        numbers = [s["step"] for s in result["plan"]]
        assert numbers == list(range(1, len(numbers) + 1))

    def test_first_step_always_list_containers(self, tmp_path):
        result = generate_plan(["something crashed"], plans_dir=tmp_path)
        assert result["plan"][0]["action"] == "list_containers"

    def test_last_step_analyze_root_causes_for_general_focus(self, tmp_path):
        result = generate_plan(["some crash"], focus="general", plans_dir=tmp_path)
        assert result["plan"][-1]["action"] == "analyze_root_causes"

    def test_step_count_matches_plan_length(self, tmp_path):
        result = generate_plan(["error"], plans_dir=tmp_path)
        assert result["step_count"] == len(result["plan"])


# --------------------------------------------------------------------------- #
# generate_plan – focus filtering
# --------------------------------------------------------------------------- #

class TestGeneratePlanFocus:
    def test_security_focus_includes_detect_data_leaks(self, tmp_path):
        result = generate_plan(["unauthorized access"], focus="security", plans_dir=tmp_path)
        actions = [s["action"] for s in result["plan"]]
        assert "detect_data_leaks" in actions

    def test_security_focus_skips_root_cause(self, tmp_path):
        # Security-only focus: no crash/cascade signals detected → root cause step omitted
        result = generate_plan(["API key exposed in logs"], focus="security", plans_dir=tmp_path)
        actions = [s["action"] for s in result["plan"]]
        assert "analyze_root_causes" not in actions

    def test_performance_focus_includes_spike_detection(self, tmp_path):
        result = generate_plan(["high latency"], focus="performance", plans_dir=tmp_path)
        actions = [s["action"] for s in result["plan"]]
        assert "analyze_error_spikes" in actions

    def test_root_cause_focus_includes_dependency_map(self, tmp_path):
        result = generate_plan(["service crash"], focus="root_cause", plans_dir=tmp_path)
        actions = [s["action"] for s in result["plan"]]
        assert "map_service_dependencies" in actions

    def test_invalid_focus_falls_back_to_general(self, tmp_path):
        result = generate_plan(["error"], focus="nonexistent", plans_dir=tmp_path)
        assert result["focus"] == "general"

    def test_focus_reflected_in_result(self, tmp_path):
        result = generate_plan(["crash"], focus="root_cause", plans_dir=tmp_path)
        assert result["focus"] == "root_cause"

    def test_security_focus_with_unrelated_symptoms_excludes_spike_tools(self, tmp_path):
        """focus='security' + latency symptoms must not activate spike/cascade tools.
        Regression: fallback `or signals` caused unrelated signals to bleed in."""
        result = generate_plan(["high latency and slow response"], focus="security", plans_dir=tmp_path)
        actions = [s["action"] for s in result["plan"]]
        assert "detect_data_leaks" in actions, "security focus must include detect_data_leaks"
        assert "analyze_error_spikes" not in actions, "security focus must not activate spike tools"
        assert "analyze_correlations" not in actions, "security focus must not activate correlation tools"
        assert "map_service_dependencies" not in actions, "security focus must not activate dependency map"

    def test_performance_focus_with_unrelated_symptoms_excludes_security_tools(self, tmp_path):
        """focus='performance' + credential symptoms must not activate security tools."""
        result = generate_plan(["API token exposed"], focus="performance", plans_dir=tmp_path)
        actions = [s["action"] for s in result["plan"]]
        assert "analyze_error_spikes" in actions, "performance focus must include spike detection"
        assert "detect_data_leaks" not in actions, "performance focus must not activate secret scanner"

    def test_focus_active_signals_are_subset_of_focus_set(self, tmp_path):
        """signals_detected in result must always be a subset of the focus's allowed signals."""
        focus_allowed = {
            "root_cause": {"crash", "cascade", "spike"},
            "security": {"security"},
            "performance": {"spike"},
        }
        for focus, allowed in focus_allowed.items():
            result = generate_plan(
                ["500 error latency token exposed connection refused"],
                focus=focus,
                plans_dir=tmp_path,
            )
            detected = set(result["signals_detected"])
            assert detected <= allowed, (
                f"focus={focus!r}: signals_detected {detected} not a subset of {allowed}"
            )


# --------------------------------------------------------------------------- #
# generate_plan – containers scoping
# --------------------------------------------------------------------------- #

class TestGeneratePlanContainers:
    def test_containers_in_scope_when_provided(self, tmp_path):
        result = generate_plan(
            ["payment-service 500 errors"],
            containers=["payment-service", "api-gateway"],
            plans_dir=tmp_path,
        )
        assert result["containers_in_scope"] == ["payment-service", "api-gateway"]

    def test_containers_default_to_all(self, tmp_path):
        result = generate_plan(["error"], plans_dir=tmp_path)
        assert result["containers_in_scope"] == ["all"]

    def test_targeted_steps_include_container_name(self, tmp_path):
        result = generate_plan(
            ["payment-service 500 errors"],
            containers=["payment-service"],
            plans_dir=tmp_path,
        )
        targeted = [s for s in result["plan"] if s.get("target") == "payment-service"]
        assert len(targeted) > 0


# --------------------------------------------------------------------------- #
# generate_plan – Markdown file output
# --------------------------------------------------------------------------- #

class TestGeneratePlanMarkdownFile:
    def test_plan_file_is_created(self, tmp_path):
        result = generate_plan(["some error"], plans_dir=tmp_path)
        path = Path(result["plan_file"])
        assert path.exists()

    def test_plan_file_has_md_extension(self, tmp_path):
        result = generate_plan(["some error"], plans_dir=tmp_path)
        assert result["plan_file"].endswith(".md")

    def test_plan_file_contains_symptoms(self, tmp_path):
        symptom = "payment-service returning 500s"
        result = generate_plan([symptom], plans_dir=tmp_path)
        content = Path(result["plan_file"]).read_text()
        assert symptom in content

    def test_plan_file_contains_all_action_names(self, tmp_path):
        result = generate_plan(["crash in service"], focus="general", plans_dir=tmp_path)
        content = Path(result["plan_file"]).read_text()
        for step in result["plan"]:
            assert step["action"] in content

    def test_plan_file_has_investigation_plan_header(self, tmp_path):
        result = generate_plan(["error"], plans_dir=tmp_path)
        content = Path(result["plan_file"]).read_text()
        assert "# Investigation Plan" in content

    def test_plan_file_name_contains_focus(self, tmp_path):
        result = generate_plan(["error"], focus="security", plans_dir=tmp_path)
        assert "security" in result["plan_file"]

    def test_multiple_plans_create_separate_files(self, tmp_path):
        r1 = generate_plan(["error 1"], plans_dir=tmp_path)
        import time; time.sleep(1)  # ensure different timestamp
        r2 = generate_plan(["error 2"], plans_dir=tmp_path)
        assert r1["plan_file"] != r2["plan_file"]

    def test_no_symptoms_uses_crash_default(self, tmp_path):
        result = generate_plan([""], plans_dir=tmp_path)
        assert result["signals_detected"] == ["crash"]


# --------------------------------------------------------------------------- #
# get_last_errors step contract
# --------------------------------------------------------------------------- #

class TestGetLastErrorsStepContract:
    """get_last_errors requires container_name; the plan must never emit it
    without a valid container_name in parameters (regression for code-review
    finding: broad flow emitted target=None with missing container_name)."""

    def test_no_get_last_errors_when_no_containers(self, tmp_path):
        """Broad (all-containers) flow must not emit a get_last_errors step."""
        for focus in ("general", "root_cause", "performance", "security"):
            result = generate_plan(
                ["service throwing 500 errors with connection refused"],
                focus=focus,
                containers=None,
                plans_dir=tmp_path,
            )
            actions = [s["action"] for s in result["plan"]]
            assert "get_last_errors" not in actions, (
                f"focus={focus!r}: get_last_errors emitted without containers — "
                "container_name would be missing from parameters"
            )

    def test_get_last_errors_has_container_name_when_scoped(self, tmp_path):
        """When containers are specified every get_last_errors step must carry
        container_name in its parameters field."""
        result = generate_plan(
            ["500 errors and connection refused"],
            focus="root_cause",
            containers=["api", "db"],
            plans_dir=tmp_path,
        )
        for step in result["plan"]:
            if step["action"] == "get_last_errors":
                assert "container_name" in (step.get("parameters") or {}), (
                    f"Step {step['step']} get_last_errors missing container_name: {step}"
                )
                assert step.get("target") is not None, (
                    f"Step {step['step']} get_last_errors has target=None"
                )
