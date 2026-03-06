# Upgrade Plan: JSONL → Parquet Log Cache

**Status:** DONE
**Target module:** `docker_log_analyzer/cache_manager.py`
**Motivation:** Replace JSONL with Parquet for faster reads, native Polars integration, and smaller cache files (5-10x compression on log data).

---

## Background

Current cache layout:
```
.cache/logs/
  ├── metadata.json
  ├── web-app/
  │   └── 2026-03-04.jsonl       # one JSON object per line
  └── database/
      └── 2026-03-04.jsonl
```

Each `.jsonl` file stores log records as `{"timestamp": "...", "message": "..."}` per line.
On every tool call, Polars must re-parse JSON text including fields it never uses.

Target cache layout:
```
.cache/logs/
  ├── metadata.json               # unchanged
  ├── web-app/
  │   └── 2026-03-04.parquet     # columnar, compressed
  └── database/
      └── 2026-03-04.parquet
```

---

## Schema

Parquet file schema (per daily file):

| Column      | Polars dtype         | Notes                              |
|-------------|----------------------|------------------------------------|
| `timestamp` | `pl.Datetime("us","UTC")` | Parsed once at write time     |
| `message`   | `pl.String`          | Full raw Docker log line           |

No schema changes to the public API — callers still receive `List[str]` log lines.

---

## Tasks

### 1. Add dependency — STATUS: DONE

Parquet support in Polars requires `pyarrow` or `fastparquet`.
`pyarrow` is the recommended engine.

- [ ] Add `pyarrow>=14.0.0` to `pyproject.toml` under `[project.dependencies]`
- [ ] Run `uv sync` and commit updated `uv.lock`

---

### 2. Update `write_cached_logs_for_date()` — STATUS: DONE

Replace JSON-lines serialization with Polars DataFrame write.

**Current flow:**
1. Parse Docker log lines
2. Build list of `{"timestamp": ..., "message": ...}` dicts
3. `json.dumps()` each → join → atomic write as `.jsonl`

**New flow:**
1. Parse Docker log lines (same)
2. Build two lists: `timestamps: List[datetime]`, `messages: List[str]`
3. Construct `pl.DataFrame({"timestamp": timestamps, "message": messages})`
4. Cast `timestamp` column to `pl.Datetime("us", "UTC")`
5. Write to `.parquet` via `df.write_parquet(tmp_path, compression="zstd")`
6. Atomic rename to final path (same tempfile + rename pattern)

**File path change:**
- Old: `container_dir / f"{date_val}.jsonl"`
- New: `container_dir / f"{date_val}.parquet"`

**Atomic write update:**
- `_atomic_write()` currently hardcodes `.jsonl` as `suffix` in `NamedTemporaryFile`
- Change to accept a `suffix` parameter (e.g. `".parquet"`)
- For Parquet: write bytes, not text — use `mode='wb'`

---

### 3. Update `read_cached_logs_for_window()` — STATUS: DONE

Replace line-by-line JSON parsing with Polars scan + filter.

**Current flow:**
1. Open `.jsonl`, iterate lines, `json.loads()` each
2. Parse timestamp string per line
3. Filter by `since <= ts <= until`
4. Return matching raw line strings

**New flow:**
1. Check for `.parquet` file existence (same cache-miss logic)
2. `df = pl.read_parquet(cache_file, columns=["timestamp", "message"])`
3. Filter: `df.filter((pl.col("timestamp") >= since) & (pl.col("timestamp") <= until))`
4. Return `df["message"].to_list()` — preserves existing `List[str]` return contract

**Note:** `since` / `until` must be `datetime` with `tzinfo=UTC` to match column dtype.

---

### 4. Update `_atomic_write()` helper — STATUS: DONE

Current signature writes text. Parquet is binary.

- Add `mode: str = 'w'` parameter
- Add `suffix: str = '.jsonl'` parameter
- Callers pass `mode='wb'` and `suffix='.parquet'` for Parquet
- `metadata.json` continues to use the existing text path (no change)

