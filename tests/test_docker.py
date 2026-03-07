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
# _parse_time_arg
# ---------------------------------------------------------------------------


class TestParseTimeArg:
    def _approx_seconds(self, parsed: datetime, expected_delta_s: float, tolerance: float = 5.0):
        actual = (datetime.now(timezone.utc) - parsed).total_seconds()
        assert abs(actual - expected_delta_s) < tolerance, f"expected ~{expected_delta_s}s, got {actual}s"

    def test_now(self):
        parsed = docker_mod._parse_time_arg("now")
        assert parsed.tzinfo is not None
        self._approx_seconds(parsed, 0)

    def test_seconds_plural(self):
        parsed = docker_mod._parse_time_arg("30 seconds ago")
        self._approx_seconds(parsed, 30)

    def test_second_singular(self):
        parsed = docker_mod._parse_time_arg("1 second ago")
        self._approx_seconds(parsed, 1)

    def test_minutes_plural(self):
        parsed = docker_mod._parse_time_arg("5 minutes ago")
        self._approx_seconds(parsed, 300)

    def test_minute_singular(self):
        parsed = docker_mod._parse_time_arg("1 minute ago")
        self._approx_seconds(parsed, 60)

    def test_hours_plural(self):
        parsed = docker_mod._parse_time_arg("2 hours ago")
        self._approx_seconds(parsed, 7200)

    def test_hour_singular(self):
        parsed = docker_mod._parse_time_arg("1 hour ago")
        self._approx_seconds(parsed, 3600)

    def test_days_plural(self):
        parsed = docker_mod._parse_time_arg("3 days ago")
        self._approx_seconds(parsed, 3 * 86400)

    def test_day_singular(self):
        parsed = docker_mod._parse_time_arg("1 day ago")
        self._approx_seconds(parsed, 86400)

    def test_weeks_plural(self):
        parsed = docker_mod._parse_time_arg("2 weeks ago")
        self._approx_seconds(parsed, 2 * 7 * 86400)

    def test_week_singular(self):
        parsed = docker_mod._parse_time_arg("1 week ago")
        self._approx_seconds(parsed, 7 * 86400)

    def test_iso8601_with_z_suffix(self):
        parsed = docker_mod._parse_time_arg("2026-03-04T10:00:00Z")
        assert parsed.year == 2026
        assert parsed.hour == 10
        assert parsed.tzinfo is not None

    def test_iso8601_with_offset(self):
        parsed = docker_mod._parse_time_arg("2026-03-04T10:00:00+00:00")
        assert parsed.year == 2026
        assert parsed.hour == 10

    def test_invalid_falls_back_to_now(self):
        before = datetime.now(timezone.utc)
        parsed = docker_mod._parse_time_arg("not a time")
        after = datetime.now(timezone.utc)
        assert before <= parsed <= after


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
