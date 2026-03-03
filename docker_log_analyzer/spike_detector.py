"""
spike_detector.py – Rolling-window error spike detection using Polars.

Algorithm:
  1. Parse Docker-prepended RFC3339 timestamps from log lines
  2. Build a Polars DataFrame of (minute_bucket, is_error) rows
  3. Aggregate error count per 1-minute bucket
  4. Compute rolling baseline = mean of previous BASELINE_BUCKETS buckets
  5. Flag any bucket where error_count > baseline × spike_threshold

All analysis is stateless and local – no external API calls.
"""

import re
from datetime import datetime, timezone
from typing import List, Optional

import polars as pl

# Docker SDK prepends RFC3339 timestamps when timestamps=True is used.
# Example line: "2024-03-02T21:19:41.123456789Z [app] ERROR connection failed"
DOCKER_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)"
)

ERROR_PATTERN_RE = re.compile(
    r"\b(ERROR|CRITICAL|FATAL|Exception|Traceback|panic:|SEVERE)\b"
    r"|HTTP [5]\d{2}",
    re.IGNORECASE,
)

BASELINE_BUCKETS = 3  # rolling look-back window


def _parse_docker_timestamp(line: str) -> Optional[str]:
    """
    Extract the minute-bucket string (YYYY-MM-DDTHH:MM) from a Docker log line.
    Returns None if no RFC3339 timestamp is found.
    """
    m = DOCKER_TS_RE.match(line.strip())
    if not m:
        return None
    ts_str = m.group(1).rstrip("Z")
    try:
        dt = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


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
        window_minutes: Unused internally but kept for API consistency.
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
    rows = []
    for line in log_lines:
        bucket = _parse_docker_timestamp(line)
        if bucket is None:
            continue
        rows.append({
            "minute_bucket": bucket,
            "is_error": bool(ERROR_PATTERN_RE.search(line)),
        })

    if not rows:
        return []

    df = pl.DataFrame(rows)

    error_only = df.filter(pl.col("is_error"))
    if error_only.is_empty():
        return []

    bucket_counts = (
        error_only
        .group_by("minute_bucket")
        .agg(pl.len().alias("error_count"))
        .sort("minute_bucket")
    )

    if bucket_counts.height < 2:
        return []

    error_series = bucket_counts["error_count"].cast(pl.Float64)
    baseline = (
        error_series
        .shift(1)
        .rolling_mean(window_size=BASELINE_BUCKETS, min_samples=1)
    )

    bucket_counts = bucket_counts.with_columns([
        baseline.fill_null(1.0).alias("baseline"),   # first bucket has no history → use 1.0
    ]).with_columns([
        (pl.col("error_count").cast(pl.Float64) / pl.col("baseline")).alias("ratio"),
    ])

    spike_rows = bucket_counts.filter(pl.col("ratio") > spike_threshold)

    return [
        {
            "container": container_name,
            "bucket_minute": row["minute_bucket"],
            "error_count": int(row["error_count"]),
            "baseline": round(float(row["baseline"]), 2),
            "ratio": round(float(row["ratio"]), 2),
        }
        for row in spike_rows.iter_rows(named=True)
    ]
