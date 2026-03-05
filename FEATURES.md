# Docker Log Analyzer Features – Canonical Documentation

This document is the definitive source for all Docker Log Analyzer features. For architecture and design principles, see [CLAUDE.md](CLAUDE.md). For quick start and usage, see [README.md](README.md).

## MCP Tools Overview

The Docker Log Analyzer exposes 7 stateless MCP tools for VSCode Copilot Agent Mode. Each tool fetches logs from Docker, analyzes locally, and returns JSON results. No background threads, no persistent state.

### 1. list_containers

Lists all running Docker containers visible to the daemon.

**Parameters:** None

**Returns:**
```json
{
  "containers": [
    {
      "id": "abc123...",
      "name": "test-web-app",
      "image": "test-web-app:latest",
      "status": "running",
      "created": "2026-03-04T10:00:00Z"
    }
  ]
}
```

**Use case:** Discovery step before running analysis tools.

---

### 2. analyze_patterns

Analyzes log patterns to detect timestamp format, programming language, log levels, health checks, and top errors.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_name` | string? | — | Specific container to analyze, or `null` for all running |
| `tail` | int | 500 | Number of log lines to fetch |
| `force_refresh` | bool | false | Skip pattern cache, re-analyze |
| `use_cache` | bool | true | Check .cache/logs/ before Docker API |

**Returns:**
```json
{
  "results": {
    "test-web-app": {
      "timestamp_format": "ISO-8601",
      "timestamp_confidence": 0.95,
      "language": "Python",
      "language_confidence": 0.92,
      "log_levels": {
        "INFO": 450,
        "ERROR": 40,
        "WARNING": 10
      },
      "health_checks": {
        "pattern": "GET /health",
        "frequency_per_minute": 2.0
      },
      "top_errors": [
        "ConnectionError: Failed to connect to database",
        "TimeoutError: Request exceeded 30s"
      ],
      "logs_cache_hit": true,
      "analyzed_at": "2026-03-04T10:30:00Z"
    }
  }
}
```

**Pattern Detection:**
- **Timestamps:** ISO-8601, syslog, epoch (Unix), Apache HTTP
- **Languages:** Python, Java, Go, Node.js, PHP, generic/unknown
- **Log Levels:** INFO, DEBUG, WARNING, ERROR, CRITICAL, SEVERE, FATAL
- **Health Checks:** Repeating patterns (e.g., `/health`, `/ping`, `/readiness`)
- **Error Patterns:** 20+ regex patterns (see [config.py](docker_log_analyzer/config.py))

**Cache Behavior:**
1. First call: parses 500 lines, stores result to `.cache/patterns/<container>.json`
2. Subsequent calls: instant response from pattern cache
3. Pattern cache persists across container restarts (keyed by name only)
4. `force_refresh=true` to skip cache and re-analyze

---

### 3. detect_error_spikes

Detects error rate anomalies using rolling-window analysis (Polars). Compares current error rate to baseline (mean of previous 3 minutes).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_name` | string? | — | Specific container to analyze, or `null` for all running |
| `tail` | int | 1000 | Number of log lines to analyze |
| `spike_threshold` | float | 2.0 | Multiplier above baseline to flag spike (e.g., 2.0 = 2x baseline error rate) |
| `use_cache` | bool | true | Check .cache/logs/ before Docker API |

**Returns:**
```json
{
  "spike_analysis": {
    "test-web-app": {
      "baseline_error_rate": 0.08,
      "current_error_rate": 0.25,
      "spike_detected": true,
      "spike_ratio": 3.13,
      "affected_time_window": {
        "start": "2026-03-04T10:45:00Z",
        "end": "2026-03-04T10:46:00Z",
        "minute_bucket": 105
      },
      "sample_errors": [
        "ERROR: Connection timeout to database",
        "ERROR: Failed to acquire database lock"
      ],
      "logs_cache_hit": true
    },
    "test-database": {
      "baseline_error_rate": 0.05,
      "current_error_rate": 0.06,
      "spike_detected": false,
      "spike_ratio": 1.2
    }
  }
}
```

**Algorithm:**
1. Parse 1000 log lines and extract timestamps
2. Bucket errors into 1-minute intervals
3. Calculate rolling baseline: mean error rate of **previous 3 buckets** (not including current)
4. Flag if `current / baseline > spike_threshold`
5. Return all buckets with context (not just spikes)

