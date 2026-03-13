"""Unit tests for request_tracer.py extraction and timeline logic."""

from types import SimpleNamespace

import pytest

from docker_log_analyzer.request_tracer import (
    _FAST_PATH_RE,
    _build_fast_path,
    RequestIdPattern,
    build_timelines,
    extract_ids,
    group_by_request,
)


pytestmark = pytest.mark.unit


class TestBuildFastPath:
    def test_extracts_non_capturing_group_alternations(self):
        patterns = [
            RequestIdPattern(
                name="request_id",
                pattern=r"(?:request_id|req_id)[:=]\s*([\w-]+)",
            ),
            RequestIdPattern(
                name="trace_id",
                pattern=r"(?:trace_id|x-request-id)[:=]\s*([\w-]+)",
            ),
        ]

        fast = _build_fast_path(patterns)

        assert fast.search("INFO req_id=abc-123") is not None
        assert fast.search("INFO x-request-id: zyx-1") is not None
        assert fast.search("INFO unrelated line") is None

    def test_falls_back_to_default_on_invalid_combined_regex(self):
        # Simulate a compiled pattern that would produce an invalid combined regex.
        fake = SimpleNamespace(compiled=SimpleNamespace(pattern="["))
        patterns = [fake]

        fast = _build_fast_path(patterns)

        assert fast.pattern == _FAST_PATH_RE.pattern
        assert fast.flags == _FAST_PATH_RE.flags


class TestExtractIds:
    def test_returns_empty_when_no_patterns(self):
        assert extract_ids(["request_id=abc"], []) == []

    def test_extracts_matches_and_timestamp_when_present(self):
        lines = [
            "2026-03-13T10:01:00.000Z INFO request_id=req-1 started",
            "2026-03-13T10:01:00.300Z INFO trace_id=req-1 processing",
        ]
        patterns = [
            RequestIdPattern(
                name="request_id",
                pattern=r"(?:request_id|req_id)[:=]\s*([\w-]+)",
            ),
            RequestIdPattern(
                name="trace_id",
                pattern=r"(?:trace_id|x-request-id)[:=]\s*([\w-]+)",
            ),
        ]

        matches = extract_ids(lines, patterns)

        assert len(matches) == 2
        assert matches[0][0] == "req-1"
        assert matches[0][1] == "request_id"
        assert matches[0][2] is not None
        assert matches[1][0] == "req-1"
        assert matches[1][1] == "trace_id"
        assert matches[1][2] is not None

    def test_skips_lines_not_matching_fast_path(self):
        lines = [
            "2026-03-13T10:01:00.000Z INFO start",
            "2026-03-13T10:01:00.010Z INFO request_id=req-2 accepted",
            "2026-03-13T10:01:00.020Z INFO finished",
        ]
        patterns = [
            RequestIdPattern(
                name="request_id",
                pattern=r"(?:request_id|req_id)[:=]\s*([\w-]+)",
            )
        ]

        matches = extract_ids(lines, patterns)

        assert len(matches) == 1
        assert matches[0][0] == "req-2"

    def test_pattern_without_capture_group_is_ignored(self):
        lines = ["2026-03-13T10:01:00.000Z INFO request_id=req-3"]
        patterns = [
            RequestIdPattern(name="bad", pattern=r"request_id=[\w-]+"),
            RequestIdPattern(name="good", pattern=r"request_id=([\w-]+)"),
        ]

        matches = extract_ids(lines, patterns)

        assert len(matches) == 1
        assert matches[0][0] == "req-3"
        assert matches[0][1] == "good"

    def test_multiple_matches_on_single_line(self):
        lines = ["2026-03-13T10:01:00.000Z INFO request_id=req-a req_id=req-b"]
        patterns = [
            RequestIdPattern(name="request_id", pattern=r"request_id=([\w-]+)"),
            RequestIdPattern(name="req_id", pattern=r"req_id=([\w-]+)"),
        ]

        matches = extract_ids(lines, patterns)

        got = {(rid, name) for rid, name, _, _ in matches}
        assert got == {("req-a", "request_id"), ("req-b", "req_id")}


class TestGroupByRequest:
    def test_groups_by_request_id_and_sorts_none_last(self):
        matches = [
            ("req-1", "request_id", None, "line no ts"),
            ("req-1", "trace_id", 100.2, "line later"),
            ("req-1", "request_id", 100.1, "line earlier"),
            ("req-2", "request_id", 90.0, "other request"),
        ]

        grouped = group_by_request(matches)

        assert set(grouped.keys()) == {"req-1", "req-2"}
        assert grouped["req-1"] == [
            (100.1, "request_id", "line earlier"),
            (100.2, "trace_id", "line later"),
            (None, "request_id", "line no ts"),
        ]


class TestBuildTimelines:
    def test_builds_timeline_fields_and_duration(self):
        grouped = {
            "req-1": [
                (1710324000.0, "request_id", "start"),
                (1710324000.342, "trace_id", "finish"),
            ]
        }

        timelines = build_timelines(grouped, container_name="svc-a")

        assert len(timelines) == 1
        t = timelines[0]
        assert t["request_id"] == "req-1"
        assert t["container"] == "svc-a"
        assert t["event_count"] == 2
        assert t["first_seen"] == "2024-03-13T10:00:00.000Z"
        assert t["last_seen"] == "2024-03-13T10:00:00.342Z"
        assert t["duration_ms"] == 342.0
        assert t["events"][0]["pattern_name"] == "request_id"
        assert t["events"][1]["pattern_name"] == "trace_id"

    def test_message_is_capped_to_500_chars(self):
        long_message = "x" * 700
        grouped = {"req-1": [(1710324000.0, "request_id", long_message)]}

        timelines = build_timelines(grouped, container_name="svc-a")

        assert len(timelines[0]["events"][0]["message"]) == 500

    def test_timeline_sorts_by_first_seen_with_none_last(self):
        grouped = {
            "late": [(200.0, "request_id", "late")],
            "none": [(None, "request_id", "no ts")],
            "early": [(100.0, "request_id", "early")],
        }

        timelines = build_timelines(grouped, container_name="svc")

        assert [t["request_id"] for t in timelines] == ["early", "late", "none"]


class TestRequestIdPatternValidation:
    def test_invalid_regex_raises_value_error(self):
        with pytest.raises(ValueError, match="invalid regex"):
            RequestIdPattern(name="bad", pattern=r"([")
