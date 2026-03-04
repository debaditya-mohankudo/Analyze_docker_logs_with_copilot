"""
Time-window based log cache manager with atomic writes.

All tools use cache-first pattern:
1. Check .cache/logs/<container>/<YYYY-MM-DD>.jsonl
2. If available and covers time window, use cached logs
3. Otherwise, fetch fresh from Docker API

Cache structure:
.cache/logs/
  ├── metadata.json
  ├── web-app/
  │   ├── 2026-03-04.jsonl
  │   ├── 2026-03-03.jsonl
  │   └── 2026-03-02.jsonl
  └── database/
      └── 2026-03-04.jsonl
"""

import json
import tempfile
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional, List

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
        # Take first part (timestamp) before space
        ts_str = log_line.split()[0]
        # Parse ISO-8601 with 'Z' suffix
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except (IndexError, ValueError):
        return None


def _atomic_write(file_path: Path, content: str) -> None:
    """Write to file atomically using temp file + rename."""
    _ensure_cache_dir()
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file first
    with tempfile.NamedTemporaryFile(
        mode='w',
        dir=file_path.parent,
        prefix='.tmp-',
        suffix='.jsonl',
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    # Atomic rename
    Path(tmp_path).replace(file_path)
    logger.debug(f"Atomic write: {file_path}")


def read_cached_logs_for_window(
    container_name: str,
    since: datetime,
    until: datetime,
) -> Optional[List[str]]:
    """
    Read cached logs for a specific time window.

    Queries across multiple daily files if window spans days.
    Returns None if any part of window is missing (fallback to Docker API).

    Args:
        container_name: Container name (e.g., "web-app")
        since: Start of time window (datetime)
        until: End of time window (datetime)

    Returns:
        List of log lines, or None if cache miss/incomplete
    """
    logs = []
    current_date = since.date()

    try:
        while current_date <= until.date():
            cache_file = CACHE_DIR / container_name / f"{current_date}.jsonl"

            if not cache_file.exists():
                logger.debug(f"Cache miss: {cache_file} (missing)")
                return None  # Missing data, fetch fresh

            # Read file and filter by timestamp range
            try:
                with open(cache_file) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            log_obj = json.loads(line)
                            ts_str = log_obj.get("timestamp")
                            if ts_str:
                                # Parse ISO-8601 timestamp
                                if ts_str.endswith("Z"):
                                    ts_str = ts_str[:-1] + "+00:00"
                                ts = datetime.fromisoformat(ts_str)

                                if since <= ts <= until:
                                    logs.append(line.strip())
                        except (json.JSONDecodeError, ValueError):
                            # Skip malformed lines
                            continue
            except IOError as e:
                logger.warning(f"Error reading cache file {cache_file}: {e}")
                return None

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


def write_cached_logs_for_date(
    container_name: str,
    logs: List[str],
    date_val: date,
) -> None:
    """
    Write logs for a specific date to cache (atomic).

    Each log line is stored as JSON: {"timestamp": "...", "message": "..."}

    Args:
        container_name: Container name (e.g., "web-app")
        logs: List of log lines (Docker format: "TIMESTAMP MESSAGE")
        date_val: Date for this batch (e.g., 2026-03-04)
    """
    _ensure_cache_dir()
    container_dir = CACHE_DIR / container_name
    container_dir.mkdir(parents=True, exist_ok=True)

    cache_file = container_dir / f"{date_val}.jsonl"

    # Convert Docker log lines to JSON format
    json_lines = []
    for line in logs:
        if not line.strip():
            continue

        ts = _parse_timestamp(line)
        if ts:
            ts_iso = ts.isoformat()
        else:
            ts_iso = datetime.now(timezone.utc).isoformat()

        log_obj = {
            "timestamp": ts_iso,
            "message": line,
        }
        json_lines.append(json.dumps(log_obj))

    if json_lines:
        # Atomic write
        _atomic_write(cache_file, '\n'.join(json_lines) + '\n')

        # Update metadata
        _update_metadata(container_name, date_val, len(json_lines))

        logger.info(f"Cached {len(json_lines)} logs for {container_name} ({date_val})")


def _update_metadata(container_name: str, date_val: date, line_count: int) -> None:
    """Update metadata.json with cache info (atomic)."""
    _ensure_cache_dir()

    # Read existing metadata
    if METADATA_FILE.exists():
        try:
            with open(METADATA_FILE) as f:
                metadata = json.load(f)
        except (json.JSONDecodeError, IOError):
            metadata = {}
    else:
        metadata = {}

    # Update entry
    if container_name not in metadata:
        metadata[container_name] = {}

    metadata[container_name][str(date_val)] = {
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "line_count": line_count,
    }

    # Atomic write
    _atomic_write(METADATA_FILE, json.dumps(metadata, indent=2) + '\n')


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
