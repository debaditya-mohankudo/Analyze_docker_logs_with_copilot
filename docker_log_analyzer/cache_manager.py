"""
Time-window based log cache manager (SQLite).

All tools use cache-first pattern:
1. Check .cache/logs.db for full day-coverage across the requested time window
2. If every day in the window is covered, serve logs from SQLite
3. Otherwise, fetch fresh from Docker API

Cache structure:
.cache/logs.db
  logs table:        (id, container, timestamp, message, cache_date)
  cache_days table:  (container, cache_date, synced_at, line_count)

cache_days is the single source of truth for "is this day cached" — it is
written together with logs in one transaction, and is checked independently
of whether logs actually has matching rows for a given sub-window (see the
empty-window-returns-None behavior in read_cached_logs_for_window).
"""

import sqlite3
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional, List

from .logger import logger
from .patterns import parse_timestamp

DB_PATH = Path(".cache") / "logs.db"


def _parse_timestamp(log_line: str) -> Optional[datetime]:
    """Extract a Docker-prepended timestamp using the shared parser."""
    return parse_timestamp(log_line)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            container  TEXT NOT NULL,
            timestamp  REAL NOT NULL,
            message    TEXT NOT NULL,
            cache_date TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_logs_container_ts   ON logs(container, timestamp);
        CREATE INDEX IF NOT EXISTS idx_logs_container_date ON logs(container, cache_date);

        CREATE TABLE IF NOT EXISTS cache_days (
            container  TEXT NOT NULL,
            cache_date TEXT NOT NULL,
            synced_at  TEXT NOT NULL,
            line_count INTEGER NOT NULL,
            PRIMARY KEY (container, cache_date)
        );
        """
    )


def _connect() -> sqlite3.Connection:
    """Open a short-lived connection with WAL mode and schema ensured.

    Stateless per-call, matching every other tool in this project — no
    module-level global connection or pooling.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _ensure_schema(conn)
    return conn


