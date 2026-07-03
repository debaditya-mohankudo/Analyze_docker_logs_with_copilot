# CLAUDE.md

Docker Log Analyzer – MCP Server

This file defines architectural rules, coding standards, and operational constraints
for contributors and AI agents working on this repository.

**Project Type:**
Stateless MCP (Model Context Protocol) stdio server for VSCode Copilot Agent Mode.
No LLMs. No Kafka. All analysis is local and deterministic.

-------------------------------------------------------------------------------
## 0. DESIGN PHILOSOPHY
-------------------------------------------------------------------------------

When facing a design decision, consult the Zen of Python (`python -c "import this"`).
Key principles that have guided this codebase:

- **Explicit is better than implicit** — data a struct owns should live on the struct,
  not in a lookup table elsewhere (e.g. `SecretPattern.recommendation`).
- **Simple is better than complex** — prefer the straightforward solution before
  reaching for abstractions.
- **Readability counts** — a reader should understand intent at the definition site,
  not by tracing through dispatch logic.
- **Enforce contracts with tests, not code complexity** — use test classes with
  `frozenset` key assertions to guard return structures instead of TypedDict,
  runtime assertions, or defensive wrappers.

-------------------------------------------------------------------------------
## 1. CORE DESIGN PRINCIPLES
-------------------------------------------------------------------------------

### 1.1 Stateless Tools (CRITICAL)

Every MCP tool MUST:
- Fetch logs from Docker SDK
- Perform analysis
- Return JSON
- Exit

No global state.
No background threads.
No in-memory caches (except per-call).
No long-lived connections.

All state must come from:
- Docker logs
- Configuration
- Explicit cache directory (.cache/)

### 1.2 Deterministic & Local

- No OpenAI / Anthropic APIs.
- No network calls except Docker daemon.
- No telemetry.
- No external SaaS dependencies.

All analysis must be reproducible offline.

### 1.3 Tool Isolation

Each tool must:
- Work independently
- Not rely on other tools being called first
- Not mutate shared data

-------------------------------------------------------------------------------
## 2. ARCHITECTURE OVERVIEW
-------------------------------------------------------------------------------

```
VSCode Copilot (Agent Mode)
        │
        ▼
MCP stdio server (mcp_server.py)   ← @mcp.tool() registrations
        │
        └── tools.py               ← tool_*() implementations
              ├── docker.py             log fetching + cache
              ├── spike_detector.py     rolling-window spike detection
              ├── correlator.py         pairwise temporal correlation
              ├── dependency_mapper.py  service graph inference
              ├── root_cause_analyzer.py  fan-in + cascade scoring
              ├── log_pattern_analyzer.py  timestamp/language/level detection
              ├── secret_detector.py    20-pattern secret/PII scanner
              ├── request_tracer.py     request/trace/correlation ID extraction + timelines
              ├── error_classifier.py   semantic error categorization
              ├── investigation_planner.py  symptom → signal → ordered tool-call plan
              ├── coderepo.py           stack-trace parsing + repo-relative source resolution
              ├── cache_manager.py      Parquet log cache (.cache/logs/)
              ├── patterns.py           shared compiled regexes
              ├── config.py             Pydantic BaseSettings singleton
              └── logger.py             LoggerWithRunID singleton + JsonlFormatter
```

Tool implementations live in `tools.py`. `mcp_server.py` contains only `@mcp.tool()` wiring.

-------------------------------------------------------------------------------
## 3. PERFORMANCE RULES
-------------------------------------------------------------------------------

### 3.1 Log Fetching (Cache-First Strategy)

All tools use cache-first pattern, keyed by exact time-window coverage —
**there is no age-based expiry/TTL setting**:

1. Check `.cache/logs/<container>/<YYYY-MM-DD>.parquet` for every day in the requested window
2. If every day in the window is present, use cached logs regardless of age
3. Otherwise (any day missing/incomplete), fetch fresh from Docker API and write a fresh Parquet file

**Log Caching Rules:**
- Keyed by: container name + date
- Stored under: `.cache/logs/<container>/`
- Format: Parquet (zstd), columns: `timestamp` (Datetime[us,UTC]), `message` (String)
- Atomic writes via tempfile + rename
- Metadata: `.cache/logs/metadata.json` tracks sync times
- Default window: 24 hours per tool (configurable)
- Fallback: Always works without cache (just slower)

**sync_docker_logs tool:**
- Explicitly caches logs for time window
- Accepts ISO-8601 UTC timestamps only (e.g. `"2026-03-04T10:00:00Z"`) — natural
  language like "2 hours ago" is resolved by the Copilot agent upstream, not
  parsed by this tool; `since` defaults to 24h ago, `until` defaults to now
- Enables offline analysis after containers stop
- Enables instant bug reproduction (no 2-min wait)

**Response fields:**
- `cache_hits` – dict showing which containers used cache
- Enables monitoring cache effectiveness

### 3.2 Polars Usage

- Prefer vectorized operations.
- Avoid Python loops over log lines.
- Parse timestamps once.

### 3.3 Pattern Analysis Cache

`analyze_patterns` results cached separately.

Cache rules:
- Keyed by container name only
- Stored under `.cache/patterns/`
- Must include:
    - logs_cache_hit
    - analyzed_at (ISO-8601 UTC)
- Independent from log cache

