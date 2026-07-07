"""
Unit tests for cache_manager.py (SQLite log cache).

Tests cover:
- write_cached_logs_for_date() writes rows to logs + cache_days
- write_cached_logs_for_date() uses datetime.now(UTC) fallback for unparseable timestamps
- read_cached_logs_for_window() reads from SQLite and filters by time window
- Cache miss returns None (missing day, or empty range result within a covered day)
- Empty log list writes nothing
- get_cache_info() handles missing/corrupted cache
- clear_cache() removes rows
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

import docker_log_analyzer.cache_manager as cm


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp file for every test."""
    db_path = tmp_path / ".cache" / "logs.db"
    monkeypatch.setattr(cm, "DB_PATH", db_path)
    return db_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_log_line(ts: datetime, message: str) -> str:
    ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    return f"{ts_str} {message}"


def _utc(year, month, day, hour=0, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _rows(db_path, container=None):
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        if container:
            return conn.execute(
                "SELECT container, timestamp, message, cache_date FROM logs WHERE container = ? ORDER BY id",
                (container,),
            ).fetchall()
        return conn.execute(
            "SELECT container, timestamp, message, cache_date FROM logs ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _cache_days(db_path, container=None):
    conn = sqlite3.connect(db_path)
    try:
        if container:
            return conn.execute(
                "SELECT container, cache_date, synced_at, line_count FROM cache_days WHERE container = ?",
                (container,),
            ).fetchall()
        return conn.execute(
            "SELECT container, cache_date, synced_at, line_count FROM cache_days"
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# write_cached_logs_for_date
# ---------------------------------------------------------------------------

class TestWriteCachedLogsForDate:
    def test_creates_db_file(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(ts, "hello world")]
        cm.write_cached_logs_for_date("web-app", logs, ts.date())

        assert isolated_cache.exists()

    def test_writes_one_row_per_log_line(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(ts, "test message")]
        cm.write_cached_logs_for_date("web-app", logs, ts.date())

        rows = _rows(isolated_cache, "web-app")
        assert len(rows) == 1
        assert rows[0][0] == "web-app"
        assert rows[0][3] == "2026-03-06"

    def test_message_content_preserved(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        raw_line = _make_log_line(ts, "[INFO] service started")
        cm.write_cached_logs_for_date("web-app", [raw_line], ts.date())

        rows = _rows(isolated_cache, "web-app")
        assert rows[0][2] == raw_line

    def test_multiple_logs_written(self, isolated_cache):
        base = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(base + timedelta(seconds=i), f"msg {i}") for i in range(5)]
        cm.write_cached_logs_for_date("web-app", logs, base.date())

        rows = _rows(isolated_cache, "web-app")
        assert len(rows) == 5

    def test_empty_logs_writes_nothing(self, isolated_cache):
        from datetime import date
        cm.write_cached_logs_for_date("web-app", [], date(2026, 3, 6))

        # No DB file is even created — early-exit before any connection is opened.
        assert not isolated_cache.exists()

    def test_skips_blank_lines(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(ts, "real log"), "", "   "]
        cm.write_cached_logs_for_date("web-app", logs, ts.date())

        rows = _rows(isolated_cache, "web-app")
        assert len(rows) == 1

    def test_updates_cache_days(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(ts, "x")]
        cm.write_cached_logs_for_date("web-app", logs, ts.date())

        days = _cache_days(isolated_cache, "web-app")
        assert len(days) == 1
        assert days[0][1] == "2026-03-06"
        assert days[0][3] == 1  # line_count

    def test_rewriting_same_day_overwrites_not_duplicates(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        cm.write_cached_logs_for_date("web-app", [_make_log_line(ts, "first")], ts.date())
        cm.write_cached_logs_for_date("web-app", [_make_log_line(ts, "second")], ts.date())

        rows = _rows(isolated_cache, "web-app")
        assert len(rows) == 1
        assert rows[0][2] == _make_log_line(ts, "second")
        days = _cache_days(isolated_cache, "web-app")
        assert len(days) == 1

    def test_unparseable_timestamp_uses_utc_now_fallback(self, isolated_cache):
        """Lines with no leading ISO-8601 timestamp must not be dropped."""
        from datetime import date
        logs = ["no-timestamp-here just plain text"]
        cm.write_cached_logs_for_date("web-app", logs, date(2026, 3, 6))

        rows = _rows(isolated_cache, "web-app")
        assert len(rows) == 1
        assert rows[0][2] == "no-timestamp-here just plain text"
        assert rows[0][1] is not None  # timestamp fallback was assigned

    def test_explicit_offset_timestamp_is_parsed_and_written(self, isolated_cache):
        logs = ["2026-03-06T10:00:00+00:00 offset message"]
        cm.write_cached_logs_for_date("web-app", logs, _utc(2026, 3, 6, 10, 0, 0).date())

        rows = _rows(isolated_cache, "web-app")
        assert len(rows) == 1
        assert rows[0][2] == logs[0]
        assert rows[0][1] == datetime(2026, 3, 6, 10, 0, 0, tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# read_cached_logs_for_window
# ---------------------------------------------------------------------------

class TestReadCachedLogsForWindow:
    def test_returns_logs_within_window(self, isolated_cache):
        base = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(base + timedelta(minutes=i), f"msg {i}") for i in range(10)]
        cm.write_cached_logs_for_date("web-app", logs, base.date())

        result = cm.read_cached_logs_for_window(
            "web-app",
            since=base + timedelta(minutes=2),
            until=base + timedelta(minutes=5),
        )
        assert result is not None
        assert len(result) == 4  # minutes 2, 3, 4, 5

    def test_returns_none_on_missing_container(self, isolated_cache):
        result = cm.read_cached_logs_for_window(
            "missing-container",
            since=_utc(2026, 3, 6, 10),
            until=_utc(2026, 3, 6, 11),
        )
        assert result is None

    def test_returns_none_when_window_empty(self, isolated_cache):
        base = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(base, "only log")]
        cm.write_cached_logs_for_date("web-app", logs, base.date())

        result = cm.read_cached_logs_for_window(
            "web-app",
            since=_utc(2026, 3, 6, 12),
            until=_utc(2026, 3, 6, 13),
        )
        assert result is None

    def test_multi_day_window(self, isolated_cache):
        day1 = _utc(2026, 3, 5, 23, 55, 0)
        day2 = _utc(2026, 3, 6, 0, 5, 0)
        logs_day1 = [_make_log_line(day1 + timedelta(minutes=i), f"d1-{i}") for i in range(5)]
        logs_day2 = [_make_log_line(day2 + timedelta(minutes=i), f"d2-{i}") for i in range(5)]
        cm.write_cached_logs_for_date("web-app", logs_day1, day1.date())
        cm.write_cached_logs_for_date("web-app", logs_day2, day2.date())

        result = cm.read_cached_logs_for_window(
            "web-app",
            since=day1,
            until=day2 + timedelta(minutes=4),
        )
        assert result is not None
        assert len(result) == 10

    def test_missing_intermediate_day_returns_none(self, isolated_cache):
        day1 = _utc(2026, 3, 4, 10, 0, 0)
        day3 = _utc(2026, 3, 6, 10, 0, 0)
        cm.write_cached_logs_for_date("web-app", [_make_log_line(day1, "d1")], day1.date())
        cm.write_cached_logs_for_date("web-app", [_make_log_line(day3, "d3")], day3.date())

        # Day 2026-03-05 is missing — should trigger cache miss
        result = cm.read_cached_logs_for_window(
            "web-app",
            since=day1,
            until=day3,
        )
        assert result is None

    def test_naive_datetimes_treated_as_utc(self, isolated_cache):
        base = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(base, "msg")]
        cm.write_cached_logs_for_date("web-app", logs, base.date())

        # Pass naive datetimes
        naive_since = datetime(2026, 3, 6, 9, 0, 0)
        naive_until = datetime(2026, 3, 6, 11, 0, 0)
        result = cm.read_cached_logs_for_window("web-app", naive_since, naive_until)
        assert result is not None
        assert len(result) == 1

    def test_corrupt_db_file_returns_none(self, isolated_cache):
        """A corrupt .db file must return None without raising."""
        isolated_cache.parent.mkdir(parents=True, exist_ok=True)
        isolated_cache.write_bytes(b"this is not a valid sqlite file")

        result = cm.read_cached_logs_for_window(
            "web-app",
            since=_utc(2026, 3, 6, 9),
            until=_utc(2026, 3, 6, 11),
        )
        assert result is None

    def test_results_ordered_chronologically(self, isolated_cache):
        base = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(base + timedelta(minutes=i), f"msg {i}") for i in range(5)]
        cm.write_cached_logs_for_date("web-app", logs, base.date())

        result = cm.read_cached_logs_for_window(
            "web-app", since=base, until=base + timedelta(minutes=4)
        )
        assert result == logs


# ---------------------------------------------------------------------------
# get_cache_info / clear_cache
# ---------------------------------------------------------------------------

class TestGetCacheInfo:
    def test_returns_empty_containers_when_no_cache(self, isolated_cache):
        result = cm.get_cache_info("web-app")
        assert result["containers"] == []
        assert result["total_size_bytes"] == 0

    def test_returns_summary_after_write(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        cm.write_cached_logs_for_date("web-app", [_make_log_line(ts, "x")], ts.date())
        result = cm.get_cache_info("web-app")
        assert len(result["containers"]) == 1
        c = result["containers"][0]
        assert c["container"] == "web-app"
        assert "2026-03-06" in c["dates_cached"]
        assert c["cached_days"] == 1
        assert c["total_lines"] == 1

    def test_returns_empty_for_unknown_container(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        cm.write_cached_logs_for_date("web-app", [_make_log_line(ts, "x")], ts.date())
        result = cm.get_cache_info("other-container")
        assert result["containers"] == []

    def test_all_containers_when_none_given(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        cm.write_cached_logs_for_date("web-app", [_make_log_line(ts, "x")], ts.date())
        cm.write_cached_logs_for_date("db", [_make_log_line(ts, "y")], ts.date())

        result = cm.get_cache_info()
        names = {c["container"] for c in result["containers"]}
        assert names == {"web-app", "db"}


class TestClearCache:
    def test_clear_specific_container(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        cm.write_cached_logs_for_date("web-app", [_make_log_line(ts, "x")], ts.date())
        cm.write_cached_logs_for_date("db", [_make_log_line(ts, "y")], ts.date())

        cm.clear_cache("web-app")

        assert _rows(isolated_cache, "web-app") == []
        assert len(_rows(isolated_cache, "db")) == 1

    def test_clear_all(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        cm.write_cached_logs_for_date("web-app", [_make_log_line(ts, "x")], ts.date())
        result = cm.clear_cache()
        assert result["cleared_containers"] == ["web-app"]
        assert _rows(isolated_cache) == []

    def test_clear_unknown_container_is_noop(self, isolated_cache):
        result = cm.clear_cache("nope")
        assert result["cleared_containers"] == []
        assert result["bytes_freed"] == 0