**Edge Cases:**
- Empty logs: returns empty results
- No timestamps: skipped silently
- Insufficient data: baseline defaults to 1.0 to prevent divide-by-zero
- Single bucket: no baseline yet, spike_detected = false

---

### 4. detect_data_leaks

Scans logs for sensitive data: API keys, tokens, credentials, credit cards, PII (emails, phone numbers, SSNs).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `duration_seconds` | int | 60 | Number of seconds of logs to scan (from now backwards) |
| `container_names` | string[]? | — | Specific containers to scan, or `null` for all running |
| `severity_filter` | string | "all" | Filter by severity: `critical`, `high`, `medium`, or `all` |
| `use_cache` | bool | true | Check .cache/logs/ before Docker API |

**Returns:**
```json
{
  "findings": [
    {
      "container": "test-web-app",
      "severity": "critical",
      "pattern_name": "AWS_SECRET_KEY",
      "matched_text": "aws_secret_access_key=AKIA2E3X5Q7R9B2M5K7L",
      "redacted_text": "aws_secret_access_key=***REDACTED***",
      "line_number": 245,
      "timestamp": "2026-03-04T10:30:15Z",
      "recommendation": "Rotate AWS credentials immediately. Do not commit secrets to logs."
    },
    {
      "container": "test-database",
      "severity": "high",
      "pattern_name": "GENERIC_API_KEY",
      "matched_text": "api_key: sk_live_9a2b3c4d5e6f7g8h9i0j",
      "redacted_text": "api_key: ***REDACTED***",
      "recommendation": "Remove API key from logs. Use environment variables or secret management."
    }
  ],
  "scan_summary": {
    "total_findings": 2,
    "critical": 1,
    "high": 1,
    "medium": 0,
    "containers_scanned": 2,
    "cache_hits": {
      "test-web-app": true,
      "test-database": false
    }
  }
}
```

**Patterns Detected (13 total):**

| Pattern | Severity | Examples |
|---------|----------|----------|
| `AWS_SECRET_KEY` | critical | `AKIA...` or `aws_secret_access_key=...` |
| `AWS_ACCESS_KEY` | critical | `AKIA...` |
| `GITHUB_TOKEN` | critical | `ghp_...`, `gho_...` |
| `GENERIC_API_KEY` | high | `api_key=sk_live_...`, `apiKey: ...` |
| `JWT_TOKEN` | high | `eyJhbGciOi...` |
| `DATABASE_URL` | high | `postgres://user:pass@host/db` |
| `BASIC_AUTH` | high | `Authorization: Basic YWRtaW46...` |
| `CREDIT_CARD` | critical | 16 digits, VISA/MasterCard/Amex |
| `EMAIL_ADDRESS` | medium | `name@example.com` |
| `PHONE_NUMBER` | medium | `+1-555-123-4567` or `555-123-4567` |
| `SSN` | critical | `XXX-XX-XXXX` |
| `PRIVATE_KEY` | critical | `-----BEGIN RSA PRIVATE KEY-----` |
| `PASSWORD_VAR` | high | `password=...`, `passwd=...` |

**Filter Options:**
- `severity_filter="critical"` – only critical findings (secrets, credit cards, SSNs)
- `severity_filter="high"` – critical + high (API keys, tokens, auth headers, database URLs)
- `severity_filter="medium"` – all of above + emails, phone numbers
- `severity_filter="all"` – all findings

---

### 5. correlate_containers

Detects correlated errors across containers within a time window. Identifies which containers are failing together.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `time_window_seconds` | int | 30 | Time window to detect co-occurring errors |
| `tail` | int | 500 | Number of log lines per container |
| `use_cache` | bool | true | Check .cache/logs/ before Docker API |

**Returns:**
```json
{
  "correlations": [
    {
      "containers": ["test-web-app", "test-database"],
      "correlation_score": 0.85,
      "interpretation": "85% of web-app errors occur within 30s of database errors",
      "co_occurrence_count": 17,
      "sample_timeline": {
        "test-web-app_error_time": "2026-03-04T10:45:32Z",
        "test-database_error_time": "2026-03-04T10:45:28Z",
        "time_delta_seconds": 4
      }
    },
    {
      "containers": ["test-gateway", "test-cache"],
      "correlation_score": 0.0,
      "interpretation": "No correlation detected"
    }
  ],
  "summary": {
    "total_pairs_analyzed": 6,
    "correlated_pairs": 1,
    "time_window_seconds": 30,
    "cache_hits": {
      "test-web-app": true,
      "test-database": true,
      "test-gateway": false,
      "test-cache": false
    }
  }
}
```

