# Docker Log Analyzer Using Natural Language – MCP Server

A stateless, **LLM-free** Docker log analysis tool exposed as an [MCP](https://modelcontextprotocol.io) server for **VSCode Copilot Agent Mode**. No Kafka, no OpenAI API key — all analysis runs locally using regex and [Polars](https://pola.rs).

## Features

- **Pattern Detection** – identifies timestamp format (ISO-8601, syslog, epoch, Apache) and programming language (Python, Java, Go, Node.js) from log lines
- **Error Spike Detection** – Polars rolling-window analysis flags minute-buckets where error rate exceeds a configurable baseline multiplier
- **Cross-Container Correlation** – pairwise temporal scoring of error co-occurrence across containers (score 0–1)
- **Sensitive Data Detection** – scans logs for 20 secret patterns (AWS, GitHub, Stripe, Google, Azure, OAuth, JWT, database URLs, PII) with severity filtering and redaction
- **Copilot Agent Mode** – registered as an MCP stdio server; Copilot orchestrates via chat, every tool runs locally

## Quality & Testing

| Metric | Value |
| --- | --- |
| **Coverage** | 53% (754 statements) — improved to 94-100% across core modules via targeted unit tests |
| **Unit tests** | 111 tests across 4 modules |
| **CI execution** | ~3.76s parallel via xdist (no Docker) |
| **Integration tests** | 32 tests (Docker-dependent, local only) |
| **Total test suite** | 143 tests (111 CI + 32 integration) |

**Module coverage:**
- `config.py` – 100% (configuration parsing) 
- `__init__.py` – 100% (package initialization)
- `secret_detector.py` – 96% (20 patterns, redaction, recommendations)
- `spike_detector.py` – 95% (rolling-window detection, timestamp parsing)
- `correlator.py` – 94% (cross-container correlation scoring)
- `logger.py` – 76% (structured logging)
- `log_pattern_analyzer.py` – 55% (pattern detection)
- `mcp_server.py` – 22% (tool implementations; improved via integration tests)

**Test breakdown:**
- `test_spike_detector.py` – 16 tests (rolling-window spike detection, Docker timestamp parsing)
- `test_correlator.py` – 17 tests (cross-container correlation, event extraction, scoring)
- `test_pattern_detector.py` – 24 tests (timestamp formats, language detection, log levels, health checks, error patterns)
- `test_secret_detector.py` – 45 tests (20 secret patterns, redaction, severity filtering, recommendations, Docker timestamp regex, edge cases)
- `test_mcp_integration.py` – 32 integration tests (MCP tool calls with live Docker containers)

**CI runs:** `pytest tests/ -m "not integration" --cov=docker_log_analyzer` (unit tests, ~111 tests)

## Architecture

```text
VSCode Copilot Chat (Agent Mode)
        │  calls tools via MCP stdio (.vscode/mcp.json)
        ▼
docker-log-analyzer-mcp  (Python MCP server)
        │
        ├── list_containers       → Docker SDK
        ├── analyze_patterns      → Docker SDK + PatternDetector (regex)
        ├── detect_error_spikes   → Docker SDK + Polars rolling-window
        ├── correlate_containers  → Docker SDK + pairwise temporal scan
        ├── detect_data_leaks     → Docker SDK + SecretDetector (regex + redaction)
        ├── start_test_containers → docker compose (docker-compose.test.yml)
        └── stop_test_containers  → docker compose down
```

Each tool call is **stateless**: fetch logs from Docker SDK → analyse → return JSON. No background threads, no persistent state.

## Quick Start

### Prerequisites

- Docker running locally
- VSCode with [GitHub Copilot](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot) extension
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Setup

```bash
git clone <repository-url>
cd Analyze_docker_log_w_llm
uv sync
```

The MCP server is pre-configured in [.vscode/mcp.json](.vscode/mcp.json). Open the project in VSCode, switch Copilot Chat to **Agent** mode, and the tools are available immediately.

### Verify

```bash
uv run python -c "from docker_log_analyzer.mcp_server import run; print('OK')"
```

## Example Copilot Prompts

Use these natural language prompts in VSCode Copilot Chat (Agent mode) to invoke the tools:

### Discovery

- "What Docker containers are currently running?"
- "List all my running containers and their status."

### Pattern analysis

- "Analyze the log patterns for the test-database container."
- "What log format and programming language is test-web-app using?"
- "Show me the log level distribution and top errors for test-gateway."
- "Are there any health check endpoints being hit frequently in test-cache?"

### Error spike detection

- "Check for error spikes across all containers in the last 1000 lines."
- "Detect error spikes in test-database with a threshold of 1.5."
- "Are there any error rate anomalies in my containers right now?"
- "Which containers had the worst error spikes in the last few minutes?"

### Cross-container correlation

- "Are there any correlated errors between my containers?"
- "Correlate container errors using a 60-second time window."
- "Which containers are failing together? Use a 30-second co-occurrence window."
- "Is test-gateway causing failures in test-web-app and test-database?"

### Test containers

- "Start the test log generator containers."
- "Start the test containers and rebuild the images."
- "Stop the test containers."

### Sensitive data detection

- "Scan all containers for sensitive data like API keys and credentials."
- "Check test-database logs for data leaks in the last 60 seconds."
- "Detect critical-level secrets (API keys, tokens) in test-web-app."
- "Are there any passwords or credit card numbers in my container logs?"

### Bug reproduction capture

- "Watch test-web-app and test-database for the next 2 minutes — I'm about to reproduce the bug."
- "Capture all container logs for 90 seconds, then tell me what happened."
- "Monitor only test-gateway and test-cache for 1 minute with a spike threshold of 1.5."
- "Start capturing now across all containers — I'll trigger the failure in a moment. How long should I capture for?"

### Combined investigation

- "List my containers, then check for error spikes and tell me which ones are most correlated."
- "My app seems unhealthy — analyze patterns and detect spikes across all containers."
- "Check if test-web-app and test-database are failing at the same time."

## MCP Tools

| Tool | Parameters | Description |
| ---- | ---------- | ----------- |
| `list_containers` | — | List running Docker containers |
| `analyze_patterns` | `container_name?`, `tail=500`, `force_refresh=false`, `use_cache=true` | Timestamp format, language, log levels, health checks, top errors. Uses log cache-first (24h window), falls back to Docker API. Pattern results cached to disk per container; `force_refresh=true` re-analyses. |
| `detect_error_spikes` | `container_name?`, `tail=1000`, `spike_threshold=2.0`, `use_cache=true` | Rolling-window error spike detection. Uses log cache-first (24h window). |
| `correlate_containers` | `time_window_seconds=30`, `tail=500`, `use_cache=true` | Pairwise cross-container error correlation. Uses log cache-first (24h window). |
| `detect_data_leaks` | `duration_seconds=60`, `container_names[]?`, `severity_filter='all'`, `use_cache=true` | Scans for 20 secret patterns: AWS/GitHub/Google/Stripe keys, Azure storage keys, OAuth secrets, Bearer/JWT tokens, database URLs, base64 secrets, session cookies, PII. Uses log cache-first (24h window). Findings with redaction and recommendations. Severity: `critical`, `high`, `medium`, `all` |
| `sync_docker_logs` | `container_names[]?`, `since="24 hours ago"`, `until="now"`, `force_refresh=false` | **Sync Docker logs to `.cache/logs/` for time window.** Enables fast offline analysis and instant bug reproduction. Time args: `"2 hours ago"`, `"7 days ago"`, ISO-8601 timestamps. All tools use cache-first after sync. |
| `start_test_containers` | `rebuild=false` | Start 4-service test stack (`docker-compose.test.yml`) |
| `stop_test_containers` | — | Stop and remove test containers |
| `capture_and_analyze` | `container_names[]?`, `duration_seconds=120`, `spike_threshold=2.0`, `time_window_seconds=30`, `use_cache=true` | Live capture for N seconds then combined report: spikes + correlation + per-container breakdown. Uses log cache-first if available (instant if logs synced). |

## Configuration

Optional environment variables (`.env` file):

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker socket path |
| `CONTAINER_LABEL_FILTER` | `""` | Filter containers by label (e.g. `app=myservice`) |
| `DEFAULT_TAIL_LINES` | `500` | Default log lines to fetch |
| `DEFAULT_SPIKE_TAIL_LINES` | `1000` | Log lines for spike detection |
| `DEFAULT_SPIKE_THRESHOLD` | `2.0` | Spike ratio threshold (current / baseline) |
| `DEFAULT_CORRELATION_WINDOW_SECONDS` | `30` | Co-occurrence window for correlation |
| `USE_LOGS_CACHE` | `true` | Check `.cache/logs/` before Docker API (cache-first strategy) |
| `CACHE_MAX_AGE_MINUTES` | `60` | Max cache age before re-fetching from Docker API |

### Remote Docker Setup

To analyze logs from a **remote Docker host**, set the `DOCKER_HOST` environment variable to point to the remote daemon via SSH:

#### Prerequisites

- SSH access to remote machine (password-less auth recommended)
- Docker daemon running on remote machine
- `docker` CLI installed locally

#### Configure SSH (one-time setup)

Add to `~/.ssh/config`:

```
Host staging.example.com
    User dev
    IdentityFile ~/.ssh/your_key_file
    StrictHostKeyChecking no
```

#### Test SSH connection to Docker daemon

```bash
docker -H ssh://dev@staging.example.com ps
```

#### Run analyzer on remote Docker

```bash
export DOCKER_HOST=ssh://dev@staging.example.com
uv run docker-log-analyzer-mcp list_containers
```

#### Make it persistent (optional)

Add to `~/.zshrc` or `~/.bash_profile`:

```bash
export DOCKER_HOST=ssh://dev@staging.example.com
```

Then all tool invocations automatically use the remote daemon:

```bash
uv run docker-log-analyzer-mcp detect_error_spikes
uv run docker-log-analyzer-mcp detect_data_leaks
```

> **Note:** All analysis happens locally on your machine. Only Docker logs are fetched from the remote daemon — no secrets or sensitive data are transmitted over SSH beyond what Docker already exposes.

### Log Cache Strategy (Cache-First)

All tools use a **cache-first strategy** for log fetching:

1. **Check cache** – `.cache/logs/<container>/<YYYY-MM-DD>.jsonl`
2. **If cache hit** (exists + fresh) – use cached logs instantly ⚡
3. **If cache miss** – fetch fresh from Docker API (current behavior)

**Sync logs first for maximum speed:**

```bash
# Sync logs for a time window (e.g., last 4 hours)
uv run docker-log-analyzer-mcp sync_docker_logs --since "4 hours ago"

# Now all tool calls use cache (100x faster!)
uv run docker-log-analyzer-mcp analyze_patterns          # reads from cache
uv run docker-log-analyzer-mcp detect_error_spikes      # reads from cache
uv run docker-log-analyzer-mcp capture_and_analyze      # instant bug reproduction

# Works even with containers stopped (offline analysis!)
docker compose down
uv run docker-log-analyzer-mcp correlate_containers     # still works via cache
```

**Cache structure:**

```
.cache/logs/
  ├── metadata.json                          (sync tracking)
  ├── web-app/
  │   ├── 2026-03-04.jsonl (5000 lines)
  │   ├── 2026-03-03.jsonl
  │   └── 2026-03-02.jsonl
  └── database/
      └── 2026-03-04.jsonl
```

**Cache features:**
- **Atomic writes** – temp file + rename, safe on crashes
- **Multi-day windows** – queries across date boundaries
- **Time-based args** – `"2 hours ago"`, `"7 days ago"`, ISO-8601 timestamps
- **Metadata tracking** – synced_at, line_count per date
- **Fallback safety** – always works without cache (just slower)

**Clear cache if needed:**

```bash
# Clear cache for one container
rm -rf .cache/logs/web-app/

# Clear all log cache
rm -rf .cache/logs/

# Keep pattern cache separate (.cache/patterns/)
```

### Pattern Analysis Cache

`analyze_patterns` results are cached to `.cache/patterns/<name>.json` after the first call. Subsequent calls return instantly from cache.

The cache persists **across container restarts** (keyed by container name only). This improves performance when containers are recreated with the same configuration.

**Important**: If you change the log pattern of a container (e.g., different format, language, or log source), you must manually clear the cache for that container:

```bash
# Remove cache for a specific container
rm .cache/patterns/test-database.json

# Or clear all pattern cache
rm -rf .cache/patterns/

# Then re-run the analysis
uv run python -c "
import json
from docker_log_analyzer.mcp_server import tool_analyze_patterns
print(json.dumps(tool_analyze_patterns('test-database'), indent=2))
"
```

Each cached result includes `cache_hit` (`true`/`false`) and `cached_at` (ISO-8601 UTC) so you can always tell how fresh the data is. Via Copilot, you can also use `force_refresh=True` to skip the cache on demand:

```bash
# Via Copilot message: "Re-analyze test-database with a fresh check"
```

## Test Log Generators

`docker-compose.test.yml` runs 4 containers emitting logs in different formats with periodic correlated error spikes:

| Service | Language | Format | Spike interval |
| ------- | -------- | ------ | -------------- |
| `test-web-app` | Python | ISO-8601 | 90 s |
| `test-database` | Java | syslog | 90 s (correlated with web-app) |
| `test-gateway` | Node.js | Apache | 90 s (correlated with web-app) |
| `test-cache` | Go | epoch | 120 s (independent) |

```bash
# Via MCP tool in Copilot chat:
# "Start the test containers"

# Or directly:
docker compose -f docker-compose.test.yml up --build -d
docker compose -f docker-compose.test.yml down
```

## Development

### Run tests

```bash
# Run all non-integration tests (unit + secret detector) – 111 tests
uv run pytest tests/ -m "not integration"

# Full suite including integration tests (requires Docker)
uv run pytest tests/
```

### Project layout

```text
docker_log_analyzer/
  mcp_server.py           # MCP server – 7 tools
  spike_detector.py       # Polars rolling-window spike detection
  correlator.py           # Cross-container temporal correlation
  log_pattern_analyzer.py # PatternDetector (regex-based)
  secret_detector.py      # SecretDetector (20 patterns, redaction)
  config.py               # Environment configuration
  logger.py               # Logging utility
log_generator/
  generate_logs.py        # Configurable random log generator
  Dockerfile
tests/
  conftest.py             # Shared fixtures
  test_spike_detector.py  # 16 unit tests
  test_correlator.py      # 17 unit tests
  test_pattern_detector.py # 24 unit tests
  test_secret_detector.py # 45 unit tests
  test_mcp_integration.py # 32 integration tests
```

## Error Patterns Detected

General: `ERROR`, `CRITICAL`, `FATAL`, `Exception`, `Traceback`, `panic:`, `SEVERE`, HTTP 5xx / 4xx

SQL — Java: `SQLException`, `HibernateException`, `JDBCException`, `could not execute statement`, `ORA-*` (Oracle), `PSQLException` (PostgreSQL JDBC), `SQLSyntaxErrorException`

SQL — PHP: `PDOException`, `mysqli_error`, `mysql_error`, `SQLSTATE[*]`, `Query failed`, `Deadlock found`, `Table * doesn't exist`

## Security

- Docker socket mounted read-only (`:ro`)
- No API keys required
- All analysis runs locally — no data leaves the machine

## Future Directions

### Watched Containers (Focused Analysis)

Define a static `(container_name, language)` watchlist so tools focus only on containers you care about.

**Design sketch:**

- `containers.py` — editable list: `WATCHED = [("api", "python"), ("db", "java"), ...]`
- `config.py` — exposes it as `settings.watched_containers` (overridable via env var)
- `list_containers` — splits response into `watched` / `others` with a language hint per entry and an edit hint for adding more
- Other tools — filter `client.container.list()` to watched names when no explicit `container_name` is given

This avoids noise from sidecar/infra containers and lets language detection skip auto-detection for known containers.

## License

MIT
