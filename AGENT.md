---
tags: [agent, agents-md, contribution, onboarding]
last_updated: 2026-07-03
---

# AGENT.md — Docker Log Analyzer MCP Server

Generic-agent entry point for this repo (Cursor, Aider, Codex, etc.). Claude
Code reads [CLAUDE.md](CLAUDE.md) directly; VSCode Copilot Chat reads
[.github/copilot-instructions.md](.github/copilot-instructions.md). All three
must stay consistent — **CLAUDE.md is the canonical source of architectural
rules**; this file is a condensed pointer for agents that only look for
`AGENT.md`.

## What this project is

A stateless, **LLM-free** MCP (Model Context Protocol) stdio server exposing
18 Docker log analysis tools. No Kafka, no OpenAI/Anthropic API calls inside
the tools themselves — every tool fetches Docker logs, runs local
regex/Polars analysis, returns JSON, and exits. No background state.

## Before making changes

Read [CLAUDE.md](CLAUDE.md) in full — it defines:
- §1 Core design principles (statelessness, determinism, tool isolation)
- §3 Cache-first log fetching (`.cache/logs/<container>/<date>.parquet`,
  window-coverage keyed, no TTL)
- §4 Test priority order — design-principle tests (wiring, isolation, error
  contract, determinism) before feature tests
- §6 Security rules (secret redaction, path confinement) — canonical detail
  in [doc/WIKI_SECURITY.md](doc/WIKI_SECURITY.md)
- §11 What not to do (no LLM calls, no message brokers, no schedulers, no
  raw log persistence outside `.cache/`)

## Adding a new tool

Follow [.claude/commands/add-new-tool.md](.claude/commands/add-new-tool.md)
(the executable scaffold template — keep it in sync with the drift-prone
copy at [SKILL.md](SKILL.md)):

1. Implement `tool_<name>()` in `docker_log_analyzer/tools.py`
2. Register with `@mcp.tool()` in `docker_log_analyzer/mcp_server.py` — no
   logic there, wiring only
3. Mock `docker_log_analyzer.tools._docker_client` in tests — this project
   uses `python_on_whales.DockerClient`, **not** the `docker-py` SDK
4. Write tests in CLAUDE.md §4.0 order, then update README's tools table

## Running tests

```bash
uv sync
uv run pytest tests/ -m "not integration"   # CI-safe, no Docker required
uv run pytest tests/                        # full suite, needs Docker running
```

## Documentation map

Start at [doc/WIKI_HOME.md](doc/WIKI_HOME.md) for the full doc index —
architecture, tool reference, security, request tracing, and historical
design-review/proposal docs all live under `doc/`.
