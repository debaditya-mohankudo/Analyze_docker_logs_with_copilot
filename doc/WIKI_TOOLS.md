---
tags: [tool, mcp, parameters, returns, reference, contract, schema]
last_updated: 2026-07-03
---

# Wiki Hub: MCP Tools Reference

Canonical reference for all 18 MCP tools — parameters, return shapes, and behavior.

---

## Agent Use Rules

- Use this page for "what does tool X do", "what parameters does X accept", "what does X return".
- For algorithm internals, see [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md).
- For Copilot prompts that invoke these tools, see [WIKI_COPILOT_PROMPTS.md](WIKI_COPILOT_PROMPTS.md).

### Tool Selection Flow (Copilot)

Use this flow to choose the first tool with high confidence, then branch deeper only if needed.

```mermaid
flowchart LR
  A[Need to investigate logs] --> B{Know target container?}
  B -- No --> L[list_containers]
  L --> B

  B -- Yes --> C{Primary question}

  C -- "Last error now" --> G[get_last_errors]
  C -- "What changed in runtime behavior?" --> P[analyze_patterns]
  C -- "Did error rate spike?" --> S[analyze_error_spikes]
  C -- "Are services failing together?" --> R[analyze_correlations]
  C -- "Is this a data leak/security issue?" --> D[detect_data_leaks]
  C -- "What depends on what?" --> M[map_service_dependencies]
  C -- "Likely root cause service?" --> RC[analyze_root_causes]
  C -- "Need fresh reproducible window?" --> CL[capture_logs]
  C -- "Need offline/fast cache first?" --> SY[sync_docker_logs]
  C -- "Need stack trace to source code" --> CC[analyze_code_context]
  C -- "Need request-level journey" --> TR[trace_request_flow]
  C -- "Need semantic error grouping" --> CE[classify_errors]
  C -- "Not sure where to start" --> PI[plan_investigation]

  PI --> S
  PI --> R
  PI --> M
  PI --> RC

  S --> CE
  R --> M
  M --> RC
  G --> CC
  CL --> S
  CL --> R
```

Confidence guidance:

- High confidence direct mapping: use the tool linked from the primary question above.
- Medium confidence broad incident: start with `plan_investigation`, then execute top steps.
- High-cardinality or historic windows: run `sync_docker_logs` first to improve repeatability and speed.

---

## Tool Index

