"""
request_tracer.py – Extract request IDs from log lines and build per-request timelines.

Algorithm:
  1. Scan each log line for request ID patterns (configurable regex list).
  2. Group lines by extracted ID → {request_id: [(unix_ts, pattern_name, line), ...]}
  3. Sort each group chronologically and build a timeline dict per request.

All analysis is local – no external API calls, no Docker dependency.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from .patterns import parse_timestamp


@dataclass
class RequestIdPattern:
    """A named, compiled pattern for extracting a request/trace/correlation ID.

    The raw `pattern` string must contain exactly one capture group whose
    value is the ID (e.g. ``r"request_id=([\\w-]+)"``).
    """

    name: str       # human label, e.g. "request_id"
    pattern: str    # raw regex string with exactly one capture group
    compiled: re.Pattern = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            self.compiled = re.compile(self.pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(
                f"RequestIdPattern '{self.name}': invalid regex '{self.pattern}': {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Pre-filter fast-path – same technique as SecretDetector._any_pattern_re.
# A combined alternation of common ID-related keywords lets us skip pattern
# matching on lines that obviously contain no request identifier.
# ---------------------------------------------------------------------------

_FAST_PATH_RE = re.compile(
    r"request[_-]?id|trace[_-]?id|correlation[_-]?id|req[_-]?id|x[_-]?request",
    re.IGNORECASE,
)


def _build_fast_path(patterns: list[RequestIdPattern]) -> re.Pattern:
    """Build a combined pre-filter regex from the literal keywords in each pattern.

    Extracts the alternation branches inside the outermost non-capturing group
    (e.g. ``(?:request_id|req_id)`` → ``request_id|req_id``), then combines
    them into a single pre-filter.  Falls back to ``_FAST_PATH_RE`` on any error.
    """
    _NC_GROUP_RE = re.compile(r"^\(\?:(.+?)\)")

    try:
        alts: list[str] = []
        for p in patterns:
            raw = p.compiled.pattern
            # Try to extract the alternation inside (?:...) prefix.
            m = _NC_GROUP_RE.search(raw)
            if m:
                alts.append(m.group(1))
            else:
                # No non-capturing wrapper; take everything before the first
                # capturing group as the keyword prefix.
                prefix = raw.split("(")[0]
                if prefix:
                    alts.append(prefix)
        combined = "|".join(alts)
        return re.compile(combined, re.IGNORECASE) if combined else _FAST_PATH_RE
    except re.error:
        return _FAST_PATH_RE


def extract_ids(
    lines: list[str],
    patterns: list[RequestIdPattern],
) -> list[tuple[str, str, Optional[float], str]]:
    """Scan log lines for request IDs and return a flat list of matches.

    For each line that matches at least one pattern, emit one tuple per
    distinct match.  Lines with no match are silently skipped.

    Args:
        lines:    Raw log lines (may include Docker-prepended timestamps).
        patterns: Compiled RequestIdPattern list.

    Returns:
        List of ``(request_id, pattern_name, unix_ts_or_None, raw_line)``.
        Lines with multiple ID matches emit one tuple per match.
    """
    if not patterns:
        return []

    fast_path = _build_fast_path(patterns)
    results: list[tuple[str, str, Optional[float], str]] = []

    for line in lines:
        # Skip lines that almost certainly contain no request ID.
        if not fast_path.search(line):
            continue

        dt = parse_timestamp(line)
        unix_ts: Optional[float] = dt.timestamp() if dt is not None else None

        for pat in patterns:
            for m in pat.compiled.finditer(line):
                try:
                    request_id = m.group(1)
                except IndexError:
                    # Pattern has no capture group – skip rather than crash.
                    continue
                if request_id:
                    results.append((request_id, pat.name, unix_ts, line.strip()))

    return results


def group_by_request(
    matches: list[tuple[str, str, Optional[float], str]],
) -> dict[str, list[tuple[Optional[float], str, str]]]:
    """Group flat match tuples by request ID.

    Args:
        matches: Output of :func:`extract_ids` —
                 ``(request_id, pattern_name, unix_ts, line)``.

    Returns:
        ``{request_id: [(unix_ts_or_None, pattern_name, raw_line), ...]}``.
        Each list is sorted by ``unix_ts`` ascending (``None`` sorts last).
    """
    grouped: dict[str, list[tuple[Optional[float], str, str]]] = {}
    for request_id, pattern_name, unix_ts, line in matches:
        grouped.setdefault(request_id, []).append((unix_ts, pattern_name, line))

    # Sort chronologically; None timestamps sort after all real timestamps.
    for events in grouped.values():
        events.sort(key=lambda t: (t[0] is None, t[0] or 0.0))

    return grouped


def build_timelines(
    grouped: dict[str, list[tuple[Optional[float], str, str]]],
    container_name: str,
) -> list[dict]:
    """Convert grouped events into timeline dicts suitable for JSON serialisation.

    Args:
        grouped:        Output of :func:`group_by_request`.
        container_name: The container these lines came from.  Injected at the
                        call site so this function stays container-agnostic.

    Returns:
        List of timeline dicts, one per request ID:

        .. code-block:: json

            {
                "request_id":  "<id>",
                "container":   "<name>",
                "event_count": 3,
                "first_seen":  "2026-03-13T10:01:00Z",
                "last_seen":   "2026-03-13T10:01:00.342Z",
                "duration_ms": 342.0,
                "events": [
                    {"timestamp": "...", "pattern_name": "request_id", "message": "..."},
                    ...
                ]
            }
    """
    from datetime import datetime, timezone

    def _to_iso(unix_ts: Optional[float]) -> Optional[str]:
        if unix_ts is None:
            return None
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"

    timelines: list[dict] = []

    for request_id, events in grouped.items():
        # Extract valid timestamps for duration calculation.
        timestamps = [ts for ts, _, _ in events if ts is not None]

        first_ts = min(timestamps) if timestamps else None
        last_ts = max(timestamps) if timestamps else None
        duration_ms = (
            round((last_ts - first_ts) * 1000, 3)
            if first_ts is not None and last_ts is not None
            else None
        )

        timeline_events = [
            {
                "timestamp": _to_iso(ts),
                "pattern_name": pattern_name,
                "message": line[:500],  # cap message length
            }
            for ts, pattern_name, line in events
        ]

        timelines.append({
            "request_id": request_id,
            "container": container_name,
            "event_count": len(events),
            "first_seen": _to_iso(first_ts),
            "last_seen": _to_iso(last_ts),
            "duration_ms": duration_ms,
            "events": timeline_events,
        })

    # Sort by first_seen ascending (None-first fallback).
    timelines.sort(key=lambda t: (t["first_seen"] is None, t["first_seen"] or ""))
    return timelines
