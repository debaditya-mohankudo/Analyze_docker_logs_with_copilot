"""
Unit tests for docker_log_analyzer.patterns.

Covers DOCKER_TS_RE, ERROR_PATTERN_RE, and parse_timestamp.
All tests are self-contained (no Docker required).
"""

import pytest
from datetime import datetime, timezone

from docker_log_analyzer.patterns import DOCKER_TS_RE, ERROR_PATTERN_RE, parse_timestamp


pytestmark = pytest.mark.unit


class TestDockerTsRe:

    def test_matches_full_rfc3339_with_z(self):
        line = "2024-03-02T21:19:41.123456789Z [app] INFO started"
        assert DOCKER_TS_RE.match(line)

    def test_matches_rfc3339_without_z(self):
        line = "2024-03-02T21:19:41 plain message"
        assert DOCKER_TS_RE.match(line)

    def test_matches_rfc3339_without_subseconds(self):
        line = "2026-03-07T12:00:00Z ERROR something"
        assert DOCKER_TS_RE.match(line)

    def test_does_not_match_plain_text(self):
        assert not DOCKER_TS_RE.match("just a plain log line")

    def test_does_not_match_partial_timestamp(self):
        assert not DOCKER_TS_RE.match("2024-03-02 not iso format")

    def test_captures_timestamp_group(self):
        line = "2026-03-07T10:05:30.000Z message"
        m = DOCKER_TS_RE.match(line)
        assert m.group(1) == "2026-03-07T10:05:30.000Z"


class TestErrorPatternRe:

    @pytest.mark.parametrize("keyword", [
        "ERROR", "CRITICAL", "FATAL", "Exception", "Traceback", "SEVERE"
    ])
    def test_matches_error_keywords(self, keyword):
        assert ERROR_PATTERN_RE.search(f"some log line with {keyword} in it")

    def test_matches_panic_colon(self):
        # panic: must be followed by a word char (no space) for \b to anchor correctly
        assert ERROR_PATTERN_RE.search("goroutine 1 [running]: panic:runtime error index out of range")

    def test_matches_http_5xx(self):
        assert ERROR_PATTERN_RE.search("HTTP 500 Internal Server Error")
        assert ERROR_PATTERN_RE.search("returned HTTP 503 unavailable")

    def test_does_not_match_http_4xx(self):
        assert not ERROR_PATTERN_RE.search("HTTP 404 Not Found")
        assert not ERROR_PATTERN_RE.search("HTTP 200 OK")

    def test_case_insensitive_error(self):
        assert ERROR_PATTERN_RE.search("error: something went wrong")
        assert ERROR_PATTERN_RE.search("fatal: disk full")

    def test_no_match_on_normal_log(self):
        assert not ERROR_PATTERN_RE.search("2026-03-07T12:00:00Z INFO request processed")


class TestParseTimestamp:

    def test_returns_utc_datetime(self):
        line = "2026-03-07T12:00:00Z INFO started"
        dt = parse_timestamp(line)
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 7
        assert dt.hour == 12
        assert dt.minute == 0
        assert dt.second == 0

    def test_parses_subsecond_precision(self):
        line = "2024-03-02T21:19:41.123456789Z msg"
        dt = parse_timestamp(line)
        assert dt is not None
        assert dt.year == 2024
        assert dt.hour == 21
        assert dt.minute == 19
        assert dt.second == 41

    def test_parses_line_without_z_suffix(self):
        line = "2026-03-07T08:30:00 some message"
        dt = parse_timestamp(line)
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 8

    def test_returns_none_for_plain_text(self):
        assert parse_timestamp("no timestamp here") is None

    def test_returns_none_for_empty_string(self):
        assert parse_timestamp("") is None

    def test_leading_whitespace_is_stripped(self):
        line = "  2026-03-07T10:00:00Z INFO msg"
        dt = parse_timestamp(line)
        assert dt is not None

    def test_unix_epoch_result_matches_known_value(self):
        line = "1970-01-01T00:00:00Z baseline"
        dt = parse_timestamp(line)
        assert dt is not None
        assert dt.timestamp() == 0.0
