# Proposal: rank_root_causes Tool

**Date:** 2026-03-07
**Status:** Partially implemented — Issues A and B done; Issues C, D, E pending (TODO comments in module)
**Target module:** `docker_log_analyzer/root_cause_analyzer.py`
**MCP tool:** `rank_root_causes` (would be tool #11)

---

## Concept

Combine outputs from three existing analysis modules to rank containers by
root-cause likelihood:

1. **Dependency graph** (`build_graph`) — who depends on whom (fan-in/fan-out)
2. **Cascade candidates** (`find_cascade_candidates`) — directed error propagation paths
3. **Error spikes** (`detect_spikes`) — when each container first spiked

The result: a scored, sorted list of containers most likely to be the root cause
of a system-wide failure.

### Copilot Workflow

```
User: "My system is failing. Find the root cause container."

Copilot calls:
1. detect_error_spikes     → spikes
2. correlate_containers    → correlations
3. map_service_dependencies → graph + cascades
4. rank_root_causes        → ranked root causes
```

---

## Evaluation

### Strengths

| Aspect | Assessment |
|--------|-----------|
| **Architecture fit** | Stateless, no LLM, local, deterministic — fully compliant with CLAUDE.md |
| **Module isolation** | Pure function taking pre-computed inputs — no tool coupling |
| **User value** | Very high — answers the #1 question operators ask during incidents |
| **Implementation cost** | Low — ~80 lines, no new dependencies, reuses existing outputs |
| **Testability** | Excellent — pure function with dict inputs, easy to unit test |

### Issues Found in Proposed Implementation

#### Issue A — Timestamp comparison with 0 default (BUG, CRITICAL) ✅ DONE

```python
spike_time = {s["container"]: s["first_spike_ts"] for s in spikes}

if spike_time.get(origin, 0) < spike_time.get(target, 0):
    scores[origin] += 3
```

`first_spike_ts` is an ISO-8601 string (e.g. `"2026-03-07T10:05:00Z"`).
Comparing strings with `0` is a type error in Python 3. Even if it were numeric,
defaulting to `0` (epoch) would make missing containers appear to spike "first"
every time.

**Fix:** Use `None` sentinel and skip comparison when either container has no spike:

```python
spike_time = {s["container"]: s["first_spike_ts"] for s in spikes if s.get("first_spike_ts")}

origin_ts = spike_time.get(origin)
target_ts = spike_time.get(target)
if origin_ts and target_ts and origin_ts < target_ts:
    scores[origin] += WEIGHT_SPIKE_FIRST
```

#### Issue B — Arbitrary magic weights (DESIGN, HIGH) ✅ DONE

The proposed weights `(2, 2, 3, -1)` have no documented rationale:

```python
scores[c] += dependents[c] * 2      # fan-in
scores[origin] += score * 2          # cascade correlation
scores[origin] += 3                  # spiked first
scores[c] -= dependencies[c]         # fan-out penalty
```

Different-scale values are summed without normalization. A container with 5
dependents (`+10`) dominates one with a perfect correlation score (`+2.0`).

**Fix:** Define named constants with documented rationale:

```python
# Weights — tuned for 4-service test stack, may need adjustment for larger topologies
WEIGHT_DEPENDENT = 2.0      # each service that depends on this one
WEIGHT_CASCADE = 3.0        # per cascade candidate with correlation
WEIGHT_SPIKE_FIRST = 4.0    # spiked before dependent services
WEIGHT_DEPENDENCY = -1.0    # penalty per outbound dependency (followers, not leaders)
```

Consider normalization in a future iteration (e.g. min-max scaling to 0-10).

#### Issue C — No evidence list (MISSING, MEDIUM) — TODO in `root_cause_analyzer.py`

The example output includes an `evidence` list per container:

```json
{"container": "database", "score": 9.4, "evidence": ["3 services depend on database", ...]}
```

But the proposed code only returns `{"container": ..., "score": ...}`. The
evidence list is what makes the tool useful for operators — it explains *why*
the container scored high.

**Fix:** Build evidence strings alongside score accumulation:

```python
evidence: Dict[str, List[str]] = defaultdict(list)

# When adding fan-in score:
evidence[c].append(f"{dependents[c]} services depend on {c}")

# When adding cascade score:
evidence[origin].append(f"cascade correlation with {target} ({score:.2f})")

# When adding spike-first score:
evidence[origin].append(f"error spike occurred before {target}")
```

#### Issue D — No handling of empty inputs (ROBUSTNESS, MEDIUM) — TODO in `root_cause_analyzer.py`

If `graph`, `cascades`, or `spikes` is empty, the function returns an empty
list. This is technically correct but unhelpful.

**Fix:** Return a structured result with a message:

```python
if not scores:
    return {"status": "success", "root_causes": [], "message": "No root cause signals found"}
```

#### Issue E — Score can go negative (UX, LOW) — TODO in `root_cause_analyzer.py`

The dependency penalty (`-1 per outbound dep`) can make scores negative for
leaf services. Negative scores are confusing in a ranking context.

**Fix:** Floor at 0.0 in the output:

```python
{"container": k, "score": round(max(v, 0.0), 3), ...}
```

---

## Revised Algorithm

```
Input:  graph (from build_graph)
        cascades (from find_cascade_candidates)
        spikes (from detect_spikes)

1. Fan-in score:  for each container C, count how many other containers
                  have C as a dependency target → score += count × WEIGHT_DEPENDENT

2. Cascade score: for each cascade candidate where C is the origin ("from"),
                  add correlation_score × WEIGHT_CASCADE

3. Spike timing:  parse first_spike_ts for each container. For each cascade
                  pair, if origin spiked before receiver → score += WEIGHT_SPIKE_FIRST

4. Fan-out penalty: for each outbound dependency C has, subtract WEIGHT_DEPENDENCY
                    (services that depend on many others are followers, not leaders)

5. Build evidence list alongside each score addition

6. Sort by score descending, floor at 0.0

Output: [{"container": str, "score": float, "evidence": [str, ...]}]
```

---

## MCP Tool Contract

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_names` | list[str] | null | Filter to specific containers (null = all running) |
| `tail` | int | 500 | Log lines per container (passed to sub-tools) |
| `time_window_seconds` | int | 3600 | Time window for analysis |
| `include_transitive` | bool | false | Include transitive edges in dependency graph |

### Return Shape

```json
{
  "status": "success",
  "root_causes": [
    {
      "container": "database",
      "score": 9.4,
      "evidence": [
        "3 services depend on database",
        "error spike occurred before web-app",
        "cascade correlation with web-app (0.81)",
        "cascade correlation with gateway (0.74)"
      ]
    },
    {
      "container": "cache",
      "score": 4.2,
      "evidence": [
        "1 service depends on cache",
        "cascade correlation with web-app (0.65)"
      ]
    }
  ],
  "analysis_inputs": {
    "containers_analyzed": 4,
    "spikes_detected": 3,
    "cascade_candidates": 5,
    "dependency_edges": 8
  },
  "parameters": {
    "container_names": null,
    "tail": 500,
    "time_window_seconds": 3600
  }
}
```

---

## File Plan

| File | Change |
|------|--------|
| `docker_log_analyzer/root_cause_analyzer.py` | New module: `rank_root_causes()` pure function |
| `docker_log_analyzer/tools.py` | New `tool_rank_root_causes()` — calls spike/correlate/graph internally |
| `docker_log_analyzer/mcp_server.py` | Register tool #11 |
| `tests/test_root_cause_analyzer.py` | Unit tests for scoring, evidence, edge cases |
| `doc/WIKI_TOOLS.md` | Add tool #11 documentation |
| `doc/WIKI_ARCHITECTURE.md` | Add root cause analysis section |
| `CLAUDE.md` | No change needed (architecture rules unchanged) |

---

## Design Decisions

### Should the tool call sub-tools internally?

**Yes.** The MCP tool wrapper (`tool_rank_root_causes`) should orchestrate:
1. Fetch logs (with cache)
2. Run `detect_spikes()`
3. Run `correlate()`
4. Run `build_graph()` + `find_cascade_candidates()`
5. Call `rank_root_causes()` with results

The core `rank_root_causes()` function in `root_cause_analyzer.py` remains a
pure function taking pre-computed inputs — compliant with tool isolation (each
tool works independently; the wrapper handles data gathering).

### Why a separate module?

The scoring algorithm is non-trivial and independently testable. Putting it
in `tools.py` would bloat that file. A dedicated `root_cause_analyzer.py`
follows the existing pattern (`spike_detector.py`, `correlator.py`,
`dependency_mapper.py`).

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| False root cause ranking | Medium | Evidence list lets operators verify; scores are relative, not absolute |
| Weight tuning for large topologies | Medium | Start with 4-service test stack; document weights as tunable constants |
| Performance on many containers | Low | Already bounded by existing tool limits (MAX_CO_OCCURRENCES, tail) |
| Circular dependency scoring | Low | Fan-out penalty naturally reduces score for bidirectional edges |

---

## Acceptance Criteria

- [ ] `rank_root_causes()` returns sorted list with `container`, `score`, `evidence`
- [ ] Empty inputs return empty list without raising
- [x] Spike timestamp comparison handles missing/None values (Issue A — DONE)
- [ ] Scores are floored at 0.0
- [ ] Evidence list explains every score contribution
- [x] Named weight constants with documented rationale (Issue B — DONE)
- [ ] Unit tests: ≥10 tests covering scoring, timing, empty inputs, single container
- [ ] Integration test: run against 4-service test stack, verify database ranks #1
- [ ] MCP tool registered and returns structured JSON
- [ ] Wiki docs updated (WIKI_TOOLS.md, WIKI_ARCHITECTURE.md)

---

## See also

- [WIKI_REVIEW_ROOT_CAUSE_ANALYZER.md](WIKI_REVIEW_ROOT_CAUSE_ANALYZER.md) — scoring algorithm review (Issues F, G pending)
- [WIKI_ARCHITECTURE.md § Root Cause Analysis](WIKI_ARCHITECTURE.md#root-cause-analysis-root_cause_analyzerpy)
- [WIKI_ARCHITECTURE.md § Dependency Mapping](WIKI_ARCHITECTURE.md#dependency-mapping)
- [WIKI_TOOLS.md § map_service_dependencies](WIKI_TOOLS.md#8-map_service_dependencies)
- [WIKI_REVIEW_DEPENDENCY_MAPPER.md](WIKI_REVIEW_DEPENDENCY_MAPPER.md)

---

**Retrieval keywords:** root cause, rank, score, ranking, root_cause_analyzer,
rank_root_causes, incident, failure, origin, evidence, spike timing, fan-in,
fan-out, cascade, dependency, correlation, orchestration, tool 11
