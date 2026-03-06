# Wiki Hub: Architecture

Use this hub for system design, module structure, key algorithms, and implementation rationale.

---

## Agent Use Rules

- Start here for "why", "how does X work internally", and "which module owns Y" questions.
- For contributor rules and hard design constraints, [../CLAUDE.md](../CLAUDE.md) is the authoritative source.
- For "how to use" or "how to configure", use [WIKI_OPERATIONS.md](WIKI_OPERATIONS.md).
- For tool parameter reference, use [WIKI_TOOLS.md](WIKI_TOOLS.md).

---

## Design Principles

All tools are **stateless**: fetch logs from Docker → analyse locally → return JSON. No background threads, no persistent in-memory state, no external APIs.

| Principle | Rule |
|-----------|------|
| Stateless | Every tool call is independent: fetch → analyse → return |
| Local-only | No LLMs, no Kafka, no telemetry. Regex + Polars only |
| Cache-first | Check `.cache/logs/` before Docker API on every log-reading tool |
| Tool isolation | No tool depends on another having been called first |
| Deterministic | Same logs → same output, always reproducible offline |

Authoritative source for all constraints: [../CLAUDE.md](../CLAUDE.md)

---

## Module Map

```text
docker_log_analyzer/
  mcp_server.py           # Tool registration, _fetch_logs_with_cache(), ToolRegistry
  spike_detector.py       # Polars rolling-window spike detection (1-min buckets)
  correlator.py           # Pairwise temporal error correlation (score 0–1)
  dependency_mapper.py    # Log-based dependency graph inference (regex → adjacency dict)
  log_pattern_analyzer.py # PatternDetector: timestamp format, language, log levels
  secret_detector.py      # SecretDetector: 20 patterns, redaction, severity
  cache_manager.py        # Atomic Parquet log cache (write + multi-day read; JSONL fallback)
  config.py               # Pydantic BaseSettings singleton (settings.*)
  logger.py               # LoggerWithRunID singleton (run_id in every log line)
```

---

## MCP Server Internals

- **ToolRegistry pattern** — `_registry.register(name, handler, schema)` called in `run()`; each tool has an explicit `_wrap_<name>()` function (no lambdas; meaningful names in stack traces).
- **`_fetch_logs_with_cache(container, tail, use_cache)`** — shared helper used by all log-reading tools; returns `(lines, cache_hit)`.
- **No tool-to-tool calls** — `correlate_containers` and `map_service_dependencies` both call `correlate()` directly from `correlator.py`. MCP tool calls are never chained internally.
- **Error handling** — all tools return `{"status": "error", "error": "..."}` on failure; server never crashes.

---

## Key Algorithms

### Spike Detection (`spike_detector.py`)

1. Parse log lines → extract error timestamps with RFC3339 regex (fractional seconds optional)
2. Bucket errors into 1-minute intervals (Polars DataFrame)
3. Rolling baseline: `rolling_mean(shift(1), window=3)` — mean of previous 3 buckets, current excluded
4. `baseline.fill_null(1.0)` — prevents divide-by-zero on early buckets
5. Flag bucket if `current_error_count / baseline > spike_threshold`

### Correlation (`correlator.py`)

1. Extract error timestamps per container (RFC3339 regex, ERROR/CRITICAL/FATAL/panic patterns)
2. For each container pair (A, B): count errors in A with ≥1 B error within ±`time_window_seconds`
3. Score = `matched_a / total_a` (0.0–1.0; 1.0 = every A error has a co-occurring B error)
4. Capped at `MAX_CO_OCCURRENCES=500` per pair to avoid O(n²) explosion
5. Example pairs limited to 3 per result

### Dependency Mapping (`dependency_mapper.py`)

1. Per container, per log line: apply four regex patterns (see confidence table below)
2. Accumulate `(target, inferred_from, confidence) → hit_count` per-line for accurate counts
3. Resolve bare hostnames to known container names; skip self-loops
4. Optional: one-hop transitive closure (`include_transitive=True`) labelled `inferred_from="transitive"`, `confidence="low"`, `hit_count=0`
5. Join graph with `correlate()` output → cascade candidates with confidence level:
   - `high` = dep confidence in (high, medium) AND correlation_score ≥ 0.5
   - `medium` = dep confidence in (high, medium) AND correlation_score > 0
   - `low` = dep confidence low, or transitive edge

