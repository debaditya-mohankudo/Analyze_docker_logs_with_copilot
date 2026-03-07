# Docker Log Analyzer – MCP Server

A stateless, **LLM-free** Docker log analysis tool exposed as an [MCP](https://modelcontextprotocol.io) server for **VSCode Copilot Agent Mode**. No Kafka, no OpenAI API key — all analysis runs locally using regex and [Polars](https://pola.rs).

**Full documentation:** [doc/WIKI_HOME.md](doc/WIKI_HOME.md)

---

## What it does

| Tool | Purpose |
| ---- | ------- |
| `list_containers` | List running Docker containers |
| `analyze_patterns` | Detect timestamp format, language, log levels, health checks |
| `detect_error_spikes` | Polars rolling-window error rate anomaly detection |
| `correlate_containers` | Pairwise temporal error co-occurrence (score 0–1) |
| `detect_data_leaks` | Scan for 20 secret patterns with redaction and remediation |
| `map_service_dependencies` | Infer service dependency graph + cascade candidates from logs |
| `sync_docker_logs` | Sync logs to cache for offline / instant analysis |
| `capture_and_analyze` | Live capture + combined spike + correlation report |
| `start_test_containers` | Start 4-service test stack |
| `stop_test_containers` | Stop and remove test containers |

---

## Quick Start

```bash
git clone <repository-url>
cd Analyze_docker_log_w_llm
uv sync
```

Pre-configured in [`.vscode/mcp.json`](.vscode/mcp.json). Open in VSCode → switch Copilot Chat to **Agent** mode → tools are immediately available.

```bash
# Verify
uv run python -c "from docker_log_analyzer.mcp_server import run; print('OK')"
```

---

## Architecture

```text
VSCode Copilot Chat (Agent Mode)
        │  calls tools via MCP stdio (.vscode/mcp.json)
        ▼
docker-log-analyzer-mcp  (Python MCP server)
        │
        ├── list_containers           → Docker SDK
        ├── analyze_patterns          → Docker SDK + PatternDetector (regex)
        ├── detect_error_spikes       → Docker SDK + Polars rolling-window
        ├── correlate_containers      → Docker SDK + pairwise temporal scan
        ├── detect_data_leaks         → Docker SDK + SecretDetector (regex + redaction)
        ├── map_service_dependencies  → Docker SDK + DependencyMapper (regex graph)
        ├── rank_root_causes          → dependency graph + cascade candidates + spike timing
        ├── sync_docker_logs          → cache-first log sync (.cache/logs/)
        ├── capture_and_analyze       → live capture + combined report
        ├── start_test_containers     → docker compose (docker-compose.test.yml)
        └── stop_test_containers      → docker compose down
```

Each tool call is **stateless**: fetch logs from Docker SDK → analyse → return JSON.

---

## Documentation

| Page | Purpose |
| ---- | ------- |
| [doc/WIKI_HOME.md](doc/WIKI_HOME.md) | Navigation hub and agent routing table |
| [doc/WIKI_TOOLS.md](doc/WIKI_TOOLS.md) | All 10 tools — parameters, return shapes, behavior |
| [doc/WIKI_OPERATIONS.md](doc/WIKI_OPERATIONS.md) | Setup, config, remote Docker, cache, Copilot prompts |
| [doc/WIKI_ARCHITECTURE.md](doc/WIKI_ARCHITECTURE.md) | Module map, algorithms, design decisions |
| [doc/WIKI_QUALITY.md](doc/WIKI_QUALITY.md) | Test suite, CI, coverage, adding tests |
| [CLAUDE.md](CLAUDE.md) | Architecture rules and contributor constraints |

---

## Tests

```bash
# Unit tests only — no Docker, ~0.8s
uv run pytest tests/ -m "not integration"

# Full suite — requires Docker + test containers
uv run pytest tests/
```

220 tests (163 unit + 57 integration). See [doc/WIKI_QUALITY.md](doc/WIKI_QUALITY.md).

---

## Security

- Docker socket mounted read-only (`:ro`)
- No API keys required
- All analysis runs locally — no data leaves the machine

## License

MIT
