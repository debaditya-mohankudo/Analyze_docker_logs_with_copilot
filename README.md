# Docker Log Analyzer – MCP Server

A stateless, **LLM-free** Docker log analysis tool exposed as an [MCP](https://modelcontextprotocol.io) server for **VSCode Copilot Agent Mode**. No Kafka, no OpenAI API key — all analysis runs locally using regex and [Polars](https://pola.rs).

**Full documentation:** [doc/WIKI_HOME.md](doc/WIKI_HOME.md)

---

## What it does

12 MCP tools covering the full triage workflow — discovery, pattern analysis, spike detection, secret scanning, dependency mapping, root cause ranking, and log caching. Full reference: **[doc/WIKI_TOOLS.md](doc/WIKI_TOOLS.md)**.

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

VSCode Copilot Chat (Agent Mode) → MCP stdio → 12 stateless tool calls → Docker SDK → JSON.

Full module map and algorithm details: **[doc/WIKI_ARCHITECTURE.md](doc/WIKI_ARCHITECTURE.md)**.

---

## Documentation

| Page | Purpose |
| ---- | ------- |
| [doc/WIKI_HOME.md](doc/WIKI_HOME.md) | Navigation hub and agent routing table |
| [doc/WIKI_TOOLS.md](doc/WIKI_TOOLS.md) | All 12 tools — parameters, return shapes, behavior |
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

319 unit tests + integration suite. See [doc/WIKI_QUALITY.md](doc/WIKI_QUALITY.md).

---

## Security

- Connects to Docker daemon read-only (no container modification)
- No API keys required
- All analysis runs locally — no data leaves the machine

## License

MIT
