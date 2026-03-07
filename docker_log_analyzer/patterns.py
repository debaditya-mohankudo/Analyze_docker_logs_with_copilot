"""
patterns.py – Shared regex patterns and timestamp utilities.

All log-analysis modules import from here to avoid duplicate definitions
and ensure behavioural consistency across spike detection, correlation,
and dependency mapping.
"""

import re
from datetime import datetime, timezone
from typing import Optional

# Docker SDK prepends RFC3339 timestamps when timestamps=True is used.
# Example: "2024-03-02T21:19:41.123456789Z [app] ERROR connection failed"
# Z is optional (Z?) to handle both UTC-explicit and bare ISO-8601 lines.
DOCKER_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)"
)

ERROR_PATTERN_RE = re.compile(
    r"\b(ERROR|CRITICAL|FATAL|Exception|Traceback|panic:|SEVERE)\b"
    r"|HTTP [5]\d{2}",
    re.IGNORECASE,
)


def parse_timestamp(line: str) -> Optional[datetime]:
    """
    Extract a UTC datetime from a Docker-prepended RFC3339 log line.
    Returns None if no parseable timestamp is found.
    """
    m = DOCKER_TS_RE.match(line.strip())
    if not m:
        return None
    ts_str = m.group(1).rstrip("Z")
    try:
        return datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
