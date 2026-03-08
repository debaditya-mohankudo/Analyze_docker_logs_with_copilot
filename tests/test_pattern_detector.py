"""
Unit tests for docker_log_analyzer.log_pattern_analyzer.PatternDetector.

All tests are self-contained (no Docker required).
"""

import pytest

from docker_log_analyzer.log_pattern_analyzer import PatternDetector
from tests.conftest import (
    PYTHON_LOGS, JAVA_LOGS, GO_LOGS, NODEJS_LOGS,
    SYSLOG_LINES, EPOCH_LINES, APACHE_LINES, NGINX_LOGS,
)


pytestmark = pytest.mark.unit


# ── Timestamp format detection ────────────────────────────────────────────────

class TestTimestampFormat:

    def test_detects_iso8601(self):
        result = PatternDetector.detect_timestamp_format(PYTHON_LOGS[0])
        assert result is not None
        fmt, sample, confidence = result
        assert fmt == "iso8601"
        assert confidence > 0

    def test_detects_syslog(self):
        result = PatternDetector.detect_timestamp_format(SYSLOG_LINES[0])
        assert result is not None
        fmt, _, _ = result
        assert fmt == "syslog"

    def test_detects_epoch(self):
        result = PatternDetector.detect_timestamp_format(EPOCH_LINES[0])
        assert result is not None
        fmt, _, _ = result
        assert fmt == "epoch"

    def test_detects_apache(self):
        result = PatternDetector.detect_timestamp_format(APACHE_LINES[0])
        assert result is not None
        fmt, _, _ = result
        assert fmt == "apache"

    def test_detects_nginx(self):
        result = PatternDetector.detect_timestamp_format(NGINX_LOGS[0])
        assert result is not None
        fmt, _, _ = result
        assert fmt == "nginx"

    def test_unknown_format_returns_none(self):
        result = PatternDetector.detect_timestamp_format("just a plain message")
        assert result is None

    def test_returns_sample_string(self):
        result = PatternDetector.detect_timestamp_format(PYTHON_LOGS[0])
        assert result is not None
        _, sample, _ = result
        assert isinstance(sample, str) and len(sample) > 0


# ── Language detection ────────────────────────────────────────────────────────

class TestLanguageDetection:

    def test_detects_python(self, python_logs):
        lang, confidence = PatternDetector.detect_language(python_logs)
        assert lang == "python"
        assert confidence > 0

    def test_detects_java(self, java_logs):
        lang, confidence = PatternDetector.detect_language(java_logs)
        assert lang == "java"
        assert confidence > 0

    def test_detects_go(self, go_logs):
        lang, confidence = PatternDetector.detect_language(go_logs)
        assert lang == "go"
        assert confidence > 0

    def test_detects_nodejs(self, nodejs_logs):
        lang, confidence = PatternDetector.detect_language(nodejs_logs)
        assert lang == "nodejs"
        assert confidence > 0

    def test_detects_php(self, php_logs):
        lang, confidence = PatternDetector.detect_language(php_logs)
        assert lang == "php"
        assert confidence > 0

    def test_detects_nginx(self, nginx_logs):
        lang, confidence = PatternDetector.detect_language(nginx_logs)
        assert lang == "nginx"
        assert confidence > 0

    def test_unknown_language(self):
        logs = ["plain log line", "another plain line", "no patterns here"]
        lang, confidence = PatternDetector.detect_language(logs)
        assert lang == "unknown"
        assert confidence == 0.0

    def test_confidence_between_zero_and_one(self, python_logs):
        _, confidence = PatternDetector.detect_language(python_logs)
        assert 0.0 <= confidence <= 1.0

    def test_empty_logs(self):
        lang, confidence = PatternDetector.detect_language([])
        assert lang == "unknown"
        assert confidence == 0.0


# ── Framework detection ───────────────────────────────────────────────────────

class TestFrameworkDetection:

    def test_detects_spring(self, java_logs):
        fw = PatternDetector.detect_framework("java", java_logs)
        assert fw == "spring"

    def test_detects_quarkus(self):
        logs = [
            "2024-03-02T21:10:00Z INFO  io.quarkus.runtime.Quarkus - Quarkus 3.8.0 started",
            "2024-03-02T21:10:01Z ERROR io.quarkus.runtime.QuarkusApplication - startup failed",
        ]
        fw = PatternDetector.detect_framework("java", logs)
        assert fw == "quarkus"

    def test_detects_micronaut(self):
        logs = [
            "2024-03-02T21:10:00Z INFO  io.micronaut.context.DefaultBeanContext - Startup completed",
            "2024-03-02T21:10:01Z ERROR io.micronaut.http.server.RouteExecutor - Error executing route",
        ]
        fw = PatternDetector.detect_framework("java", logs)
        assert fw == "micronaut"

    def test_detects_vertx(self):
        logs = [
            "2024-03-02T21:10:00Z INFO  io.vertx.core.impl.launcher.commands.VertxIsolatedDeployer - Succeeded",
            "2024-03-02T21:10:01Z ERROR io.vertx.core.net.impl.ConnectionBase - Connection reset",
        ]
        fw = PatternDetector.detect_framework("java", logs)
        assert fw == "vertx"

    def test_non_java_returns_none(self, python_logs):
        assert PatternDetector.detect_framework("python", python_logs) is None

    def test_unknown_language_returns_none(self):
        assert PatternDetector.detect_framework("unknown", []) is None

    def test_no_framework_signals_returns_none(self):
        logs = ["2024-03-02T21:10:00Z ERROR at java.lang.NullPointerException"]
        assert PatternDetector.detect_framework("java", logs) is None


