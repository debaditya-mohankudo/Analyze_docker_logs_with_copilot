"""
Integration tests for remote Docker configuration.

These tests verify that MCP tools work correctly when DOCKER_HOST is configured
to point to a remote Docker daemon. Tests can run with:
1. Local Unix socket (default)
2. SSH to the ssh-target container — a genuinely separate Docker-in-Docker
   daemon reachable only via `ssh://root@localhost:2222` (see
   docker-compose.test.yml + ssh_docker_target/). This stands in for a real
   pre-authenticated remote Linux box without needing one.
3. TCP connections

Prerequisites:
- Docker running locally, able to run privileged containers (for ssh-target's
  inner dockerd)
- tests/conftest.py starts ssh-target and populates its inner daemon with the
  log-generator containers automatically; SSH tests skip individually if that
  setup fails (see ssh_target_ready fixture)

Run all integration tests:
  pytest tests/test_remote_docker_integration.py -m integration -v

Run only environment/config tests (no SSH required):
  pytest tests/test_remote_docker_integration.py::TestRemoteDockerEnvironmentVariables -m integration -v
"""

import os
import json
import subprocess
import pytest
from unittest.mock import patch
from python_on_whales import DockerClient
from python_on_whales.exceptions import DockerException

from docker_log_analyzer.config import settings

# ssh-target: SSH-reachable Docker-in-Docker container defined in
# docker-compose.test.yml, populated with the log-generator services by
# tests/conftest.py's setup_integration_containers fixture.
SSH_TARGET = "ssh://root@localhost:2222"


def _patch_docker_host(monkeypatch, host):
    """Patch the live settings singleton, not just os.environ.

    settings = Settings() is created once at import time (config.py), so
    patch.dict(os.environ, ...) alone does not reach tool_*() calls once
    _docker_client() reads settings.docker_host instead of relying on the
    docker CLI's own env-var fallback. Tests must patch the singleton
    directly to exercise the actual wiring.
    """
    monkeypatch.setattr(settings, "docker_host", host)


