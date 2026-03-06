"""
Unit tests for correlation result caching in tools.py.

All tests are self-contained (no Docker required).
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from docker_log_analyzer.tools import (
    _correlation_cache_key,
    _read_correlation_cache,
    _write_correlation_cache,
    CORRELATION_CACHE_DIR,
)


pytestmark = pytest.mark.unit


# ── _correlation_cache_key ────────────────────────────────────────────────────

class TestCorrelationCacheKey:

    def test_sorted_container_names(self):
        key1 = _correlation_cache_key(["b", "a"], 30, 500)
        key2 = _correlation_cache_key(["a", "b"], 30, 500)
        assert key1 == key2

    def test_different_time_window_produces_different_key(self):
        key1 = _correlation_cache_key(["a", "b"], 30, 500)
        key2 = _correlation_cache_key(["a", "b"], 60, 500)
        assert key1 != key2

    def test_different_tail_produces_different_key(self):
        key1 = _correlation_cache_key(["a", "b"], 30, 500)
        key2 = _correlation_cache_key(["a", "b"], 30, 1000)
        assert key1 != key2

    def test_different_containers_produce_different_key(self):
        key1 = _correlation_cache_key(["a", "b"], 30, 500)
        key2 = _correlation_cache_key(["a", "c"], 30, 500)
        assert key1 != key2

    def test_returns_hex_string(self):
        key = _correlation_cache_key(["a", "b"], 30, 500)
        assert isinstance(key, str)
        assert len(key) == 32  # MD5 hex digest length
        int(key, 16)  # raises if not valid hex


# ── _read_correlation_cache / _write_correlation_cache ───────────────────────

class TestCorrelationCacheReadWrite:

    def test_write_creates_file(self, tmp_path):
        with patch("docker_log_analyzer.tools.CORRELATION_CACHE_DIR", tmp_path):
            _write_correlation_cache("testkey", {"status": "success", "cached_at": "2026-01-01T00:00:00Z"})
            assert (tmp_path / "testkey.json").exists()

    def test_read_returns_none_when_missing(self, tmp_path):
        with patch("docker_log_analyzer.tools.CORRELATION_CACHE_DIR", tmp_path):
            result = _read_correlation_cache("nonexistent")
            assert result is None

    def test_round_trip(self, tmp_path):
        data = {
            "status": "success",
            "correlations": [],
            "cached_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with patch("docker_log_analyzer.tools.CORRELATION_CACHE_DIR", tmp_path):
            _write_correlation_cache("abc123", data)
            result = _read_correlation_cache("abc123")
        assert result is not None
        assert result["status"] == "success"

    def test_expired_cache_returns_none(self, tmp_path):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = {"status": "success", "correlations": [], "cached_at": old_time}
        with patch("docker_log_analyzer.tools.CORRELATION_CACHE_DIR", tmp_path), \
             patch("docker_log_analyzer.tools.settings") as mock_settings:
            mock_settings.correlation_cache_ttl_minutes = 10  # 10 min TTL, data is 2h old
            _write_correlation_cache("expkey", data)
            result = _read_correlation_cache("expkey")
        assert result is None

    def test_fresh_cache_returns_data(self, tmp_path):
        fresh_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = {"status": "success", "correlations": [], "cached_at": fresh_time}
        with patch("docker_log_analyzer.tools.CORRELATION_CACHE_DIR", tmp_path), \
             patch("docker_log_analyzer.tools.settings") as mock_settings:
            mock_settings.correlation_cache_ttl_minutes = 10
            _write_correlation_cache("freshkey", data)
            result = _read_correlation_cache("freshkey")
        assert result is not None

    def test_ttl_zero_always_returns_none(self, tmp_path):
        fresh_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = {"status": "success", "correlations": [], "cached_at": fresh_time}
        with patch("docker_log_analyzer.tools.CORRELATION_CACHE_DIR", tmp_path), \
             patch("docker_log_analyzer.tools.settings") as mock_settings:
            mock_settings.correlation_cache_ttl_minutes = 0
            _write_correlation_cache("zerokey", data)
            result = _read_correlation_cache("zerokey")
        assert result is None


# ── tool_correlate_containers cache integration ───────────────────────────────

class TestCorrelateContainersCacheIntegration:

    def _make_mock_client(self, container_names: list[str]):
        """Build a mock DockerClient with fake containers."""
        containers = []
        for name in container_names:
            c = MagicMock()
            c.name = name
            containers.append(c)
        client = MagicMock()
        client.container.list.return_value = containers
        return client

    def test_cache_miss_sets_correlation_cache_hit_false(self, tmp_path):
        from docker_log_analyzer.tools import tool_correlate_containers

        mock_client = self._make_mock_client(["svc-a", "svc-b"])

        with patch("docker_log_analyzer.tools._docker_client", return_value=mock_client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], False)), \
             patch("docker_log_analyzer.tools.CORRELATION_CACHE_DIR", tmp_path), \
             patch("docker_log_analyzer.tools._read_correlation_cache", return_value=None) as mock_read, \
             patch("docker_log_analyzer.tools._write_correlation_cache") as mock_write:
            result = tool_correlate_containers(use_cache=True)

        assert result["correlation_cache_hit"] is False
        mock_read.assert_called_once()
        mock_write.assert_called_once()

    def test_cache_hit_sets_correlation_cache_hit_true(self, tmp_path):
        from docker_log_analyzer.tools import tool_correlate_containers

        mock_client = self._make_mock_client(["svc-a", "svc-b"])
        cached_result = {
            "status": "success",
            "correlations": [{"container_a": "svc-a", "container_b": "svc-b", "correlation_score": 0.9}],
            "cache_hits": {},
            "correlation_cache_hit": False,
            "cached_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "parameters": {"time_window_seconds": 30, "tail": 500},
        }

        with patch("docker_log_analyzer.tools._docker_client", return_value=mock_client), \
             patch("docker_log_analyzer.tools._read_correlation_cache", return_value=cached_result) as mock_read, \
             patch("docker_log_analyzer.tools._write_correlation_cache") as mock_write:
            result = tool_correlate_containers(use_cache=True)

        assert result["correlation_cache_hit"] is True
        assert result["correlations"][0]["correlation_score"] == 0.9
        mock_read.assert_called_once()
        mock_write.assert_not_called()

    def test_use_cache_false_skips_cache_read(self):
        from docker_log_analyzer.tools import tool_correlate_containers

        mock_client = self._make_mock_client(["svc-a", "svc-b"])

        with patch("docker_log_analyzer.tools._docker_client", return_value=mock_client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], False)), \
             patch("docker_log_analyzer.tools._read_correlation_cache") as mock_read, \
             patch("docker_log_analyzer.tools._write_correlation_cache"):
            tool_correlate_containers(use_cache=False)

        mock_read.assert_not_called()
