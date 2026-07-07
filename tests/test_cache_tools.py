"""
Unit tests for tool_cache_info and tool_clear_cache.

All tests are stateless (no Docker, no real filesystem writes).
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.unit

# ── cache_info ─────────────────────────────────────────────────────────────────

class TestToolCacheInfo:

    def test_returns_success_status(self):
        mock_result = {"containers": [], "total_size_bytes": 0, "total_size_kb": 0.0}
        with patch("docker_log_analyzer.tools.get_cache_info", return_value=mock_result):
            from docker_log_analyzer.tools import tool_cache_info
            result = tool_cache_info()
        assert result["status"] == "success"

    def test_result_contains_containers_and_totals(self):
        mock_result = {
            "containers": [{"container": "web", "cached_days": 2, "size_bytes": 1024}],
            "total_size_bytes": 1024,
            "total_size_kb": 1.0,
        }
        with patch("docker_log_analyzer.tools.get_cache_info", return_value=mock_result):
            from docker_log_analyzer.tools import tool_cache_info
            result = tool_cache_info()
        assert "containers" in result
        assert "total_size_bytes" in result
        assert result["containers"][0]["container"] == "web"

    def test_passes_container_name_to_get_cache_info(self):
        mock_result = {"containers": [], "total_size_bytes": 0, "total_size_kb": 0.0}
        with patch("docker_log_analyzer.tools.get_cache_info", return_value=mock_result) as mock:
            from docker_log_analyzer.tools import tool_cache_info
            tool_cache_info(container_name="db")
        mock.assert_called_once_with(container_name="db")

    def test_returns_error_on_exception(self):
        with patch("docker_log_analyzer.tools.get_cache_info", side_effect=OSError("disk error")):
            from docker_log_analyzer.tools import tool_cache_info
            result = tool_cache_info()
        assert result["status"] == "error"
        assert "disk error" in result["error"]


# ── clear_cache ────────────────────────────────────────────────────────────────

class TestToolClearCache:

    def test_returns_success_status(self):
        mock_result = {"cleared_containers": [], "bytes_freed": 0, "kb_freed": 0.0}
        with patch("docker_log_analyzer.tools.clear_cache", return_value=mock_result):
            from docker_log_analyzer.tools import tool_clear_cache
            result = tool_clear_cache()
        assert result["status"] == "success"

    def test_result_contains_cleared_containers_and_freed(self):
        mock_result = {
            "cleared_containers": ["web", "db"],
            "bytes_freed": 4096,
            "kb_freed": 4.0,
        }
        with patch("docker_log_analyzer.tools.clear_cache", return_value=mock_result):
            from docker_log_analyzer.tools import tool_clear_cache
            result = tool_clear_cache()
        assert result["cleared_containers"] == ["web", "db"]
        assert result["bytes_freed"] == 4096

    def test_passes_container_name_to_clear_cache(self):
        mock_result = {"cleared_containers": ["web"], "bytes_freed": 512, "kb_freed": 0.5}
        with patch("docker_log_analyzer.tools.clear_cache", return_value=mock_result) as mock:
            from docker_log_analyzer.tools import tool_clear_cache
            tool_clear_cache(container_name="web")
        mock.assert_called_once_with(container_name="web")

    def test_returns_error_on_exception(self):
        with patch("docker_log_analyzer.tools.clear_cache", side_effect=PermissionError("no perm")):
            from docker_log_analyzer.tools import tool_clear_cache
            result = tool_clear_cache()
        assert result["status"] == "error"
        assert "no perm" in result["error"]
