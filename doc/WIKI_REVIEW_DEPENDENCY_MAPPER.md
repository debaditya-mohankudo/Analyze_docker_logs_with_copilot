# Code Review: dependency_mapper.py

**Date:** 2026-03-07
**Module:** `docker_log_analyzer/dependency_mapper.py`
**Reviewer:** External analysis
**Status:** Pending triage — issues ranked by impact

---

## Summary

`dependency_mapper.py` implements log-based service dependency inference: it scans
container logs for HTTP URLs, DB connection strings, gRPC dial calls, and container
name mentions, then joins results with temporal correlation output to surface cascade
candidates. The overall design is sound. Nine concrete issues were identified,
ranging from correctness bugs to performance gaps and missing signals.

---

## Issue 1 — Container name detection is too permissive (BUG, HIGH)

### Location

`extract_dependencies()` → name-mention block ([dependency_mapper.py:134](../docker_log_analyzer/dependency_mapper.py#L134))

### Problem

Uses `\b` word-boundary anchors with `re.IGNORECASE`. `\b` transitions between
`\w` and `\W`. Container names that are substrings of longer identifiers match
incorrectly.

**Example:**

```
container: api
log line:  "grapiql server started"
```

Names like `db`, `web`, `cache` are also common English words and fire on prose
log lines, generating false `"low"` confidence edges.

### Fix

Replace word-boundary anchors with explicit separator characters:

```python
pattern = r"(?:^|[\s:/,'\"])" + re.escape(name) + r"(?:[\s:/,'\"]|$)"
```

### Verdict

**Implement.** False positives here pollute the dependency graph.

---

## Issue 2 — `_resolve_target` can mis-map services with shared prefixes (BUG, HIGH)

### Location

`_resolve_target()` ([dependency_mapper.py:80](../docker_log_analyzer/dependency_mapper.py#L80))

### Problem

```python
if target.startswith(name) or name.startswith(target):
    return name
```

The first matching container wins — iteration order of a `set` is not guaranteed.
With containers `auth`, `auth-service`, `auth-db`:

```
http://auth-service → may resolve to "auth"  (wrong)
```

### Fix

Collect all prefix-matching names and return the longest:

```python
def _resolve_target(target: str, known: Set[str]) -> str:
    if target in known:
        return target
    matches = [c for c in known if target.startswith(c) or c.startswith(target)]
    if matches:
        return max(matches, key=len)
    return target
```

### Verdict

**Implement.** Current code is non-deterministic on overlapping container names.

---

## Issue 3 — Kubernetes FQDN patterns not stripped before resolution (ENHANCEMENT, MEDIUM)

### Location

`_normalize_host()` ([dependency_mapper.py:75](../docker_log_analyzer/dependency_mapper.py#L75))

### Problem

Kubernetes FQDNs appear in logs:

```
http://user-service.default.svc.cluster.local/api
```

`_normalize_host()` does not strip the cluster suffix, so `_resolve_target()`
cannot match it to the `user-service` container.

### Fix

```python
_K8S_SUFFIX = re.compile(r"\.svc\.cluster\.local$|\.svc$|\.cluster\.local$", re.IGNORECASE)

def _normalize_host(raw: str) -> str:
    host = raw.split(":")[0].lower().strip(".")
    host = _K8S_SUFFIX.sub("", host)
    host = host.split(".")[0]   # take first label only
    return host
```

### Verdict

**Defer** — this project targets local Docker, not Kubernetes. Add to backlog.

---

## Issue 4 — `_SKIP_HOSTS` may block real intra-container dependencies (DESIGN, LOW)

### Location

`_SKIP_HOSTS` constant ([dependency_mapper.py:70](../docker_log_analyzer/dependency_mapper.py#L70))

### Problem

Proposal: skip only when `target == current_container` rather than skipping all
loopback addresses. Would require threading `current_container` into `_add()`.

### Verdict

**Reject.** In local Docker networks, localhost/127.0.0.1 dependencies are nearly
always loopback health checks, not real inter-service edges. The complexity cost
outweighs the marginal gain.

---

## Issue 5 — `extract_dependencies` called per-line instead of per-container (PERFORMANCE, MEDIUM)

### Location

`build_graph()` inner loop ([dependency_mapper.py:176](../docker_log_analyzer/dependency_mapper.py#L176))

### Problem

```python
for line in lines:
    for target, source, confidence in extract_dependencies([line], known):
```

`extract_dependencies` is called once per log line. For 10 000 lines × 4 patterns
= 40 000 regex passes, vs 4 000 if called once per container.

### Fix

Pass the full line list; `extract_dependencies` already accepts `List[str]`:

```python
for container, lines in container_logs.items():
    for target, source, confidence in extract_dependencies(lines, known):
        raw[container][(target, source, confidence)] += 1
```

`hit_count` is the accumulated dict value — no change to output schema.

### Verdict

**Implement.** One-line fix, ~3× reduction in regex passes.

---

## Issue 6 — Transitive closure can generate edges to external hosts (BUG, MEDIUM)

### Location

`_apply_transitive()` ([dependency_mapper.py:221](../docker_log_analyzer/dependency_mapper.py#L221))

### Problem

`direct.get(hop, set())` is safe today because `direct` is keyed by containers
with outbound edges only. But if resolution changes and an external hostname
enters the graph as a source, transitive edges to it will be generated.

### Fix

```python
for second_hop in direct.get(hop, set()):
    if second_hop not in known:   # guard: only known containers
        continue
    if second_hop != src and second_hop not in existing:
        ...
```

### Verdict

**Implement.** Cheap, defensive, prevents future regressions.

---

## Issue 7 — Cascade pair deduplication loses directionality (BUG, MEDIUM)

### Location

`find_cascade_candidates()` ([dependency_mapper.py:285](../docker_log_analyzer/dependency_mapper.py#L285))

### Problem

```python
pair = (min(src, target), max(src, target))
```

`(db → api)` and `(api → db)` are treated as the same pair. One directed cascade
path is silently dropped.

### Fix

```python
pair = (src, target)
```

### Verdict

**Implement.** Cascade candidates are directional by design; symmetric
deduplication corrupts the output.

---

## Issue 8 — Three missing high-value dependency signals (ENHANCEMENT, MEDIUM)

### 8a — DNS lookup failures

```
lookup redis: no such host
```

```python
_DNS_RE = re.compile(r"\blookup\s+([a-zA-Z0-9_-]+)", re.IGNORECASE)
# confidence: "medium"
```

### 8b — TCP connection-refused logs

```
dial tcp redis:6379: connection refused
```

```python
_TCP_RE = re.compile(r"\btcp\s+([a-zA-Z0-9_-]+):\d+", re.IGNORECASE)
# confidence: "high"
```

### 8c — HTTP inbound "from" attribution

```
GET /api/users from auth-service
```

```python
_FROM_RE = re.compile(r"\bfrom\s+([a-zA-Z0-9_-]{3,})\b", re.IGNORECASE)
# confidence: "low" — only emit if captured name is a known container
```

### Verdict

**8a and 8b:** Implement — strong signals, low false-positive risk.
**8c:** Implement with known-container guard only; too noisy otherwise.

---

## Issue 9 — Mermaid diagram export (FEATURE, LOW)

### Proposal

Add `export_mermaid(graph: dict) -> str` that converts the dependency graph
to a Mermaid `graph TD` block:

```
graph TD
    api --> postgres
    api --> redis
    gateway --> api
```

This lets Copilot / Claude render a live architecture diagram in chat:

> *"Show my service architecture"*

### Implementation sketch

```python
def export_mermaid(graph: Dict[str, List[dict]]) -> str:
    lines = ["graph TD"]
    for src, edges in graph.items():
        for edge in edges:
            lines.append(f"    {src} --> {edge['target']}")
    return "\n".join(lines)
```

Could live in `dependency_mapper.py` or a new `formatters.py`.

### Verdict

**Implement next sprint** — pure function, no algorithm changes, high user value.

---

## Prioritised Action List

| Priority | Issue | Type | Action |
|----------|-------|------|--------|
| 1 | Issue 7 — cascade direction lost | BUG HIGH | Implement now |
| 2 | Issue 2 — `_resolve_target` non-deterministic | BUG HIGH | Implement now |
| 3 | Issue 1 — name-mention false positives | BUG HIGH | Implement now |
| 4 | Issue 5 — per-line regex loop | PERF MED | Implement now (one-liner) |
| 5 | Issue 6 — transitive edge to external hosts | BUG MED | Implement now (guard) |
| 6 | Issue 8a/b — DNS + TCP signals | ENHANCE MED | Implement now |
| 7 | Issue 9 — Mermaid export | FEATURE LOW | Next sprint |
| 8 | Issue 8c — HTTP "from" signal | ENHANCE LOW | Implement with guard |
| 9 | Issue 3 — Kubernetes FQDN stripping | ENHANCE LOW | Defer |
| 10 | Issue 4 — localhost skip policy | DESIGN LOW | Reject |

---

## See also

- [WIKI_ARCHITECTURE.md § Dependency Mapping](WIKI_ARCHITECTURE.md#dependency-mapping)
- [WIKI_TOOLS.md § map_service_dependencies](WIKI_TOOLS.md#10-map_service_dependencies)
- [`docker_log_analyzer/dependency_mapper.py`](../docker_log_analyzer/dependency_mapper.py)

---

**Retrieval keywords:** dependency mapper, dependency_mapper, review, code review,
false positive, name mention, resolve target, transitive, cascade, mermaid, export,
regex, gRPC, DNS, TCP, Kubernetes, performance, hit_count, extract_dependencies,
build_graph, word boundary, prefix match, directionality
