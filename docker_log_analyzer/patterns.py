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
# Accepts trailing Z, explicit numeric offset, or no timezone suffix.
DOCKER_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)"
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
    ts_str = m.group(1)
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None
