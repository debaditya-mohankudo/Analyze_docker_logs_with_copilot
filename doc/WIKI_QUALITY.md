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
| Unit tests | 275 (no Docker required) |
| Integration tests | 67 (Docker + test containers) |
| Total | 342 |
| CI execution (unit only) | ~0.8 s parallel via pytest-xdist |
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
| `test_pattern_detector.py` | 24 | unit | Timestamp formats (ISO/syslog/epoch/Apache), language detection, log levels, health checks |
| `test_secret_detector.py` | 45 | unit | 20 secret patterns, redaction, severity filtering, remediation, Docker timestamp regex |
| `test_dependency_mapper.py` | 35 | unit | HTTP/HTTPS/DB/gRPC/name-mention extraction, graph builder, cascade candidates, hit_count, transitive |
| `test_tools_unit.py` | 49 | unit | tools.py helper functions, Docker/cache/time parsing helpers, sync/async tool error branches, lifecycle and sync paths |
| `test_cache_manager.py` | 25 | unit | Parquet write/read, schema validation, window filtering, multi-day, corrupt file, atomic write cleanup, metadata, clear cache |
| `test_mcp_integration.py` | 43 | integration | All 10 MCP tool functions, live Docker, field presence, value ranges, error cases |
| `test_remote_docker_integration.py` | 14 | integration | Remote Docker via SSH/TCP, graceful fallback when unavailable (12 auto-skip) |

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
| `config.py` | 100% | Config parsing, DOCKER_HOST, validators |
| `__init__.py` | 100% | Package init |
| `secret_detector.py` | 96% | 20 patterns, redaction, recommendations |
| `spike_detector.py` | 95% | Rolling-window, timestamp parsing |
| `correlator.py` | 94% | Pairwise correlation, event extraction |
| `dependency_mapper.py` | ~90% | Graph builder, cascade candidates |
| `cache_manager.py` | ~95% | Parquet write/read, atomic write, corrupt file handling, metadata, clear cache |
| `tools.py` | 93% | Helper branches + sync/async tool contract and error-path unit coverage |
| `logger.py` | 76% | LoggerWithRunID singleton |
| `log_pattern_analyzer.py` | 55% | Pattern detection (regex heuristics) |
| `mcp_server.py` | 22% (unit); improved via integration | Tool implementations |

Target: core modules ≥ 90% (per [../CLAUDE.md](../CLAUDE.md) §4).

### Recent Coverage Uplift: `tools.py`

- Added `tests/test_tools_unit.py` (49 unit tests) to cover helper and tool branches without Docker.
- Measured result: `docker_log_analyzer/tools.py` is now **93%** covered in unit scope.

Reproduce:

```bash
uv run pytest tests/test_tools_unit.py tests/test_correlation_cache.py -q \
	--cov=docker_log_analyzer.tools --cov-report=term-missing
```

---

## CI Configuration

`.github/workflows/tests.yml`:

- **Unit job:** runs on every `push` + `pull_request` to `main`; command: `pytest tests/ -m "not integration"`
- **Integration job:** runs on `push` only (after PR merge); requires Docker-in-Docker runner
- **Skip condition:** markdown-only changes (`**.md`) skip CI automatically
- **Reproducibility:** `uv.lock` cached via `cache-dependency-path` for fast installs
- **Parallelism:** `pytest-xdist` for unit tests (`-n auto`)

---

## Shared Test Fixtures

Defined in [`tests/conftest.py`](../tests/conftest.py):

| Fixture | Scope | What it provides |
|---------|-------|-----------------|
| `docker_client` | session | `DockerClient` instance; auto-skips if Docker unavailable |
| `setup_integration_containers` | session, autouse | Starts test containers before integration tests; stops after |
| `spike_logs_single` | function | 14 log lines: 3 baseline buckets + 1 spike bucket (ratio 4.0) |
| `spike_logs_uniform` | function | 12 log lines: uniform errors, no spike |
| `corr_aligned_logs` | function | web + db errors within 30s → high correlation |
| `corr_distant_logs` | function | web + db errors 2 min apart → zero correlation |
| `python_logs`, `java_logs`, `go_logs`, `nodejs_logs` | function | Language-specific sample log lines |

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
3. Import the tool function: `from docker_log_analyzer.mcp_server import tool_<name>`
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
