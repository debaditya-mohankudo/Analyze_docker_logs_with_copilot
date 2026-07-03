---
tags: [test, testing, ci, coverage, unit, integration, pytest, quality]
last_updated: 2026-07-03
---

# Wiki Hub: Quality & Testing

Use this hub for test strategy, CI configuration, coverage targets, and adding new tests.

---

## Agent Use Rules

- Start here for "how many tests", "how to run CI", "what's the coverage", "how do I add a test".
- For module architecture when adding new tools, see [WIKI_ARCHITECTURE.md § Adding New Tools](WIKI_ARCHITECTURE.md#adding-new-tools).
- Test strategy is defined authoritatively in [../CLAUDE.md](../CLAUDE.md) §4.

---

## Test Suite Summary

| Metric | Value |
|--------|-------|
| Unit tests | 598 (no Docker required) |
| Integration tests | 90 (Docker + test containers) |
| Total | 688 |
| CI execution (unit only) | ~1.2 s parallel via pytest-xdist |
| Coverage (core modules) | 90–100% |

### Run commands

```bash
# Unit tests only — CI-safe, no Docker, ~0.8s
uv run pytest tests/ -m "not integration"

# Unit tests with coverage
uv run pytest tests/ -m "not integration" --cov=docker_log_analyzer

# Full suite — requires Docker + test containers running
uv run pytest tests/

# Run a single file
uv run pytest tests/test_dependency_mapper.py -v
```

---

## Test File Breakdown

| File | Count | Marker | What it covers |
|------|-------|--------|----------------|
| `test_config_remote_docker.py` | 17 | unit | DOCKER_HOST parsing, SSH/TCP/Unix socket scenarios, config validation |
| `test_spike_detector.py` | 16 | unit | Rolling-window spike detection, Docker timestamp parsing, edge cases |
| `test_correlator.py` | 17 | unit | Correlation scoring, event extraction, empty/single container |
| `test_correlation_cache.py` | 14 | unit | Cache key stability, TTL expiry, TTL=0 disable, cache miss/hit flow, use_cache=false bypass |
| `test_pattern_detector.py` | 46 | unit | Timestamp formats (ISO/syslog/epoch/Apache), language detection, framework detection, log levels, health checks |
| `test_secret_detector.py` | 45 | unit | 20 secret patterns, redaction, severity filtering, remediation, Docker timestamp regex |
| `test_dependency_mapper.py` | 36 | unit | HTTP/HTTPS/DB/gRPC/DNS/TCP/name-mention extraction, graph builder, cascade direction, hit_count, transitive |
| `test_root_cause_analyzer.py` | 27 | unit | Fan-in scoring, cascade scoring, spike timing bonus, combined signals, zero-score exclusion |
| `test_tools_unit.py` | 87 | unit | tools.py helper functions, Docker/cache/time parsing helpers, tool error branches, lifecycle and cache paths; wrapper-level design-principle tests for `tool_analyze_code_context`, `cache_info`, `clear_cache` |
| `test_cache_manager.py` | 26 | unit | Parquet write/read, schema validation, window filtering, multi-day, corrupt file, atomic write cleanup, metadata, clear cache |
| `test_cache_tools.py` | 8 | unit | `tool_cache_info` / `tool_clear_cache` wrapper contracts |
| `test_docker.py` | 18 | unit | `_docker_client` (incl. `settings.docker_host` wiring), `_fetch_logs`, `_fetch_logs_window`, `_fetch_logs_with_cache` helpers |
| `test_patterns.py` | 27 | unit | DOCKER_TS_RE and ERROR_PATTERN_RE regex: matches, non-matches, edge cases |
| `test_investigation_planner.py` | 40 | unit | Signal classification, focus modes, plan generation, Markdown file output, container scoping; single-container correlation skip, explicit-scope list_containers skip |
| `test_coderepo.py` | 44 | unit | Stack trace parsers (Python/Java/Go/Node.js), repo resolution, file finding, code context extraction |
| `test_error_classifier.py` | 57 | unit | Semantic error categorization (database, network, timeout, auth, etc.), category filtering, summary aggregation |
| `test_request_tracer.py` | 65 | unit | ID extraction (strict UUID + loose fallback patterns), fast-path prefilter, cross-container timeline grouping, window-based collision dropping |
| `test_logger.py` | 8 | unit | JsonlFormatter, rotating file-handler wiring, idempotency, caller-attribution (`stacklevel`) correctness |
| `test_mcp_integration.py` | 76 | integration | All 18 MCP tools, live Docker, field presence, value ranges, error cases |
| `test_remote_docker_integration.py` | 14 | integration | Remote Docker via SSH against a real Docker-in-Docker target (`ssh-target`), graceful fallback when unavailable |

---

## Test Markers

```python
@pytest.mark.unit        # no Docker required — fast, CI-safe
@pytest.mark.integration # requires Docker daemon + test containers
@pytest.mark.serial      # must not run in parallel (uses xdist_group)
```

Marker registration: [`tests/conftest.py`](../tests/conftest.py)

CI must run: `pytest tests/ -m "not integration"`

---

## Module Coverage

| Module | Coverage | Notes |
|--------|----------|-------|
| `__init__.py` | 100% | Package init |
| `correlator.py` | 100% | Pairwise correlation, event extraction |
| `docker.py` | 100% | `_docker_client` (incl. `docker_host` wiring), log fetch helpers |
| `error_classifier.py` | 100% | Semantic error categorization |
| `spike_detector.py` | 100% | Rolling-window, timestamp parsing |
| `secret_detector.py` | 99% | 20 patterns, redaction, recommendations |
| `coderepo.py` | 99% | Stack trace parsers, repo resolution, code context |
| `request_tracer.py` | 99% | ID extraction (strict + loose), cross-container timelines |
| `root_cause_analyzer.py` | 97% | Fan-in/cascade/spike-timing scoring |
| `config.py` | 96% | Config parsing, DOCKER_HOST, validators |
| `investigation_planner.py` | 98% | Signal classification, plan generation |
| `cache_manager.py` | 94% | Parquet write/read, atomic write, corrupt file handling, metadata, clear cache |
| `dependency_mapper.py` | 94% | Graph builder, cascade candidates |
| `logger.py` | 88% | LoggerWithRunID singleton, JsonlFormatter, file-handler wiring |
| `patterns.py` | 89% | Shared regex patterns |
| `tools.py` | 89% | Helper branches + tool contract and error-path unit coverage |
| `mcp_server.py` | 57% (unit); improved via integration | Tool registration wiring |
| `log_pattern_analyzer.py` | 59% | Pattern detection (regex heuristics) |

Target: core modules ≥ 90% (per [../CLAUDE.md](../CLAUDE.md) §4) — `logger.py`,
`patterns.py`, `tools.py`, `mcp_server.py`, and `log_pattern_analyzer.py`
currently fall short; the wiring-critical paths in each are still covered
(see per-file breakdown above), the gaps are mostly in defensive/rarely-hit
branches.

### Recent Coverage Uplift: `tools.py`

- `tests/test_tools_unit.py` (87 unit tests) covers helper and tool branches without Docker.
- Includes wrapper-level design-principle tests for `tool_analyze_code_context`, `tool_cache_info`, and `tool_clear_cache` (Docker wiring, error contract, frame parsing, language auto-detection).
- Measured result: `docker_log_analyzer/tools.py` is **89%** covered in unit scope.

Reproduce:

```bash
uv run pytest tests/test_tools_unit.py tests/test_correlation_cache.py -q \
	--cov=docker_log_analyzer.tools --cov-report=term-missing
```

---

## CI Configuration

`.github/workflows/tests.yml` defines a single `tests` job:

- **Trigger:** `push` and `pull_request` to `main`
- **Skip condition:** paths-ignore on `**/*.md` and `.history` — markdown-only changes skip CI automatically
- **Command:** `pytest tests/ -m "not integration" --cov=docker_log_analyzer --cov-report=term-missing -n auto --durations=10`
- **Parallelism:** `pytest-xdist` (`-n auto`)
- **Caching:** `astral-sh/setup-uv@v5` with `enable-cache: true` (not a manual `cache-dependency-path` step)
- **There is no separate integration-test CI job.** Integration tests (needing
  Docker + the `ssh-target` DinD container) are local-only — run them yourself
  with `pytest tests/` before pushing; CI only runs the unit-marked suite.

---

## Shared Test Fixtures

Defined in [`tests/conftest.py`](../tests/conftest.py):

| Fixture | Scope | What it provides |
|---------|-------|-----------------|
| `docker_client` | session | `DockerClient` instance; auto-skips if Docker unavailable |
| `setup_integration_containers` | session, autouse | Starts test containers before integration tests; populates `ssh-target`'s inner Docker-in-Docker daemon with the same log-generator containers over SSH; stops/tears down after |
| `ssh_test_keypair` | session | Generates (or reuses) an ephemeral ed25519 keypair for `ssh-target`, registers it with the running ssh-agent |
| `ssh_target_ready` | session | `True` once `ssh-target`'s inner daemon is populated and reachable over SSH; SSH-dependent tests should skip (not fail) when `False` |
| `spike_logs_single` | function | 14 log lines: 3 baseline buckets + 1 spike bucket (ratio 4.0) |
| `spike_logs_uniform` | function | 12 log lines: uniform errors, no spike |
| `corr_aligned_logs` | function | web + db errors within 30s → high correlation |
| `corr_distant_logs` | function | web + db errors 2 min apart → zero correlation |
| `python_logs`, `java_logs`, `go_logs`, `nodejs_logs` | function | Language-specific sample log lines |

---

## Testing Priority (CRITICAL)

Per [CLAUDE.md §4.0](../CLAUDE.md), **design-principle verification must be written before feature tests.**

For every new `tool_*()` wrapper, add tests in this order:

### 1. Stateless wiring (first)

Patch `_docker_client` and `_fetch_logs` (or the relevant fetch helper) and call
`tool_<name>()` directly. Confirm the full Docker → fetch → analyse → return path
executes without error and returns the expected top-level shape.

```python
def test_docker_error_returns_error_status():
    with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("down")):
        out = tools.tool_my_new_tool("api")
    assert out["status"] == "error"
```

### 2. Tool isolation (second)

Call the tool with no prior tool calls. Do not set up any shared state. Confirm it
does not raise and returns a valid JSON dict.

### 3. Error contract (third)

Cover every early-exit branch: Docker down, container not found, empty logs.
Each must return `{"status": "error", ...}` — never raise an exception.

### 4. Feature / output correctness (last)

Only after the above pass: assert output field values, ranges, and shapes.

---

**Why this order matters:** Wiring bugs (wrong function signatures, mis-unpacked
return values, wrong argument types) are invisible to pure-module tests. The
`analyze_code_context` incident illustrated this — a broken `_fetch_logs_with_cache`
call and a `(str, float)` tuple passed where a `str` was expected both slipped
through because only `test_coderepo.py` pure-module tests existed. Wrapper-level
tests catch these at the boundary.

---

## Adding New Tests

### Unit test for a new module

1. Create `tests/test_<module>.py`
2. Add `pytestmark = pytest.mark.unit` at module level
3. No Docker fixtures — use synthetic log lines (see conftest examples)
4. Keep tests self-contained; no external dependencies

### Integration test for a new MCP tool

1. Add a `class Test<ToolName>` to `tests/test_mcp_integration.py`
2. Mark with `@pytest.mark.integration`
3. Import the tool function: `from docker_log_analyzer.tools import tool_<name>`
4. Use `docker_client` session fixture (auto-skips if Docker unavailable)
5. Test: success status, required keys present, types correct, error case (invalid container)

### New secret pattern

1. Add regex to `config.py` → `error_patterns`
2. Add pattern name + severity to `SecretDetector`
3. Add test case to `test_secret_detector.py`
4. Verify no false positives on sample logs before merging

---

## Retrieval keywords

test, testing, CI, coverage, unit, integration, pytest, markers, xdist, workflow, GitHub Actions, conftest, fixture, test breakdown, add test, new test, coverage target, quality

**[negative keywords / not-this-doc]**
setup, install, architecture, module design, MCP tool params, cache strategy, Copilot prompts

---

## See also

- Architecture hub (adding new tools): [WIKI_ARCHITECTURE.md § Adding New Tools](WIKI_ARCHITECTURE.md#adding-new-tools)
- Operations hub: [WIKI_OPERATIONS.md](WIKI_OPERATIONS.md)
- Home: [WIKI_HOME.md](WIKI_HOME.md)
