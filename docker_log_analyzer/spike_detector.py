"""
spike_detector.py – Rolling-window error spike detection (plain Python).

Algorithm:
  1. Parse Docker-prepended RFC3339 timestamps from log lines
  2. Count errors per 1-minute bucket (buckets with zero errors are absent,
     not zero-filled — the rolling window below operates over the sequence
     of error-containing buckets, not literal calendar minutes)
  3. Compute rolling baseline = mean of the previous `window_minutes` buckets
  4. Flag any bucket where error_count > baseline × spike_threshold

All analysis is stateless and local – no external API calls. This operates
on an already in-memory list of log lines (not the SQLite log cache), so
there is no persistent store to query — a plain rolling window over a
sorted dict is sufficient at dev-scale line counts; no Polars/SQL needed.
"""

from collections import Counter, deque
from typing import List, Optional

# DOCKER_TS_RE is unused directly in this module — it's re-exported here so
# tests/test_spike_detector.py can import it alongside detect_spikes without
# reaching into patterns.py separately (existing convention, predates this file).
from .patterns import DOCKER_TS_RE, ERROR_PATTERN_RE, parse_timestamp  # noqa: F401


def _parse_docker_timestamp(line: str) -> Optional[str]:
    """
    Extract the minute-bucket string (YYYY-MM-DDTHH:MM) from a Docker log line.
    Returns None if no RFC3339 timestamp is found.
    """
    dt = parse_timestamp(line)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M")


def detect_spikes(
    log_lines: List[str],
    container_name: str,
    window_minutes: int = 5,
    spike_threshold: float = 2.0,
) -> List[dict]:
    """
    Detect error rate spikes in the given log lines.

    Args:
        log_lines: Raw log lines with Docker-prepended timestamps.
        container_name: Container label for spike event output.
        window_minutes: Rolling baseline look-back window, in number of prior
            error-containing buckets (not literal calendar minutes).
        spike_threshold: Ratio (current / baseline) that constitutes a spike.

    Returns:
        List of spike-event dicts:
        {
            "container": str,
            "bucket_minute": str,   # e.g. "2024-03-02T21:19"
            "error_count": int,
            "baseline": float,
            "ratio": float,
        }
        Empty list if there are no parseable timestamps or fewer than 2 buckets.
    """
    error_counts: Counter[str] = Counter()
    saw_parseable_line = False

    for line in log_lines:
        bucket = _parse_docker_timestamp(line)
        if bucket is None:
            continue
        saw_parseable_line = True
        if ERROR_PATTERN_RE.search(line):
            error_counts[bucket] += 1

    if not saw_parseable_line or not error_counts:
        return []

    buckets = sorted(error_counts.keys())
    if len(buckets) < 2:
        return []

    window_size = max(1, window_minutes)
    history: deque[int] = deque(maxlen=window_size)
    history_sum = 0  # kept in sync with history — avoids O(window_size) sum() per bucket
    spikes: List[dict] = []

    for bucket in buckets:
        error_count = error_counts[bucket]
        baseline = (history_sum / len(history)) if history else 1.0  # no history → 1.0
        ratio = error_count / baseline

        if ratio > spike_threshold:
            spikes.append({
                "container": container_name,
                "bucket_minute": bucket,
                "error_count": error_count,
                "baseline": round(baseline, 2),
                "ratio": round(ratio, 2),
            })

        if len(history) == window_size:
            history_sum -= history[0]  # about to be evicted by the deque's maxlen
        history_sum += error_count
        history.append(error_count)

    return spikes