| # | Tool | Purpose |
|---|------|---------|
| 1 | [list_containers](#1-list_containers) | List running Docker containers |
| 2 | [analyze_patterns](#2-analyze_patterns) | Timestamp format, language, log levels, health checks |
| 3 | [analyze_error_spikes](#3-analyze_error_spikes) | Rolling-window error rate anomaly detection |
| 4 | [detect_data_leaks](#4-detect_data_leaks) | Scan logs for secrets, credentials, PII |
| 5 | [analyze_correlations](#5-analyze_correlations) | Pairwise temporal error co-occurrence scoring |
| 6 | [sync_docker_logs](#6-sync_docker_logs) | Sync logs to cache for offline / fast analysis |
| 7 | [capture_logs](#7-capture_logs) | Live capture + combined spike + correlation report |
| 8 | [map_service_dependencies](#8-map_service_dependencies) | Log-based dependency graph + cascade candidates |
| 9 | [start_test_containers](#9-start_test_containers) | Start 4-service test stack |
| 10 | [stop_test_containers](#10-stop_test_containers) | Stop and remove test containers |
| 11 | [analyze_root_causes](#11-analyze_root_causes) | Score containers by root-cause likelihood |
| 12 | [get_last_errors](#12-get_last_errors) | Last N error/fatal lines from a single container |
| 13 | [plan_investigation](#13-plan_investigation) | Generate a structured investigation plan from symptoms |
| 14 | [analyze_code_context](#14-analyze_code_context) | Parse stack traces + surface source code around error lines |
| 15 | [trace_request_flow](#15-trace_request_flow) | Trace individual request IDs across container boundaries |
| 16 | [classify_errors](#16-classify_errors) | Categorise errors into semantic classes (database, network, timeout, etc.) |
| 17 | [cache_info](#17-cache_info) | Summarize the local log cache — files, dates, size, last sync per container |
| 18 | [clear_cache](#18-clear_cache) | Delete cached Parquet log files, forcing fresh fetches |

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

Analyzes log patterns to detect timestamp format, programming language, web framework, log levels, health checks, and top errors.

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
      "timestamp_format": "iso8601",
      "language": "java",
      "language_confidence": 0.92,
      "framework": "spring",
      "log_levels": { "INFO": 450, "ERROR": 40, "WARN": 10 },
      "health_check": { "detected": true, "pattern": "GET /health", "frequency_per_minute": 2.0 },
      "common_errors": [{ "pattern": "Connection refused", "count": 5 }],
      "logs_cache_hit": true,
      "analyzed_at": "2026-03-04T10:30:00Z"
    }
  }
}
```

**Detection capabilities:**

- **Timestamps:** ISO-8601, syslog, epoch (Unix), Apache HTTP, Nginx (`YYYY/MM/DD HH:MM:SS`)
- **Languages:** Python, Java, Go, Node.js, PHP, Nginx, generic/unknown
- **Java frameworks** (`"framework"` field): Spring, Quarkus, Micronaut, Vert.x, Helidon, WildFly, Dropwizard — `null` if undetected
- **Log levels:** standard (`INFO`, `DEBUG`, `WARN`, `ERROR`, `CRITICAL`, `FATAL`, `TRACE`, `SEVERE`) + Nginx bracketed (`[error]`, `[warn]`, `[crit]`, `[alert]`, `[emerg]`, `[notice]`)
- **Java patterns:** Spring (DI, MVC, Security, Boot startup), Cassandra/DataStax (v3 + v4), Apache Kafka client
- **PHP patterns:** Slim Framework (HTTP exceptions, routing, middleware, FastRoute), MySQL/PDO errors, php-rdkafka Kafka errors
- **Nginx patterns:** upstream failures, SSL handshake, FastCGI stderr, oversized body, missing file
- **Health checks:** Repeating patterns (e.g., `/health`, `/ping`, `/readiness`)

**Cache behavior:**
1. First call: parses N lines, stores result to `.cache/patterns/<container>.json`
2. Subsequent calls: instant response from pattern cache
3. Cache persists across container restarts (keyed by name only)
4. `force_refresh=true` skips cache and re-analyzes

---

## 3. analyze_error_spikes

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

## 5. analyze_correlations

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
| `since` | string? | 24 hours ago | Window start as ISO-8601 UTC, e.g. `"2026-03-04T10:00:00Z"`. Omit to default to 24h ago. |
| `until` | string? | now | Window end as ISO-8601 UTC. Omit to default to now. |
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

**Time format:** ISO-8601 UTC only (e.g. `"2026-03-04T10:30:00Z"`). The Copilot agent resolves natural language expressions like "4 hours ago" to ISO-8601 before calling this tool.

**Workflow:**
```bash
uv run docker-log-analyzer-mcp sync_docker_logs --since "2026-03-07T10:00:00Z"
# All subsequent tool calls use cache (instant):
uv run docker-log-analyzer-mcp analyze_patterns
uv run docker-log-analyzer-mcp analyze_error_spikes
# Works with containers stopped:
docker compose down
uv run docker-log-analyzer-mcp analyze_correlations  # still works
```

---

## 7. capture_logs

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

**Differentiation from `analyze_correlations`:**

| Tool | What it answers |
|------|----------------|
| `analyze_correlations` | Did errors in A and B happen at the same time? (temporal) |
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

## 11. analyze_root_causes

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
1. analyze_error_spikes      → confirm which containers have errors
2. analyze_correlations     → confirm temporal co-occurrence
3. map_service_dependencies → understand error propagation paths
4. analyze_root_causes         → get scored ranking
```

**Notes:**
- Scores are relative, not absolute — compare ranks within a result set
- Containers only appear in results if they contributed to at least one score signal

---

---

## 12. get_last_errors

Fast triage tool — returns the last N error, fatal, or panic lines from a single container without running full spike or pattern analysis.

**Parameters:**

| Parameter | Type | Default | Description |
| --------- | ---- | ------- | ----------- |
| `container_name` | string | — | Target container name (required) |
| `tail` | int | 200 | Recent log lines to scan |
| `limit` | int | 10 | Maximum error entries to return |

**Returns:**

```json
{
  "status": "success",
  "container": "test-cache",
  "errors_found": 12,
  "limit": 10,
  "errors": [
    {
      "timestamp": "2026-03-07T16:04:51Z",
      "level": "fatal",
      "message": "2026-03-07T16:04:51Z level=fatal msg=\"panic: runtime error: index out of range [5] with length 3\""
    },
    {
      "timestamp": "2026-03-07T16:05:23Z",
      "level": "error",
      "message": "2026-03-07T16:05:23Z level=error msg=\"Request timeout\" path=/api/data timeout=30s"
    }
  ]
}
```

**Level classification:**

| Detected keyword | Reported `level` |
| ---------------- | ---------------- |
| `fatal`, `panic` | `fatal` |
| `critical` | `critical` |
| `error`, `exception`, `traceback`, `severe` | `error` |

**Error patterns matched:** ERROR, CRITICAL, FATAL, Exception, Traceback, panic, SEVERE, HTTP 5xx (same regex as `analyze_error_spikes`).

**`errors_found`** is the total count of matching lines in the scanned window; `errors` contains only the last `limit` of them in chronological order.

**Use case:** Quickest way to answer "what broke in container X?" — single tool call, no duration or multi-container overhead.

**Differentiation from other tools:**

| Tool | Scope | When to use |
| ---- | ----- | ----------- |
| `get_last_errors` | Single container, last N lines | Immediate triage of one container |
| `analyze_error_spikes` | All containers, rolling window | Confirm error rate anomaly |
| `analyze_root_causes` | All containers, full analysis | Find which container caused the failure |

---

---

## 13. plan_investigation

Deterministic, rule-based DevOps investigation planner. Classifies symptom
descriptions into signal categories and generates an ordered investigation plan
mapped to available MCP tools. Returns the structured plan (list of steps) in
the JSON response **and** saves a human-readable Markdown version to
`.cache/plans/`. Use `plan` for automated execution; open `plan_file` for the
formatted table.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symptoms` | string[] | required | Observed problem descriptions in plain English |
| `containers` | string[]? | — | Limit scope to specific containers; omit for all |
| `focus` | string? | `"general"` | One of `root_cause`, `security`, `performance`, `general` |

**Focus modes:**

| Focus | Signal categories used | When to use |
|-------|----------------------|-------------|
| `root_cause` | crash, cascade, spike | System-wide failure, cascading errors |
| `security` | security | Suspected secret/credential exposure |
| `performance` | spike | Latency, throughput, or resource pressure |
| `general` | all detected | Unknown issue, broad sweep |

**Returns:**

```json
{
  "status": "success",
  "signals_detected": ["cascade", "crash"],
  "focus": "root_cause",
  "containers_in_scope": ["payment-service", "api-gateway"],
  "step_count": 7,
  "plan": [
    {
      "step": 1,
      "action": "list_containers",
      "reason": "Discover which containers are running to scope the investigation"
    },
    {
      "step": 2,
      "action": "get_last_errors",
      "target": "payment-service",
      "reason": "Extract recent error and fatal log lines from payment-service to characterize the failure mode",
      "parameters": { "container_name": "payment-service", "limit": 20 }
    }
  ],
  "plan_file": ".cache/plans/20260311T120000Z_root_cause_payment-service_api-gateway.md"
}
```

**Plan file:** A Markdown table of all steps (action, target, reason, parameters), symptom summary, and tool reference is written to `plan_file`. Open it to read the full plan.

**Step priority order:**

| Priority | Action | Triggered by |
|----------|--------|-------------|
| 1 | `list_containers` | Always |
| 2 | `analyze_patterns` | crash, pattern, root_cause, general |
| 3 | `get_last_errors` | crash, cascade, root_cause, general |
| 4 | `analyze_error_spikes` | spike, crash, performance, root_cause, general |
| 5 | `analyze_correlations` | cascade, spike, root_cause, general |
| 6 | `map_service_dependencies` | cascade, root_cause, general |
| 7 | `detect_data_leaks` | security focus |
| 8 | `analyze_root_causes` | root_cause, general, cascade |

**Signal detection keywords:**

| Signal | Example keywords |
|--------|----------------|
| `crash` | error, 500, exception, traceback, panic, fatal, OOM |
| `spike` | latency, slow, timeout, burst, high load, CPU, memory |
| `cascade` | connection refused, downstream, upstream, circuit-breaker |
| `security` | token, credential, API key, password, PII, 401, 403 |
| `pattern` | log level, timestamp, health-check, format |

**Implementation:** `docker_log_analyzer/investigation_planner.py` — see [WIKI_INVESTIGATION_PLANNER.md](WIKI_INVESTIGATION_PLANNER.md) for full reference.

---

## 14. analyze_code_context

Bridges container error logs and local source code. Parses stack traces from
recent error logs, resolves each frame's file path against a configured
repository root, and returns the surrounding source lines for immediate
code-level context.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_name` | string | required | Target container |
| `tail` | int | 200 | Log lines to scan for stack traces |
| `context_lines` | int? | config (10) | Source lines before/after the error line |
| `max_frames` | int? | config (10) | Maximum stack frames to return |
| `repo_path` | string? | — | Explicit repo root — overrides all config |
| `language` | string? | auto | Force parser: `python`, `java`, `go`, `nodejs` |

**Repository configuration (`.env`):**

```env
REPO_PATHS=["/home/user/myapp", "/srv/api"]
CONTAINER_REPO_MAP={"api-service": "/home/user/api", "worker": "/home/user/worker"}
CODE_CONTEXT_LINES=10
MAX_STACK_FRAMES=10
```

**Repository resolution order:**

1. `repo_path` parameter (highest priority)
2. `CONTAINER_REPO_MAP` exact match
3. `CONTAINER_REPO_MAP` prefix match
4. First valid path in `REPO_PATHS`

**Returns:**

```json
{
  "status": "success",
  "container": "payment-service",
  "language": "python",
  "repo_root": "/home/user/payment-service",
  "frames_found": 2,
  "frames": [
    {
      "language": "python",
      "raw_frame": "  File \"app/payments.py\", line 87, in charge_card",
      "function": "charge_card",
      "file_in_log": "app/payments.py",
      "line_no": 87,
      "resolved_file": "/home/user/payment-service/app/payments.py",
      "code_context": {
        "file": "/home/user/payment-service/app/payments.py",
        "error_line": 87,
        "before": [[85, "    amount = request.json['amount']"], [86, "    card = ..."]],
        "at": [87, "    result = stripe.Charge.create(amount=amount, customer=card.id)"],
        "after": [[88, "    return jsonify({'status': 'ok'})"]]
      }
    }
  ],
  "unresolved_files": [],
  "warnings": []
}
```

**Status values:**

| Status | Meaning |
|--------|---------|
| `success` | Frames found; code context present if repo configured |
| `no_frames` | No stack traces detected in error logs |
| `error` | Container not found or Docker unavailable |

**Supported stack trace formats:**

| Language | Example |
|----------|---------|
| Python | `File "app/server.py", line 42, in handle_request` |
| Java | `at com.example.Service.process(Service.java:123)` |
| Go | `/home/user/app/main.go:42 +0x1a3` |
| Node.js | `at handleRequest (/app/server.js:42:10)` |

**Use case:** After `get_last_errors` or `analyze_error_spikes` identifies the
failing container, call this tool to immediately see the failing lines of code
without leaving the Copilot chat. Also the final step in `plan_investigation`
when containers are explicitly scoped.

**Full reference:** [WIKI_CODE_REPO.md](WIKI_CODE_REPO.md)

---

## 15. trace_request_flow

**Status:** Implemented — 2026-03-13

Traces individual request flows across container boundaries by extracting request/trace/correlation IDs from log lines and assembling per-request chronological timelines. Useful when you need to follow a specific HTTP request, transaction, or job as it propagates through multiple microservices.

**Parameters:**

| Parameter          | Type       | Default | Description                                                          |
|--------------------|------------|---------|----------------------------------------------------------------------|
| `container_names`  | string[]?  | —       | Containers to scan; omit for all running containers                  |
| `tail`             | int        | 500     | Log lines to fetch per container                                     |
| `min_events`       | int        | 2       | Minimum event count per request ID — filters single-occurrence noise |
| `max_requests`     | int        | 50      | Maximum timelines to return, sorted by event count descending        |

**ID pattern configuration (`.env`):**

```env
# Named patterns — each value must contain exactly one capture group
REQUEST_ID_PATTERNS={"request_id": "request[_-]?id[=:]([\\w-]+)", "trace_id": "traceId=([\\w-]+)"}
```

Default patterns cover 5 ID concepts — `request_id`, `trace_id`,
`correlation_id`, `transaction_id`, `session_id` — each with two variants:
a **strict** pattern requiring a well-formed UUID value, and a **loose**
`*_loose` fallback accepting any 4–64 char alphanumeric/hyphen token (short
numeric IDs, base62/nanoid IDs, raw hex trace IDs). Both match
`key[=:\s]+value` separators (`key=value`, `key: value`, `key:value`,
header-style `X-Request-Id: <id>`) — **not** quoted-JSON-key format like
`"requestId":"<id>"`, since the closing quote breaks the separator match.
Full detail: [WIKI_TRACE_REQUEST_FLOW.md](WIKI_TRACE_REQUEST_FLOW.md).

**Returns:**

```json
{
  "status": "success",
  "request_count": 3,
  "timelines": [
    {
      "id_value": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
      "id_patterns": ["request_id", "trace_id"],
      "containers": ["gateway", "web-app", "database"],
      "event_count": 4,
      "first_seen": "2026-03-13T10:01:00.123Z",
      "last_seen": "2026-03-13T10:01:00.465Z",
      "duration_ms": 342.0,
      "events": [
        {
          "container": "gateway",
          "timestamp": "2026-03-13T10:01:00.123Z",
          "pattern_name": "request_id",
          "message": "POST /api/order request_id=f47ac10b-58cc-4372-a567-0e02b2c3d479 status=200"
        }
      ]
    }
  ],
  "containers_scanned": ["gateway", "web-app", "database"],
  "cache_hits": { "gateway": true, "web-app": false, "database": true },
  "parameters": { "tail": 500, "min_events": 2, "max_requests": 50 }
}
```

**Field notes:**

- Cross-container stitching is already done for you: each timeline is grouped
  by the literal ID **value**, regardless of which pattern or container
  produced it — `containers` lists every container the ID appeared in, and
  `events` (each tagged with its own `container` field) is the merged,
  chronologically-sorted timeline across all of them. No manual grouping needed.
- `request_count` — total timelines found before `max_requests` truncation
- `id_patterns` — every pattern name that matched this ID value (e.g. both
  `request_id` and `transaction_id` if the same UUID appeared under both keys)
- A timeline is dropped entirely if its events span more than
  `TRACE_WINDOW_SECONDS` (default 120s) — treated as an accidental ID collision
  rather than one real request
- `duration_ms` — milliseconds between the first and last event across all
  containers for that ID; `null` if no parseable timestamps
- `first_seen` / `last_seen` — ISO-8601 UTC with millisecond precision; `null` if no timestamps
- `events` — sorted chronologically across containers; capped at 500 characters per message

**Differentiation from `analyze_correlations`:**

| Tool                   | What it answers                                                                 |
|------------------------|---------------------------------------------------------------------------------|
| `analyze_correlations` | Did errors in A and B happen at the same time? (temporal, statistical)          |
| `trace_request_flow`   | Did *this specific request* touch A and then B? (request-level, causal)         |

**Use case:** After `analyze_correlations` shows containers A and B are correlated, use `trace_request_flow` to confirm by finding request IDs that appear in both containers' logs — turning statistical correlation into causal evidence.

**Notes:**

- Requires logs to contain request IDs — if none are found, `timelines` is empty and `request_count` is 0
- Pattern matching is case-insensitive
- Invalid regex patterns in `REQUEST_ID_PATTERNS` are skipped with a warning (server does not crash)
- Per-container scope: cross-container stitching is the caller's responsibility (group by `request_id`)

---

## 16. classify_errors

**Status:** Implemented — 2026-03-13

Classifies error log lines into semantic categories using rule-based regex matching. Answers "what *kind* of errors are happening?" — turning a raw error count into an actionable breakdown by failure mode.

**Parameters:**

| Parameter          | Type       | Default | Description                                                |
|--------------------|------------|---------|------------------------------------------------------------|
| `container_names`  | string[]?  | —       | Containers to scan; omit for all running containers        |
| `tail`             | int        | 1000    | Log lines to fetch per container                           |
| `categories`       | string[]?  | —       | Filter to specific categories (e.g. `["database","timeout"]`) |

**Error categories (checked in specificity order):**

| Category        | Example patterns                                                          | Recommendation |
|-----------------|---------------------------------------------------------------------------|----------------|
| `database`      | `deadlock detected`, `too many connections`, `PSQLException`, port 5432/3306/6379 | Check connection pool limits and DB health |
| `network`       | `ECONNREFUSED`, `DNS resolution failed`, `socket hang up`, `no such host` | Check network connectivity and DNS |
| `timeout`       | `read timeout`, `gateway timeout`, `504`, `context deadline exceeded`     | Review upstream latency and timeout configs |
| `auth`          | `401 Unauthorized`, `403 Forbidden`, `invalid token`, `JWT expired`       | Verify credentials and token validity |
| `oom`           | `OOMKilled`, `OutOfMemoryError`, `heap space`, `MemoryError`             | Inspect memory limits and fix leaks |
| `disk`          | `no space left on device`, `ENOSPC`, `disk quota exceeded`               | Check disk usage and log rotation |
| `rate_limit`    | `429 Too Many Requests`, `rate limit exceeded`, `throttled`               | Implement backoff/retry and review limits |
| `configuration` | `missing required`, `invalid config`, `environment variable not set`      | Verify env vars and config files |
| `application`   | `NullPointerException`, `TypeError`, `panic:`, `unhandled exception`      | Review application code at stack trace |
| `unknown`       | Error lines matching no specific category                                 | Inspect sample lines manually |

**Returns:**

```json
{
  "status": "success",
  "containers": {
    "web-app": {
      "total_errors": 142,
      "categories": {
        "database": {
          "count": 89,
          "percentage": 62.7,
          "first_seen": "2026-03-13T10:01:00.123Z",
          "last_seen": "2026-03-13T10:05:23.456Z",
          "samples": ["ERROR deadlock detected in transaction 42"],
          "recommendation": "Check database connection pool limits..."
        },
        "timeout": {
          "count": 31,
          "percentage": 21.8,
          "first_seen": "...",
          "last_seen": "...",
          "samples": ["..."],
          "recommendation": "..."
        }
      },
      "dominant_category": "database",
      "category_timeline": [
        {"minute": "2026-03-13T10:01", "database": 12, "timeout": 3}
      ]
    }
  },
  "summary": {
    "total_errors": 142,
    "dominant_category": "database",
    "categories": { "...same shape as per-container..." }
  },
  "containers_scanned": ["web-app"],
  "cache_hits": { "web-app": true },
  "parameters": { "tail": 1000, "categories": null }
}
```

**Differentiation from `analyze_error_spikes`:**

| Tool                   | What it answers                                             |
|------------------------|-------------------------------------------------------------|
| `analyze_error_spikes` | *When* did error rates spike? (temporal anomaly detection)  |
| `classify_errors`      | *What kind* of errors are happening? (semantic breakdown)   |

**Use case:** After `analyze_error_spikes` confirms a spike, call `classify_errors` to understand whether it's database timeouts, auth failures, or application crashes — then use the recommendation to guide remediation.

**Notes:**

- Categories are checked in specificity order: `database` matches before `network` (DB timeouts are a subset of network issues)
- `unknown` catches error lines that match ERROR_PATTERN_RE but no specific category
- Non-error lines (INFO, DEBUG) are silently skipped
- Invalid category names in the `categories` filter return an error with the list of valid categories
- `summary` aggregates across all scanned containers; `containers` gives per-container breakdowns

---

## 17. cache_info

Reports the current state of the local log cache (`.cache/logs/`) — files present, dates covered, total lines, disk usage, and last sync time, per container. Useful for checking cache coverage/staleness before running analysis tools, or diagnosing why a tool returned fewer logs than expected.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_name` | string? | `None` | Specific container to inspect; omit for all cached containers |

**Returns:**

```json
{
  "status": "success",
  "containers": [
    {
      "container": "test-web-app",
      "parquet_files": 3,
      "dates_cached": ["2026-07-01", "2026-07-02", "2026-07-03"],
      "total_lines": 15234,
      "size_bytes": 204800,
      "size_kb": 200.0,
      "last_synced": "2026-07-03T04:12:00+00:00"
    }
  ],
  "total_size_bytes": 204800,
  "total_size_kb": 200.0
}
```

**Notes:**

- Discovers containers from both `metadata.json` and the on-disk directory structure, so it still reports correctly if metadata is missing/corrupted
- There is no age/staleness field in the response — the cache itself has no TTL concept (see [WIKI_OPERATIONS.md § Log Cache Strategy](WIKI_OPERATIONS.md#log-cache-strategy)); `last_synced` is informational only
- Returns an empty `containers` list (not an error) if the requested `container_name` has never been cached

---

## 18. clear_cache

Deletes cached Parquet log files for one or all containers, forcing the next analysis tool call to fetch fresh logs from the Docker API.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_name` | string? | `None` | Specific container to clear; omit to clear the entire log cache |

**Returns:**

```json
{
  "status": "success",
  "cleared_containers": ["test-web-app"],
  "bytes_freed": 204800,
  "kb_freed": 200.0
}
```

**Notes:**

- Also removes the container's entry from `metadata.json` when clearing a single container
- Idempotent — clearing a container with no cache returns `cleared_containers: []`, `bytes_freed: 0`, not an error
- Equivalent to the shell commands in [WIKI_OPERATIONS.md § Clear cache](WIKI_OPERATIONS.md#clear-cache), but callable directly from Copilot Agent Mode without a terminal

---

tool, MCP, parameters, returns, list_containers, analyze_patterns, analyze_error_spikes, detect_data_leaks, analyze_correlations, sync_docker_logs, capture_logs, map_service_dependencies, analyze_root_causes, get_last_errors, plan_investigation, start_test_containers, stop_test_containers, trace_request_flow, classify_errors, cache_info, clear_cache, reference, contract, schema, tail, use_cache, confidence, hit_count, cascade, dependency, spike, correlation, secret, pattern, root cause, scoring, fan-in, fan-out, last error, fatal, panic, triage, investigation, planner, symptoms, signals, focus, request id, trace id, correlation id, request tracing, error classification, semantic, category, database, network, timeout, auth, oom, disk, rate limit, configuration, application, cache info, clear cache, cache size, cache staleness

**[negative keywords / not-this-doc]**
algorithm internals, module design, CI, coverage, test suite, setup, installation, Copilot prompts

---

## See also

- Algorithm internals: [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md)
- Copilot prompts for each tool: [WIKI_OPERATIONS.md § Copilot Prompts](WIKI_OPERATIONS.md#copilot-prompts)
- Quality & testing: [WIKI_QUALITY.md](WIKI_QUALITY.md)
- Home: [WIKI_HOME.md](WIKI_HOME.md)