# ── Health check detection ────────────────────────────────────────────────────

class TestHealthCheckDetection:

    def test_detects_health_check(self, python_logs):
        result = PatternDetector.detect_health_checks(python_logs)
        assert result is not None

    def test_returns_none_when_no_health_checks(self):
        logs = [
            "2024-03-02T21:10:00Z ERROR something failed",
            "2024-03-02T21:10:01Z INFO processing request",
        ]
        result = PatternDetector.detect_health_checks(logs)
        assert result is None

    def test_health_check_has_pattern(self, python_logs):
        result = PatternDetector.detect_health_checks(python_logs)
        assert result is not None
        assert isinstance(result.pattern, str)

    def test_health_check_frequency_positive(self, python_logs):
        result = PatternDetector.detect_health_checks(python_logs)
        assert result is not None
        assert result.frequency_per_minute >= 0

    def test_detects_liveness_probe(self):
        logs = ["2024-03-02T21:10:00Z INFO liveness probe ok"] * 10
        result = PatternDetector.detect_health_checks(logs)
        assert result is not None

    def test_detects_ping_pong(self):
        logs = ["2024-03-02T21:10:00Z DEBUG ping pong"] * 5
        result = PatternDetector.detect_health_checks(logs)
        assert result is not None


# ── Log level extraction ──────────────────────────────────────────────────────

class TestLogLevelExtraction:

    def test_extracts_error_count(self, python_logs):
        levels = PatternDetector.extract_log_levels(python_logs)
        assert "ERROR" in levels
        assert levels["ERROR"] >= 1

    def test_extracts_info_count(self, python_logs):
        levels = PatternDetector.extract_log_levels(python_logs)
        assert "INFO" in levels

    def test_extracts_debug_count(self, python_logs):
        levels = PatternDetector.extract_log_levels(python_logs)
        assert "DEBUG" in levels

    def test_counts_are_positive(self, python_logs):
        levels = PatternDetector.extract_log_levels(python_logs)
        assert all(v > 0 for v in levels.values())

    def test_empty_logs_returns_empty_dict(self):
        assert PatternDetector.extract_log_levels([]) == {}

    def test_no_known_levels(self):
        logs = ["just a plain message with no level"]
        assert PatternDetector.extract_log_levels(logs) == {}

    def test_detects_fatal(self):
        logs = ["2024-03-02T21:10:00Z FATAL system crash"]
        levels = PatternDetector.extract_log_levels(logs)
        assert "FATAL" in levels

    def test_detects_critical(self):
        logs = ["2024-03-02T21:10:00Z CRITICAL disk full"]
        levels = PatternDetector.extract_log_levels(logs)
        assert "CRITICAL" in levels

    def test_detects_nginx_bracketed_levels(self, nginx_logs):
        levels = PatternDetector.extract_log_levels(nginx_logs)
        assert "ERROR" in levels
        assert "CRIT" in levels or "WARN" in levels or "NOTICE" in levels

    def test_total_matches_error_lines(self, python_logs):
        levels = PatternDetector.extract_log_levels(python_logs)
        total = sum(levels.values())
        assert total <= len(python_logs)


# ── Error pattern extraction ──────────────────────────────────────────────────

class TestErrorPatternExtraction:

    def test_extracts_patterns_from_error_lines(self, python_logs):
        patterns = PatternDetector.extract_error_patterns(python_logs)
        assert isinstance(patterns, list)

    def test_returns_tuples(self, python_logs):
        patterns = PatternDetector.extract_error_patterns(python_logs)
        for p in patterns:
            assert isinstance(p, tuple)
            assert len(p) == 2
            text, count = p
            assert isinstance(text, str)
            assert isinstance(count, int) and count > 0

    def test_no_more_than_ten_patterns(self):
        logs = [f"2024-03-02T21:10:{i:02d}Z ERROR Connection refused" for i in range(30)]
        patterns = PatternDetector.extract_error_patterns(logs)
        assert len(patterns) <= 10

    def test_empty_logs_returns_empty(self):
        assert PatternDetector.extract_error_patterns([]) == []

    def test_no_error_lines_returns_empty(self):
        logs = ["2024-03-02T21:10:00Z INFO all fine"]
        assert PatternDetector.extract_error_patterns(logs) == []

    def test_extracts_nginx_upstream_errors(self, nginx_logs):
        patterns = PatternDetector.extract_error_patterns(nginx_logs)
        texts = [p for p, _ in patterns]
        assert any("connect() failed" in t or "upstream timed out" in t or "no live upstreams" in t for t in texts)
