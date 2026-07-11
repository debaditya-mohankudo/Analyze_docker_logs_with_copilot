"""Unit tests for api_query_extractor.py extraction logic."""

import pytest

from docker_log_analyzer.api_query_extractor import extract_api_calls, extract_queries


pytestmark = pytest.mark.unit


class TestExtractApiCallsGeneric:
    def test_extracts_combined_access_log_line(self):
        lines = ['127.0.0.1 - - [07/Jul/2026:10:00:00] "GET /api/pets/1 HTTP/1.1" 200 512']

        results = extract_api_calls(lines)

        assert len(results) == 1
        method, path, status, unix_ts, line = results[0]
        assert method == "GET"
        assert path == "/api/pets/1"
        assert status == "200"

    def test_extracts_bare_method_path_line(self):
        lines = ["2026-07-07T10:00:00Z INFO POST /orders 201"]

        results = extract_api_calls(lines)

        assert len(results) == 1
        assert results[0][0] == "POST"
        assert results[0][1] == "/orders"

    def test_returns_empty_when_no_match(self):
        assert extract_api_calls(["just a plain log line"]) == []

    def test_captures_timestamp_when_present(self):
        lines = ['2026-07-07T10:00:00.000Z "GET /health HTTP/1.1" 200']

        results = extract_api_calls(lines)

        assert results[0][3] is not None


class TestExtractApiCallsJava:
    def test_prefers_spring_mapped_pattern_for_java(self):
        lines = ["Mapped [{GET [/api/pets/{id}]}] to public org.springframework...Owner"]

        results = extract_api_calls(lines, language="java")

        assert len(results) == 1
        method, path, status, unix_ts, line = results[0]
        assert method == "GET"
        assert path == "/api/pets/{id}"
        assert status is None

    def test_prefers_spring_debug_request_pattern_for_java(self):
        lines = ['GET "/owners/1", parameters={}']

        results = extract_api_calls(lines, language="java")

        assert results[0][0] == "GET"
        assert results[0][1] == "/owners/1"

    def test_falls_back_to_generic_when_java_pattern_does_not_match(self):
        lines = ['"POST /orders HTTP/1.1" 201']

        results = extract_api_calls(lines, language="java")

        assert len(results) == 1
        assert results[0][0] == "POST"
        assert results[0][1] == "/orders"

    def test_non_java_language_never_uses_java_pattern(self):
        # Java-shaped line, but language hint says python — should still match
        # via the generic fallback only (no method/path from JAVA_API_RE captured
        # separately, but the bare METHOD /path form is a subset the generic regex
        # also covers via its second alternative).
        lines = ["Mapped [{GET [/api/pets/{id}]}] to public ...Owner"]

        results = extract_api_calls(lines, language="python")

        # Generic regex does not understand the Mapped[...] format at all.
        assert results == []


class TestExtractQueriesGeneric:
    def test_extracts_select_statement(self):
        lines = ["2026-07-07T10:00:00Z DEBUG SELECT * FROM owners WHERE id = 1"]

        results = extract_queries(lines)

        assert len(results) == 1
        query, unix_ts, line = results[0]
        assert query.upper().startswith("SELECT")

    def test_extracts_insert_and_delete(self):
        lines = [
            "INSERT INTO owners (name) VALUES ('bob')",
            "DELETE FROM owners WHERE id = 1",
        ]

        results = extract_queries(lines)

        assert len(results) == 2

    def test_returns_empty_when_no_match(self):
        assert extract_queries(["just a plain log line"]) == []


class TestExtractQueriesJava:
    def test_prefers_hibernate_pattern_for_java(self):
        lines = ["Hibernate: select owner0_.id as id1_0_ from owners owner0_"]

        results = extract_queries(lines, language="java")

        assert len(results) == 1
        query, unix_ts, line = results[0]
        assert query.lower().startswith("hibernate: select")

    def test_falls_back_to_generic_when_no_hibernate_prefix(self):
        lines = ["SELECT * FROM owners"]

        results = extract_queries(lines, language="java")

        assert len(results) == 1
        assert results[0][0].upper().startswith("SELECT")
