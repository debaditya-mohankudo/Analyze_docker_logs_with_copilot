# Code Review: root_cause_analyzer.py

**Date:** 2026-03-07
**Module:** `docker_log_analyzer/root_cause_analyzer.py`
**Reviewer:** External analysis
**Status:** 5 issues assessed — 1 accepted (new enhancement), 3 rejected or already handled, 1 partially valid

---

## Summary

Five review comments were raised against the scoring algorithm in `rank_root_causes()`.
This document assesses each one against the actual module code, the input contracts,
and the upstream modules that produce the inputs. Two issues are new signals worth
tracking; three are either incorrect, already handled, or mischaracterise the
algorithm's intent.

---

## Issue 1 — Cascade score double-counts correlation (DESIGN, MEDIUM)

### Claim

`scores[origin] += corr_score * WEIGHT_CASCADE` double-counts correlation because
cascade candidates already embed dependency + correlation evidence, and spike timing
also fires on the same container pair. Suggestion: make cascade a **binary** signal
(`scores[origin] += WEIGHT_CASCADE`) and reserve correlation only for ordering.

### Assessment

**Reject — the two signals measure independent aspects of causation.**

| Signal | What it measures |
|--------|-----------------|
| Cascade score | Strength of temporal co-occurrence between origin and receiver errors (0–1 continuous) |
| Spike timing | Whether the origin's *first* spike preceded the receiver's *first* spike (binary) |

These are not the same measurement. A container can have high correlation (frequent
co-occurring errors within the window) but still spike *after* the receiver —
feedback-loop failures are a real example. Treating them as the same signal and
deduplicating by making cascade binary would lose the distinction between a cascade
pair with correlation 0.9 and one with correlation 0.1 — both equally important in
the reviewer's model but clearly not equivalent in practice.

The reviewer's concern about "spike timing dominates" is a **weight calibration
question**, not a double-counting bug. With current weights:

```
max cascade contribution  = 1.0 × 3.0 = 3.0
spike timing contribution = 4.0 (fixed)
```

For a container that earns both, the combined 7.0 is intentional: it has the
strongest possible causal evidence (both correlation and ordering). Whether 4.0 vs
3.0 is the right split is a tuning question to revisit after testing on larger
topologies. Named weight constants (`WEIGHT_CASCADE`, `WEIGHT_SPIKE_FIRST`) make
this trivial to adjust without code changes.

**Action:** No change. Note weight calibration as a future tuning point once tested
beyond the 4-service stack.

---

## Issue 2 — Fan-out penalty is conceptually wrong (DESIGN, LOW)

### Claim

High fan-out does not imply follower. Example: API gateway calls 10 services but
may not be the root cause. Suggestion: use `in_degree / out_degree` ratio, or
reduce the penalty to `-0.5`.

### Assessment

**Reject the conceptual critique; note the mild-penalty suggestion as a tuning option.**

The reviewer conflates two different graph concepts. Fan-out in this context means
*number of outbound dependencies* (services this container calls). A container with
high fan-out relies on many others — it is by definition downstream of failures in
any of those dependencies.

The reviewer's own example demonstrates the algorithm is correct: if auth-service
fails and gateway fails because of it, auth-service has high **fan-in** (gateway and
others depend on it) and is correctly scored high. Gateway has high **fan-out** and
is correctly penalized — it is the error *receiver*, not the origin. The penalty
accurately models this.

The `in_degree / out_degree` ratio is more complex to compute and interpret, and
adds no signal the current fan-in + fan-out combination does not already capture.
Division also introduces a zero-denominator edge case.

However, the weight asymmetry suggestion has practical merit:

```python
# Current: fan-in = +2.0, fan-out = -1.0 → 2:1 asymmetry
WEIGHT_DEPENDENCY = -0.5  # → 4:1 asymmetry, milder penalty for highly-connected services
```

Reducing the fan-out penalty would prevent scenarios where a legitimately high-fan-in
service (database) is partially offset by having one or two outbound deps (e.g.,
calling a metrics service). This is a conservative tuning option, not a correctness fix.

**Action:** No code change now. Track as a tuning option alongside Issue 1's weight
calibration. Document in weight constant comments.

---

## Issue 3 — Missing error density signal (ENHANCEMENT, MEDIUM)

### Claim

All spikes are treated equally regardless of magnitude. A container with 500 errors/min
should score higher than one with 5 errors/min. Suggestion: incorporate
`spike["error_rate"]` as a weighted signal.

### Assessment

**Accept — valid missing signal. Track as Issue F (new enhancement).**

The `detect_spikes()` output already includes spike magnitude data (`error_rate`,
`spike_count`, `max_error_rate`) that `rank_root_causes()` currently ignores. A
catastrophic failure (500 errors) and a marginal one (5 errors) are treated
identically for the spike timing bonus — only *ordering* is considered, not *severity*.

Incorporating magnitude would look like:

