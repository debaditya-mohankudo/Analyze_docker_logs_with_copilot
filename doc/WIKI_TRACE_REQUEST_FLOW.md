---
tags: [trace_request_flow, request-tracer, correlation-id, trace-id, session-id, cross-container]
last_updated: 2026-07-03
---

# Trace Request Flow

`trace_request_flow` is a deterministic MCP tool that correlates
request/trace/correlation IDs across containers to reconstruct a
per-request timeline — no LLM, no Docker-side instrumentation required.
It complements `analyze_correlations` (§ [WIKI_TOOLS.md](WIKI_TOOLS.md)),
which correlates errors by *time proximity*: this tool correlates by
*exact ID match*, giving a deterministic causal path when logs actually
propagate a shared ID across services.

---

## What It Does

1. Scans log lines in each target container for configurable request ID patterns
2. Groups matched lines by the literal ID value, across all containers
3. Drops groups whose timestamp spread implies an accidental ID collision
4. Builds a chronological, cross-container timeline per surviving request ID
5. Filters and truncates the result before returning

---

## Tool Signature

```python
trace_request_flow(
    container_names: list[str] | None = None,
    tail:            int = 500,
    use_cache:       bool = True,
    min_events:      int = 2,
    max_requests:    int = 50,
) -> dict
```

### Parameters

| Parameter         | Type         | Default  | Description |
|-------------------|--------------|----------|-------------|
| `container_names` | `list[str]`  | `None`   | Containers to scan; omit for all running containers |
| `tail`            | `int`        | `500`    | Log lines fetched per container |
| `use_cache`       | `bool`       | `True`   | Use cached logs when available |
| `min_events`      | `int`        | `2`      | Minimum events per ID to include — filters IDs seen only once (likely noise) |
| `max_requests`    | `int`        | `50`     | Max timelines returned, sorted by `event_count` descending before truncation |

### Response

```json
{
  "status": "success",
  "timelines": [
    {
      "id_value": "8f2a1c3e-...",
      "id_patterns": ["request_id", "trace_id"],
      "containers": ["gateway", "web-app", "database"],
      "event_count": 5,
      "first_seen": "2026-03-13T10:01:00.000Z",
      "last_seen": "2026-03-13T10:01:00.342Z",
      "duration_ms": 342.0,
      "events": [
        {"container": "gateway", "timestamp": "...", "pattern_name": "request_id", "message": "..."}
      ]
    }
  ],
  "request_count": 12,
  "containers_scanned": ["gateway", "web-app", "database", "cache"],
  "cache_hits": {"gateway": true, "web-app": false},
  "parameters": {"tail": 500, "min_events": 2, "max_requests": 50}
}
```

---

## ID Pattern Configuration

Patterns live in `Settings.request_id_patterns` (`config.py`) — a `Dict[str, str]`
where each value is a regex with exactly one capture group, configurable via
env vars / `.env` per repo convention (no hard-coded values). Two tiers exist:

**Strict (UUID-shaped)** — high precision, matches only well-formed UUIDs:

| Pattern name       | Keyword(s) matched |
|--------------------|---------------------|
| `request_id`       | `request_id`, `req_id`, `x_request_id` |
| `trace_id`         | `trace_id`, `traceid` |
| `correlation_id`   | `correlation_id`, `corr_id` |
| `transaction_id`   | `transaction_id`, `txn_id`, `tx_id` |
| `session_id`       | `session_id`, `sess_id` |

**Loose fallback** (`*_loose` variants of the same five) — same keywords, but
capture any `[\w-]{4,64}` token instead of requiring a UUID shape. Added
because the strict patterns miss common non-UUID ID formats: short numeric
IDs, base62/nanoid IDs, raw 32-char hex trace IDs, W3C `traceparent`, AWS
X-Ray IDs, etc. Trade-off: shorter/looser tokens carry a higher chance of
accidental collision across unrelated requests — mitigated by the window
guard below.

A UUID also satisfies the loose regex, so a single line with a strict-format
ID will match **both** its strict and loose pattern — this is harmless (see
step 2 below) and does not create duplicate timelines.

