"""
correlator.py – Cross-container temporal error correlation.

Algorithm:
  1. Extract (unix_ts, line) tuples for error lines per container.
  2. For each ordered pair (A, B), count errors in B within ±time_window_seconds
     of each error in A (co-occurrence).
  3. correlation_score = matched_a / errors_A  (fraction of A errors that co-occurred with B).
  4. Return pairs sorted by score descending with ≤3 example pairs each.

All analysis is local – no external API calls.
"""

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from itertools import combinations

DOCKER_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)"
)

ERROR_PATTERN_RE = re.compile(
    r"\b(ERROR|CRITICAL|FATAL|Exception|Traceback|panic:|SEVERE)\b"
    r"|HTTP [5]\d{2}",
    re.IGNORECASE,
)

def _parse_ts(line: str) -> Optional[float]:
    """Return Unix timestamp (float) from Docker-prepended timestamp, or None."""
    m = DOCKER_TS_RE.match(line.strip())
    if not m:
        return None
    ts_str = m.group(1).rstrip("Z")
    try:
        return datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _extract_error_events(lines: List[str]) -> List[Tuple[float, str]]:
    """Return [(unix_ts, line)] for lines that are errors with parseable timestamps."""
    events = []
    for line in lines:
        ts = _parse_ts(line)
        if ts is None:
            continue
        if ERROR_PATTERN_RE.search(line):
            events.append((ts, line.strip()))
    return events


def _correlate_events(
    events_a: List[Tuple[float, str]],
    events_b: List[Tuple[float, str]],
    window: int,
) -> Tuple[int, int, List[dict]]:
    """
    Sliding two-pointer correlation between two sorted event lists.

    O(n + m + k) where k = total co-occurrences.
    j_start only advances forward, so the boundary scan is O(m) total.

    Returns (matched_a, co_occurrences, example_pairs)
      - matched_a:     number of A events that matched ≥1 B event
      - co_occurrences: total (A, B) pairs within ±window
    """
    matched_a = 0
    co_occurrences = 0
    example_pairs: List[dict] = []
    j_start = 0
    len_b = len(events_b)

    for ts_a, line_a in events_a:
        # Drop B events that are now too far in the past.
        while j_start < len_b and events_b[j_start][0] < ts_a - window:
            j_start += 1

        # Count every B event inside [ts_a - window, ts_a + window].
        j = j_start
        found = False
        while j < len_b and events_b[j][0] <= ts_a + window:
            co_occurrences += 1
            if not found:
                matched_a += 1
                found = True
            if len(example_pairs) < 3:
                example_pairs.append({
                    "a": line_a[:120],
                    "b": events_b[j][1][:120],
                    "delta_seconds": round(abs(ts_a - events_b[j][0]), 2),
                })
            j += 1

    return matched_a, co_occurrences, example_pairs


def correlate(
    container_logs: Dict[str, List[str]],
    time_window_seconds: int = 30,
) -> List[dict]:
    """
    Compute pairwise temporal correlation of errors across containers.

    Args:
        container_logs: {container_name: [raw_log_line, ...]}
        time_window_seconds: ±window in seconds for co-occurrence matching.

    Returns:
        List of correlation dicts sorted by score descending:
        {
            "container_a": str,
            "container_b": str,
            "correlation_score": float,   # 0.0 – 1.0
            "co_occurrences": int,
            "errors_a": int,
            "errors_b": int,
            "example_pairs": [
                {"a": str, "b": str, "delta_seconds": float}
            ]
        }
        Empty list if fewer than 2 containers have parseable error events.
    """
    error_events: Dict[str, List[Tuple[float, str]]] = {}
    for container, lines in container_logs.items():
        events = sorted(_extract_error_events(lines), key=lambda x: x[0])
        if events:
            error_events[container] = events

    if len(error_events) < 2:
        return []

    results = []

    for name_a, name_b in combinations(error_events.keys(), 2):
        events_a = error_events[name_a]
        events_b = error_events[name_b]

        matched_a, co_occurrences, example_pairs = _correlate_events(
            events_a, events_b, time_window_seconds
        )

        total_a = len(events_a)
        total_b = len(events_b)
        score = round(matched_a / total_a, 4) if total_a > 0 else 0.0

        results.append({
            "container_a": name_a,
            "container_b": name_b,
            "correlation_score": score,
            "co_occurrences": co_occurrences,
            "errors_a": total_a,
            "errors_b": total_b,
            "example_pairs": example_pairs,
        })

    return sorted(results, key=lambda x: x["correlation_score"], reverse=True)