**Correlation Score:**
- Ranges 0.0 to 1.0
- Formula: `matched_a / total_a` = fraction of container A errors with at least one co-occurring B error within window
- 1.0 = every error in A coincides with an error in B
- 0.0 = no overlap

**Algorithm:**
1. Extract all error timestamps from each container
2. For each pair of containers, count how many errors in A occur within ±window_seconds of any error in B
3. Score = co_occurrences / total_errors_in_A
4. Cap analysis at MAX_CO_OCCURRENCES=500 per pair to avoid O(n²) explosion

---

### 6. sync_docker_logs

Explicitly syncs Docker logs to `.cache/logs/` for a time window. Enables instant offline analysis and bug reproduction capture.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_names` | string[]? | — | Specific containers to sync, or `null` for all running |
| `since` | string | "24 hours ago" | Start of time window: `"2 hours ago"`, `"7 days ago"`, `"2026-03-04T10:30:00Z"` |
| `until` | string | "now" | End of time window: `"now"` or ISO-8601 timestamp |
| `force_refresh` | bool | false | Delete and re-sync cache (ignore existing) |

**Returns:**
```json
{
  "sync_result": {
    "test-web-app": {
      "lines_synced": 5000,
      "date_range": ["2026-03-04", "2026-03-03", "2026-03-02"],
      "status": "success",
      "time_window": {
        "since": "2026-03-01T10:30:00Z",
        "until": "2026-03-04T10:30:00Z"
      }
    },
    "test-database": {
      "lines_synced": 3200,
      "date_range": ["2026-03-04"],
      "status": "success"
    }
  },
  "summary": {
    "total_lines_synced": 8200,
    "containers_synced": 2,
    "cache_directory": ".cache/logs/",
    "time_window_seconds": 259200
  }
}
```

**Time Argument Format:**

| Format | Examples | Result |
|--------|----------|--------|
| Relative | `"2 hours ago"`, `"30 minutes ago"`, `"7 days ago"` | Calculated from current system time |
| ISO-8601 | `"2026-03-04T10:30:00Z"`, `"2026-03-04T10:30:00+00:00"` | Exact timestamp |
| Special | `"now"` | Current system time |

**Cache Structure:**
```
.cache/logs/
  ├── metadata.json                    (sync tracking metadata)
  ├── test-web-app/
  │   ├── 2026-03-04.jsonl            (5000 lines, 2.3 MB)
  │   ├── 2026-03-03.jsonl
  │   └── 2026-03-02.jsonl
  └── test-database/
      └── 2026-03-04.jsonl            (3200 lines, 1.8 MB)
```

**Cache Format (JSONL):**
```json
{"timestamp": "2026-03-04T10:30:45.123456Z", "message": "INFO: Request started"}
{"timestamp": "2026-03-04T10:30:46.234567Z", "message": "ERROR: Connection timeout"}
```

**Metadata Tracking:**
```json
{
  "test-web-app": {
    "2026-03-04": {
      "synced_at": "2026-03-04T10:31:00Z",
      "line_count": 5000
    }
  }
}
```

**Workflow Examples:**

```bash
# Sync last 4 hours for all containers
uv run docker-log-analyzer-mcp sync_docker_logs --since "4 hours ago"

# Sync specific date range for one container
uv run docker-log-analyzer-mcp sync_docker_logs \
  --container_names test-web-app \
  --since "2026-03-01T00:00:00Z" \
  --until "2026-03-05T00:00:00Z"

# Re-sync (force delete and refetch)
uv run docker-log-analyzer-mcp sync_docker_logs \
  --since "24 hours ago" \
  --force_refresh true

# Now use cached logs for instant analysis
uv run docker-log-analyzer-mcp analyze_patterns test-web-app
uv run docker-log-analyzer-mcp detect_error_spikes test-web-app
uv run docker-log-analyzer-mcp correlate_containers