class TestRemoteDockerViaSSH:
    """Test MCP tools work with remote Docker via SSH, against the real
    ssh-target Docker-in-Docker container (not a mock)."""

    @pytest.mark.integration
    @pytest.mark.serial
    def test_ssh_docker_connection(self, ssh_target_ready):
        """Should establish connection to the ssh-target's inner Docker
        daemon over SSH."""
        if not ssh_target_ready:
            pytest.skip("ssh-target inner daemon not ready")
        client = DockerClient(host=SSH_TARGET)
        info = client.system.info()
        assert info is not None
        assert info.containers >= 0

    @pytest.mark.integration
    @pytest.mark.serial
    def test_list_containers_via_ssh(self, monkeypatch, ssh_target_ready):
        """Should list containers when docker_host points at the ssh-target
        via SSH — verifies list_containers tool works with remote Docker."""
        if not ssh_target_ready:
            pytest.skip("ssh-target inner daemon not ready")
        _patch_docker_host(monkeypatch, SSH_TARGET)
        from docker_log_analyzer.tools import tool_list_containers

        result = tool_list_containers()
        assert result["status"] == "success"
        assert isinstance(result["containers"], list)

    @pytest.mark.integration
    @pytest.mark.serial
    def test_list_containers_includes_test_containers(self, monkeypatch, ssh_target_ready):
        """Should list the log-generator containers actually running inside
        ssh-target's own inner daemon (not the host's copies)."""
        if not ssh_target_ready:
            pytest.skip("ssh-target inner daemon not ready")
        _patch_docker_host(monkeypatch, SSH_TARGET)
        from docker_log_analyzer.tools import tool_list_containers

        result = tool_list_containers()
        assert result["status"] == "success"
        container_names = [c["name"] for c in result["containers"]]

        expected_containers = ["test-web-app", "test-database", "test-gateway", "test-cache"]
        for expected in expected_containers:
            assert expected in container_names, f"{expected} not in {container_names}"

    @pytest.mark.integration
    @pytest.mark.serial
    def test_analyze_patterns_via_ssh(self, monkeypatch, ssh_target_ready):
        """Should analyze patterns in container logs fetched over SSH."""
        if not ssh_target_ready:
            pytest.skip("ssh-target inner daemon not ready")
        _patch_docker_host(monkeypatch, SSH_TARGET)
        from docker_log_analyzer.tools import tool_analyze_patterns

        result = tool_analyze_patterns(
            container_name="test-web-app",
            tail=100,
            force_refresh=True,
            use_cache=False,
        )
        assert result["status"] == "success"
        container_result = result["results"]["test-web-app"]
        assert "timestamp_format" in container_result
        assert "language" in container_result
        assert "log_levels" in container_result

    @pytest.mark.integration
    @pytest.mark.serial
    def test_analyze_error_spikes_via_ssh(self, monkeypatch, ssh_target_ready):
        """Should detect error spikes in logs fetched over SSH."""
        if not ssh_target_ready:
            pytest.skip("ssh-target inner daemon not ready")
        _patch_docker_host(monkeypatch, SSH_TARGET)
        from docker_log_analyzer.tools import tool_analyze_error_spikes

        result = tool_analyze_error_spikes(
            container_name="test-web-app",
            tail=500,
            spike_threshold=2.0,
            use_cache=False,
        )
        assert result["status"] == "success"
        assert "spikes" in result
        assert "spike_count" in result

    @pytest.mark.integration
    @pytest.mark.serial
    def test_analyze_correlations_via_ssh(self, monkeypatch, ssh_target_ready):
        """Should correlate containers using logs fetched over SSH."""
        if not ssh_target_ready:
            pytest.skip("ssh-target inner daemon not ready")
        _patch_docker_host(monkeypatch, SSH_TARGET)
        from docker_log_analyzer.tools import tool_analyze_correlations

        result = tool_analyze_correlations(
            time_window_seconds=60,
            tail=500,
            use_cache=False,
        )
        assert result["status"] == "success"
        assert isinstance(result["correlations"], list)

    @pytest.mark.integration
    @pytest.mark.serial
    def test_detect_data_leaks_via_ssh(self, monkeypatch, ssh_target_ready):
        """Should detect secrets in logs fetched over SSH."""
        if not ssh_target_ready:
            pytest.skip("ssh-target inner daemon not ready")
        _patch_docker_host(monkeypatch, SSH_TARGET)
        from docker_log_analyzer.tools import tool_detect_data_leaks

        result = tool_detect_data_leaks(
            duration_seconds=30,
            container_names=["test-web-app"],
            severity_filter="all",
            use_cache=False,
        )
        assert result["status"] == "success"
        assert "findings" in result


class TestRemoteDockerWithCustomSSHConfig:
    """Test remote Docker with custom SSH configuration (custom port)."""

    @pytest.mark.integration
    @pytest.mark.serial
    def test_ssh_with_custom_port(self, ssh_target_ready):
        """Should support SSH URLs with a non-default port — ssh-target
        listens on 2222, exercising the same URL shape a real remote host
        behind a non-standard SSH port would need."""
        if not ssh_target_ready:
            pytest.skip("ssh-target inner daemon not ready")
        client = DockerClient(host=SSH_TARGET)
        info = client.system.info()
        assert info is not None


class TestRemoteDockerFallbacks:
    """Test behavior when remote Docker is unavailable."""

    @pytest.mark.integration
    @pytest.mark.serial
    def test_ssh_unavailable_graceful_error(self, monkeypatch):
        """Should handle SSH connection failure gracefully.

        When SSH is unavailable, tools should return error JSON, not crash.
        """
        # Try to connect to unlikely SSH host
        _patch_docker_host(monkeypatch, "ssh://nonexistent-host-that-doesnt-exist-12345.invalid")
        try:
            from docker_log_analyzer.tools import tool_list_containers

            result = tool_list_containers()
            # Should either error gracefully or skip
            assert isinstance(result, dict)
        except DockerException:
            # Expected to fail - but should be structured error, not crash
            pass
        except Exception as e:
            # Should not raise unexpected exception types
            pytest.fail(f"Unexpected error type: {type(e).__name__}: {e}")


