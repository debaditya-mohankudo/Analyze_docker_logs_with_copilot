---
tags: [docker, log, analyzer, mcp, copilot, agent, quick-start, readme, tui]
last_updated: 2026-07-12
---

# Docker Log Analyzer – MCP Server

A stateless, **LLM-free** Docker log analysis tool exposed as an [MCP](https://modelcontextprotocol.io) server for **VSCode Copilot Agent Mode**, plus a standalone terminal UI (`docker-log-analyzer-tui`) for running the same analysis without VSCode. No Kafka, no OpenAI API key — all analysis runs locally using regex and [Polars](https://pola.rs).

**Full documentation:** [doc/WIKI_HOME.md](doc/WIKI_HOME.md)

---

## What you can say in Copilot

Just type naturally in Copilot Chat (Agent mode). No tool names needed.

### Triage an incident

```text
Something is broken — start from scratch and give me a full system health report.
Which container is most likely causing the cascade of errors?
Is test-gateway causing failures in test-web-app and test-database?
```

### Investigate a specific container

```text
What was the last error in test-cache?
Show me the last 5 fatal errors from test-web-app.
Analyze the log patterns for the test-database container.
What log format and programming language is test-web-app using?
```

### Detect anomalies

```text
Are there any error rate anomalies in my containers right now?
Which containers had the worst error spikes in the last few minutes?
Correlate container errors using a 60-second time window.
Which containers are failing together?
```

### Map dependencies

```text
Map the service dependencies across all my containers.
Which containers depend on the database?
Are there any likely error cascade paths between my services?
```

### Security scanning

```text
Scan all containers for sensitive data like API keys and credentials.
Are there any passwords or credit card numbers in my container logs?
```

### Capture and cache

```text
Watch test-web-app and test-database for the next 2 minutes — I'm about to reproduce the bug.
I'm about to stop the containers — sync their logs first so I can analyze offline.
Sync logs from the last 4 hours for all containers.
```

Full prompt reference: **[doc/WIKI_COPILOT_PROMPTS.md](doc/WIKI_COPILOT_PROMPTS.md)** · Tool API details: **[doc/WIKI_TOOLS.md](doc/WIKI_TOOLS.md)**.

---

## Terminal UI

Prefer a terminal over VSCode? `docker-log-analyzer-tui` is a [Textual](https://textual.textualize.io) app that walks through the same `tool_*` functions the MCP server exposes — connect to local or remote (SSH) Docker, pick containers, run an analysis, view/save the result. No mouse required; every action is a key binding.

```bash
uv run docker-log-analyzer-tui
```

---

## Quick Start

```bash
git clone <repository-url>
cd Analyze_docker_logs_with_copilot
uv sync
```

Pre-configured in [`.vscode/mcp.json`](.vscode/mcp.json). Open in VSCode → switch Copilot Chat to **Agent** mode → tools are immediately available.

```bash
# Verify
uv run python -c "from docker_log_analyzer.mcp_server import run; print('OK')"
```

> **Remote desktop / non-local Docker?** See **[doc/WIKI_OPERATIONS.md](doc/WIKI_OPERATIONS.md)** for remote Docker host configuration, SSH tunnelling, and environment variable setup.

Configuration is a Pydantic `Settings` singleton in [`docker_log_analyzer/config.py`](docker_log_analyzer/config.py), loaded from environment variables or a `.env` file at the repo root. Copy [`.env.example`](.env.example) to `.env` and edit as needed.

---

## Configuration

Optional environment variables (`.env` file or shell):

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker daemon socket or SSH URL — wired into every tool's `DockerClient(host=...)` call |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOG_FILE_ENABLED` | `true` | Also write this server's own operational log as rotating JSONL (separate from container logs the tools analyze) |
| `LOG_FILE_PATH` | `.cache/app_logs/docker-log-analyzer.jsonl` | Path to the rotating JSONL log file |
| `LOG_FILE_MAX_BYTES` | `10000000` | Rotate the JSONL log after this size |
| `LOG_FILE_BACKUP_COUNT` | `3` | Rotated JSONL files to retain |
| `CONTAINER_LABEL_FILTER` | `""` | Filter containers by label (e.g., `env=prod`) |
| `DEFAULT_TAIL_LINES` | `500` | Default log lines to fetch |
| `DEFAULT_SPIKE_TAIL_LINES` | `1000` | Log lines for spike detection |
| `DEFAULT_SPIKE_THRESHOLD` | `2.0` | Spike ratio threshold (current / baseline) |
| `DEFAULT_CORRELATION_WINDOW_SECONDS` | `30` | Co-occurrence window for correlation |
| `CORRELATION_CACHE_TTL_MINUTES` | `30` | TTL for correlation result cache (0 = disabled) |
| `REPO_PATHS` | `[]` | Local repo roots searched by `analyze_code_context` to resolve stack-trace files |
| `CONTAINER_REPO_MAP` | `{}` | Explicit container→repo-path overrides; takes precedence over `REPO_PATHS` auto-detection |
| `CODE_CONTEXT_LINES` | `10` | Source lines shown before/after the error line in `analyze_code_context` |
| `MAX_STACK_FRAMES` | `10` | Max stack frames extracted per error event |
| `REQUEST_ID_PATTERNS` | see `config.py` | Named regex patterns used by `trace_request_flow` — see [doc/WIKI_TRACE_REQUEST_FLOW.md](doc/WIKI_TRACE_REQUEST_FLOW.md) |
| `TRACE_WINDOW_SECONDS` | `120` | Max spread between first/last event for one request ID before it's dropped as an accidental collision |

All settings are validated at startup via Pydantic BaseSettings. There is **no global cache-disable toggle or TTL setting** — `use_cache` is a per-call tool parameter (default `True`); see [doc/WIKI_OPERATIONS.md](doc/WIKI_OPERATIONS.md) for cache strategy details.

---

## Architecture

VSCode Copilot Chat (Agent Mode) → MCP stdio → 19 stateless tool calls → Docker SDK → JSON. The TUI (`docker_log_analyzer/tui.py`) calls the same `tool_*` functions directly, without an MCP hop.

Full module map and algorithm details: **[doc/WIKI_ARCHITECTURE.md](doc/WIKI_ARCHITECTURE.md)**.

---

## Documentation

| Page | Purpose |
| ---- | ------- |
| [doc/WIKI_HOME.md](doc/WIKI_HOME.md) | Navigation hub and agent routing table |
| [doc/WIKI_TOOLS.md](doc/WIKI_TOOLS.md) | All 19 tools — parameters, return shapes, behavior |
| [doc/WIKI_OPERATIONS.md](doc/WIKI_OPERATIONS.md) | Setup, config, remote Docker, cache, Copilot prompts |
| [doc/WIKI_ARCHITECTURE.md](doc/WIKI_ARCHITECTURE.md) | Module map, algorithms, design decisions |
| [doc/WIKI_QUALITY.md](doc/WIKI_QUALITY.md) | Test suite, CI, coverage, adding tests |
| [doc/WIKI_SECURITY.md](doc/WIKI_SECURITY.md) | Secret detection, redaction rules, path confinement guardrails |
| [doc/WIKI_TRACE_REQUEST_FLOW.md](doc/WIKI_TRACE_REQUEST_FLOW.md) | Request/trace/correlation ID tracing across containers |
| [CLAUDE.md](CLAUDE.md) | Architecture rules and contributor constraints |

---

## Tests

```bash
# Unit tests only — no Docker, ~0.8s
uv run pytest tests/ -m "not integration"

# Full suite — requires Docker + test containers
uv run pytest tests/
```

644 unit tests + integration suite. See [doc/WIKI_QUALITY.md](doc/WIKI_QUALITY.md).

---

## Security

- Connects to Docker daemon read-only (no container modification)
- No API keys required
- All analysis runs locally — no data leaves the machine

Full details: **[doc/WIKI_SECURITY.md](doc/WIKI_SECURITY.md)** — secret detection patterns, redaction rules, path confinement guardrails.

## Project Planning
Epic planning, subtask creation, task grooming, task implementation using https://github.com/debaditya-mohankudo/Lite-Task-Framework

## License

MIT