# Works offline (containers can be stopped)
docker compose down
uv run docker-log-analyzer-mcp detect_error_spikes test-web-app  # Still works via cache!
```

---

### 7. capture_and_analyze

Live capture for N seconds, then combined report: error spikes + container correlation + per-container breakdown.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_names` | string[]? | — | Specific containers to monitor, or `null` for all running |
| `duration_seconds` | int | 120 | Live capture duration in seconds |
| `spike_threshold` | float | 2.0 | Spike detection threshold multiplier |
| `time_window_seconds` | int | 30 | Time window for correlation analysis |
| `use_cache` | bool | true | Use cached logs if available (instant if synced) |

**Returns:**
```json
{
  "capture_metadata": {
    "duration_seconds": 120,
    "start_time": "2026-03-04T10:30:00Z",
    "end_time": "2026-03-04T10:32:00Z",
    "containers_monitored": 2
  },
  "spikes": {
    "test-web-app": {
      "spike_detected": true,
      "spike_ratio": 2.5,
      "affected_minute": "2026-03-04T10:31:00Z"
    },
    "test-database": {
      "spike_detected": false
    }
  },
  "correlations": [
    {
      "containers": ["test-web-app", "test-database"],
      "correlation_score": 0.8
    }
  ],
  "per_container_breakdown": {
    "test-web-app": {
      "total_lines": 450,
      "error_count": 80,
      "error_rate": 0.178,
      "top_errors": ["ConnectionError", "TimeoutError"]
    },
    "test-database": {
      "total_lines": 380,
      "error_count": 12,
      "error_rate": 0.032,
      "top_errors": []
    }
  }
}
```

**Use Case:** Bug reproduction — capture all activity during failure scenario, get immediate analysis.

---

### 8. start_test_containers

Start 4-service test log generator stack (from `docker-compose.test.yml`).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rebuild` | bool | false | Force rebuild images (`docker compose build`) |

**Returns:**
```json
{
  "status": "success",
  "containers_started": ["test-web-app", "test-database", "test-gateway", "test-cache"],
  "details": {
    "test-web-app": {
      "image": "test-web-app:latest",
      "status": "running",
      "id": "abc123..."
    }
  }
}
```

**Services:**

| Service | Language | Format | Spike Interval | Purpose |
|---------|----------|--------|----------------|---------|
| `test-web-app` | Python | ISO-8601 | 90s | Primary application logs |
| `test-database` | Java | syslog | 90s (synced with web-app) | Database with correlated errors |
| `test-gateway` | Node.js | Apache | 90s (synced with web-app) | API gateway with correlated errors |
| `test-cache` | Go | epoch | 120s (independent) | Cache system with independent spikes |

**Spike Behavior:**
- All 4 services emit logs continuously
- Every 90 seconds: coordinated error spikes in test-web-app, test-database, test-gateway
- Every 120 seconds: independent error spike in test-cache
- Enables testing error spike detection and cross-container correlation

---

### 9. stop_test_containers

Stop and remove test containers.

**Parameters:** None

**Returns:**
```json
{
  "status": "success",
  "containers_removed": ["test-web-app", "test-database", "test-gateway", "test-cache"]
}
```

---

## Log Caching System

The cache-first strategy enables **100x faster analysis** by checking disk before Docker API.

### Design

**Flow:**
```
Tool call (e.g., analyze_patterns)
  ↓
Check .cache/logs/<container>/YYYY-MM-DD.jsonl
  ↓
  ├─ Hit (recent)  → Use cached logs → Instant response ⚡
  ├─ Miss          → Fetch from Docker API → Cache result
  └─ Stale (24h+)  → Fetch fresh from Docker API
```

### Cache-First Window

- **Default:** 24-hour rolling window
- **Configurable:** `CACHE_MAX_AGE_MINUTES` environment variable
- **Per-tool:** Each tool has `use_cache` parameter (default=true)

### Atomic Writes

All cache writes are atomic (crash-safe):

```python
# Pseudo-code
1. Write to temporary file (.tmp-xxxxxx.jsonl)
2. Atomic rename to target (YYYY-MM-DD.jsonl)
3. On crash: temp file left behind, original untouched
4. On success: atomic rename completes or fails completely
```

### Multi-Day Queries

Cache handles time windows spanning date boundaries:

```python
# Example: Query 2026-03-02 23:00 to 2026-03-04 01:00
read_cached_logs_for_window("web-app", since, until):
  - Read 2026-03-02.jsonl, filter by timestamp range
  - Read 2026-03-03.jsonl (all lines in this date)
  - Read 2026-03-04.jsonl, filter by timestamp range
  - Return merged results
