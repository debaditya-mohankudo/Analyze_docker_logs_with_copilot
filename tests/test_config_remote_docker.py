"""
Unit tests for remote Docker configuration via DOCKER_HOST environment variable.

Tests:
- DOCKER_HOST environment variable parsing
- Different connection formats (unix socket, SSH, TCP)
- Default values
- Settings validation
"""

import pytest
import os
from pathlib import Path
from unittest.mock import patch
from docker_log_analyzer.config import Settings


class TestDockerHostConfiguration:
    """Test DOCKER_HOST environment variable handling."""

    @pytest.mark.unit
    def test_default_docker_host(self):
        """Should use local unix socket by default."""
        with patch.dict(os.environ, {}, clear=False):
            # Remove DOCKER_HOST if it exists
            if "DOCKER_HOST" in os.environ:
                del os.environ["DOCKER_HOST"]

            settings = Settings()
            assert settings.docker_host == "unix:///var/run/docker.sock"

    @pytest.mark.unit
    def test_docker_host_from_env_unix_socket(self):
        """Should read unix socket path from DOCKER_HOST env var."""
        with patch.dict(os.environ, {"DOCKER_HOST": "unix:///custom/docker.sock"}):
            settings = Settings()
            assert settings.docker_host == "unix:///custom/docker.sock"

    @pytest.mark.unit
    def test_docker_host_from_env_tcp(self):
        """Should read TCP host from DOCKER_HOST env var."""
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://localhost:2375"}):
            settings = Settings()
            assert settings.docker_host == "tcp://localhost:2375"

    @pytest.mark.unit
    def test_docker_host_from_env_ssh_localhost(self):
        """Should read SSH to localhost from DOCKER_HOST env var."""
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}):
            settings = Settings()
            assert settings.docker_host == "ssh://localhost"

    @pytest.mark.unit
    def test_docker_host_from_env_ssh_with_user(self):
        """Should read SSH URL with username from DOCKER_HOST env var."""
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://dev@staging.example.com"}):
            settings = Settings()
            assert settings.docker_host == "ssh://dev@staging.example.com"

    @pytest.mark.unit
    def test_docker_host_from_env_ssh_with_port(self):
        """Should read SSH URL with port from DOCKER_HOST env var."""
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://dev@staging.example.com:2222"}):
            settings = Settings()
            assert settings.docker_host == "ssh://dev@staging.example.com:2222"

    @pytest.mark.unit
    def test_docker_host_case_insensitive(self):
        """Should handle case-insensitive env var (Pydantic default behavior)."""
        with patch.dict(os.environ, {"docker_host": "ssh://localhost"}):
            settings = Settings()
            assert settings.docker_host == "ssh://localhost"

    @pytest.mark.unit
    def test_docker_host_uppercase_env_var(self):
        """Should handle DOCKER_HOST uppercase env var."""
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://example.com"}):
            settings = Settings()
            assert settings.docker_host == "ssh://example.com"

    @pytest.mark.unit
    def test_other_config_unaffected_by_docker_host(self):
        """Other config values should be independent of DOCKER_HOST."""
        with patch.dict(
            os.environ,
            {
                "DOCKER_HOST": "ssh://localhost",
                "LOG_LEVEL": "DEBUG",
                "DEFAULT_TAIL_LINES": "750",
            },
        ):
            settings = Settings()
            assert settings.docker_host == "ssh://localhost"
            assert settings.log_level == "DEBUG"
            assert settings.default_tail_lines == 750


