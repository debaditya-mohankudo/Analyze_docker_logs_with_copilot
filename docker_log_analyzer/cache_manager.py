"""
Time-window based log cache manager with atomic writes.

All tools use cache-first pattern:
1. Check .cache/logs/<container>/<YYYY-MM-DD>.parquet
2. If available and covers time window, use cached logs
3. Otherwise, fetch fresh from Docker API

Cache structure:
.cache/logs/
  ├── metadata.json
  ├── web-app/
  │   ├── 2026-03-04.parquet
  │   ├── 2026-03-03.parquet
  │   └── 2026-03-02.parquet
  └── database/
      └── 2026-03-04.parquet
"""

import json
import tempfile
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional, List

import polars as pl

from .logger import logger

CACHE_DIR = Path(".cache/logs")
METADATA_FILE = CACHE_DIR / "metadata.json"


def _ensure_cache_dir() -> None:
    """Create cache directory if it doesn't exist."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _parse_timestamp(log_line: str) -> Optional[datetime]:
    """Extract ISO-8601 timestamp from Docker log line."""
    # Docker logs format: TIMESTAMP MESSAGE
    # e.g., "2026-03-04T10:30:45.123456789Z [INFO] ..."
    try:
        ts_str = log_line.split()[0]
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except (IndexError, ValueError):
        return None


def _atomic_write(file_path: Path, content: str) -> None:
    """Write text to file atomically using temp file + rename."""
    _ensure_cache_dir()
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=file_path.parent,
        prefix=".tmp-",
        suffix=".json",
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    Path(tmp_path).replace(file_path)
    logger.debug(f"Atomic write: {file_path}")


def _atomic_write_parquet(file_path: Path, df: pl.DataFrame) -> None:
    """Write a Polars DataFrame to a Parquet file atomically (temp + rename).

    Uses zstd compression. On failure the temp file is cleaned up.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.parent / f".tmp-{file_path.name}"
    try:
        df.write_parquet(tmp_path, compression="zstd")
        tmp_path.replace(file_path)
        logger.debug(f"Atomic write (parquet): {file_path}")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def read_cached_logs_for_window(
    container_name: str,
    since: datetime,
    until: datetime,
) -> Optional[List[str]]:
    """
    Read cached logs for a specific time window.

    Queries across multiple daily .parquet files if window spans days.
    Returns None if any part of window is missing (fallback to Docker API).

    Args:
        container_name: Container name (e.g., "web-app")
        since: Start of time window (UTC-aware datetime)
        until: End of time window (UTC-aware datetime)

    Returns:
        List of log lines (raw Docker format), or None on cache miss/incomplete.
    """
    logs: List[str] = []
    current_date = since.date()

    # Ensure timezone-aware for Polars comparisons
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)

    try:
        while current_date <= until.date():
            parquet_file = CACHE_DIR / container_name / f"{current_date}.parquet"

            if not parquet_file.exists():
                logger.debug(f"Cache miss: no file for {container_name} on {current_date}")
                return None  # Missing data — fetch fresh from Docker

            result = _read_parquet_file(parquet_file, since, until)
            if result is None:
                return None  # Read error
            logs.extend(result)
            current_date += timedelta(days=1)

        if logs:
            logger.debug(f"Cache hit: {container_name} ({len(logs)} lines)")
            return logs
        else:
            logger.debug(f"Cache miss: {container_name} (no logs in window)")
            return None

    except Exception as e:
        logger.error(f"Cache read error for {container_name}: {e}")
        return None


def _read_parquet_file(
    cache_file: Path,
    since: datetime,
    until: datetime,
) -> Optional[List[str]]:
    """Read and filter a Parquet cache file by timestamp range."""
    try:
        df = pl.read_parquet(cache_file, columns=["timestamp", "message"])
        filtered = df.filter(
            (pl.col("timestamp") >= since) & (pl.col("timestamp") <= until)
        )
        return filtered["message"].to_list()
    except Exception as e:
        logger.warning(f"Error reading parquet cache {cache_file}: {e}")
        return None



def write_cached_logs_for_date(
    container_name: str,
    logs: List[str],
    date_val: date,
) -> None:
    """
    Write logs for a specific date to cache as Parquet (atomic).

    Schema: timestamp (Datetime[us, UTC]), message (String)

    Args:
        container_name: Container name (e.g., "web-app")
        logs: List of log lines (Docker format: "TIMESTAMP MESSAGE")
        date_val: Date for this batch (e.g., 2026-03-04)
    """
    _ensure_cache_dir()
    container_dir = CACHE_DIR / container_name
    container_dir.mkdir(parents=True, exist_ok=True)

    cache_file = container_dir / f"{date_val}.parquet"

    timestamps: List[datetime] = []
    messages: List[str] = []

    for line in logs:
        if not line.strip():
            continue
        ts = _parse_timestamp(line)
        if ts is None:
            ts = datetime.now(timezone.utc)
        elif ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        timestamps.append(ts)
        messages.append(line)

    if not timestamps:
        return

    df = pl.DataFrame({"timestamp": timestamps, "message": messages}).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )

    _atomic_write_parquet(cache_file, df)
    _update_metadata(container_name, date_val, len(timestamps))
    logger.info(f"Cached {len(timestamps)} logs for {container_name} ({date_val})")


def _update_metadata(container_name: str, date_val: date, line_count: int) -> None:
    """Update metadata.json with cache info (atomic)."""
    _ensure_cache_dir()

    if METADATA_FILE.exists():
        try:
            with open(METADATA_FILE) as f:
                metadata = json.load(f)
        except (json.JSONDecodeError, IOError):
            metadata = {}
    else:
        metadata = {}

    if container_name not in metadata:
        metadata[container_name] = {}

    metadata[container_name][str(date_val)] = {
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "line_count": line_count,
    }

    _atomic_write(METADATA_FILE, json.dumps(metadata, indent=2) + "\n")


def get_cache_info(container_name: str) -> Optional[dict]:
    """Get cache metadata for a container."""
    if not METADATA_FILE.exists():
        return None

    try:
        with open(METADATA_FILE) as f:
            metadata = json.load(f)
        return metadata.get(container_name)
    except (json.JSONDecodeError, IOError):
        return None


def clear_cache(container_name: Optional[str] = None) -> None:
    """
    Clear cache (all containers or specific container).

    Args:
        container_name: Clear specific container, or None for all
    """
    if container_name:
        cache_dir = CACHE_DIR / container_name
        if cache_dir.exists():
            import shutil
            shutil.rmtree(cache_dir)
            logger.info(f"Cleared cache for {container_name}")
    else:
        import shutil
        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR)
            logger.info("Cleared all cache")