```

### Cache Invalidation

**Manual cleanup:**

```bash
# Clear cache for one container
rm -rf .cache/logs/test-web-app/

# Clear all log cache
rm -rf .cache/logs/

# Keep pattern cache separate
ls -la .cache/patterns/
```

**Automatic re-fetch:**
- `use_cache=false` parameter on any tool call
- `CACHE_MAX_AGE_MINUTES` expired (default 60 minutes = 1 hour)

### Metadata Tracking

`.cache/logs/metadata.json` tracks all syncs:

```json
{
  "test-web-app": {
    "2026-03-04": {
      "synced_at": "2026-03-04T10:31:00Z",
      "line_count": 5000
    },
    "2026-03-03": {
      "synced_at": "2026-03-04T10:31:05Z",
      "line_count": 4800
    }
  }
}
```

Use for auditing and cache performance analysis.

---

## Configuration

Environment variables in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker daemon socket |
| `LOG_LEVEL` | `INFO` | Logging: DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `CONTAINER_LABEL_FILTER` | `""` | Filter containers by label (e.g., `env=prod`) |
| `DEFAULT_TAIL_LINES` | `500` | Default log lines for pattern analysis |
| `DEFAULT_SPIKE_TAIL_LINES` | `1000` | Default log lines for spike detection |
| `DEFAULT_SPIKE_THRESHOLD` | `2.0` | Spike ratio threshold |
| `DEFAULT_CORRELATION_WINDOW_SECONDS` | `30` | Co-occurrence window for correlation |
| `USE_LOGS_CACHE` | `true` | Enable cache-first strategy |
| `CACHE_MAX_AGE_MINUTES` | `60` | Max cache age before re-fetch |

---

## Performance Characteristics

### Benchmarks (Local Test)

| Operation | Time | Notes |
|-----------|------|-------|
| `list_containers` | ~50 ms | Docker SDK call, no I/O |
| `analyze_patterns` (cached) | ~10 ms | Disk read + pattern extraction |
| `analyze_patterns` (uncached) | ~200 ms | Docker API fetch + analysis |
| `detect_error_spikes` (cached) | ~50 ms | Polars rolling-window on 1000 lines |
| `detect_error_spikes` (uncached) | ~300 ms | Docker fetch + Polars analysis |
| `correlate_containers` (cached) | ~80 ms | Pairwise comparison on 500 lines each |
| `detect_data_leaks` (cached, 13 patterns) | ~60 ms | Regex scanning on cached logs |
| `sync_docker_logs` (4 hours, all containers) | ~1.2 s | Docker fetch + atomic cache writes |

**Expected Speedup:**
- Cold (no cache): 200–300 ms per tool
- Warm (cached): 10–50 ms per tool
- **Speedup: 4x–20x faster** after initial sync

### Memory Usage

- Pattern cache: ~50 KB per container
- Log cache (daily JSONL): ~0.5–2 MB per container per day
- In-memory Polars DataFrames: ~10–50 MB for 5000+ log lines

---

## Troubleshooting

### Cache Not Updating

**Problem:** Tool returns stale data after logs change.

**Solution:**
```bash
# Full cache refresh
rm -rf .cache/logs/

