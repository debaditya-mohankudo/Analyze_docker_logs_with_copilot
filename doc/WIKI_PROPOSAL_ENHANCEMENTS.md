---
tags: [proposal, enhancements, historical, trace_request_flow, classify_errors]
last_updated: 2026-07-03
---

# Proposed Enhancements — Docker Log Analyzer MCP Server

**Date:** 2026-03-13
**Status:** Enhancements 1 & 2 implemented (2026-03-13) — Enhancement 3 deferred

> **Historical note (2026-07-03):** the tool signatures and JSON schemas below
> are the *original design*, not the shipped API. `trace_request_flow` in
> particular changed substantially during implementation — different
> parameter names (`min_events`/`max_requests` vs. proposed
> `request_id`/`failed_only`/`limit`), a completely different response shape
> (`id_value`/`containers`/`events` vs. proposed `request_id`/`containers_touched`/`timeline`/`status`),
> and no `failed_only` filtering concept at all. Neither tool is `async def`
> as shown here — all MCP tools in this project are plain `def`. For the
> **authoritative current signature**, see
> [WIKI_TOOLS.md § trace_request_flow](WIKI_TOOLS.md#15-trace_request_flow) /
> [WIKI_TRACE_REQUEST_FLOW.md](WIKI_TRACE_REQUEST_FLOW.md) and
> [WIKI_TOOLS.md § classify_errors](WIKI_TOOLS.md#16-classify_errors) — not this page.
> The cross-tool integrations described under "Integration with Existing
> Tools" (root-cause category weighting, spike-bucket dominant category,
> plan_investigation category keywords) were **not implemented** — only the
> two standalone tools shipped.

---

## Overview

Three enhancements selected for maximum impact within the project's design
constraints (stateless, deterministic, local-only, no LLMs). Each builds on
existing modules and fills a documented gap.

| # | Enhancement | New Tool | Modules Touched | Effort | Status |
|---|-------------|----------|-----------------|--------|--------|
| 1 | Request ID Correlation Tracing | `trace_request_flow` | new `request_tracer.py`, `tools.py`, `mcp_server.py` | Medium | ✅ Implemented 2026-03-13 |
| 2 | Semantic Error Classification | `classify_errors` | new `error_classifier.py`, `tools.py`, `mcp_server.py` | Medium | ✅ Implemented 2026-03-13 |
| 3 | Mermaid Architecture Diagram Export | `export_dependency_graph` | `dependency_mapper.py`, `tools.py`, `mcp_server.py` | Low | 🔜 Deferred |

---

## Enhancement 1: Request ID Correlation Tracing

### Problem

Current cross-container correlation (`correlate_containers`) relies solely on
**temporal co-occurrence** — errors in container A within ±N seconds of errors
in container B. This answers "did A and B fail around the same time?" but
cannot answer **"did a specific request fail as it traversed A → B → C?"**

Without request-level tracing, operators must manually grep for request IDs
across multiple containers. In a 4-service stack this is tedious; in a 12-service
stack it is impractical.

### Proposed Solution

A new `request_tracer.py` module and `trace_request_flow` MCP tool that:

1. **Extracts request/correlation IDs** from log lines using configurable regex
   patterns for common formats:
   - `X-Request-Id: <uuid>` / `x-request-id=<uuid>` (HTTP headers logged)
   - `request_id=<uuid>` / `req_id=<uuid>` / `correlation_id=<uuid>` (structured KV)
   - `[<uuid>]` bracketed trace IDs (common in Java/Spring)
   - JSON fields: `"requestId":"<uuid>"`, `"traceId":"<hex>"`

2. **Groups log lines by request ID** across all (or specified) containers,
   producing a per-request timeline:
   ```json
   {
     "request_id": "abc-123",
     "containers_touched": ["gateway", "web-app", "database"],
     "timeline": [
       {"container": "gateway",  "timestamp": "...", "message": "POST /api/order"},
       {"container": "web-app",  "timestamp": "...", "message": "Processing order"},
       {"container": "database", "timestamp": "...", "message": "ERROR: deadlock detected"}
     ],
     "status": "failed",
     "error_container": "database",
     "total_duration_ms": 342
   }
   ```

3. **Filters to failed requests** by default (requests touching at least one
   ERROR/FATAL line), with option to show all.

4. **Computes per-service latency** (time between first and last log line in
   each container for that request ID).

### Tool Signature

```python
@mcp.tool()
async def trace_request_flow(
    container_names: list[str] | None = None,   # None = all running
    tail: int = 1000,
    request_id: str | None = None,              # trace a specific ID
    failed_only: bool = True,                   # only show errored requests
    limit: int = 20,                            # max requests to return
    use_cache: bool = True,
) -> dict: ...
```

### Architecture

```
trace_request_flow (tools.py)
    │
    ├── _fetch_logs_with_cache()          # existing cache-first log fetch
    ├── request_tracer.extract_ids()      # new: regex ID extraction per line
    ├── request_tracer.group_by_request() # new: cross-container grouping
    ├── request_tracer.build_timelines()  # new: sort by timestamp, compute durations
    └── return JSON
```

**Key design decisions:**
- Regex-only ID extraction (no OpenTelemetry SDK dependency) — consistent with
  the project's deterministic, local-only philosophy.
- ID patterns configurable via `config.py` (`request_id_patterns: list[str]`).
- Reuses `_fetch_logs_with_cache()` — no new data source.
- Polars DataFrame for grouping/sorting (vectorized, consistent with codebase).

### Integration with Existing Tools

- **`analyze_root_causes`** can use request traces as a new signal: if container C
  is the `error_container` in >50% of failed requests, boost its root-cause score.
- **`plan_investigation`** can detect "trace" signal keywords and recommend
  `trace_request_flow` as the first tool in its investigation plan.
- **`analyze_correlations`** results can be validated against request traces:
  high temporal correlation + shared request IDs = strong causal link.

### Test Plan

- Unit tests: ID extraction across all pattern formats, grouping logic,
  timeline ordering, duration computation, failed-only filtering.
- Contract tests: return-structure verification (`frozenset` key assertions).
- Integration tests: spin up test containers with injected request IDs,
  verify cross-container trace reconstruction.

---

## Enhancement 2: Semantic Error Classification

### Problem

The current analysis pipeline treats **all errors as equivalent**. A database
connection timeout, an authentication failure, an OOM kill, and a null pointer
exception all contribute the same weight to spike detection, correlation scoring,
and root cause ranking.

This means:
- `analyze_error_spikes` cannot distinguish "spike in auth errors" from "spike
  in timeout errors" — the operator sees a count but not a category.
- `analyze_root_causes` weights a 500-error/min database timeout spike the same
  as a 500-error/min validation error spike, even though the former is far more
  likely to be a root cause.
- Operators must manually read error samples to understand the failure mode.

### Proposed Solution

A new `error_classifier.py` module and `classify_errors` MCP tool that:

1. **Categorizes each error line** into one of these semantic classes using
   rule-based regex matching (no LLMs):

   | Category | Example Patterns |
   |----------|-----------------|
   | `database` | `connection refused.*5432`, `deadlock detected`, `too many connections`, `query timeout` |
   | `network` | `connection timed out`, `DNS resolution failed`, `ECONNREFUSED`, `socket hang up` |
   | `timeout` | `read timeout`, `gateway timeout`, `504`, `context deadline exceeded` |
   | `auth` | `401 Unauthorized`, `403 Forbidden`, `invalid token`, `authentication failed` |
   | `oom` | `OOMKilled`, `out of memory`, `heap space`, `GC overhead limit` |
   | `disk` | `no space left on device`, `disk quota exceeded`, `ENOSPC` |
   | `application` | `NullPointerException`, `TypeError`, `panic:`, `unhandled exception` |
   | `rate_limit` | `429 Too Many Requests`, `rate limit exceeded`, `throttled` |
   | `configuration` | `missing required`, `invalid config`, `environment variable not set` |
   | `unknown` | Anything not matching above categories |

2. **Returns per-container error breakdown** with counts, samples, and
   time distribution:
   ```json
   {
     "container": "web-app",
     "total_errors": 142,
     "categories": {
       "database": {"count": 89, "percentage": 62.7, "first_seen": "...", "last_seen": "...", "samples": ["..."]},
       "timeout":  {"count": 31, "percentage": 21.8, "first_seen": "...", "last_seen": "...", "samples": ["..."]},
       "application": {"count": 22, "percentage": 15.5, "...": "..."}
     },
     "dominant_category": "database",
     "category_timeline": [
       {"minute": "2026-03-13T10:01", "database": 12, "timeout": 3, "application": 1}
     ]
   }
   ```

3. **Provides actionable category-specific recommendations:**
   - `database` → "Check database connection pool limits and query performance"
   - `timeout` → "Review upstream service latency and timeout configurations"
   - `oom` → "Inspect container memory limits and heap allocation"

### Tool Signature

```python
@mcp.tool()
async def classify_errors(
    container_names: list[str] | None = None,
    tail: int = 1000,
    categories: list[str] | None = None,   # filter to specific categories
    use_cache: bool = True,
) -> dict: ...
```

### Architecture

```
classify_errors (tools.py)
    │
    ├── _fetch_logs_with_cache()              # existing
    ├── error_classifier.classify_lines()     # new: regex category matching
    ├── error_classifier.aggregate_stats()    # new: Polars groupby category + minute
    └── return JSON
```

**Key design decisions:**
- Categories and patterns defined as `list[ErrorCategory]` dataclass with
  `name`, `patterns: list[re.Pattern]`, `recommendation: str` — same pattern
  as `SecretPattern` in `secret_detector.py`.
- Combined pre-filter regex (fast-path) following the P3 optimization pattern
  already used in secret detection.
- Categories are ordered by specificity: `database` checked before `network`
  (since DB timeouts are a subset of network issues).

### Integration with Existing Tools

- **`analyze_root_causes`** gains a new scoring signal: containers whose dominant
  error category is `database` or `network` (infrastructure) score higher than
  those dominated by `application` errors. This addresses **Issue F** (error
  density) with semantic weighting rather than just count weighting.
- **`analyze_error_spikes`** can optionally include `dominant_category` per spike
  bucket, giving operators instant triage context.
- **`plan_investigation`** can detect category keywords in symptoms and recommend
  category-focused investigation.

### Test Plan

- Unit tests: classification accuracy for each category (positive and negative
  examples), priority ordering (specific categories match before generic ones).
- Contract tests: return-structure verification.
- Edge cases: multi-category lines (should match most specific), empty logs,
  logs with no errors.

---

## Enhancement 3: Mermaid Architecture Diagram Export

### Problem

The `map_service_dependencies` tool returns a JSON adjacency list — useful for
programmatic consumption but **difficult for operators to reason about visually**.
Understanding a 6-service dependency graph from nested JSON requires mental
effort that a diagram eliminates instantly.

This is already identified as **Issue 9** in `WIKI_REVIEW_DEPENDENCY_MAPPER.md`
(status: pending, planned).

### Proposed Solution

A new `export_dependency_graph` MCP tool that:

1. **Calls the existing dependency mapper** internally to get the adjacency list.

2. **Renders a Mermaid `graph TD` diagram** with:
   - Nodes labeled with container names
   - Edges labeled with dependency type + hit count
   - Color-coded nodes based on health status (error rate from spike detection):
     - Green: no spikes detected
     - Orange: spikes detected, not top root cause
     - Red: top-ranked root cause candidate
   - Edge thickness proportional to correlation score (if available)

3. **Returns the Mermaid source** as a string (renderable in GitHub, VSCode
   Markdown preview, Mermaid Live Editor, and most documentation tools):

   ```mermaid
   graph TD
       classDef healthy fill:#2d6a2d,stroke:#1a3d1a,color:#fff
       classDef warning fill:#b8860b,stroke:#8b6508,color:#fff
       classDef critical fill:#8b1a1a,stroke:#5c1010,color:#fff

       gateway["gateway"]:::healthy
       web-app["web-app"]:::critical
       database["database"]:::warning
       cache["cache"]:::healthy

       gateway -->|"HTTP (23 hits)"| web-app
       web-app -->|"postgres (89 hits)"| database
       web-app -->|"redis (45 hits)"| cache
       gateway -->|"mention (5 hits)"| cache
   ```

4. **Optionally writes to a file** (`.cache/graphs/<timestamp>_dependencies.md`)
   for persistent reference — same pattern as `plan_investigation` writing to
   `.cache/plans/`.

### Tool Signature

```python
@mcp.tool()
async def export_dependency_graph(
    containers: list[str] | None = None,
    tail: int = 500,
    include_transitive: bool = False,
    include_health: bool = True,        # color-code by error status
    output_file: bool = False,          # write to .cache/graphs/
    use_cache: bool = True,
) -> dict: ...
```

### Return Structure

```json
{
  "status": "success",
  "mermaid": "graph TD\n    ...",
  "node_count": 4,
  "edge_count": 4,
  "health_summary": {
    "gateway": "healthy",
    "web-app": "critical",
    "database": "warning",
    "cache": "healthy"
  },
  "file_path": ".cache/graphs/20260313_103000_dependencies.md"
}
```

### Architecture

```
export_dependency_graph (tools.py)
    │
    ├── tool_map_service_dependencies()        # existing: get adjacency list
    ├── tool_analyze_error_spikes() (optional)  # existing: get health status
    ├── _render_mermaid_graph()                 # new: graph → Mermaid string
    └── return JSON + optional file write
```

**Key design decisions:**
- No new module needed — rendering logic is a single function (~60 lines) in
  `tools.py` or a small `mermaid_renderer.py` helper.
- Composes existing tools (dependency mapper + spike detector) rather than
  reimplementing analysis — true to tool isolation principle.
- Mermaid chosen over DOT/Graphviz because it renders natively in GitHub
  Markdown, VSCode preview, and Copilot chat — zero extra tooling for users.
- Health-status overlay is optional (`include_health=True`) to keep the tool
  fast when only the graph structure is needed.

### Integration with Existing Tools

- **`plan_investigation`** can include `export_dependency_graph` as the final
  step in its investigation plan, giving operators a visual summary.
- **`capture_logs`** (the combined report tool) could embed the Mermaid diagram
  in its output for a complete incident snapshot.
- **VSCode Copilot** can render the Mermaid block directly in chat, making the
  graph immediately visible without leaving the editor.

### Test Plan

- Unit tests: Mermaid output format validation (parseable by Mermaid.js),
  correct node/edge counts, health class assignment, edge label formatting.
- Contract tests: return-structure verification.
- Edge cases: single container (no edges), self-referencing dependency (excluded),
  containers with special characters in names (must be quoted in Mermaid).

---

## Implementation Priority

Recommended order based on impact-to-effort ratio:

| Priority | Enhancement | Rationale |
|----------|-------------|-----------|
| **1st** | Mermaid Diagram Export | Lowest effort, immediately visual, already planned (Issue 9), composes existing tools |
| **2nd** | Semantic Error Classification | Medium effort, high impact on root cause quality, fills biggest analytical gap |
| **3rd** | Request ID Tracing | Medium effort, highest ceiling value, but depends on logs containing request IDs |

---

## Appendix: Design Constraint Checklist

All three enhancements satisfy the project's core constraints:

| Constraint | Enhancement 1 | Enhancement 2 | Enhancement 3 |
|-----------|:---:|:---:|:---:|
| Stateless (no global state) | Yes | Yes | Yes |
| Deterministic (same input → same output) | Yes | Yes | Yes |
| Local-only (no external APIs) | Yes | Yes | Yes |
| No LLMs | Yes | Yes | Yes |
| Cache-first pattern | Yes | Yes | Yes |
| Returns structured JSON | Yes | Yes | Yes |
| Tool isolation (independent) | Yes | Yes | Yes |
| Error contract (structured errors) | Yes | Yes | Yes |

---

**END OF PROPOSAL**