```python
# Issue F (not yet implemented): weight spike timing by error rate magnitude
# spike_magnitude = {s["container"]: s.get("max_error_rate", 1.0) for s in spikes}
# if origin_ts and target_ts and origin_ts < target_ts:
#     scores[origin] += WEIGHT_SPIKE_FIRST * log1p(spike_magnitude.get(origin, 1.0))
```

Using `log1p` avoids a single catastrophic spike drowning all other signals. Raw
`error_rate` multiplication could make a 1000-error spike 200× more impactful than
a 5-error one, distorting rankings unpredictably.

**This is the one genuinely new signal raised in this review that should be
implemented.** It requires no new dependencies and is directly available from
existing spike data.

**Action:** Track as Issue F. Add `# TODO (Issue F)` comment to
`root_cause_analyzer.py`.

---

## Issue 4 (6.1) — Containers missing from score (ROBUSTNESS, LOW)

### Claim

Containers with no dependents and no cascades never appear in results. Suggested fix:
`scores.setdefault(container, 0)` for all graph keys at initialisation.

### Assessment

**Reject — already handled by the fan-out step.**

Step 4 iterates `for container, edges in graph.items()`, which covers every container
in the graph regardless of whether it has dependents or cascades:

```python
scores[container] += outbound * WEIGHT_DEPENDENCY  # 0 × -1.0 = 0.0 for no-dep containers
```

Because `scores` is a `defaultdict(float)`, this creates the key even when
`outbound == 0`. The unit test `test_single_container_no_deps` confirms: a container
with an empty edge list appears in results with `score == 0.0`.

The reviewer's specific motivating case — "cache fails, everything depends on cache"
— is also already handled: cache would have high fan-in count, so `dependents["cache"]`
would be populated and its score elevated accordingly.

The `setdefault` suggestion would add noise: containers with score 0.0 provide no
useful ranking signal and pollute the output for operators.

**Action:** No change. Existing behaviour is correct.

---

## Issue 5 (6.2) — Cascades may reference external (unknown) nodes (ROBUSTNESS, LOW)

### Claim

`cascade["from"]` may be an external service (e.g., `stripe.com`) not present in the
graph. No guard exists, so external hostnames could appear in ranking output.

### Assessment

**Partially valid — unlikely in practice but defensive guard is worthwhile.**

Upstream guards reduce this risk considerably:

1. `build_graph()` calls `_resolve_target()` which maps raw hostnames to known container names.
2. `find_cascade_candidates()` iterates `graph.items()` as sources; the cascade `"from"`
   field is set to `edge["target"]`, which is the resolved target.

However, `_resolve_target()` returns the *raw hostname* if no container matches
(by design — it falls through to `return target`). If a container calls an external
service and the resolution fails, that external hostname can appear as `"from"` in
cascade output and then get scored in `rank_root_causes()`.

A one-line guard in `rank_root_causes()` would be a cheap, defensive improvement:

```python
# Guard: only score containers known to the graph to prevent external hosts
# from appearing in root cause rankings.
# (cascade["from"] is normally a resolved container name, but _resolve_target()
# can pass through unresolved hostnames when no container matches.)
known_containers = set(graph.keys())
for cascade in cascades:
    origin = cascade["from"]
    if known_containers and origin not in known_containers:
        continue  # skip external hosts
    ...
```

The `if known_containers` check preserves behaviour when graph is empty (unit tests
with `graph={}` pass cascades directly and should still be scored).

**Action:** Track as Issue G. Add `# TODO (Issue G)` comment to
`root_cause_analyzer.py`.

---

## Prioritised Action List

| Priority | Issue | Type | Verdict | Action |
|----------|-------|------|---------|--------|
| 1 | Issue 3 — error density signal | ENHANCE MED | **Accept** | Implement as Issue F (TODO comment added) |
| 2 | Issue 5 (6.2) — external node guard | ROBUSTNESS LOW | Partially valid | Implement as Issue G (TODO comment added) |
| 3 | Issue 1 — cascade binary vs weighted | DESIGN MED | **Reject** | No change; weight tuning noted |
| 4 | Issue 2 — fan-out penalty direction | DESIGN LOW | **Reject** | No change; mild-penalty tuning noted |
| 5 | Issue 4 (6.1) — missing containers | ROBUSTNESS LOW | **Reject** | Already handled by fan-out step |

---

## See also

- [WIKI_PROPOSAL_ROOT_CAUSE_ANALYZER.md](WIKI_PROPOSAL_ROOT_CAUSE_ANALYZER.md) — original proposal and Issues A–E
- [`docker_log_analyzer/root_cause_analyzer.py`](../docker_log_analyzer/root_cause_analyzer.py)
- [`docker_log_analyzer/dependency_mapper.py`](../docker_log_analyzer/dependency_mapper.py) — upstream `find_cascade_candidates()`
- [`docker_log_analyzer/spike_detector.py`](../docker_log_analyzer/spike_detector.py) — upstream `detect_spikes()` output schema

---

**Retrieval keywords:** root cause, rank_root_causes, code review, cascade double count,
fan-out penalty, fan-in, error density, spike magnitude, external node, unknown container,
weight calibration, scoring, Issue F, Issue G, root_cause_analyzer review