# Or use force_refresh parameter
uv run docker-log-analyzer-mcp sync_docker_logs --force_refresh true
```

### Time Window Not Inclusive

**Problem:** `sync_docker_logs --since "10 hours ago"` returns fewer logs than expected.

**Solution:** Check container creation time.
```bash
docker inspect test-web-app | grep Created
# If created < 10 hours ago, logs don't exist before that
```

### Disk Space Growing

**Problem:** `.cache/logs/` growing large over time.

**Solution:** Implement cache rotation.
```bash
# Keep only last 7 days
find .cache/logs -name "*.jsonl" -mtime +7 -delete
```

### Metadata Corruption

**Problem:** `metadata.json` corrupted or missing.

**Solution:** Safe to delete — re-synced on next tool call.
```bash
rm .cache/logs/metadata.json
# Next sync_docker_logs will regenerate it
```

---

## Development Notes

### Adding New Tools

1. Implement tool function in `mcp_server.py`
2. Add wrapper function `_wrap_<tool_name>()`
3. Register in MCP registry (end of `run()`)
4. Update FEATURES.md and README.md
5. Add unit tests to `tests/test_mcp_integration.py`
6. Mark integration tests with `@pytest.mark.integration`

### Adding New Secret Patterns

1. Add regex pattern to `config.py` → `error_patterns`
2. Add pattern name and severity to `SecretDetector` docstring
3. Update FEATURES.md table
4. Add test case to `tests/test_secret_detector.py`
5. Verify no false positives on sample logs

### Cache Performance Optimization

- **Consider:** Query only date ranges needed (not full 24h)
- **Current:** 24-hour window is safest default
- **Future:** Implement adaptive window based on log velocity

---

## DIFF: map_service_dependencies

Planned addition. Not yet implemented.

**Proposed signature:**

```python
tool_map_service_dependencies(
    containers: Optional[List[str]] = None,
    include_transitive: bool = False,
) -> dict
```

**Purpose:** Parse container logs for service calls, database connections, and external API calls to build a structural dependency graph with error cascade candidates.

**Copilot use case:** "Show me how errors cascade between my microservices"

### Differentiation from existing tools

| Tool | What it answers |
| ---- | --------------- |
| `correlate_containers` | Did errors in A and B happen at the same time? (temporal co-occurrence) |
| `map_service_dependencies` | Does A's logs show it calls B? (structural dependency) |
| Combined | "A depends on B, and their errors correlate at r=0.82 — B errors likely cause A errors" |

### Dependency signals (regex-parseable from logs)

- HTTP calls: `http(s)://service-name:port`, `requests.get`, `fetch`, `axios`
- DB connections: `postgres://`, `redis://`, `mongodb://`, `mysql://`
- gRPC: `calling grpc`, service mesh headers
- Container name mentions in log output

Each edge includes `"inferred_from": ["http_url", "connection_string"]` to qualify confidence.

### Output shape

```json
{
  "dependencies": {
    "gateway": ["web-app", "auth-service"],
    "web-app": ["database", "cache"]
  },
  "cascade_candidates": [
    {
      "from": "database",
      "to": ["web-app", "gateway"],
      "evidence": "dependency_graph + error_correlation",
      "confidence": "medium"
    }
  ],
  "cache_hits": {}
}
```

### Design decisions

- **`trace_depth` dropped** — implies runtime tracing semantics (Jaeger/Zipkin) that logs cannot deliver. From logs, reliable dependency detection is 1-hop per container. Transitive closure is computed when `include_transitive=True` but labeled as speculative.
- **"Error propagation paths" scoped to "cascade candidates"** — true propagation direction requires dependency graph + temporal correlation + causal ordering. The tool surfaces candidates with confidence levels (high/medium/low) based on correlation score + dependency evidence; it does not assert causality.
- **Confidence qualifiers on all edges** — HTTP URL matches are high-confidence; container name mentions in log text are low-confidence.
- **gRPC/event-driven coverage is limited** — HTTP-heavy microservices get the most value. Framework-specific gRPC patterns have false negatives; documented in tool description.

### Implementation plan

1. New module `dependency_mapper.py`:
   - Regex patterns for HTTP URLs, DB connection strings, container name references
   - `extract_dependencies(lines) -> List[Tuple[str, str, str]]` — `(target, inferred_from, confidence)`
   - `build_graph(container_logs) -> dict` — adjacency dict with edge metadata
   - `find_cascade_candidates(graph, correlation_results) -> List[dict]` — join with correlator output

2. `mcp_server.py` tool registration:
   - Fetch logs (cache-first, same pattern as existing tools)
   - Call `correlate()` directly (no tool-to-tool calls) for cascade candidate scoring
   - Return structured JSON

3. Tests:
   - Unit: regex patterns, graph builder, cascade candidate logic (~20 tests)
   - Integration: live test containers, verify known dependency detected (~10 tests)

**Estimated scope:** ~120 lines `dependency_mapper.py`, ~60 lines in `mcp_server.py`, ~30 tests.

---

## See Also

- [README.md](README.md) – Quick start, usage examples
- [CLAUDE.md](CLAUDE.md) – Architecture, design principles, constraints
- [docker_log_analyzer/config.py](docker_log_analyzer/config.py) – Configuration schema
- [docker_log_analyzer/cache_manager.py](docker_log_analyzer/cache_manager.py) – Cache implementation
- [tests/](tests/) – 95 unit tests + 32 integration tests
