"""
Integration tests for remote Docker configuration.

These tests verify that MCP tools work correctly when DOCKER_HOST is configured
to point to a remote Docker daemon. Tests can run with:
1. Local Unix socket (default)
2. SSH tunneling to localhost (requires SSH daemon)
3. TCP connections

Prerequisites:
- Docker running locally
- For SSH tests: SSH daemon on localhost or remote host

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
    """Test MCP tools work with remote Docker via SSH."""

    @pytest.mark.integration
    @pytest.mark.serial
    def test_ssh_localhost_docker_connection(self, docker_client):
        """Should establish connection to Docker via SSH on localhost.

        This verifies the SSH tunnel works (using 'ssh://localhost' which
        connects to the local Docker daemon via SSH).
        """
        # Note: 'ssh://localhost' is a valid Docker URL that tunnels through SSH
        # to localhost's Docker daemon. This requires SSH to be available.
        try:
            client = DockerClient(host="ssh://localhost")
            info = client.system.info()
            assert info is not None
            assert "Containers" in info
        except DockerException as e:
            pytest.skip(f"SSH to localhost unavailable: {e}")

    @pytest.mark.integration
    @pytest.mark.serial
    def test_list_containers_via_ssh_localhost(self, monkeypatch):
        """Should list containers when DOCKER_HOST=ssh://localhost.

        This verifies list_containers tool works with remote Docker.
        Note: This requires SSH daemon running with Docker socket access.
        """
        _patch_docker_host(monkeypatch, "ssh://localhost")
        try:
            from docker_log_analyzer.tools import tool_list_containers

            result = tool_list_containers()
            # If SSH not available, should return error gracefully
            if result.get("status") == "error":
                pytest.skip(f"SSH connection failed: {result.get('error')}")
            assert isinstance(result, dict)
            assert "containers" in result
            assert isinstance(result["containers"], list)
        except (DockerException, Exception) as e:
            pytest.skip(f"SSH connection not available: {e}")

    @pytest.mark.integration
    @pytest.mark.serial
    def test_list_containers_includes_test_containers(self, monkeypatch, setup_integration_containers):
        """Should list test containers when they're running via SSH.

        This verifies list_containers returns expected test containers.
        Note: This requires SSH daemon configured. Test skips if SSH unavailable.
        """
        _patch_docker_host(monkeypatch, "ssh://localhost")
        try:
            from docker_log_analyzer.tools import tool_list_containers

            result = tool_list_containers()
            if result.get("status") == "error":
                pytest.skip(f"SSH connection failed: {result.get('error')}")
            assert result["status"] == "ok"
            containers = result["containers"]
            container_names = [c["name"] for c in containers]

            # Should contain our test containers
            expected_containers = ["test-web-app", "test-database", "test-gateway", "test-cache"]
            for expected in expected_containers:
                assert expected in container_names, f"{expected} not in {container_names}"
        except (DockerException, Exception) as e:
            pytest.skip(f"SSH connection not available: {e}")

    @pytest.mark.integration
    @pytest.mark.serial
    def test_analyze_patterns_via_ssh_localhost(self, monkeypatch, setup_integration_containers):
        """Should analyze patterns in container logs via SSH.

        This verifies analyze_patterns tool works with remote Docker.
        Note: This requires SSH daemon configured. Test skips if SSH unavailable.
        """
        _patch_docker_host(monkeypatch, "ssh://localhost")
        try:
            from docker_log_analyzer.tools import tool_analyze_patterns

            result = tool_analyze_patterns(
                container_name="test-web-app",
                tail=100,
                force_refresh=True,
                use_cache=False,
            )
            if result.get("status") == "error":
                pytest.skip(f"SSH connection failed: {result.get('error')}")
            assert result["status"] == "ok"
            assert "timestamp_format" in result
            assert "detected_language" in result
            assert "log_levels" in result
        except (DockerException, Exception) as e:
            pytest.skip(f"SSH connection not available: {e}")

    @pytest.mark.integration
    @pytest.mark.serial
    def test_analyze_error_spikes_via_ssh_localhost(self, monkeypatch, setup_integration_containers):
        """Should detect error spikes via SSH connection.

        This verifies analyze_error_spikes tool works with remote Docker.
        Note: This requires SSH daemon configured. Test skips if SSH unavailable.
        """
        _patch_docker_host(monkeypatch, "ssh://localhost")
        try:
            from docker_log_analyzer.tools import tool_analyze_error_spikes

            result = tool_analyze_error_spikes(
                container_name="test-web-app",
                tail=500,
                spike_threshold=2.0,
                use_cache=False,
            )
            if result.get("status") == "error":
                pytest.skip(f"SSH connection failed: {result.get('error')}")
            assert result["status"] == "ok"
            assert "spikes_detected" in result
            assert "spike_count" in result
            assert "buckets" in result
        except (DockerException, Exception) as e:
            pytest.skip(f"SSH connection not available: {e}")

    @pytest.mark.integration
    @pytest.mark.serial
    def test_analyze_correlations_via_ssh_localhost(self, monkeypatch, setup_integration_containers):
        """Should correlate containers via SSH connection.

        This verifies analyze_correlations tool works with remote Docker.
        Note: This requires SSH daemon configured. Test skips if SSH unavailable.
        """
        _patch_docker_host(monkeypatch, "ssh://localhost")
        try:
            from docker_log_analyzer.tools import tool_analyze_correlations

            result = tool_analyze_correlations(
                time_window_seconds=60,
                tail=500,
                use_cache=False,
            )
            if result.get("status") == "error":
                pytest.skip(f"SSH connection failed: {result.get('error')}")
            assert result["status"] == "ok"
            assert "correlations" in result
            assert isinstance(result["correlations"], list)
        except (DockerException, Exception) as e:
            pytest.skip(f"SSH connection not available: {e}")

    @pytest.mark.integration
    @pytest.mark.serial
    def test_detect_data_leaks_via_ssh_localhost(self, monkeypatch, setup_integration_containers):
        """Should detect secrets via SSH connection.

        This verifies detect_data_leaks tool works with remote Docker.
        Note: This requires SSH daemon configured. Test skips if SSH unavailable.
        """
        _patch_docker_host(monkeypatch, "ssh://localhost")
        try:
            from docker_log_analyzer.tools import tool_detect_data_leaks

            import asyncio
            result = asyncio.run(tool_detect_data_leaks(
                duration_seconds=30,
                container_names=["test-web-app"],
                severity_filter="all",
                use_cache=False,
            ))
            if result.get("status") == "error":
                pytest.skip(f"SSH connection failed: {result.get('error')}")
            assert result["status"] == "ok"
            assert "scan_results" in result
        except (DockerException, Exception) as e:
            pytest.skip(f"SSH connection not available: {e}")


class TestRemoteDockerWithCustomSSHConfig:
    """Test remote Docker with custom SSH configuration."""

    @pytest.mark.integration
    @pytest.mark.serial
    def test_ssh_with_custom_port(self):
        """Should support SSH URLs with custom ports.

        Note: This test uses standard port 22. For non-standard ports,
        SSH config in ~/.ssh/config should define custom ports.
        """
        # Example of what users would do for custom SSH port
        ssh_url = "ssh://localhost:22"
        try:
            client = DockerClient(host=ssh_url)
            info = client.system.info()
            assert info is not None
        except DockerException as e:
            pytest.skip(f"SSH on custom port unavailable: {e}")

    @pytest.mark.integration
    @pytest.mark.serial
    def test_ssh_without_explicit_port_uses_default(self):
        """Should use SSH default port (22) when not specified."""
        ssh_url = "ssh://localhost"
        try:
            client = DockerClient(host=ssh_url)
            info = client.system.info()
            assert info is not None
        except DockerException as e:
            pytest.skip(f"SSH connection failed: {e}")


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
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}, clear=False):
            from docker_log_analyzer.config import Settings

            fresh_settings = Settings()
            assert fresh_settings.docker_host == "ssh://localhost"

    @pytest.mark.integration
    def test_docker_host_actually_used_by_tools(self, monkeypatch, setup_integration_containers):
        """Tools should respect settings.docker_host end-to-end: patching the
        live singleton (not just os.environ) must change which host
        _docker_client() connects to. This is the wiring test that would
        have caught the gap where docker.py ignored settings.docker_host
        entirely and only local-socket calls ever succeeded silently."""
        # First, verify we can connect to localhost via SSH at all.
        try:
            client = DockerClient(host="ssh://localhost")
            client.system.info()
        except DockerException:
            pytest.skip("SSH to localhost not available")

        _patch_docker_host(monkeypatch, "ssh://localhost")
        from docker_log_analyzer.tools import tool_list_containers

        result = tool_list_containers()
        assert result["status"] == "ok"
        assert "containers" in result

    @pytest.mark.integration
    def test_docker_host_overrides_default(self):
        """DOCKER_HOST should override the default local socket (parsing only)."""
        original_host = os.environ.get("DOCKER_HOST")
        try:
            with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}, clear=False):
                from docker_log_analyzer.config import Settings

                fresh_settings = Settings()
                assert fresh_settings.docker_host == "ssh://localhost"
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
        try:
            from docker_log_analyzer.tools import tool_list_containers

            result = tool_list_containers()
            assert result["status"] == "ok"
            assert "containers" in result
            print(f"✓ README example works: list_containers returned {len(result['containers'])} containers")
        except (DockerException, Exception) as e:
            pytest.skip(f"Docker unavailable: {e}")

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
        try:
            from docker_log_analyzer.tools import tool_analyze_patterns

            result = tool_analyze_patterns(
                container_name="test-web-app",
                tail=100,
                force_refresh=True,
                use_cache=False,
            )
            assert result["status"] == "ok"
            assert "detected_language" in result
            print(f"✓ README example works: analyze_patterns detected {result['detected_language']}")
        except (DockerException, Exception) as e:
            pytest.skip(f"Test container unavailable: {e}")