---

### 5. Cache migration / coexistence — STATUS: DONE

Existing `.jsonl` files will remain on disk after the upgrade.
Decide on migration strategy:

**Option A (recommended): Silent fallback**
- On cache read: check for `.parquet` first, then fall back to `.jsonl`
- If `.jsonl` hit → return data (old format still works)
- New writes always produce `.parquet`
- Old `.jsonl` files expire naturally (stale cache age check already exists)

**Option B: One-shot migration script**
- Add `scripts/migrate_cache_jsonl_to_parquet.py`
- Reads all `.jsonl`, writes `.parquet`, deletes `.jsonl`
- Run once manually

**Decision:** Implement Option A first (zero-downtime, no manual step).
Option B can be added as a follow-up if disk cleanup is needed.

---

### 6. Update `_atomic_write` suffix in `_update_metadata()` — STATUS: DONE

`_update_metadata()` calls `_atomic_write(METADATA_FILE, ...)`.
`METADATA_FILE` is `.json` — keep this path and text mode unchanged.
Only pass explicit `suffix='.json'` to avoid any default collision after the refactor.

---

### 7. Update tests — STATUS: DONE

Affected test files (to be identified during implementation):

- [ ] Tests asserting `.jsonl` file existence → change to `.parquet`
- [ ] Tests reading raw cache content → use `pl.read_parquet()` to verify
- [ ] Tests for `read_cached_logs_for_window()` → fixture data must be `.parquet`
- [ ] Add test: old `.jsonl` file still readable after upgrade (Option A fallback)
- [ ] Add test: Parquet schema matches expected columns and dtypes

---

### 8. Update CLAUDE.md — STATUS: DONE

Section **3.1 Log Fetching** references `.jsonl` explicitly:

- Update file extension reference: `.jsonl` → `.parquet`
- Update format description: `JSONL with ISO-8601 timestamps` → `Parquet (zstd), timestamp column as UTC Datetime`
- Keep all other cache rules unchanged

---

### 9. Update `_atomic_write` temp suffix — STATUS: DONE

Current: `suffix='.jsonl'` hardcoded in `NamedTemporaryFile`.
The temp file suffix is cosmetic but should match the final file to avoid confusion in crash/recovery scenarios.

---

### 10. Update README / doc references — STATUS: DONE

- [ ] [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md) — Cache System section: update format description
- [ ] [WIKI_OPERATIONS.md](WIKI_OPERATIONS.md) — Log Cache Strategy section: update extension and format
- [ ] [README.md](../README.md) — if cache file format is mentioned

---

## Acceptance Criteria

- [ ] All unit tests pass: `uv run pytest tests/ -m "not integration"`
- [ ] All integration tests pass: `uv run pytest tests/`
- [ ] `.cache/logs/<container>/<date>.parquet` files are created on fresh sync
- [ ] Existing `.jsonl` cache files are still served correctly (Option A)
- [ ] Parquet files are smaller than equivalent JSONL files (spot-check)
- [ ] No change to MCP tool return shapes

---

## Files to Modify

| File | Change |
|------|--------|
| `docker_log_analyzer/cache_manager.py` | Core read/write logic |
| `pyproject.toml` | Add `pyarrow` dependency |
| `uv.lock` | Updated by `uv sync` |
| `CLAUDE.md` | Update cache format description |
| `doc/WIKI_ARCHITECTURE.md` | Update cache format description |
| `doc/WIKI_OPERATIONS.md` | Update cache format description |
| `tests/` (multiple) | Update fixtures and assertions |

---

## See Also

- [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md) — Cache System design
- [WIKI_OPERATIONS.md](WIKI_OPERATIONS.md) — Log Cache Strategy (operator view)
- [WIKI_QUALITY.md](WIKI_QUALITY.md) — Test strategy and CI

---

**Retrieval keywords:** parquet, jsonl, cache, upgrade, migration, polars, pyarrow, log cache, cache_manager, file format, zstd, compression, columnar, datetime, schema, atomic write, fallback
