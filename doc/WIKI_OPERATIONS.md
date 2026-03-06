# Wiki Hub: Operations

Use this hub for setup, configuration, cache management, remote Docker, Copilot prompts, and day-to-day usage.

---

## Agent Use Rules

- Start here for "how to set up", "which environment variable", "what Copilot prompt", "how does caching work in practice".
- For tool parameter reference, use [WIKI_TOOLS.md](WIKI_TOOLS.md).
- For design decisions behind the cache strategy or algorithms, use [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md).

---

## Quick Start

### Prerequisites

- Docker running locally
- VSCode with [GitHub Copilot](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot) extension
- [uv](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`

### Install

```bash
git clone <repository-url>
cd Analyze_docker_log_w_llm
uv sync
```

Pre-configured in [`.vscode/mcp.json`](../.vscode/mcp.json). Open project in VSCode → switch Copilot Chat to **Agent** mode → tools are immediately available.

### Verify

```bash
uv run python -c "from docker_log_analyzer.mcp_server import run; print('OK')"
```

---

## Remote Docker Setup

Analyze logs from a remote host by setting `DOCKER_HOST` to point at the remote daemon via SSH.

### Prerequisites

- SSH access to the remote machine (passwordless auth recommended)
- Docker daemon running on the remote machine
- `docker` CLI installed locally

### One-time SSH config

Add to `~/.ssh/config`:

```
Host staging.example.com
    User dev
    IdentityFile ~/.ssh/your_key_file
    StrictHostKeyChecking no
```

### Test the connection

```bash
docker -H ssh://dev@staging.example.com ps
```

### Run the analyzer against remote Docker

```bash
export DOCKER_HOST=ssh://dev@staging.example.com
uv run docker-log-analyzer-mcp list_containers
```

### Make it persistent

Add to `~/.zshrc` or `~/.bash_profile`:

```bash
export DOCKER_HOST=ssh://dev@staging.example.com
```

> All analysis runs locally on your machine. Only logs are fetched over SSH — no sensitive data is transmitted beyond what Docker already exposes.

---

## Configuration

Optional environment variables (`.env` file or shell):

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker daemon socket or SSH URL |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `CONTAINER_LABEL_FILTER` | `""` | Filter containers by label (e.g., `env=prod`) |
| `DEFAULT_TAIL_LINES` | `500` | Default log lines to fetch |
| `DEFAULT_SPIKE_TAIL_LINES` | `1000` | Log lines for spike detection |
| `DEFAULT_SPIKE_THRESHOLD` | `2.0` | Spike ratio threshold (current / baseline) |
| `DEFAULT_CORRELATION_WINDOW_SECONDS` | `30` | Co-occurrence window for correlation |
| `CORRELATION_CACHE_TTL_MINUTES` | `30` | TTL for correlation result cache (0 = disabled) |
| `USE_LOGS_CACHE` | `true` | Enable cache-first strategy |
| `CACHE_MAX_AGE_MINUTES` | `60` | Max cache age before re-fetching from Docker API |

All settings are validated at startup via Pydantic BaseSettings. See [../docker_log_analyzer/config.py](../docker_log_analyzer/config.py).

---

## Log Cache Strategy

All log-reading tools use a **cache-first strategy**:

```
Tool call (e.g., detect_error_spikes)
  ↓
Check .cache/logs/<container>/YYYY-MM-DD.jsonl
  ↓
  ├─ Hit (recent)  → Use cached logs → Instant response ⚡
  ├─ Miss          → Fetch from Docker API → Cache result
  └─ Stale (>60m)  → Re-fetch from Docker API
```

### Sync for maximum speed

```bash
# Sync last 4 hours for all containers
uv run docker-log-analyzer-mcp sync_docker_logs --since "4 hours ago"

# All tools now read from cache (instant):
uv run docker-log-analyzer-mcp analyze_patterns
uv run docker-log-analyzer-mcp detect_error_spikes
uv run docker-log-analyzer-mcp map_service_dependencies

# Works even with containers stopped:
docker compose down
uv run docker-log-analyzer-mcp correlate_containers   # still works via cache
```

### Cache structure

```
.cache/logs/
  ├── metadata.json                 (sync tracking: synced_at, line_count per date)
  ├── test-web-app/
  │   ├── 2026-03-06.jsonl          (line-per-JSON: {"timestamp": "...", "message": "..."})
  │   └── 2026-03-05.jsonl
  └── test-database/
      └── 2026-03-06.jsonl
.cache/patterns/
  └── test-web-app.json             (analyze_patterns result, separate from log cache)
.cache/correlations/
  └── <md5>.json                    (correlate_containers result, keyed by container set + params)
```

### Clear cache

```bash
rm -rf .cache/logs/               # all log cache
rm -rf .cache/logs/test-web-app/  # one container
rm -rf .cache/patterns/           # pattern analysis cache (separate)
rm .cache/patterns/test-web-app.json  # one container's pattern cache
rm -rf .cache/correlations/       # correlation result cache
```

---

## Copilot Prompts

Use these natural language prompts in VSCode Copilot Chat (Agent mode):

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

### Service dependency mapping

- "Map the service dependencies across all my containers."
- "Which containers depend on the database?"
- "Show me the full dependency graph including transitive hops."
- "Are there any likely error cascade paths between my services?"
- "What services does test-web-app call based on its logs?"

### Sensitive data detection

- "Scan all containers for sensitive data like API keys and credentials."
- "Check test-database logs for data leaks in the last 60 seconds."
- "Detect critical-level secrets (API keys, tokens) in test-web-app."
- "Are there any passwords or credit card numbers in my container logs?"

### Bug reproduction capture

- "Watch test-web-app and test-database for the next 2 minutes — I'm about to reproduce the bug."
- "Capture all container logs for 90 seconds, then tell me what happened."
- "Monitor only test-gateway and test-cache for 1 minute with a spike threshold of 1.5."

### Combined investigation

- "List my containers, then check for error spikes and tell me which ones are most correlated."
- "My app seems unhealthy — analyze patterns and detect spikes across all containers."
- "Check if test-web-app and test-database are failing at the same time."

---

## Test Log Generators

`docker-compose.test.yml` runs 4 containers that emit correlated error spikes for testing:

| Service | Language | Log format | Spike interval |
|---------|----------|-----------|----------------|
| `test-web-app` | Python | ISO-8601 | 90 s |
| `test-database` | Java | syslog | 90 s (correlated with web-app) |
| `test-gateway` | Node.js | Apache | 90 s (correlated with web-app) |
| `test-cache` | Go | epoch | 120 s (independent) |

```bash
# Start via Copilot: "Start the test containers"

# Or directly:
docker compose -f docker-compose.test.yml up --build -d
docker compose -f docker-compose.test.yml down
```

---

## Troubleshooting

| Problem | Solution |
|---------|---------|
| Cache returning stale data | `rm -rf .cache/logs/` or pass `use_cache=false` to any tool |
| `sync_docker_logs` returns fewer logs than expected | Container may have been created more recently than your `--since` window; check with `docker inspect <name> \| grep Created` |
| Disk space growing | `find .cache/logs -name "*.jsonl" -mtime +7 -delete` |
| `metadata.json` corrupted or missing | Safe to delete — regenerated on next sync |
| Docker unavailable in tests | Unit tests auto-skip Docker; run `uv run pytest tests/ -m "not integration"` |

---

## Retrieval keywords

setup, install, quick start, configuration, environment variable, DOCKER_HOST, SSH, remote, cache, sync, cache-first, copilot, prompt, test containers, workflow, usage, operations, hub

**[negative keywords / not-this-doc]**
algorithm, module design, test suite, CI, coverage, unit tests, rolling window, correlation score, confidence level

---

## See also

- Tools reference: [WIKI_TOOLS.md](WIKI_TOOLS.md)
- Architecture hub: [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md)
- Quality hub: [WIKI_QUALITY.md](WIKI_QUALITY.md)
- Home: [WIKI_HOME.md](WIKI_HOME.md)