---

## Sequence: How an ID Gets Correlated

1. **Pattern list built** (`tool_trace_request_flow`, `tools.py`) —
   `settings.request_id_patterns` is iterated in insertion order and compiled
   into `RequestIdPattern` objects: the 5 strict patterns first, then the 5
   `_loose` fallbacks.

2. **Per container, per line** (`extract_ids`, `request_tracer.py`):
   - **Fast-path prefilter** — a single combined regex of all pattern
     keywords (strict and loose share keywords) checks whether the line
     mentions an ID concept at all. Lines that don't are skipped before any
     per-pattern regex runs — keeps large tails cheap.
   - **Every pattern is tried, not first-match-wins** — each pattern in the
     list runs `finditer` independently. If a line contains a UUID, both its
     strict pattern (e.g. `request_id`) and its loose counterpart
     (`request_id_loose`) match it, producing two tuples with the same
     captured ID string but different `pattern_name`.

3. **Flat match list assembled globally** — `(id_value, pattern_name,
   unix_ts, line, container)` tuples accumulate across *all* scanned
   containers, not per-container.

4. **Cross-container grouping by raw `id_value`** (`cross_container_timelines`) —
   matches are bucketed purely by the literal captured string, ignoring
   pattern name and container of origin. This is the actual correlation
   step: if `gateway` and `database` both produce a match with the same
   `id_value`, they land in the same bucket regardless of which pattern
   (strict or loose) caught it on each side.

5. **Window guard** — if a bucket's timestamps span more than
   `trace_window_seconds` (default **120s**, configurable in `config.py`),
   the group is dropped as a probable accidental collision rather than one
   real request. This matters more for loose IDs, which are shorter and more
   collision-prone than UUIDs.

6. **Timeline built + sorted** — surviving buckets become timeline dicts
   (`id_patterns`, `containers`, `event_count`, `first_seen`/`last_seen`,
   `duration_ms`, chronological `events`), sorted by `first_seen`.

7. **Filter + truncate** (back in `tool_trace_request_flow`) — timelines with
   `event_count < min_events` are dropped (single-container/single-occurrence
   IDs are treated as noise), remaining timelines sorted by `event_count`
   descending, then truncated to `max_requests`.

---

## Known Limitation

If an application logs an ID format not covered by any configured pattern
(strict or loose), `extract_ids` silently returns no match for that line —
no error, no warning. The tool will simply return sparse or empty
`timelines`, which can be mistaken for "no cross-service traffic to trace."
Add a pattern to `request_id_patterns` matching your service's actual ID
format if you observe this.

---

## Relationship to `analyze_correlations`

| | `correlator.py` (`analyze_correlations`) | `request_tracer.py` (`trace_request_flow`) |
|---|---|---|
| Signal | Time proximity of errors | Exact ID match |
| Precision | Heuristic/statistical | Deterministic — same ID = same request |
| Output | Container-pair correlation score | Per-request cross-container timeline |
| Weakness | False positives from unrelated coincident errors | Requires the app to actually emit/propagate a correlation ID |

Use `analyze_correlations` to find which containers tend to fail together;
use `trace_request_flow` to find the exact causal path a single request took
through those containers, when the logs carry an ID.

---

## Retrieval keywords

trace_request_flow, request tracer, request id, trace id, correlation id, transaction id, session id, cross-container timeline, UUID, loose pattern, strict pattern, id_value, id_patterns, trace_window_seconds, request_id_patterns

**[negative keywords / not-this-doc]**
time-window correlation algorithm internals (see analyze_correlations), CI, coverage, cache strategy

---

## See also

- Tool parameter reference: [WIKI_TOOLS.md § trace_request_flow](WIKI_TOOLS.md#15-trace_request_flow)
- Time-proximity correlation (complementary tool): [WIKI_ARCHITECTURE.md § Correlation](WIKI_ARCHITECTURE.md#correlation-correlatorpy)
- Architecture hub: [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md)
- Home: [WIKI_HOME.md](WIKI_HOME.md)