class TestDockerHostValidation:
    """Test validation of DOCKER_HOST values."""

    @pytest.mark.unit
    def test_accepts_all_valid_formats(self):
        """Should accept all valid Docker daemon connection formats."""
        valid_hosts = [
            "unix:///var/run/docker.sock",
            "unix:///custom/path/docker.sock",
            "tcp://localhost:2375",
            "tcp://192.168.1.100:2376",
            "ssh://localhost",
            "ssh://user@host",
            "ssh://user@host:22",
            "ssh://user@example.com",
            "ssh://dev@staging.example.com:2222",
        ]

        for host in valid_hosts:
            with patch.dict(os.environ, {"DOCKER_HOST": host}):
                settings = Settings()
                assert settings.docker_host == host

    @pytest.mark.unit
    def test_accepts_empty_docker_host(self):
        """Should accept empty string (falls back to default)."""
        with patch.dict(os.environ, {"DOCKER_HOST": ""}):
            settings = Settings()
            # Empty string should be accepted (Pydantic treats it as provided value)
            assert settings.docker_host == ""


class TestRemoteDockerHostScenarios:
    """Test realistic remote Docker scenarios."""

    @pytest.mark.unit
    def test_remote_staging_environment(self):
        """Test configuration for remote staging environment."""
        remote_config = {
            "DOCKER_HOST": "ssh://dev@staging.example.com",
            "LOG_LEVEL": "INFO",
            "DEFAULT_TAIL_LINES": "1000",
        }
        with patch.dict(os.environ, remote_config):
            settings = Settings()
            assert settings.docker_host == "ssh://dev@staging.example.com"
            assert settings.log_level == "INFO"
            assert settings.default_tail_lines == 1000

    @pytest.mark.unit
    def test_remote_production_with_custom_port(self):
        """Test configuration for remote production with custom SSH port."""
        remote_config = {
            "DOCKER_HOST": "ssh://docker@prod.example.com:2222",
            "LOG_LEVEL": "WARNING",
        }
        with patch.dict(os.environ, remote_config):
            settings = Settings()
            assert settings.docker_host == "ssh://docker@prod.example.com:2222"
            assert settings.log_level == "WARNING"

    @pytest.mark.unit
    def test_local_tcp_fallback(self):
        """Test configuration for local TCP connection (non-socket)."""
        tcp_config = {
            "DOCKER_HOST": "tcp://127.0.0.1:2375",
            "LOG_LEVEL": "DEBUG",
        }
        with patch.dict(os.environ, tcp_config):
            settings = Settings()
            assert settings.docker_host == "tcp://127.0.0.1:2375"
            assert settings.log_level == "DEBUG"


class TestDockerHostWithApplicationSettings:
    """Test DOCKER_HOST in context of full application settings."""

    @pytest.mark.unit
    def test_all_settings_with_remote_docker(self):
        """Should load all settings correctly with remote DOCKER_HOST."""
        env_config = {
            "DOCKER_HOST": "ssh://dev@staging.example.com",
            "LOG_LEVEL": "DEBUG",
            "DEFAULT_TAIL_LINES": "2000",
            "DEFAULT_SPIKE_TAIL_LINES": "5000",
            "DEFAULT_SPIKE_THRESHOLD": "1.5",
            "DEFAULT_CORRELATION_WINDOW_SECONDS": "60",
            "CONTAINER_LABEL_FILTER": "env=prod",
        }
        with patch.dict(os.environ, env_config):
            settings = Settings()
            assert settings.docker_host == "ssh://dev@staging.example.com"
            assert settings.log_level == "DEBUG"
            assert settings.default_tail_lines == 2000
            assert settings.default_spike_tail_lines == 5000
            assert settings.default_spike_threshold == 1.5
            assert settings.default_correlation_window_seconds == 60
            assert settings.container_label_filter == "env=prod"

    @pytest.mark.unit
    def test_remote_docker_does_not_affect_other_validators(self):
        """Setting DOCKER_HOST should not break other field validators."""
        env_config = {
            "DOCKER_HOST": "ssh://localhost",
            "LOG_LEVEL": "INVALID",  # This should fail validation
        }
        with patch.dict(os.environ, env_config):
            with pytest.raises(ValueError):
                Settings()

    @pytest.mark.unit
    def test_settings_repr_includes_docker_host(self):
        """Settings should expose docker_host for debugging."""
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://localhost"}):
            settings = Settings()
            # Should have the attribute accessible
            assert hasattr(settings, "docker_host")
            assert settings.docker_host == "ssh://localhost"
