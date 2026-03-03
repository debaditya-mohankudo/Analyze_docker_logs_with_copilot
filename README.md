# Docker Log Analyzer – MCP Server

A stateless, **LLM-free** Docker log analysis tool exposed as an [MCP](https://modelcontextprotocol.io) server for **VSCode Copilot Agent Mode**. No Kafka, no OpenAI API key — all analysis runs locally using regex and [Polars](https://pola.rs).

## Features

- **Pattern Detection** – identifies timestamp format (ISO-8601, syslog, epoch, Apache) and programming language (Python, Java, Go, Node.js) from log lines
- **Error Spike Detection** – Polars rolling-window analysis flags minute-buckets where error rate exceeds a configurable baseline multiplier
- **Cross-Container Correlation** – pairwise temporal scoring of error co-occurrence across containers (score 0–1)
- **Copilot Agent Mode** – registered as an MCP stdio server; Copilot orchestrates via chat, every tool runs locally

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

## MCP Tools

| Tool | Parameters | Description |
| ---- | ---------- | ----------- |
| `list_containers` | — | List running Docker containers |
| `analyze_patterns` | `container_name?`, `tail=500`, `force_refresh=false` | Timestamp format, language, log levels, health checks, top errors. Results cached to disk per container; `force_refresh=true` bypasses the cache. |
| `detect_error_spikes` | `container_name?`, `tail=1000`, `spike_threshold=2.0` | Rolling-window error spike detection |
| `correlate_containers` | `time_window_seconds=30`, `tail=500` | Pairwise cross-container error correlation |
| `start_test_containers` | `rebuild=false` | Start 4-service test stack (`docker-compose.test.yml`) |
| `stop_test_containers` | — | Stop and remove test containers |
| `capture_and_analyze` | `container_names[]?`, `duration_seconds=120`, `spike_threshold=2.0`, `time_window_seconds=30` | Live capture for N seconds then combined report: spikes + correlation + per-container breakdown |

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

### Pattern Analysis Cache

`analyze_patterns` results are cached to `.cache/patterns/<name>_<short_id>.json` after the first call. Subsequent calls return instantly from cache.

The cache is **automatically invalidated** when a container is recreated (the short ID changes). To force a fresh analysis on a still-running container:

```bash
# Via Copilot: "Re-analyze test-database, force a refresh"
# Or directly:
uv run python -c "
import json
from docker_log_analyzer.mcp_server import tool_analyze_patterns
print(json.dumps(tool_analyze_patterns('test-database', force_refresh=True), indent=2))
"
```

Each cached result includes `cache_hit` (`true`/`false`) and `cached_at` (ISO-8601 UTC) so you can always tell how fresh the data is.

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

## Example Copilot Prompts

Use these natural language prompts in VSCode Copilot Chat (Agent mode) to invoke the tools:

### Discovery

> "What Docker containers are currently running?"
> "List all my running containers and their status."

### Pattern analysis

> "Analyze the log patterns for the test-database container."
> "What log format and programming language is test-web-app using?"
> "Show me the log level distribution and top errors for test-gateway."
> "Are there any health check endpoints being hit frequently in test-cache?"

### Error spike detection

> "Check for error spikes across all containers in the last 1000 lines."
> "Detect error spikes in test-database with a threshold of 1.5."
> "Are there any error rate anomalies in my containers right now?"
> "Which containers had the worst error spikes in the last few minutes?"

### Cross-container correlation

> "Are there any correlated errors between my containers?"
> "Correlate container errors using a 60-second time window."
> "Which containers are failing together? Use a 30-second co-occurrence window."
> "Is test-gateway causing failures in test-web-app and test-database?"

### Test containers

> "Start the test log generator containers."
> "Start the test containers and rebuild the images."
> "Stop the test containers."

### Bug reproduction capture

> "Watch test-web-app and test-database for the next 2 minutes — I'm about to reproduce the bug."
> "Capture all container logs for 90 seconds, then tell me what happened."
> "Monitor only test-gateway and test-cache for 1 minute with a spike threshold of 1.5."
> "Start capturing now across all containers — I'll trigger the failure in a moment."

### Combined investigation

> "List my containers, then check for error spikes and tell me which ones are most correlated."
> "My app seems unhealthy — analyze patterns and detect spikes across all containers."
> "Check if test-web-app and test-database are failing at the same time."

## Development

### Run tests

```bash
# Unit tests only (no Docker required) – 66 tests
uv run pytest tests/ -m unit

# Full suite including integration tests (requires Docker)
uv run pytest tests/
```

### Project layout

```text
docker_log_analyzer/
  mcp_server.py           # MCP server – 6 tools
  spike_detector.py       # Polars rolling-window spike detection
  correlator.py           # Cross-container temporal correlation
  log_pattern_analyzer.py # PatternDetector (regex-based)
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

## License

MIT
