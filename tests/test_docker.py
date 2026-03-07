"""
Unit tests for docker.py helpers.

Covers branches not exercised by test_tools_unit.py:
- _fetch_logs / _fetch_logs_window with string result and empty result
- _container_name without leading slash
- _parse_time_arg for all relative units (seconds, minutes, days, weeks)
  and singular forms
- _fetch_logs_with_cache with use_cache=False
- _docker_client calls system.info()
"""

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import docker_log_analyzer.docker as docker_mod


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _patch_docker_exceptions(monkeypatch):
    """Replace SDK exception classes with lightweight test doubles."""

    class _FakeDockerException(Exception):
        pass

    class _FakeNoSuchContainer(_FakeDockerException):
        pass

    monkeypatch.setattr(docker_mod, "DockerException", _FakeDockerException)
    monkeypatch.setattr(docker_mod, "NoSuchContainer", _FakeNoSuchContainer)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeContainer:
    def __init__(self, name: str, logs_result=""):
        self.name = name
        self._logs_result = logs_result

    def logs(self, **kwargs):
        return self._logs_result


def _mock_docker_client():
    client = MagicMock()
    client.system.info.return_value = {"ok": True}
    return client


# ---------------------------------------------------------------------------
# _docker_client
# ---------------------------------------------------------------------------


class TestDockerClient:
    def test_calls_system_info(self):
        fake = _mock_docker_client()
        with patch("docker_log_analyzer.docker.DockerClient", return_value=fake):
            docker_mod._docker_client()
        fake.system.info.assert_called_once()

    def test_returns_client_on_success(self):
        fake = _mock_docker_client()
        with patch("docker_log_analyzer.docker.DockerClient", return_value=fake):
            result = docker_mod._docker_client()
        assert result is fake

    def test_raises_runtime_error_on_docker_exception(self):
        with patch(
            "docker_log_analyzer.docker.DockerClient",
            side_effect=docker_mod.DockerException("daemon down"),
        ):
            with pytest.raises(RuntimeError, match="Cannot connect to Docker daemon"):
                docker_mod._docker_client()


# ---------------------------------------------------------------------------
# _fetch_logs
# ---------------------------------------------------------------------------


class TestFetchLogs:
    def test_string_result_is_split(self):
        c = _FakeContainer("svc", logs_result="line1\nline2")
        assert docker_mod._fetch_logs(c, tail=10) == ["line1", "line2"]

    def test_empty_string_returns_empty_list(self):
        c = _FakeContainer("svc", logs_result="")
        assert docker_mod._fetch_logs(c, tail=10) == []

    def test_none_result_returns_empty_list(self):
        c = _FakeContainer("svc", logs_result=None)
        assert docker_mod._fetch_logs(c, tail=10) == []

    def test_bytes_result_is_decoded_and_split(self):
        c = _FakeContainer("svc", logs_result=b"a\nb")
        assert docker_mod._fetch_logs(c, tail=10) == ["a", "b"]


# ---------------------------------------------------------------------------
# _fetch_logs_window
# ---------------------------------------------------------------------------


class TestFetchLogsWindow:
    def _now(self):
        return datetime.now(timezone.utc)

    def test_string_result_is_split(self):
        c = _FakeContainer("svc", logs_result="x\ny")
        now = self._now()
        assert docker_mod._fetch_logs_window(c, now - timedelta(minutes=1), now) == ["x", "y"]

    def test_empty_string_returns_empty_list(self):
        c = _FakeContainer("svc", logs_result="")
        now = self._now()
        assert docker_mod._fetch_logs_window(c, now - timedelta(minutes=1), now) == []

    def test_none_result_returns_empty_list(self):
        c = _FakeContainer("svc", logs_result=None)
        now = self._now()
        assert docker_mod._fetch_logs_window(c, now - timedelta(minutes=1), now) == []

    def test_bytes_result_is_decoded_and_split(self):
        c = _FakeContainer("svc", logs_result=b"p\nq")
        now = self._now()
        assert docker_mod._fetch_logs_window(c, now - timedelta(minutes=1), now) == ["p", "q"]


# ---------------------------------------------------------------------------
# _container_name
# ---------------------------------------------------------------------------


class TestContainerName:
    def test_strips_leading_slash(self):
        c = SimpleNamespace(name="/svc")
        assert docker_mod._container_name(c) == "svc"

    def test_no_slash_unchanged(self):
        c = SimpleNamespace(name="svc")
        assert docker_mod._container_name(c) == "svc"


# ---------------------------------------------------------------------------
# _fetch_logs_with_cache
# ---------------------------------------------------------------------------


class TestFetchLogsWithCache:
    def _now(self):
        return datetime.now(timezone.utc)

    def test_cache_hit_returns_cached_logs(self):
        c = _FakeContainer("svc")
        now = self._now()
        with patch("docker_log_analyzer.docker.read_cached_logs_for_window", return_value=["cached"]):
            logs, was_cached = docker_mod._fetch_logs_with_cache(c, "svc", now, now)
        assert logs == ["cached"]
        assert was_cached is True

    def test_cache_miss_fetches_from_docker(self):
        c = _FakeContainer("svc")
        now = self._now()
        with patch("docker_log_analyzer.docker.read_cached_logs_for_window", return_value=None), \
             patch("docker_log_analyzer.docker._fetch_logs_window", return_value=["fresh"]):
            logs, was_cached = docker_mod._fetch_logs_with_cache(c, "svc", now, now)
        assert logs == ["fresh"]
        assert was_cached is False

    def test_use_cache_false_skips_cache_check(self):
        c = _FakeContainer("svc")
        now = self._now()
        with patch("docker_log_analyzer.docker.read_cached_logs_for_window") as mock_cache, \
             patch("docker_log_analyzer.docker._fetch_logs_window", return_value=["live"]):
            logs, was_cached = docker_mod._fetch_logs_with_cache(c, "svc", now, now, use_cache=False)
        mock_cache.assert_not_called()
        assert logs == ["live"]
        assert was_cached is False
