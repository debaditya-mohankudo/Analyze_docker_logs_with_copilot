"""
Unit tests for cache_manager.py (Parquet log cache).

Tests cover:
- write_cached_logs_for_date() produces .parquet with correct schema
- read_cached_logs_for_window() reads parquet and filters by time window
- _read_jsonl_file() fallback for legacy .jsonl files
- Cache miss returns None
- Empty log list produces no file
- _update_metadata() writes metadata.json
- clear_cache() removes files
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import polars as pl
import pytest

import docker_log_analyzer.cache_manager as cm


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Redirect CACHE_DIR and METADATA_FILE to a temp directory for every test."""
    cache_dir = tmp_path / ".cache" / "logs"
    monkeypatch.setattr(cm, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(cm, "METADATA_FILE", cache_dir / "metadata.json")
    return cache_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_log_line(ts: datetime, message: str) -> str:
    ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    return f"{ts_str} {message}"


def _utc(year, month, day, hour=0, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# write_cached_logs_for_date
# ---------------------------------------------------------------------------

class TestWriteCachedLogsForDate:
    def test_creates_parquet_file(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(ts, "hello world")]
        cm.write_cached_logs_for_date("web-app", logs, ts.date())

        parquet_file = isolated_cache / "web-app" / "2026-03-06.parquet"
        assert parquet_file.exists()

    def test_no_jsonl_file_created(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(ts, "hello world")]
        cm.write_cached_logs_for_date("web-app", logs, ts.date())

        jsonl_file = isolated_cache / "web-app" / "2026-03-06.jsonl"
        assert not jsonl_file.exists()

    def test_parquet_schema(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(ts, "test message")]
        cm.write_cached_logs_for_date("web-app", logs, ts.date())

        df = pl.read_parquet(isolated_cache / "web-app" / "2026-03-06.parquet")
        assert "timestamp" in df.columns
        assert "message" in df.columns
        assert df["timestamp"].dtype == pl.Datetime("us", "UTC")
        assert df["message"].dtype == pl.String

    def test_message_content_preserved(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        raw_line = _make_log_line(ts, "[INFO] service started")
        cm.write_cached_logs_for_date("web-app", [raw_line], ts.date())

        df = pl.read_parquet(isolated_cache / "web-app" / "2026-03-06.parquet")
        assert df["message"][0] == raw_line

    def test_multiple_logs_written(self, isolated_cache):
        base = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(base + timedelta(seconds=i), f"msg {i}") for i in range(5)]
        cm.write_cached_logs_for_date("web-app", logs, base.date())

        df = pl.read_parquet(isolated_cache / "web-app" / "2026-03-06.parquet")
        assert len(df) == 5

    def test_empty_logs_produces_no_file(self, isolated_cache):
        from datetime import date
        cm.write_cached_logs_for_date("web-app", [], date(2026, 3, 6))

        parquet_file = isolated_cache / "web-app" / "2026-03-06.parquet"
        assert not parquet_file.exists()

    def test_skips_blank_lines(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(ts, "real log"), "", "   "]
        cm.write_cached_logs_for_date("web-app", logs, ts.date())

        df = pl.read_parquet(isolated_cache / "web-app" / "2026-03-06.parquet")
        assert len(df) == 1

    def test_updates_metadata(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        logs = [_make_log_line(ts, "x")]
        cm.write_cached_logs_for_date("web-app", logs, ts.date())

        with open(isolated_cache / "metadata.json") as f:
            meta = json.load(f)
        assert "web-app" in meta
        assert "2026-03-06" in meta["web-app"]
        assert meta["web-app"]["2026-03-06"]["line_count"] == 1


# ---------------------------------------------------------------------------
# read_cached_logs_for_window (parquet path)
# ---------------------------------------------------------------------------

class TestReadCachedLogsForWindowParquet:
    def _write(self, container, logs, isolated_cache):
        if not logs:
            return
        ts = _parse_ts(logs[0])
        cm.write_cached_logs_for_date(container, logs, ts.date())

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

    def test_returns_none_on_missing_file(self, isolated_cache):
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


# ---------------------------------------------------------------------------
# _read_jsonl_file (legacy fallback)
# ---------------------------------------------------------------------------

class TestReadJsonlFallback:
    def _write_jsonl(self, path: Path, entries: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_falls_back_to_jsonl_when_no_parquet(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        raw = _make_log_line(ts, "legacy log")
        jsonl_path = isolated_cache / "web-app" / "2026-03-06.jsonl"
        self._write_jsonl(jsonl_path, [{"timestamp": ts.isoformat(), "message": raw}])

        result = cm.read_cached_logs_for_window(
            "web-app",
            since=_utc(2026, 3, 6, 9),
            until=_utc(2026, 3, 6, 11),
        )
        assert result == [raw]

    def test_parquet_takes_priority_over_jsonl(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        parquet_log = _make_log_line(ts, "from parquet")
        jsonl_log = _make_log_line(ts, "from jsonl")

        cm.write_cached_logs_for_date("web-app", [parquet_log], ts.date())

        jsonl_path = isolated_cache / "web-app" / "2026-03-06.jsonl"
        self._write_jsonl(jsonl_path, [{"timestamp": ts.isoformat(), "message": jsonl_log}])

        result = cm.read_cached_logs_for_window(
            "web-app",
            since=_utc(2026, 3, 6, 9),
            until=_utc(2026, 3, 6, 11),
        )
        assert result == [parquet_log]

    def test_jsonl_filters_by_timestamp(self, isolated_cache):
        base = _utc(2026, 3, 6, 10, 0, 0)
        entries = [
            {"timestamp": (base + timedelta(minutes=i)).isoformat(), "message": _make_log_line(base + timedelta(minutes=i), f"m{i}")}
            for i in range(10)
        ]
        jsonl_path = isolated_cache / "web-app" / "2026-03-06.jsonl"
        self._write_jsonl(jsonl_path, entries)

        result = cm.read_cached_logs_for_window(
            "web-app",
            since=base + timedelta(minutes=3),
            until=base + timedelta(minutes=6),
        )
        assert result is not None
        assert len(result) == 4  # minutes 3, 4, 5, 6


# ---------------------------------------------------------------------------
# _atomic_write_parquet
# ---------------------------------------------------------------------------

class TestAtomicWriteParquet:
    def test_writes_and_renames(self, tmp_path):
        df = pl.DataFrame({"a": [1, 2, 3]})
        dest = tmp_path / "output.parquet"
        cm._atomic_write_parquet(dest, df)

        assert dest.exists()
        assert not (tmp_path / ".tmp-output.parquet").exists()

    def test_no_tmp_file_left_on_success(self, tmp_path):
        df = pl.DataFrame({"x": ["a", "b"]})
        dest = tmp_path / "sub" / "out.parquet"
        cm._atomic_write_parquet(dest, df)

        tmp_files = list((tmp_path / "sub").glob(".tmp-*"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# get_cache_info / clear_cache
# ---------------------------------------------------------------------------

class TestGetCacheInfo:
    def test_returns_none_when_no_metadata(self, isolated_cache):
        assert cm.get_cache_info("web-app") is None

    def test_returns_metadata_after_write(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        cm.write_cached_logs_for_date("web-app", [_make_log_line(ts, "x")], ts.date())
        info = cm.get_cache_info("web-app")
        assert info is not None
        assert "2026-03-06" in info


class TestClearCache:
    def test_clear_specific_container(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        cm.write_cached_logs_for_date("web-app", [_make_log_line(ts, "x")], ts.date())
        cm.write_cached_logs_for_date("db", [_make_log_line(ts, "y")], ts.date())

        cm.clear_cache("web-app")

        assert not (isolated_cache / "web-app").exists()
        assert (isolated_cache / "db").exists()

    def test_clear_all(self, isolated_cache):
        ts = _utc(2026, 3, 6, 10, 0, 0)
        cm.write_cached_logs_for_date("web-app", [_make_log_line(ts, "x")], ts.date())
        cm.clear_cache()
        assert not isolated_cache.exists()


# ---------------------------------------------------------------------------
# Helpers (module-level)
# ---------------------------------------------------------------------------

def _parse_ts(log_line: str) -> datetime:
    ts_str = log_line.split()[0]
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.fromisoformat(ts_str)
