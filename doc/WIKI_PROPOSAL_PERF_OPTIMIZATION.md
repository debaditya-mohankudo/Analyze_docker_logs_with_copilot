---
tags: [proposal, performance, regex, implemented, historical]
last_updated: 2026-07-03
---

# Proposal: Regex Pre-compilation & Fast-Path Optimisations

**Status:** IMPLEMENTED (2026-03-12)
**Scope:** `log_pattern_analyzer.py`, `dependency_mapper.py`, `secret_detector.py`
**Motivation:** Slowest unit tests clustered around regex-heavy inner loops
  - 0.19s `test_detects_python` — `detect_language` O(lines × langs × patterns)
  - 0.14s `test_language_auto_detected_as_string` — same path through wrapper
  - 0.09s `test_hit_count_reflects_occurrences` — per-container name regex inside line loop
  - 0.05s secret detector tests — 20 individual compiled `finditer` per line

---

## P1 — Language & framework detection: alternation regex (log_pattern_analyzer.py)

### Problem

`detect_language` and `detect_framework` loop three levels deep:

```python
for log_line in log_lines:
    for language, patterns in LANGUAGE_PATTERNS.items():   # 6 langs
        for pattern in patterns:                            # up to 21 patterns for Java
            if re.search(pattern, log_line, re.IGNORECASE): # string → cache lookup each time
```

For 200 log lines: `200 × 6 langs × ~8 avg patterns = ~9,600 re.search calls`.

### Fix

Pre-compile per-language alternation regexes at class-definition time:

```python
_LANGUAGE_RE = {
    lang: re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)
    for lang, pats in LANGUAGE_PATTERNS.items()
}
```

Inner loop collapses to one `.search()` call per language per line:
`200 × 6 = 1,200 calls` — **8× fewer** for typical Java-heavy logs.

**Score semantics change:** scoring shifts from "pattern hits" to "line hits per language".
Relative language ranking is preserved; arguably more correct since lines with many
Java-specific tokens no longer dominate unfairly.

Same fix applied to `detect_framework` via `_FRAMEWORK_RE`.

---

## P2 — Container name-mention scan: combined regex (dependency_mapper.py)

### Problem

`extract_dependencies` builds a fresh regex per container per line:

```python
for name in known_containers:
    if len(name) >= 4 and re.search(
        r"(?:^|[\s:/,'\"])" + re.escape(name) + r"(?:[\s:/,'\"]|$)",
        body, re.IGNORECASE,
    ):
```

With N containers and L lines: N × L separate `re.search` calls, each involving
a string concatenation and a regex compile (or cache lookup).

### Fix

Build one combined alternation regex from all qualifying container names before
the line loop:

```python
qualifying = [n for n in known_containers if len(n) >= 4]
if qualifying:
    alts = "|".join(re.escape(n) for n in qualifying)
    name_re = re.compile(
        r"(?:^|[\s:/,'\"])(" + alts + r")(?:[\s:/,'\"]|$)",
        re.IGNORECASE,
    )
```

One `findall` per line replaces N `re.search` calls per line.

---

## P3 — Secret scanner: combined pre-filter (secret_detector.py)

### Problem

`scan_logs` runs up to 20 individual `finditer` calls per line — even on lines
that contain no secrets at all (the common case).

### Fix

Build a single combined "any secret" regex at `__init__` time:

```python
self._any_pattern_re = re.compile(
    "|".join(f"(?:{p.pattern})" for p in self.patterns),
    re.IGNORECASE,
)
```

Use it as a fast-path guard in the scan loop:

```python
if not self._any_pattern_re.search(message):
    continue   # skip all 20 individual patterns for clean lines
```

On typical production logs (99%+ lines clean), this reduces 20 `finditer` calls
to 1 combined check per line.

---

## Test impact

All existing tests must still pass unchanged — no output contracts change.
New benchmark tests are out of scope (the suite runs in ~1s already).

## Allowed by CLAUDE.md §13

> Improved pattern heuristics
> Faster Polars aggregation

This proposal falls squarely under "Improved pattern heuristics".

---

## See also

- Architecture hub: [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md)
- Security guardrails: [WIKI_SECURITY.md](WIKI_SECURITY.md)
- Home: [WIKI_HOME.md](WIKI_HOME.md)