def read_cached_logs_for_window(
    container_name: str,
    since: datetime,
    until: datetime,
) -> Optional[List[str]]:
    """
    Read cached logs for a specific time window.

    Checks day-coverage (cache_days) for every day the window spans, then
    runs a single range query across all of them. Returns None if any day
    is missing, or if the range query yields zero rows even though every
    day is covered — this mirrors the exact cache-hit contract the Parquet
    implementation had (see tests/test_cache_manager.py), not a new rule.

    Args:
        container_name: Container name (e.g., "web-app")
        since: Start of time window (UTC-aware datetime)
        until: End of time window (UTC-aware datetime)

    Returns:
        List of log lines (raw Docker format), or None on cache miss/incomplete.
    """
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)

    conn = None
    try:
        conn = _connect()

        current_date = since.date()
        while current_date <= until.date():
            row = conn.execute(
                "SELECT 1 FROM cache_days WHERE container = ? AND cache_date = ?",
                (container_name, str(current_date)),
            ).fetchone()
            if row is None:
                logger.debug(f"Cache miss: no day coverage for {container_name} on {current_date}")
                return None
            current_date += timedelta(days=1)

        rows = conn.execute(
            "SELECT message FROM logs WHERE container = ? AND timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp ASC, id ASC",
            (container_name, since.timestamp(), until.timestamp()),
        ).fetchall()

        messages = [r[0] for r in rows]
        if messages:
            logger.debug(f"Cache hit: {container_name} ({len(messages)} lines)")
            return messages
        else:
            logger.debug(f"Cache miss: {container_name} (no logs in window)")
            return None

    except Exception as e:
        logger.error(f"Cache read error for {container_name}: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


def write_cached_logs_for_date(
    container_name: str,
    logs: List[str],
    date_val: date,
) -> None:
    """
    Write logs for a specific date to cache (SQLite, atomic transaction).

    Args:
        container_name: Container name (e.g., "web-app")
        logs: List of log lines (Docker format: "TIMESTAMP MESSAGE")
        date_val: Date for this batch (e.g., 2026-03-04)
    """
    timestamps: List[float] = []
    messages: List[str] = []

    for line in logs:
        if not line.strip():
            continue
        ts = _parse_timestamp(line)
        if ts is None:
            ts = datetime.now(timezone.utc)
        elif ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        timestamps.append(ts.timestamp())
        messages.append(line)

    if not timestamps:
        return

    cache_date_str = str(date_val)
    synced_at = datetime.now(timezone.utc).isoformat()

    conn = _connect()
    try:
        conn.execute(
            "DELETE FROM logs WHERE container = ? AND cache_date = ?",
            (container_name, cache_date_str),
        )
        conn.executemany(
            "INSERT INTO logs (container, timestamp, message, cache_date) VALUES (?, ?, ?, ?)",
            [(container_name, ts, msg, cache_date_str) for ts, msg in zip(timestamps, messages)],
        )
        conn.execute(
            """
            INSERT INTO cache_days (container, cache_date, synced_at, line_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(container, cache_date) DO UPDATE SET
                synced_at = excluded.synced_at,
                line_count = excluded.line_count
            """,
            (container_name, cache_date_str, synced_at, len(timestamps)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(f"Cached {len(timestamps)} logs for {container_name} ({date_val})")


def _known_containers(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute("SELECT DISTINCT container FROM cache_days ORDER BY container").fetchall()
    return [r[0] for r in rows]


def _container_known(conn: sqlite3.Connection, container_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM cache_days WHERE container = ? LIMIT 1", (container_name,)
    ).fetchone()
    return row is not None


def _container_cache_summary(conn: sqlite3.Connection, container_name: str) -> dict:
    """Build a per-container summary dict from cache_days + logs."""
    days = conn.execute(
        "SELECT cache_date, synced_at, line_count FROM cache_days WHERE container = ? ORDER BY cache_date",
        (container_name,),
    ).fetchall()
    dates = [d[0] for d in days]
    total_lines = sum(d[2] for d in days)
    synced_ats = [d[1] for d in days if d[1]]
    last_synced = max(synced_ats) if synced_ats else None

    size_row = conn.execute(
        "SELECT COALESCE(SUM(LENGTH(message)), 0) FROM logs WHERE container = ?",
        (container_name,),
    ).fetchone()
    size_bytes = size_row[0] if size_row else 0

    return {
        "container": container_name,
        # Count of cached days — the SQLite equivalent of the old "one parquet
        # file per day" count. Not a real file count anymore (one shared .db).
        "cached_days": len(dates),
        "dates_cached": dates,
        "total_lines": total_lines,
        # Content-byte estimate (SUM of message lengths), not actual on-disk
        # size — there's no longer a per-container file to stat. See
        # get_cache_info()'s docstring for the aggregate (real) figure.
        "size_bytes": size_bytes,
        "size_kb": round(size_bytes / 1024, 1),
        "last_synced": last_synced,
    }


def get_cache_info(container_name: Optional[str] = None) -> dict:
    """
    Return cache summary for one container or all containers.

    Args:
        container_name: Specific container name, or None for all containers.

    Returns:
        Dict with 'containers' list and 'total_size_bytes' summary.
        NOTE: per-container 'size_bytes' is a content-byte estimate (SUM of
        message lengths) since all containers now share one .cache/logs.db
        file — there's no longer a per-container file to stat. 'total_size_bytes'
        at the top level is the same content-byte estimate summed across
        containers, not the real .db file size on disk (which includes
        SQLite page overhead and indexes).
    """
    conn = _connect()
    try:
        if container_name:
            if not _container_known(conn, container_name):
                return {"containers": [], "total_size_bytes": 0, "total_size_kb": 0.0}
            summaries = [_container_cache_summary(conn, container_name)]
        else:
            summaries = [_container_cache_summary(conn, c) for c in _known_containers(conn)]
    finally:
        conn.close()

    total_bytes = sum(s["size_bytes"] for s in summaries)
    return {
        "containers": summaries,
        "total_size_bytes": total_bytes,
        "total_size_kb": round(total_bytes / 1024, 1),
    }


def clear_cache(container_name: Optional[str] = None) -> dict:
    """
    Clear log cache for one container or all containers.

    Args:
        container_name: Clear specific container, or None for all.

    Returns:
        Dict with cleared containers and bytes freed (content-byte estimate
        for a single-container clear — SQLite does not shrink the .db file
        on DELETE; real disk space is only reclaimed via VACUUM, which this
        function only runs on a full clear, not a per-container one, to
        avoid an expensive full-file rewrite on every call).
    """
    conn = _connect()
    try:
        if container_name:
            size_row = conn.execute(
                "SELECT COALESCE(SUM(LENGTH(message)), 0) FROM logs WHERE container = ?",
                (container_name,),
            ).fetchone()
            bytes_freed = size_row[0] if size_row else 0
            existed = _container_known(conn, container_name)

            cleared: List[str] = []
            if existed:
                conn.execute("DELETE FROM logs WHERE container = ?", (container_name,))
                conn.execute("DELETE FROM cache_days WHERE container = ?", (container_name,))
                conn.commit()
                cleared = [container_name]
                logger.info(f"Cleared cache for {container_name} ({bytes_freed} bytes)")
        else:
            size_row = conn.execute("SELECT COALESCE(SUM(LENGTH(message)), 0) FROM logs").fetchone()
            bytes_freed = size_row[0] if size_row else 0
            cleared = _known_containers(conn)

            conn.execute("DELETE FROM logs")
            conn.execute("DELETE FROM cache_days")
            conn.commit()
            conn.execute("VACUUM")  # full clear — safe & cheap to reclaim real disk space here
            logger.info(f"Cleared all cache ({bytes_freed} bytes)")
    finally:
        conn.close()

    return {
        "cleared_containers": cleared,
        "bytes_freed": bytes_freed,
        "kb_freed": round(bytes_freed / 1024, 1),
    }
