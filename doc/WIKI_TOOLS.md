# Wiki Hub: MCP Tools Reference

Canonical reference for all 11 MCP tools — parameters, return shapes, and behavior.

---

## Agent Use Rules

- Use this page for "what does tool X do", "what parameters does X accept", "what does X return".
- For algorithm internals, see [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md).
- For Copilot prompts that invoke these tools, see [WIKI_OPERATIONS.md § Copilot Prompts](WIKI_OPERATIONS.md#copilot-prompts).

---

## Tool Index

| # | Tool | Purpose |
|---|------|---------|
| 1 | [list_containers](#1-list_containers) | List running Docker containers |
| 2 | [analyze_patterns](#2-analyze_patterns) | Timestamp format, language, log levels, health checks |
| 3 | [detect_error_spikes](#3-detect_error_spikes) | Rolling-window error rate anomaly detection |
| 4 | [detect_data_leaks](#4-detect_data_leaks) | Scan logs for secrets, credentials, PII |
| 5 | [correlate_containers](#5-correlate_containers) | Pairwise temporal error co-occurrence scoring |
| 6 | [sync_docker_logs](#6-sync_docker_logs) | Sync logs to cache for offline / fast analysis |
| 7 | [capture_and_analyze](#7-capture_and_analyze) | Live capture + combined spike + correlation report |
| 8 | [map_service_dependencies](#8-map_service_dependencies) | Log-based dependency graph + cascade candidates |
| 9 | [start_test_containers](#9-start_test_containers) | Start 4-service test stack |
| 10 | [stop_test_containers](#10-stop_test_containers) | Stop and remove test containers |
| 11 | [rank_root_causes](#11-rank_root_causes) | Score containers by root-cause likelihood |

---

## 1. list_containers

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

## 2. analyze_patterns

Analyzes log patterns to detect timestamp format, programming language, log levels, health checks, and top errors.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_name` | string? | — | Specific container, or `null` for all running |
| `tail` | int | 500 | Log lines to fetch |
| `force_refresh` | bool | false | Skip pattern cache, re-analyze from raw logs |
| `use_cache` | bool | true | Check `.cache/logs/` before Docker API |

**Returns:**
```json
{
  "results": {
    "test-web-app": {
      "timestamp_format": "ISO-8601",
      "timestamp_confidence": 0.95,
      "language": "Python",
      "language_confidence": 0.92,
      "log_levels": { "INFO": 450, "ERROR": 40, "WARNING": 10 },
      "health_checks": { "pattern": "GET /health", "frequency_per_minute": 2.0 },
      "top_errors": ["ConnectionError: Failed to connect to database"],
      "logs_cache_hit": true,
      "analyzed_at": "2026-03-04T10:30:00Z"
    }
  }
}
```

**Detection capabilities:**
- **Timestamps:** ISO-8601, syslog, epoch (Unix), Apache HTTP
- **Languages:** Python, Java, Go, Node.js, PHP, generic/unknown
- **Log levels:** INFO, DEBUG, WARNING, ERROR, CRITICAL, SEVERE, FATAL
- **Health checks:** Repeating patterns (e.g., `/health`, `/ping`, `/readiness`)

**Cache behavior:**
1. First call: parses N lines, stores result to `.cache/patterns/<container>.json`
2. Subsequent calls: instant response from pattern cache
3. Cache persists across container restarts (keyed by name only)
4. `force_refresh=true` skips cache and re-analyzes

---

## 3. detect_error_spikes

Detects error rate anomalies using Polars rolling-window analysis. Compares current error rate against a 3-bucket rolling baseline.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_name` | string? | — | Specific container, or `null` for all running |
| `tail` | int | 1000 | Log lines to analyze |
| `spike_threshold` | float | 2.0 | Multiplier above baseline to flag spike (2.0 = 2× baseline) |
| `use_cache` | bool | true | Check `.cache/logs/` before Docker API |

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
      "sample_errors": ["ERROR: Connection timeout to database"],
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

**Edge cases:** Empty logs → empty results. No timestamps → skipped. Baseline defaults to `1.0` on first bucket (no divide-by-zero).

---

## 4. detect_data_leaks

Scans logs for sensitive data using 20 regex patterns.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `duration_seconds` | int | 60 | Seconds of logs to scan (from now backwards) |
| `container_names` | string[]? | — | Specific containers, or `null` for all running |
| `severity_filter` | string | `"all"` | `critical`, `high`, `medium`, or `all` |
| `use_cache` | bool | true | Check `.cache/logs/` before Docker API |

**Returns:**
```json
{
  "findings": [
    {
      "container": "test-web-app",
      "severity": "critical",
      "pattern_name": "AWS_SECRET_KEY",
      "matched_text": "aws_secret_access_key=AKIAIOSFODNN7EXAMPLE",
      "redacted_text": "aws_secret_access_key=***REDACTED***",
      "line_number": 245,
      "timestamp": "2026-03-04T10:30:15Z",
      "recommendation": "Rotate AWS credentials immediately."
    }
  ],
  "scan_summary": {
    "total_findings": 1,
    "critical": 1,
    "high": 0,
    "medium": 0,
    "containers_scanned": 2,
    "cache_hits": { "test-web-app": true, "test-database": false }
  }
}
```

**Patterns detected (20 total):**

| Pattern | Severity |
|---------|----------|
| `AWS_SECRET_KEY`, `AWS_ACCESS_KEY` | critical |
| `GITHUB_TOKEN` | critical |
| `CREDIT_CARD`, `SSN`, `PRIVATE_KEY` | critical |
| `STRIPE_SECRET_KEY` | critical |
| `GENERIC_API_KEY`, `JWT_TOKEN`, `DATABASE_URL`, `BASIC_AUTH`, `PASSWORD_VAR` | high |
| `GOOGLE_API_KEY`, `STRIPE_PUBLISHABLE_KEY`, `AZURE_STORAGE_KEY`, `OAUTH_CLIENT_SECRET` | high |
| `EMAIL_ADDRESS`, `PHONE_NUMBER` | medium |
| `BASE64_SECRET`, `SESSION_COOKIE` | medium |

**Severity filter behavior:** `critical` only shows critical; `high` shows critical+high; `medium` shows all three; `all` shows everything.

---

## 5. correlate_containers

Detects correlated errors across containers within a configurable time window.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `time_window_seconds` | int | 30 | Co-occurrence window for error pairs |
| `tail` | int | 500 | Log lines per container |
| `use_cache` | bool | true | Check `.cache/logs/` before Docker API |

**Returns:**
```json
{
  "correlations": [
    {
      "container_a": "test-web-app",
      "container_b": "test-database",
      "correlation_score": 0.85,
      "co_occurrences": 17,
      "errors_a": 20,
      "errors_b": 18,
      "example_pairs": [
        {
          "a": "2026-03-04T10:45:32Z ERROR connection refused",
          "b": "2026-03-04T10:45:28Z ERROR database down",
          "delta_seconds": 4
        }
      ]
    }
  ],
  "cache_hits": { "test-web-app": true, "test-database": true },
  "correlation_cache_hit": false,
  "cached_at": "2026-03-06T10:30:00Z"
}
```

**Score:** `co_occurrences / errors_a` — fraction of container A errors with at least one co-occurring B error within window. Range: 0.0–1.0. Results sorted descending by score.

**Correlation result cache:** Results are cached to `.cache/correlations/<md5>.json` keyed by sorted container names + `time_window_seconds` + `tail`. TTL controlled by `CORRELATION_CACHE_TTL_MINUTES` (default 30 min). Set to `0` to disable. `correlation_cache_hit: true` when served from cache.

---

## 6. sync_docker_logs

Explicitly syncs Docker logs to `.cache/logs/` for a time window. Enables fast offline analysis and bug reproduction capture.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_names` | string[]? | — | Specific containers, or `null` for all running |
| `since` | string | `"24 hours ago"` | Window start: `"2 hours ago"`, `"7 days ago"`, ISO-8601 |
| `until` | string | `"now"` | Window end: `"now"` or ISO-8601 |
| `force_refresh` | bool | false | Delete existing cache and re-sync |

**Returns:**
```json
{
  "sync_result": {
    "test-web-app": {
      "lines_synced": 5000,
      "date_range": ["2026-03-04", "2026-03-03"],
      "status": "success",
      "time_window": { "since": "2026-03-02T10:00:00Z", "until": "2026-03-04T10:00:00Z" }
    }
  },
  "summary": {
    "total_lines_synced": 8200,
    "containers_synced": 2,
    "cache_directory": ".cache/logs/"
  }
}
```

**Time argument formats:** `"2 hours ago"`, `"30 minutes ago"`, `"7 days ago"`, `"2026-03-04T10:30:00Z"`, `"now"`

**Workflow:**
```bash
uv run docker-log-analyzer-mcp sync_docker_logs --since "4 hours ago"
# All subsequent tool calls use cache (instant):
uv run docker-log-analyzer-mcp analyze_patterns
uv run docker-log-analyzer-mcp detect_error_spikes
# Works with containers stopped:
docker compose down
uv run docker-log-analyzer-mcp correlate_containers  # still works
```

---

## 7. capture_and_analyze

Live capture for N seconds, then combined report: error spikes + cross-container correlation + per-container breakdown.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_names` | string[]? | — | Specific containers, or `null` for all running |
| `duration_seconds` | int | 120 | Live capture duration |
| `spike_threshold` | float | 2.0 | Spike detection multiplier |
| `time_window_seconds` | int | 30 | Correlation time window |
| `use_cache` | bool | true | Use cached logs if available |

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
    "test-web-app": { "spike_detected": true, "spike_ratio": 2.5 },
    "test-database": { "spike_detected": false }
  },
  "correlations": [
    { "container_a": "test-web-app", "container_b": "test-database", "correlation_score": 0.8 }
  ],
  "per_container_breakdown": {
    "test-web-app": { "total_lines": 450, "error_count": 80, "error_rate": 0.178 },
    "test-database": { "total_lines": 380, "error_count": 12, "error_rate": 0.032 }
  }
}
```

**Use case:** Bug reproduction — trigger a failure while this tool is capturing, then get an instant combined report.

---

## 8. map_service_dependencies

**Status:** Implemented — 2026-03-06

Infers a directed service dependency graph from log patterns. Surfaces cascade candidates by joining the dependency graph with temporal error correlation.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `containers` | string[]? | — | Specific containers, or `null` for all running |
| `tail` | int | 500 | Log lines per container |
| `include_transitive` | bool | false | Add one hop of transitive edges (A→B + B→C → A→C, labelled speculative) |
| `use_cache` | bool | true | Check `.cache/logs/` before Docker API |

**Returns:**
```json
{
  "status": "success",
  "dependencies": {
    "test-web-app": [
      {
        "target": "test-database",
        "inferred_from": "http_url",
        "confidence": "high",
        "hit_count": 42
      },
      {
        "target": "test-cache",
        "inferred_from": "redis_connection",
        "confidence": "high",
        "hit_count": 18
      }
    ],
    "test-gateway": [
      {
        "target": "test-web-app",
        "inferred_from": "http_url",
        "confidence": "high",
        "hit_count": 35
      }
    ]
  },
  "cascade_candidates": [
    {
      "from": "test-database",
      "to": "test-web-app",
      "dependency_type": "http_url",
      "correlation_score": 0.82,
      "confidence": "high",
      "evidence": "dependency_graph(high) + error_correlation(0.82)"
    }
  ],
  "cache_hits": { "test-web-app": true, "test-database": false },
  "parameters": { "containers": null, "tail": 500, "include_transitive": false }
}
```

**Dependency signals detected:**

| Signal | Example | Confidence |
|--------|---------|-----------|
| HTTP/HTTPS URL | `http://payment-service:8080/api/charge` | high |
| DB connection string | `postgres://db:5432`, `redis://cache:6379` | high |
| TCP dial with port | `dial tcp redis:6379: connection refused` | high |
| gRPC / dial call | `dialing order-service:50051` | medium |
| DNS lookup failure | `lookup redis: no such host` | medium |
| Container name mention | bare name delimited by separators (≥4 chars) | low |
| Transitive edge | A→B + B→C (computed) | low |

**Cascade candidate confidence:**

| Condition | Confidence |
|-----------|-----------|
| dep confidence high/medium AND correlation_score ≥ 0.5 | high |
| dep confidence high/medium AND correlation_score > 0 | medium |
| dep confidence low, or transitive edge | low |

**Differentiation from `correlate_containers`:**

| Tool | What it answers |
|------|----------------|
| `correlate_containers` | Did errors in A and B happen at the same time? (temporal) |
| `map_service_dependencies` | Does A's logs show it calls B? (structural) |
| Combined (via cascade_candidates) | A depends on B, errors correlate at r=0.82 — B likely causes A errors |

**Notes:**
- `hit_count` accumulates across log lines (one count per line that contains the signal)
- Self-loops (container depending on itself) are excluded
- Transitive edges are labelled `inferred_from="transitive"` and `hit_count=0`; guarded to known containers only
- Cascade candidates preserve direction — `db→api` and `api→db` are distinct entries
- Container name resolution uses longest-prefix match (e.g. `auth-service` wins over `auth`)
- Skips: `localhost`, `127.0.0.1`, `0.0.0.0`, `::1`

---

## 9. start_test_containers

Starts the 4-service test log generator stack from `docker-compose.test.yml`.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rebuild` | bool | false | Force rebuild images before starting |

**Returns:**
```json
{
  "status": "success",
  "containers_started": ["test-web-app", "test-database", "test-gateway", "test-cache"]
}
```

**Test services:**

| Service | Language | Log format | Spike interval | Notes |
|---------|----------|-----------|----------------|-------|
| `test-web-app` | Python | ISO-8601 | 90 s | Primary app |
| `test-database` | Java | syslog | 90 s | Correlated with web-app |
| `test-gateway` | Node.js | Apache | 90 s | Correlated with web-app |
| `test-cache` | Go | epoch | 120 s | Independent spikes |

---

## 10. stop_test_containers

Stops and removes test containers.

**Parameters:** None

**Returns:**
```json
{
  "status": "success",
  "containers_removed": ["test-web-app", "test-database", "test-gateway", "test-cache"]
}
```

---

## 11. rank_root_causes

**Status:** Implemented — 2026-03-07

Ranks containers by root-cause likelihood by combining dependency fan-in, error cascade paths, and spike timing. Internally orchestrates spike detection, correlation, and dependency graph analysis in a single call.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `containers` | string[]? | — | Specific containers, or `null` for all running |
| `tail` | int | 500 | Log lines per container |
| `time_window_seconds` | int | 3600 | Analysis window in seconds |
| `include_transitive` | bool | false | Include transitive edges in the dependency graph |
| `use_cache` | bool | true | Check `.cache/logs/` before Docker API |

**Returns:**
```json
{
  "status": "success",
  "root_causes": [
    { "container": "test-database", "score": 9.0 },
    { "container": "test-cache",    "score": 4.0 },
    { "container": "test-web-app",  "score": 2.0 }
  ],
  "analysis_inputs": {
    "containers_analyzed": 4,
    "spikes_detected": 3,
    "cascade_candidates": 5,
    "dependency_edges": 8
  },
  "cache_hits": { "test-web-app": true, "test-database": false },
  "parameters": {
    "containers": null,
    "tail": 500,
    "time_window_seconds": 3600,
    "include_transitive": false
  }
}
```

**Scoring algorithm (summary):**

| Signal | Weight | Notes |
|--------|--------|-------|
| Fan-in (services depending on this container) | +2.0 per dependent | From dependency graph |
| Cascade origin (correlation score × weight) | +3.0 × `correlation_score` | From cascade candidates |
| Spiked before a dependent container | +4.0 | ISO-8601 string comparison |
| Fan-out (outbound dependencies) | −1.0 per edge | Followers penalised, not leaders |

Scores are rounded to 3 decimal places and sorted descending. Containers with no signals are not included in results.

**Copilot workflow:**
```
User: "Find the root cause of my system failure."

Copilot calls:
1. detect_error_spikes      → confirm which containers have errors
2. correlate_containers     → confirm temporal co-occurrence
3. map_service_dependencies → understand error propagation paths
4. rank_root_causes         → get scored ranking
```

**Notes:**
- Scores are relative, not absolute — compare ranks within a result set
- Containers only appear in results if they contributed to at least one score signal

---

tool, MCP, parameters, returns, list_containers, analyze_patterns, detect_error_spikes, detect_data_leaks, correlate_containers, sync_docker_logs, capture_and_analyze, map_service_dependencies, rank_root_causes, start_test_containers, stop_test_containers, reference, contract, schema, tail, use_cache, confidence, hit_count, cascade, dependency, spike, correlation, secret, pattern, root cause, scoring, fan-in, fan-out

**[negative keywords / not-this-doc]**
algorithm internals, module design, CI, coverage, test suite, setup, installation, Copilot prompts

---

## See also

- Algorithm internals: [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md)
- Copilot prompts for each tool: [WIKI_OPERATIONS.md § Copilot Prompts](WIKI_OPERATIONS.md#copilot-prompts)
- Quality & testing: [WIKI_QUALITY.md](WIKI_QUALITY.md)
- Home: [WIKI_HOME.md](WIKI_HOME.md)
