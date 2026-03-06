# CLAUDE.md

Docker Log Analyzer – MCP Server

This file defines architectural rules, coding standards, and operational constraints
for contributors and AI agents working on this repository.

**Project Type:**
Stateless MCP (Model Context Protocol) stdio server for VSCode Copilot Agent Mode.
No LLMs. No Kafka. All analysis is local and deterministic.

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
- Explicit cache directory (.cache/patterns)

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
MCP stdio server (mcp_server.py)
        │
        ├── spike_detector.py
        ├── correlator.py
        ├── log_pattern_analyzer.py
        ├── secret_detector.py
        ├── config.py
        └── logger.py
```

All tools are registered in mcp_server.py.

-------------------------------------------------------------------------------
## 3. PERFORMANCE RULES
-------------------------------------------------------------------------------

### 3.1 Log Fetching (Cache-First Strategy)

All tools use cache-first pattern:

1. Check `.cache/logs/<container>/<YYYY-MM-DD>.parquet`
2. If fresh (< CACHE_MAX_AGE_MINUTES), use cached logs
3. Otherwise, fetch fresh from Docker API

**Log Caching Rules:**
- Keyed by: container name + date
- Stored under: `.cache/logs/<container>/`
- Format: Parquet (zstd), columns: `timestamp` (Datetime[us,UTC]), `message` (String)
- Legacy `.jsonl` files are still readable as a fallback; new writes always produce `.parquet`
- Atomic writes via tempfile + rename
- Metadata: `.cache/logs/metadata.json` tracks sync times
- Default window: 24 hours per tool (configurable)
- Fallback: Always works without cache (just slower)

**sync_docker_logs tool:**
- Explicitly caches logs for time window
- Accepts relative ("2 hours ago") or ISO-8601 timestamps
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
    - cache_hit
    - cached_at (ISO-8601 UTC)
- Independent from log cache

If log format detection logic changes,
contributors must manually clear `.cache/patterns/`.

-------------------------------------------------------------------------------
## 4. TESTING STRATEGY
-------------------------------------------------------------------------------

### Test Types:

**Unit tests (CI-safe)**
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
## 6. SECRET DETECTION SAFETY
-------------------------------------------------------------------------------

SecretDetector rules:

- Must redact secrets before returning
- Must categorize severity:
    - critical
    - high
    - medium
- Must include remediation suggestions
- Never echo full credential value

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

1. Define tool function in mcp_server.py
2. Keep it stateless
3. Add unit tests (if logic-heavy)
4. Add integration test if Docker-dependent
5. Update README
6. Update this CLAUDE.md if architectural impact

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
