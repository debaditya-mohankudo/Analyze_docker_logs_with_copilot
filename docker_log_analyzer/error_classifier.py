"""
error_classifier.py – Semantic error classification for Docker container logs.

Categorises error log lines into actionable classes (database, network, timeout,
auth, oom, disk, application, rate_limit, configuration) using rule-based regex
matching.  No LLMs — all analysis is local and deterministic.

Categories are ordered by specificity: database is checked before network
(since DB timeouts are a subset of network issues), and application is checked
last as the broadest catch-all before "unknown".
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from .patterns import ERROR_PATTERN_RE, parse_timestamp


# ---------------------------------------------------------------------------
# Category definitions
# ---------------------------------------------------------------------------

@dataclass
class ErrorCategory:
    """A named error category with compiled patterns and a remediation hint."""

    name: str
    patterns: list[str]         # raw regex strings
    recommendation: str
    compiled: list[re.Pattern] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.compiled = [re.compile(p, re.IGNORECASE) for p in self.patterns]


# Categories ordered by specificity — most specific first, broadest last.
CATEGORIES: list[ErrorCategory] = [
    ErrorCategory(
        name="database",
        patterns=[
            r"connection refused.*(?:5432|3306|27017|6379|1433|1521|9042)",
            r"deadlock detected",
            r"too many connections",
            r"query timeout",
            r"(?:SQL|JDBC|Hibernate|PSQLException|PDOException|mysqli_error)",
            r"could not execute statement",
            r"ORA-\d+",
            r"SQLSTATE\[",
            r"Table .+ doesn't exist",
            r"connection pool (?:exhausted|full|timeout)",
            r"(?:postgres|mysql|mongo|redis|cassandra).*(?:error|fail|timeout|refused)",
        ],
        recommendation="Check database connection pool limits, query performance, and database server health",
    ),
    ErrorCategory(
        name="network",
        patterns=[
            r"connection timed out",
            r"DNS resolution failed",
            r"ECONNREFUSED",
            r"ECONNRESET",
            r"ENETUNREACH",
            r"EHOSTUNREACH",
            r"socket hang up",
            r"no such host",
            r"network (?:is )?unreachable",
            r"connection reset by peer",
            r"broken pipe",
        ],
        recommendation="Check network connectivity, DNS resolution, and firewall rules between services",
    ),
    ErrorCategory(
        name="timeout",
        patterns=[
            r"read timeout",
            r"write timeout",
            r"gateway timeout",
            r"\b504\b",
            r"context deadline exceeded",
            r"request timeout",
            r"operation timed? ?out",
            r"client timeout",
            r"idle timeout",
            r"upstream timed out",
        ],
        recommendation="Review upstream service latency and timeout configurations; consider increasing timeouts or adding circuit breakers",
    ),
    ErrorCategory(
        name="auth",
        patterns=[
            r"401 Unauthorized",
            r"403 Forbidden",
            r"invalid token",
            r"authentication failed",
            r"access denied",
            r"permission denied",
            r"unauthorized",
            r"invalid credentials",
            r"token expired",
            r"JWT (?:expired|invalid|malformed)",
        ],
        recommendation="Verify authentication credentials, token validity, and service-to-service auth configuration",
    ),
    ErrorCategory(
        name="oom",
        patterns=[
            r"OOMKilled",
            r"out of memory",
            r"heap space",
            r"GC overhead limit",
            r"Cannot allocate memory",
            r"MemoryError",
            r"java\.lang\.OutOfMemoryError",
            r"ENOMEM",
            r"memory allocation failed",
        ],
        recommendation="Inspect container memory limits and heap allocation; consider increasing memory or fixing memory leaks",
    ),
    ErrorCategory(
        name="disk",
        patterns=[
            r"no space left on device",
            r"disk quota exceeded",
            r"ENOSPC",
            r"filesystem full",
            r"write failed.*(?:disk|storage|volume)",
            r"IOError.*(?:disk|storage)",
        ],
        recommendation="Check disk usage, log rotation, and volume mounts; consider expanding storage or cleaning up old data",
    ),
    ErrorCategory(
        name="rate_limit",
        patterns=[
            r"429 Too Many Requests",
            r"rate limit exceeded",
            r"throttled",
            r"too many requests",
            r"quota exceeded",
            r"request rate.*exceeded",
        ],
        recommendation="Implement backoff/retry logic and review rate limit configurations; consider caching or request batching",
    ),
    ErrorCategory(
        name="configuration",
        patterns=[
            r"missing required",
            r"invalid config",
            r"environment variable not set",
            r"configuration error",
            r"missing (?:env|environment|config)",
            r"unknown option",
            r"invalid (?:setting|parameter|argument|option)",
        ],
        recommendation="Verify environment variables, config files, and deployment manifests for missing or invalid settings",
    ),
    ErrorCategory(
        name="application",
        patterns=[
            r"NullPointerException",
            r"TypeError",
            r"ValueError",
            r"KeyError",
            r"IndexError",
            r"AttributeError",
            r"RuntimeError",
            r"panic:",
            r"unhandled exception",
            r"assertion (?:failed|error)",
            r"segmentation fault",
            r"stack overflow",
            r"uncaught exception",
        ],
        recommendation="Review application code at the stack trace location; check for null/undefined values and type mismatches",
    ),
]

# Category names for validation.
VALID_CATEGORIES = frozenset(c.name for c in CATEGORIES) | {"unknown"}

# Combined pre-filter: skip lines that don't look like errors at all.
# Uses the shared ERROR_PATTERN_RE from patterns.py.
_ERROR_PREFILTER = ERROR_PATTERN_RE


# ---------------------------------------------------------------------------
# Classification functions
# ---------------------------------------------------------------------------

def classify_line(line: str) -> Optional[str]:
    """Classify a single log line into an error category.

    Returns the category name (e.g. ``"database"``) or ``None`` if the line
    is not an error.  Lines that are errors but match no specific category
    return ``"unknown"``.

    Categories are checked in specificity order — the first match wins.
    """
    if not _ERROR_PREFILTER.search(line):
        return None

    for cat in CATEGORIES:
        for pat in cat.compiled:
            if pat.search(line):
                return cat.name

    return "unknown"


def classify_lines(
    lines: list[str],
) -> list[tuple[str, Optional[float], str]]:
    """Classify all error lines and return ``(category, unix_ts, line)`` tuples.

    Non-error lines are silently skipped.

    Args:
        lines: Raw log lines (may include Docker-prepended timestamps).

    Returns:
        List of ``(category, unix_ts_or_None, raw_line)`` for every error line.
    """
    results: list[tuple[str, Optional[float], str]] = []
    for line in lines:
        category = classify_line(line)
        if category is None:
            continue
        dt = parse_timestamp(line)
        unix_ts = dt.timestamp() if dt is not None else None
        results.append((category, unix_ts, line.strip()))
    return results


def aggregate_stats(
    classified: list[tuple[str, Optional[float], str]],
    max_samples: int = 3,
) -> dict:
    """Aggregate classified errors into per-category statistics.

    Args:
        classified: Output of :func:`classify_lines`.
        max_samples: Maximum sample lines to include per category.

    Returns:
        Dict with ``categories``, ``dominant_category``, and ``category_timeline``::

            {
                "total_errors": 142,
                "categories": {
                    "database": {
                        "count": 89,
                        "percentage": 62.7,
                        "first_seen": "2026-...",
                        "last_seen": "2026-...",
                        "samples": ["..."],
                        "recommendation": "..."
                    }
                },
                "dominant_category": "database",
                "category_timeline": [
                    {"minute": "2026-03-13T10:01", "database": 12, "timeout": 3}
                ]
            }
    """
    from datetime import datetime, timezone

    if not classified:
        return {
            "total_errors": 0,
            "categories": {},
            "dominant_category": None,
            "category_timeline": [],
        }

    # Build per-category stats.
    cat_data: dict[str, dict] = {}
    for category, unix_ts, line in classified:
        if category not in cat_data:
            cat_data[category] = {
                "count": 0,
                "timestamps": [],
                "samples": [],
            }
        entry = cat_data[category]
        entry["count"] += 1
        if unix_ts is not None:
            entry["timestamps"].append(unix_ts)
        if len(entry["samples"]) < max_samples:
            entry["samples"].append(line[:300])

    total = len(classified)

    def _to_iso(unix_ts: float) -> str:
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"

    # Look up recommendations.
    rec_map = {c.name: c.recommendation for c in CATEGORIES}

    categories: dict[str, dict] = {}
    dominant_category: Optional[str] = None
    dominant_count = 0

    for cat_name, data in cat_data.items():
        count = data["count"]
        ts_list = sorted(data["timestamps"])
        categories[cat_name] = {
            "count": count,
            "percentage": round(count / total * 100, 1),
            "first_seen": _to_iso(ts_list[0]) if ts_list else None,
            "last_seen": _to_iso(ts_list[-1]) if ts_list else None,
            "samples": data["samples"],
            "recommendation": rec_map.get(cat_name, ""),
        }
        if count > dominant_count:
            dominant_count = count
            dominant_category = cat_name

    # Build minute-level timeline.
    minute_buckets: dict[str, dict[str, int]] = {}
    for category, unix_ts, _ in classified:
        if unix_ts is None:
            continue
        minute_key = datetime.fromtimestamp(
            unix_ts, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M")
        if minute_key not in minute_buckets:
            minute_buckets[minute_key] = {}
        bucket = minute_buckets[minute_key]
        bucket[category] = bucket.get(category, 0) + 1

    category_timeline = [
        {"minute": minute, **counts}
        for minute, counts in sorted(minute_buckets.items())
    ]

    return {
        "total_errors": total,
        "categories": categories,
        "dominant_category": dominant_category,
        "category_timeline": category_timeline,
    }