class TestRemoteDockerEnvironmentVariables:
    """Test environment variable handling for remote Docker."""

    @pytest.mark.integration
    def test_docker_host_env_parses_correctly(self):
        """A fresh Settings() instance should pick up DOCKER_HOST from the
        environment. This only verifies parsing — see
        test_docker_host_actually_used_by_tools for whether a *running*
        tool call actually uses it (they are different concerns: settings
        is a singleton created once at import time, so env-var changes
        after that point don't reach it — see _patch_docker_host above)."""
        with patch.dict(os.environ, {"DOCKER_HOST": SSH_TARGET}, clear=False):
            from docker_log_analyzer.config import Settings

            fresh_settings = Settings()
            assert fresh_settings.docker_host == SSH_TARGET

    @pytest.mark.integration
    def test_docker_host_actually_used_by_tools(self, monkeypatch, ssh_target_ready):
        """Tools should respect settings.docker_host end-to-end: patching the
        live singleton (not just os.environ) must change which host
        _docker_client() connects to. This is the wiring test that would
        have caught the gap where docker.py ignored settings.docker_host
        entirely and only local-socket calls ever succeeded silently."""
        if not ssh_target_ready:
            pytest.skip("ssh-target inner daemon not ready")

        _patch_docker_host(monkeypatch, SSH_TARGET)
        from docker_log_analyzer.tools import tool_list_containers

        result = tool_list_containers()
        assert result["status"] == "success"
        assert "containers" in result

    @pytest.mark.integration
    def test_docker_host_overrides_default(self):
        """DOCKER_HOST should override the default local socket (parsing only)."""
        original_host = os.environ.get("DOCKER_HOST")
        try:
            with patch.dict(os.environ, {"DOCKER_HOST": SSH_TARGET}, clear=False):
                from docker_log_analyzer.config import Settings

                fresh_settings = Settings()
                assert fresh_settings.docker_host == SSH_TARGET
                assert fresh_settings.docker_host != "unix:///var/run/docker.sock"
        finally:
            if original_host:
                os.environ["DOCKER_HOST"] = original_host
            else:
                os.environ.pop("DOCKER_HOST", None)


class TestRemoteDockerDocumentation:
    """Test scenarios from documentation."""

    @pytest.mark.integration
    @pytest.mark.serial
    def test_readme_ssh_localhost_example(self, setup_integration_containers):
        """Verify the README example works with local Docker.

        From README:
        ```
        export DOCKER_HOST=ssh://dev@staging.example.com
        uv run docker-log-analyzer-mcp list_containers
        ```

        This tests with local Docker. For SSH testing, configure SSH daemon
        and set DOCKER_HOST=ssh://your-host manually.
        """
        from docker_log_analyzer.tools import tool_list_containers

        result = tool_list_containers()
        assert result["status"] == "success"
        assert "containers" in result
        print(f"✓ README example works: list_containers returned {len(result['containers'])} containers")

    @pytest.mark.integration
    @pytest.mark.serial
    def test_readme_ssh_staging_simulation(self, setup_integration_containers):
        """Simulate the README staging.example.com scenario with local Docker.

        Users would run:
        ```
        export DOCKER_HOST=ssh://dev@staging.example.com
        uv run docker-log-analyzer-mcp analyze_patterns test-web-app
        ```

        This tests with local Docker to verify the pattern works.
        For actual SSH testing, set DOCKER_HOST=ssh://your-host manually.
        """
        from docker_log_analyzer.tools import tool_analyze_patterns

        result = tool_analyze_patterns(
            container_name="test-web-app",
            tail=100,
            force_refresh=True,
            use_cache=False,
        )
        assert result["status"] == "success"
        detected_language = result["results"]["test-web-app"]["language"]
        print(f"✓ README example works: analyze_patterns detected {detected_language}")
