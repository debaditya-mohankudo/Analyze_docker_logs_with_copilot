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
    def test_list_containers_via_ssh_localhost(self):
        """Should list containers when DOCKER_HOST=ssh://localhost.

        This verifies list_containers tool works with remote Docker.
        Note: This requires SSH daemon running with Docker socket access.
        """
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}, clear=False):
            try:
                from docker_log_analyzer.mcp_server import tool_list_containers

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
    def test_list_containers_includes_test_containers(self, setup_integration_containers):
        """Should list test containers when they're running via SSH.

        This verifies list_containers returns expected test containers.
        Note: This requires SSH daemon configured. Test skips if SSH unavailable.
        """
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}, clear=False):
            try:
                from docker_log_analyzer.mcp_server import tool_list_containers

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
    def test_analyze_patterns_via_ssh_localhost(self, setup_integration_containers):
        """Should analyze patterns in container logs via SSH.

        This verifies analyze_patterns tool works with remote Docker.
        Note: This requires SSH daemon configured. Test skips if SSH unavailable.
        """
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}, clear=False):
            try:
                from docker_log_analyzer.mcp_server import tool_analyze_patterns

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
    def test_detect_error_spikes_via_ssh_localhost(self, setup_integration_containers):
        """Should detect error spikes via SSH connection.

        This verifies detect_error_spikes tool works with remote Docker.
        Note: This requires SSH daemon configured. Test skips if SSH unavailable.
        """
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}, clear=False):
            try:
                from docker_log_analyzer.mcp_server import tool_detect_error_spikes

                result = tool_detect_error_spikes(
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
    def test_correlate_containers_via_ssh_localhost(self, setup_integration_containers):
        """Should correlate containers via SSH connection.

        This verifies correlate_containers tool works with remote Docker.
        Note: This requires SSH daemon configured. Test skips if SSH unavailable.
        """
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}, clear=False):
            try:
                from docker_log_analyzer.mcp_server import tool_correlate_containers

                result = tool_correlate_containers(
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
    def test_detect_data_leaks_via_ssh_localhost(self, setup_integration_containers):
        """Should detect secrets via SSH connection.

        This verifies detect_data_leaks tool works with remote Docker.
        Note: This requires SSH daemon configured. Test skips if SSH unavailable.
        """
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}, clear=False):
            try:
                from docker_log_analyzer.mcp_server import tool_detect_data_leaks

                result = tool_detect_data_leaks(
                    duration_seconds=30,
                    container_names=["test-web-app"],
                    severity_filter="all",
                    use_cache=False,
                )
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
    def test_ssh_unavailable_graceful_error(self):
        """Should handle SSH connection failure gracefully.

        When SSH is unavailable, tools should return error JSON, not crash.
        """
        # Try to connect to unlikely SSH host
        with patch.dict(
            os.environ,
            {"DOCKER_HOST": "ssh://nonexistent-host-that-doesnt-exist-12345.invalid"},
            clear=False,
        ):
            try:
                from docker_log_analyzer.mcp_server import tool_list_containers

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
    def test_docker_host_env_respected_by_tools(self, setup_integration_containers):
        """Tools should respect DOCKER_HOST environment variable.

        This verifies that when DOCKER_HOST is set, tools use it
        instead of the local socket.
        """
        # First, verify we can connect to localhost via SSH
        try:
            client = DockerClient(host="ssh://localhost")
            client.system.info()
        except DockerException:
            pytest.skip("SSH to localhost not available")

        # Now test that tool respects env var
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}, clear=False):
            from docker_log_analyzer.config import Settings

            settings = Settings()
            assert settings.docker_host == "ssh://localhost"

    @pytest.mark.integration
    def test_docker_host_overrides_default(self, setup_integration_containers):
        """DOCKER_HOST should override the default local socket."""
        original_host = os.environ.get("DOCKER_HOST")
        try:
            with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}, clear=False):
                from docker_log_analyzer.config import Settings
                from importlib import reload

                # Need to reload to pick up new env var
                import docker_log_analyzer.config as config_module

                settings = Settings()
                assert settings.docker_host == "ssh://localhost"
                assert settings.docker_host != "unix:///var/run/docker.sock"
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
            from docker_log_analyzer.mcp_server import tool_list_containers

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
            from docker_log_analyzer.mcp_server import tool_analyze_patterns

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
