"""
api_query_extractor.py – Extract HTTP API calls and DB queries from log lines.

Two independent extraction families:
  - extract_api_calls  – HTTP method/path/status lines (access-log style)
  - extract_queries    – SQL statements (SELECT/INSERT/UPDATE/DELETE)

Each accepts an optional `language` hint (from PatternDetector.detect_language
in log_pattern_analyzer.py). When language == "java", Java/Spring/Hibernate-
specific patterns are tried first; lines that don't match fall through to the
generic patterns. All analysis is local — no external API calls, no Docker
dependency.
"""

import re
from typing import Optional

from .patterns import parse_timestamp

# ---------------------------------------------------------------------------
# Generic (framework-agnostic) patterns
# ---------------------------------------------------------------------------

# Apache/nginx/Express-style combined access log: "METHOD /path HTTP/1.1" 200
GENERIC_API_RE = re.compile(
    r'"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(\S+)\s+HTTP/[\d.]+"\s*(\d{3})?'
    r'|\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/\S*)\s*(?:-\s*)?(\d{3})?\b',
    re.IGNORECASE,
)

GENERIC_QUERY_RE = re.compile(
    r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b.*",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Java-specific patterns (Spring / embedded Tomcat / Hibernate)
# ---------------------------------------------------------------------------

# Spring DispatcherServlet request-mapping log, e.g.:
#   "Mapped [{GET [/api/pets/{id}]}] to public ..."
#   "GET \"/owners/1\", parameters={}"  (Spring's DEBUG request log)
JAVA_API_RE = re.compile(
    r"Mapped\s+\[\{(GET|POST|PUT|DELETE|PATCH)\s+\[([^\]]+)\]"
    r'|\b(GET|POST|PUT|DELETE|PATCH)\s+"(/[^"]*)"',
    re.IGNORECASE,
)

# Hibernate query logging, e.g. "Hibernate: select owner0_.id as ..."
JAVA_QUERY_RE = re.compile(
    r"Hibernate:\s*(select|insert|update|delete)\b.*",
    re.IGNORECASE,
)


def extract_api_calls(
    lines: list[str],
    language: Optional[str] = None,
) -> list[tuple[str, str, Optional[str], Optional[float], str]]:
    """Extract HTTP API calls from log lines.

    Args:
        lines:    Raw log lines (may include Docker-prepended timestamps).
        language: Optional language hint from PatternDetector.detect_language.
                  When "java", Java-specific patterns are tried first.

    Returns:
        List of (method, path, status_or_None, unix_ts_or_None, raw_line).
    """
    results: list[tuple[str, str, Optional[str], Optional[float], str]] = []

    for line in lines:
        dt = parse_timestamp(line)
        unix_ts: Optional[float] = dt.timestamp() if dt is not None else None

        matched = False
        if language == "java":
            m = JAVA_API_RE.search(line)
            if m:
                if m.group(1):
                    method, path = m.group(1), m.group(2)
                else:
                    method, path = m.group(3), m.group(4)
                results.append((method.upper(), path, None, unix_ts, line.strip()))
                matched = True

        if matched:
            continue

        m = GENERIC_API_RE.search(line)
        if m:
            if m.group(1):
                method, path, status = m.group(1), m.group(2), m.group(3)
            else:
                method, path, status = m.group(4), m.group(5), m.group(6)
            results.append((method.upper(), path, status, unix_ts, line.strip()))

    return results


def extract_queries(
    lines: list[str],
    language: Optional[str] = None,
) -> list[tuple[str, Optional[float], str]]:
    """Extract SQL query statements from log lines.

    Args:
        lines:    Raw log lines (may include Docker-prepended timestamps).
        language: Optional language hint from PatternDetector.detect_language.
                  When "java", Hibernate-style query lines are tried first.

    Returns:
        List of (query_text, unix_ts_or_None, raw_line). `query_text` is the
        matched statement only — bind parameters logged on separate lines
        (as Hibernate does) are not stitched in.
    """
    results: list[tuple[str, Optional[float], str]] = []

    for line in lines:
        dt = parse_timestamp(line)
        unix_ts: Optional[float] = dt.timestamp() if dt is not None else None

        matched = False
        if language == "java":
            m = JAVA_QUERY_RE.search(line)
            if m:
                results.append((m.group(0).strip(), unix_ts, line.strip()))
                matched = True

        if matched:
            continue

        m = GENERIC_QUERY_RE.search(line)
        if m:
            results.append((m.group(0).strip(), unix_ts, line.strip()))

    return results
