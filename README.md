# Docker Log Analyzer – MCP Server

A stateless, **LLM-free** Docker log analysis tool exposed as an [MCP](https://modelcontextprotocol.io) server for **VSCode Copilot Agent Mode**. No Kafka, no OpenAI API key — all analysis runs locally using regex and [Polars](https://pola.rs).

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

> **Remote desktop / non-local Docker?** See **[doc/WIKI_OPERATIONS.md](doc/WIKI_OPERATIONS.md)** for remote Docker host configuration, SSH tunnelling, and environment variable setup.

---

## Architecture

VSCode Copilot Chat (Agent Mode) → MCP stdio → 16 stateless tool calls → Docker SDK → JSON.

Full module map and algorithm details: **[doc/WIKI_ARCHITECTURE.md](doc/WIKI_ARCHITECTURE.md)**.

---

## Documentation

| Page | Purpose |
| ---- | ------- |
| [doc/WIKI_HOME.md](doc/WIKI_HOME.md) | Navigation hub and agent routing table |
| [doc/WIKI_TOOLS.md](doc/WIKI_TOOLS.md) | All 16 tools — parameters, return shapes, behavior |
| [doc/WIKI_OPERATIONS.md](doc/WIKI_OPERATIONS.md) | Setup, config, remote Docker, cache, Copilot prompts |
| [doc/WIKI_ARCHITECTURE.md](doc/WIKI_ARCHITECTURE.md) | Module map, algorithms, design decisions |
| [doc/WIKI_QUALITY.md](doc/WIKI_QUALITY.md) | Test suite, CI, coverage, adding tests |
| [doc/WIKI_SECURITY.md](doc/WIKI_SECURITY.md) | Secret detection, redaction rules, path confinement guardrails |
| [CLAUDE.md](CLAUDE.md) | Architecture rules and contributor constraints |

---

## Tests

```bash
# Unit tests only — no Docker, ~0.8s
uv run pytest tests/ -m "not integration"

# Full suite — requires Docker + test containers
uv run pytest tests/
```

319 unit tests + integration suite. See [doc/WIKI_QUALITY.md](doc/WIKI_QUALITY.md).

---

## Security

- Connects to Docker daemon read-only (no container modification)
- No API keys required
- All analysis runs locally — no data leaves the machine

Full details: **[doc/WIKI_SECURITY.md](doc/WIKI_SECURITY.md)** — secret detection patterns, redaction rules, path confinement guardrails.

## License

MIT