### Cache System (`cache_manager.py` + `tools.py`)

**Log cache:**
1. Key: `container_name + YYYY-MM-DD`
2. Path: `.cache/logs/<container>/<YYYY-MM-DD>.parquet`
3. **Write:** `_atomic_write_parquet()` — zstd-compressed Parquet, crash-safe via temp + rename
4. **Read:** `_read_parquet_file()` — Polars columnar filter; falls back to `_read_jsonl_file()` for legacy `.jsonl` files
5. **Metadata:** `.cache/logs/metadata.json` tracks `synced_at` + `line_count` per date per container
6. **Schema:** `timestamp` (`Datetime[us,UTC]`), `message` (`String`)

**Correlation result cache (`tools.py`):**
1. Key: MD5 of `sorted(container_names) + time_window_seconds + tail`
2. Path: `.cache/correlations/<md5>.json`
3. **TTL:** `CORRELATION_CACHE_TTL_MINUTES` (default 30 min); set to `0` to disable
4. **Write:** atomic tempfile + rename; stores full result JSON including `cached_at`
5. **Response field:** `correlation_cache_hit: true` when result served from cache

---

## Dependency Mapping

### Signal Confidence

| Signal type | Example pattern | Confidence |
|-------------|----------------|-----------|
| HTTP/HTTPS URL | `http://payment-service:8080/api` | high |
| DB connection string | `postgres://db:5432`, `redis://cache:6379` | high |
| gRPC / dial call | `dialing order-service`, `connecting to auth` | medium |
| Container name mention | bare name in log body (min 4 chars, word boundary) | low |
| Transitive edge | A→B + B→C → A→C (computed, not observed) | low |

### Skipped hosts

`localhost`, `127.0.0.1`, `0.0.0.0`, `::1` are never emitted as dependency targets.

### Supported DB protocols

`postgres`, `postgresql`, `redis`, `mongodb`, `mongo`, `mysql`, `mariadb`, `cassandra`, `elasticsearch`, `amqp`, `amqps`, `rabbitmq`, `kafka`

---

## Adding New Tools

1. Implement `tool_<name>(...)` in `mcp_server.py`
2. Add `_wrap_<name>(**kwargs)` wrapper function
3. Register via `_registry.register(name, _wrap_<name>, schema)` in `run()`
4. Use `_fetch_logs_with_cache()` for any log-reading operation
5. Return `{"status": "success", ...}` or `{"status": "error", "error": "..."}`
6. Add unit tests in `tests/test_<module>.py` + integration tests in `test_mcp_integration.py`
7. Update [WIKI_TOOLS.md](WIKI_TOOLS.md), [../README.md](../README.md), [../CLAUDE.md](../CLAUDE.md)

---

## Retrieval keywords

architecture, design, module, stateless, cache, polars, correlator, spike_detector, dependency_mapper, secret_detector, log_pattern_analyzer, mcp_server, tool registry, wrap, algorithm, signal, confidence, transitive, hit_count, rolling_mean, MAX_CO_OCCURRENCES, atomic write, parquet, pyarrow, zstd, JSONL fallback, BaseSettings, run_id

**[negative keywords / not-this-doc]**
setup, install, configure, environment variable, copilot prompt, test suite, CI, coverage, unit tests, remote docker, SSH

---

## See also

- Design constraints (authoritative): [../CLAUDE.md](../CLAUDE.md)
- Tools reference: [WIKI_TOOLS.md](WIKI_TOOLS.md)
- Operations hub: [WIKI_OPERATIONS.md](WIKI_OPERATIONS.md)
- Quality hub: [WIKI_QUALITY.md](WIKI_QUALITY.md)
- Home: [WIKI_HOME.md](WIKI_HOME.md)