If log format detection logic changes,
contributors must manually clear `.cache/patterns/`.

-------------------------------------------------------------------------------
## 4. TESTING STRATEGY
-------------------------------------------------------------------------------

### 4.0 Test Priority Order (CRITICAL)

**Design-principle verification comes before feature testing.**

Every new tool or module must have tests that confirm the core design
principles hold BEFORE adding tests for functional correctness:

1. **Stateless wiring** – verify the tool's Docker → fetch → analyse → return
   path works end-to-end (no state leaks between calls).
2. **Tool isolation** – confirm the tool can be called without any other tool
   having been called first (no hidden dependencies).
3. **Error contract** – confirm every failure mode returns structured JSON
   (`{"status": "error", ...}`) and does not raise an exception.
4. **Determinism** – same inputs must produce the same outputs.

Only once these pass should feature-level tests (output shape, value ranges,
field presence) be added.

**Why this order matters:**
Wiring bugs (wrong function signatures, mis-unpacked return values, wrong
argument types) are invisible to pure-module tests. A test that mocks the
Docker client and exercises `tool_<name>()` directly will catch these before
they reach production. The `analyze_code_context` incident (broken
`_fetch_logs_with_cache` call, tuple passed where string expected) slipped
through because only pure-module tests existed for that path.

### Test Types:

**Unit tests (CI-safe)**

- Design-principle verification for every `tool_*()` wrapper (see §4.0)
- spike_detector
- correlator
- pattern detector
- secret detector

**Integration tests (Docker required)**
- MCP tool calls
- Live log generation
- Cross-container correlation

### Markers:

```python
@pytest.mark.integration
```

CI must run:
```bash
uv run pytest tests/ -m "not integration"
```

Full local run:
```bash
pytest tests/
```

Coverage target:
- Core modules ≥ 90%
- mcp_server.py covered via integration tests

-------------------------------------------------------------------------------
## 5. MCP TOOL CONTRACTS
-------------------------------------------------------------------------------

All tools must:

- Accept typed parameters
- Validate inputs
- Return structured JSON
- Never print to stdout (except MCP protocol)
- Never log secrets in raw form

Errors must:
- Return structured error JSON
- Not crash server

-------------------------------------------------------------------------------
## 6. SECURITY
-------------------------------------------------------------------------------

**Canonical document:** [doc/WIKI_SECURITY.md](doc/WIKI_SECURITY.md)

All security rules live there. Summary of the two critical areas:

- **Secret detection** — `SecretDetector` must redact before returning,
  categorize severity (critical / high / medium), and never echo raw credential
  values. See §1 of the security doc.

- **Repository path confinement** — `find_file_in_repo` must call
  `relative_to(repo_root)` before any `is_file()` check on absolute paths,
  and must `return None` after the absolute block to prevent Python's `/`
  operator from discarding the repo root. See §2 of the security doc.

-------------------------------------------------------------------------------
## 7. DOCKER INTERACTION RULES
-------------------------------------------------------------------------------

- Docker socket must be mounted read-only.
- Do not attempt container modification.
- start_test_containers / stop_test_containers
  are the only allowed lifecycle tools.

-------------------------------------------------------------------------------
## 8. CONFIGURATION
-------------------------------------------------------------------------------

All configuration must be read from:

- Pydantic Settings (config.py)
- Environment variables
- .env file (optional)

No hard-coded paths.

-------------------------------------------------------------------------------
## 9. LOG PARSING STANDARDS
-------------------------------------------------------------------------------

### Supported timestamp formats:
- ISO-8601
- syslog
- epoch
- Apache

### Language detection:
- Python
- Java
- Go
- Node.js

Detection must:
- Use regex heuristics
- Not require full parsing engine
- Be tolerant to malformed lines

-------------------------------------------------------------------------------
## 10. CONTRIBUTION GUIDELINES
-------------------------------------------------------------------------------

When adding a new tool:

1. Implement `tool_<name>(...)` in `tools.py`
2. Register with `@mcp.tool()` in `mcp_server.py`
3. Keep it stateless
4. Add unit tests (if logic-heavy)
5. Add integration test if Docker-dependent
6. Update README
7. Update this CLAUDE.md if architectural impact

-------------------------------------------------------------------------------
## 11. WHAT NOT TO DO
-------------------------------------------------------------------------------

❌ Add LLM summarization
❌ Add Kafka or message brokers
❌ Add background schedulers
❌ Add persistent in-memory state
❌ Store raw logs to disk
❌ Send logs to external services
❌ Introduce hidden caching layers

-------------------------------------------------------------------------------
## 12. DEVELOPMENT WORKFLOW
-------------------------------------------------------------------------------

Install:
```bash
uv sync
```

Run unit tests:
```bash
uv run pytest tests/ -m "not integration"
```

Run full suite:
```bash
uv run pytest tests/
```

Start MCP server manually:
```bash
uv run docker-log-analyzer-mcp
```

-------------------------------------------------------------------------------
## 13. FUTURE EXTENSIONS (ALLOWED DIRECTIONS)
-------------------------------------------------------------------------------

- Improved pattern heuristics
- Faster Polars aggregation
- Additional secret patterns
- Better correlation scoring
- Smarter health-check detection
- Improved structured logging

-------------------------------------------------------------------------------
**END OF FILE**
-------------------------------------------------------------------------------
