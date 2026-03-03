"""
correlator.py – Cross-container temporal error correlation.

Algorithm:
  1. Extract (unix_ts, line) tuples for error lines per container.
  2. For each ordered pair (A, B), count errors in B within ±time_window_seconds
     of each error in A (co-occurrence).
  3. correlation_score = co_occurrences / max(errors_A, errors_B)  (0–1, Jaccard-style).
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

# Cap inner-loop iterations per container pair to avoid O(n²) blow-up
MAX_CO_OCCURRENCES = 500


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

        # Count A errors that have at least one B error within the time window.
        # score = matched_a / total_a  →  proper 0-1 range.
        matched_a = 0
        co_occurrences = 0
        example_pairs: List[dict] = []
        checked = 0

        for ts_a, line_a in events_a:
            found = False
            for ts_b, line_b in events_b:
                checked += 1
                if checked > MAX_CO_OCCURRENCES:
                    break
                delta = abs(ts_a - ts_b)
                if delta <= time_window_seconds:
                    co_occurrences += 1
                    if not found:
                        matched_a += 1   # count this A error only once
                        found = True
                    if len(example_pairs) < 3:
                        example_pairs.append({
                            "a": line_a[:120],
                            "b": line_b[:120],
                            "delta_seconds": round(delta, 2),
                        })
            if checked > MAX_CO_OCCURRENCES:
                break

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
