"""Unit tests for request_tracer.py extraction and timeline logic."""

from types import SimpleNamespace

import pytest

from docker_log_analyzer.config import settings
from docker_log_analyzer.request_tracer import (
    _FAST_PATH_RE,
    _build_fast_path,
    RequestIdPattern,
    build_timelines,
    cross_container_timelines,
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


# ---------------------------------------------------------------------------
# cross_container_timelines
# ---------------------------------------------------------------------------

class TestCrossContainerTimelines:

    def _match(self, id_value, pattern_name, unix_ts, line, container):
        return (id_value, pattern_name, unix_ts, line, container)

    def test_single_container_single_pattern_passthrough(self):
        matches = [
            self._match("abc-123", "request_id", 1000.0, "GET /api request_id=abc-123", "web"),
            self._match("abc-123", "request_id", 1001.0, "200 OK request_id=abc-123", "web"),
        ]
        result = cross_container_timelines(matches, trace_window_seconds=60)
        assert len(result) == 1
        tl = result[0]
        assert tl["id_value"] == "abc-123"
        assert tl["containers"] == ["web"]
        assert tl["id_patterns"] == ["request_id"]
        assert tl["event_count"] == 2
        assert tl["duration_ms"] == 1000.0

    def test_same_value_different_pattern_names_merges(self):
        """request_id=abc in web and transaction_id=abc in db → one timeline."""
        matches = [
            self._match("abc-123", "request_id",    1000.0, "web log",  "web"),
            self._match("abc-123", "transaction_id", 1002.0, "db log",   "database"),
        ]
        result = cross_container_timelines(matches, trace_window_seconds=60)
        assert len(result) == 1
        tl = result[0]
        assert sorted(tl["containers"]) == ["database", "web"]
        assert sorted(tl["id_patterns"]) == ["request_id", "transaction_id"]
        assert tl["event_count"] == 2

    def test_events_sorted_chronologically(self):
        matches = [
            self._match("abc-123", "request_id", 1005.0, "late",  "web"),
            self._match("abc-123", "request_id", 1000.0, "early", "db"),
        ]
        result = cross_container_timelines(matches, trace_window_seconds=60)
        assert result[0]["events"][0]["message"] == "early"
        assert result[0]["events"][1]["message"] == "late"

    def test_spread_exceeds_window_dropped(self):
        """Events > trace_window_seconds apart are dropped as a collision."""
        matches = [
            self._match("short-id", "request_id", 0.0,    "first",  "web"),
            self._match("short-id", "request_id", 200.0,  "second", "db"),
        ]
        result = cross_container_timelines(matches, trace_window_seconds=60)
        assert result == []

    def test_spread_within_window_kept(self):
        matches = [
            self._match("abc-123", "request_id", 0.0,  "first",  "web"),
            self._match("abc-123", "request_id", 59.0, "second", "db"),
        ]
        result = cross_container_timelines(matches, trace_window_seconds=60)
        assert len(result) == 1

    def test_each_event_has_container_field(self):
        matches = [
            self._match("abc-123", "request_id", 1000.0, "line", "web"),
        ]
        result = cross_container_timelines(matches, trace_window_seconds=60)
        assert result[0]["events"][0]["container"] == "web"

    def test_different_ids_produce_separate_timelines(self):
        matches = [
            self._match("req-1", "request_id", 1000.0, "line1", "web"),
            self._match("req-2", "request_id", 2000.0, "line2", "web"),
        ]
        result = cross_container_timelines(matches, trace_window_seconds=60)
        assert len(result) == 2

    def test_empty_input_returns_empty(self):
        assert cross_container_timelines([], trace_window_seconds=60) == []

    def test_results_sorted_by_first_seen(self):
        matches = [
            self._match("late-req",  "request_id", 2000.0, "late",  "web"),
            self._match("early-req", "request_id", 1000.0, "early", "web"),
        ]
        result = cross_container_timelines(matches, trace_window_seconds=60)
        assert result[0]["id_value"] == "early-req"
        assert result[1]["id_value"] == "late-req"


# ---------------------------------------------------------------------------
# Default request_id_patterns from config.py — strict UUID + loose fallbacks
# ---------------------------------------------------------------------------

def _default_patterns() -> list[RequestIdPattern]:
    return [
        RequestIdPattern(name=name, pattern=pat)
        for name, pat in settings.request_id_patterns.items()
    ]


class TestDefaultPatternsConfig:
    """All patterns built from Settings.request_id_patterns must compile
    and expose exactly one capture group."""

    @pytest.mark.parametrize("name", [
        "request_id", "trace_id", "correlation_id", "transaction_id", "session_id",
        "request_id_loose", "trace_id_loose", "correlation_id_loose",
        "transaction_id_loose", "session_id_loose",
    ])
    def test_all_expected_pattern_names_present(self, name):
        assert name in settings.request_id_patterns

    def test_all_patterns_compile_without_error(self):
        patterns = _default_patterns()
        assert len(patterns) == len(settings.request_id_patterns)


class TestStrictUuidPatterns:
    """Strict patterns must match well-formed UUIDs and reject non-UUID IDs."""

    UUID = "8f2a1c3e-4b5d-4e6f-9a0b-1c2d3e4f5a6b"

    @pytest.mark.parametrize("name,keyword", [
        ("request_id", "request_id"),
        ("trace_id", "trace_id"),
        ("correlation_id", "correlation_id"),
        ("transaction_id", "transaction_id"),
        ("session_id", "session_id"),
    ])
    def test_strict_pattern_matches_uuid(self, name, keyword):
        pattern = settings.request_id_patterns[name]
        line = f"INFO {keyword}={self.UUID} handled"
        matches = extract_ids([line], [RequestIdPattern(name=name, pattern=pattern)])
        assert len(matches) == 1
        assert matches[0][0] == self.UUID

    @pytest.mark.parametrize("name,keyword", [
        ("request_id", "request_id"),
        ("trace_id", "trace_id"),
        ("correlation_id", "correlation_id"),
        ("transaction_id", "transaction_id"),
        ("session_id", "session_id"),
    ])
    def test_strict_pattern_rejects_non_uuid(self, name, keyword):
        pattern = settings.request_id_patterns[name]
        line = f"INFO {keyword}=req-8xk2p9 handled"
        matches = extract_ids([line], [RequestIdPattern(name=name, pattern=pattern)])
        assert matches == []


class TestLooseFallbackPatterns:
    """Loose patterns must catch non-UUID ID formats the strict patterns miss:
    short numeric IDs, base62/nanoid IDs, and raw hex trace IDs."""

    @pytest.mark.parametrize("name,keyword", [
        ("request_id_loose", "request_id"),
        ("trace_id_loose", "trace_id"),
        ("correlation_id_loose", "correlation_id"),
        ("transaction_id_loose", "transaction_id"),
        ("session_id_loose", "session_id"),
    ])
    @pytest.mark.parametrize("id_value", [
        "42891",                              # short numeric
        "req-8xk2p9",                         # dash-separated alnum
        "V1StGXR8_Z5jdHi6BmyT",               # base62/nanoid style
        "8f14e45fceea167a5a36dedd4bad3bd9",   # raw 32-char hex (no dashes)
    ])
    def test_loose_pattern_matches_non_uuid_formats(self, name, keyword, id_value):
        pattern = settings.request_id_patterns[name]
        line = f"INFO {keyword}={id_value} handled"
        matches = extract_ids([line], [RequestIdPattern(name=name, pattern=pattern)])
        assert len(matches) == 1
        assert matches[0][0] == id_value

    def test_loose_pattern_also_matches_uuid(self):
        """A UUID satisfies the loose [\\w-]{6,64} shape too — both strict and
        loose patterns fire on the same line, tagged with different pattern_name."""
        uuid = "8f2a1c3e-4b5d-4e6f-9a0b-1c2d3e4f5a6b"
        line = f"INFO request_id={uuid} handled"
        patterns = [
            RequestIdPattern(name="request_id", pattern=settings.request_id_patterns["request_id"]),
            RequestIdPattern(name="request_id_loose", pattern=settings.request_id_patterns["request_id_loose"]),
        ]
        matches = extract_ids([line], patterns)
        assert len(matches) == 2
        assert {m[1] for m in matches} == {"request_id", "request_id_loose"}
        assert all(m[0] == uuid for m in matches)


class TestLooseFallbackCrossContainerCorrelation:
    """End-to-end: a non-UUID ID that only the loose patterns catch must still
    correlate across containers via cross_container_timelines, exactly like a
    strict UUID match would."""

    def test_loose_id_correlates_across_containers(self):
        patterns = _default_patterns()
        gateway_lines = [
            "2026-03-13T10:00:00.000Z INFO request_id=req-8xk2p9 received",
        ]
        web_lines = [
            "2026-03-13T10:00:00.050Z INFO request_id=req-8xk2p9 processing",
        ]
        db_lines = [
            "2026-03-13T10:00:00.090Z INFO transaction_id=req-8xk2p9 committed",
        ]

        all_matches = []
        for lines, container in [
            (gateway_lines, "gateway"),
            (web_lines, "web-app"),
            (db_lines, "database"),
        ]:
            for id_value, pattern_name, unix_ts, line in extract_ids(lines, patterns):
                all_matches.append((id_value, pattern_name, unix_ts, line, container))

        timelines = cross_container_timelines(all_matches, trace_window_seconds=120)

        assert len(timelines) == 1
        tl = timelines[0]
        assert tl["id_value"] == "req-8xk2p9"
        assert sorted(tl["containers"]) == ["database", "gateway", "web-app"]
        assert tl["event_count"] == 3

    def test_non_uuid_id_dropped_without_loose_patterns(self):
        """Sanity check: with only strict patterns, the same scenario yields
        no matches at all, since 'req-8xk2p9' is not UUID-shaped."""
        strict_only = [
            RequestIdPattern(name=n, pattern=p)
            for n, p in settings.request_id_patterns.items()
            if not n.endswith("_loose")
        ]
        line = "2026-03-13T10:00:00.000Z INFO request_id=req-8xk2p9 received"
        matches = extract_ids([line], strict_only)
        assert matches == []
