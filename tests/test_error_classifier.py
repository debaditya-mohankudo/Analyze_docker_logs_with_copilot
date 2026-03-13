"""
Unit tests for error_classifier.py – semantic error classification.

Covers:
  - Category matching accuracy (positive + negative per category)
  - Specificity ordering (database before network, application last)
  - classify_lines integration
  - aggregate_stats shape and correctness
  - Edge cases: empty input, no errors, unknown category
"""

import pytest

from docker_log_analyzer.error_classifier import (
    classify_line,
    classify_lines,
    aggregate_stats,
    CATEGORIES,
    VALID_CATEGORIES,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# classify_line – category accuracy
# ---------------------------------------------------------------------------

class TestClassifyLineDatabase:
    def test_postgres_connection_refused(self):
        assert classify_line("ERROR connection refused to 10.0.0.5:5432") == "database"

    def test_mysql_connection_refused(self):
        assert classify_line("ERROR connection refused to db:3306") == "database"

    def test_deadlock(self):
        assert classify_line("ERROR deadlock detected in transaction 42") == "database"

    def test_too_many_connections(self):
        assert classify_line("FATAL too many connections for role 'app'") == "database"

    def test_sql_exception(self):
        assert classify_line("ERROR PSQLException: could not execute statement") == "database"

    def test_connection_pool_exhausted(self):
        assert classify_line("ERROR connection pool exhausted, waiting") == "database"

    def test_redis_error(self):
        assert classify_line("ERROR redis connection timeout after 5s") == "database"


class TestClassifyLineNetwork:
    def test_connection_timed_out(self):
        assert classify_line("ERROR connection timed out to upstream") == "network"

    def test_econnrefused(self):
        assert classify_line("ERROR ECONNREFUSED 10.0.0.1:8080") == "network"

    def test_dns_resolution(self):
        assert classify_line("ERROR DNS resolution failed for api.svc") == "network"

    def test_no_such_host(self):
        assert classify_line("ERROR lookup redis: no such host") == "network"

    def test_broken_pipe(self):
        assert classify_line("ERROR write: broken pipe") == "network"


class TestClassifyLineTimeout:
    def test_read_timeout(self):
        assert classify_line("ERROR read timeout after 30s") == "timeout"

    def test_gateway_timeout(self):
        assert classify_line("ERROR gateway timeout for /api/orders") == "timeout"

    def test_504(self):
        assert classify_line("ERROR HTTP 504 upstream timed out") == "timeout"

    def test_context_deadline(self):
        assert classify_line("ERROR context deadline exceeded") == "timeout"


class TestClassifyLineAuth:
    def test_401(self):
        assert classify_line("ERROR 401 Unauthorized for /api/admin") == "auth"

    def test_403(self):
        assert classify_line("ERROR 403 Forbidden") == "auth"

    def test_invalid_token(self):
        assert classify_line("ERROR invalid token: signature mismatch") == "auth"

    def test_jwt_expired(self):
        assert classify_line("ERROR JWT expired at 2026-03-13T10:00:00Z") == "auth"


class TestClassifyLineOom:
    def test_oomkilled(self):
        assert classify_line("FATAL OOMKilled container web-app") == "oom"

    def test_java_oom(self):
        assert classify_line("Exception java.lang.OutOfMemoryError: heap space") == "oom"

    def test_python_memory_error(self):
        assert classify_line("ERROR MemoryError: unable to allocate 4GB") == "oom"


class TestClassifyLineDisk:
    def test_no_space(self):
        assert classify_line("ERROR no space left on device") == "disk"

    def test_enospc(self):
        assert classify_line("FATAL ENOSPC: write failed") == "disk"

    def test_disk_quota(self):
        assert classify_line("ERROR disk quota exceeded for /data") == "disk"


class TestClassifyLineRateLimit:
    def test_429(self):
        assert classify_line("ERROR 429 Too Many Requests") == "rate_limit"

    def test_rate_limit_exceeded(self):
        assert classify_line("ERROR rate limit exceeded for client 10.0.0.1") == "rate_limit"

    def test_throttled(self):
        assert classify_line("ERROR request throttled by gateway") == "rate_limit"


class TestClassifyLineConfiguration:
    def test_missing_required(self):
        assert classify_line("ERROR missing required field 'database_url'") == "configuration"

    def test_invalid_config(self):
        assert classify_line("FATAL invalid config: port must be integer") == "configuration"

    def test_env_var_not_set(self):
        assert classify_line("ERROR environment variable not set: API_KEY") == "configuration"


class TestClassifyLineApplication:
    def test_null_pointer(self):
        assert classify_line("ERROR NullPointerException at com.app.Main:42") == "application"

    def test_type_error(self):
        assert classify_line("ERROR TypeError: cannot read property 'id' of undefined") == "application"

    def test_panic(self):
        assert classify_line("ERROR panic: runtime error: index out of range") == "application"

    def test_unhandled_exception(self):
        assert classify_line("FATAL unhandled exception in worker thread") == "application"


class TestClassifyLineUnknownAndNonError:
    def test_generic_error_is_unknown(self):
        assert classify_line("ERROR something went wrong") == "unknown"

    def test_non_error_returns_none(self):
        assert classify_line("INFO: request processed successfully") is None

    def test_empty_line_returns_none(self):
        assert classify_line("") is None

    def test_plain_text_returns_none(self):
        assert classify_line("Starting server on port 8080") is None


# ---------------------------------------------------------------------------
# Specificity ordering
# ---------------------------------------------------------------------------

class TestSpecificityOrdering:
    """Database patterns should match before network patterns when both could apply."""

    def test_db_connection_refused_is_database_not_network(self):
        # "connection refused" + port 5432 → database, not network
        line = "ERROR connection refused to postgres:5432"
        assert classify_line(line) == "database"

    def test_generic_connection_refused_is_network(self):
        # "ECONNREFUSED" without a DB port → network
        line = "ERROR ECONNREFUSED 10.0.0.1:8080"
        assert classify_line(line) == "network"


# ---------------------------------------------------------------------------
# classify_lines
# ---------------------------------------------------------------------------

class TestClassifyLines:
    def test_filters_non_errors(self):
        lines = [
            "INFO request ok",
            "ERROR connection refused to db:5432",
            "DEBUG processing",
            "FATAL OOMKilled",
        ]
        result = classify_lines(lines)
        assert len(result) == 2
        assert result[0][0] == "database"
        assert result[1][0] == "oom"

    def test_empty_input(self):
        assert classify_lines([]) == []

    def test_no_errors(self):
        assert classify_lines(["INFO ok", "DEBUG trace"]) == []

    def test_timestamps_extracted(self):
        lines = ["2026-03-13T10:00:00Z ERROR deadlock detected"]
        result = classify_lines(lines)
        assert len(result) == 1
        assert result[0][0] == "database"
        assert result[0][1] is not None  # unix_ts extracted

    def test_no_timestamp_returns_none_ts(self):
        lines = ["ERROR deadlock detected"]
        result = classify_lines(lines)
        assert result[0][1] is None


# ---------------------------------------------------------------------------
# aggregate_stats
# ---------------------------------------------------------------------------

class TestAggregateStats:
    def test_empty_input(self):
        stats = aggregate_stats([])
        assert stats["total_errors"] == 0
        assert stats["dominant_category"] is None
        assert stats["categories"] == {}
        assert stats["category_timeline"] == []

    def test_single_category(self):
        classified = [
            ("database", 1710324000.0, "ERROR deadlock"),
            ("database", 1710324060.0, "ERROR deadlock"),
        ]
        stats = aggregate_stats(classified)
        assert stats["total_errors"] == 2
        assert stats["dominant_category"] == "database"
        assert stats["categories"]["database"]["count"] == 2
        assert stats["categories"]["database"]["percentage"] == 100.0
        assert stats["categories"]["database"]["recommendation"] != ""

    def test_multiple_categories(self):
        classified = [
            ("database", 1710324000.0, "ERROR db"),
            ("database", 1710324000.0, "ERROR db"),
            ("timeout", 1710324000.0, "ERROR timeout"),
        ]
        stats = aggregate_stats(classified)
        assert stats["total_errors"] == 3
        assert stats["dominant_category"] == "database"
        assert stats["categories"]["database"]["percentage"] == pytest.approx(66.7, abs=0.1)
        assert stats["categories"]["timeout"]["percentage"] == pytest.approx(33.3, abs=0.1)

    def test_timeline_buckets(self):
        classified = [
            ("database", 1710324000.0, "ERROR db"),  # minute A
            ("database", 1710324060.0, "ERROR db"),  # minute B
            ("timeout", 1710324000.0, "ERROR timeout"),  # minute A
        ]
        stats = aggregate_stats(classified)
        timeline = stats["category_timeline"]
        assert len(timeline) == 2  # two distinct minutes
        assert "minute" in timeline[0]

    def test_samples_capped(self):
        classified = [("database", None, f"ERROR line {i}") for i in range(10)]
        stats = aggregate_stats(classified, max_samples=3)
        assert len(stats["categories"]["database"]["samples"]) == 3

    def test_none_timestamps_handled(self):
        classified = [("database", None, "ERROR no timestamp")]
        stats = aggregate_stats(classified)
        assert stats["categories"]["database"]["first_seen"] is None
        assert stats["categories"]["database"]["last_seen"] is None
        assert stats["category_timeline"] == []

    def test_unknown_category_has_no_recommendation(self):
        classified = [("unknown", None, "ERROR something weird")]
        stats = aggregate_stats(classified)
        assert stats["categories"]["unknown"]["recommendation"] == ""


# ---------------------------------------------------------------------------
# VALID_CATEGORIES constant
# ---------------------------------------------------------------------------

class TestValidCategories:
    def test_contains_all_defined_categories(self):
        for cat in CATEGORIES:
            assert cat.name in VALID_CATEGORIES

    def test_contains_unknown(self):
        assert "unknown" in VALID_CATEGORIES

    def test_no_extras(self):
        expected = {c.name for c in CATEGORIES} | {"unknown"}
        assert VALID_CATEGORIES == expected
