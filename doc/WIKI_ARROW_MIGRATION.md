# Arrow Migration Plan: `datetime` → `arrow`

Evaluate replacing Python's `datetime` stdlib with the [`arrow`](https://arrow.readthedocs.io) library.

---

## TL;DR Recommendation

**Defer.** Arrow brings real ergonomic gains in 2–3 spots, but the codebase has significant
friction points where arrow objects cannot be used directly (Polars, python-on-whales,
Pydantic). Net result is more boilerplate, not less. Revisit if a second use-case emerges
that needs human-readable time parsing or timezone arithmetic at scale.

---

## Why Arrow Is Worth Considering

| Pain point in current code | Arrow solution |
|---|---|
| `datetime.combine(date, datetime.min.time()).replace(tzinfo=timezone.utc)` (tools.py:434-437) | `arrow.get(date).floor('day')` |
| `datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)` scattered across 4 modules | `arrow.get(ts_str).to('UTC')` |
| Multiple `now = datetime.now(timezone.utc)` + manual `timedelta` arithmetic | `arrow.utcnow().shift(hours=-24)` |
| ISO-8601 formatting via `.strftime("%Y-%m-%dT%H:%M:%SZ")` (6 call sites) | `.isoformat()` (arrow always includes timezone) |
| `_parse_time_arg()` regex + manual delta_map in docker.py | `arrow.get("2 hours ago")` — **but see Friction #1** |

---

## Friction Points (read before deciding)

### Friction 1 — `_parse_time_arg()` doesn't get simpler

Arrow does **not** natively parse `"2 hours ago"` / `"30 minutes ago"` style strings.
That requires a separate `dateparser` library. The current hand-rolled regex in
`docker.py` is actually the right approach here.

### Friction 2 — Polars expects stdlib `datetime`

`cache_manager.py` uses Polars for Parquet I/O. Polars `.filter()`, `.sort()`, and
schema inference all expect `datetime.datetime` objects, not `arrow.Arrow`. Every
arrow object passed to Polars needs `.datetime` unwrapping, which negates the readability gain.

```python
# Before (stdlib)
df.filter(pl.col("timestamp") >= since)

# After (arrow — must unwrap)
df.filter(pl.col("timestamp") >= since.datetime)
```

### Friction 3 — `python-on-whales` API expects stdlib `datetime`

`container.logs(since=..., until=...)` in `docker.py` expects `datetime.datetime`.
Passing arrow objects would raise a type error. Every call site needs `.datetime`.

### Friction 4 — Pydantic/FastMCP schema serialisation

MCP tool parameters flow through Pydantic. Arrow objects are not Pydantic-native types.
JSON serialisation of timestamps would need custom encoders.

### Friction 5 — New dependency with no existing use

`arrow` is not in `pyproject.toml`. Adding it purely for cleaner datetime calls in
~6 files adds a production dependency that has no other function in the project.

---

## Module-by-Module Impact

| File | Uses | Arrow benefit | Friction |
|---|---|---|---|
| `docker.py` | `now()`, `fromisoformat`, `timedelta` delta_map | Low — `shift()` is cleaner | Must unwrap for `container.logs()` |
| `tools.py` | `now()`, `timedelta`, `strftime`, `combine` | Medium — `combine` replaced by `floor('day')`, `shift()` cleaner | Must unwrap for Polars comparisons |
| `cache_manager.py` | `fromisoformat`, `replace(tzinfo=utc)`, `timedelta` | Low — `.to('UTC')` cleaner | All Polars calls need `.datetime` |
| `patterns.py` | `fromisoformat`, `replace(tzinfo=utc)` | Low | None here |
| `spike_detector.py` | `strftime` only | None | None |
| `log_pattern_analyzer.py` | `datetime.now().isoformat()` (naive, no tz) | Minor fix | None |

---

## Scope of Change If Migrated

1. Add `arrow>=1.3.0` to `pyproject.toml`
2. Replace `from datetime import datetime, timezone, timedelta` → `import arrow` in 6 files
3. Unwrap `.datetime` at every Polars boundary (≈ 8 call sites in `cache_manager.py` + `tools.py`)
4. Unwrap `.datetime` at every `container.logs()` call (2 call sites in `docker.py`)
5. Keep `_parse_time_arg()` regex logic unchanged (arrow can't replace it)
6. Update tests: mocks that freeze `datetime.now` via `unittest.mock.patch` need adjusting
   to `patch("arrow.utcnow")` or use `freezegun`

Estimated files changed: **7–8**. Net line delta: roughly neutral (gains in some places,
`.datetime` unwrapping adds lines at boundaries).

---

## Conditions That Would Change the Recommendation

Migrate if **any** of the following becomes true:

- A new tool needs timezone conversion between multiple zones (e.g. correlating logs
  from containers in different TZs)
- `_parse_time_arg()` needs to support natural language beyond `"X unit ago"`
  (e.g. `"yesterday"`, `"last Monday"`) — at which point `dateparser` is the better fit anyway
- Polars is removed in favour of a datetime-native dataframe library
- The project adds a scheduling or calendar feature

---

## Decision Log

| Date | Decision | Reason |
|---|---|---|
| 2026-03-07 | Deferred | Polars + python-on-whales boundaries require `.datetime` unwrapping that negates ergonomic gains; `_parse_time_arg()` can't be simplified; no second consumer of arrow in the project |

---

*Keywords: arrow, datetime, timezone, migration, timedelta, fromisoformat, Polars, python-on-whales*
