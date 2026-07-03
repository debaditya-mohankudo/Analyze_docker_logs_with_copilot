# Copilot instructions — Docker Log Analyzer MCP Server

This repo is a **stateless, LLM-free** MCP server exposing 18 Docker log
analysis tools to VSCode Copilot Chat (Agent Mode). It is already wired up
via [`.vscode/mcp.json`](../.vscode/mcp.json) — switch Copilot Chat to
**Agent** mode and the tools below are available directly, no extra setup.

## What this project is (and isn't)

- All analysis is local, deterministic regex/Polars — **no OpenAI/Anthropic
  API calls, no LLM summarization inside the tools themselves.**
- Every tool is stateless: fetch from Docker → analyze → return JSON → exit.
  No background threads, no long-lived connections, no hidden caching layers
  beyond the explicit `.cache/` directory.
- Full architectural rules live in [CLAUDE.md](../CLAUDE.md) — read it before
  proposing changes to `tools.py`, `mcp_server.py`, or the caching layer.

## Tool selection

Don't guess which tool to call — use the selection flow and full parameter
reference in [doc/WIKI_TOOLS.md](../doc/WIKI_TOOLS.md). Quick summary:

| Question | Tool |
|---|---|
| What containers are running? | `list_containers` |
| Last error in a container right now | `get_last_errors` |
| What log format/language/health-checks does X use? | `analyze_patterns` |
| Did error rate spike? | `analyze_error_spikes` |
| Are services failing together? | `analyze_correlations` |
| Likely root-cause service? | `analyze_root_causes` |
| What depends on what? | `map_service_dependencies` |
| Secrets/PII in logs? | `detect_data_leaks` |
| Need a request's cross-service journey? | `trace_request_flow` |
| Semantic error grouping (db/network/timeout/...)? | `classify_errors` |
| Stack trace → source code? | `analyze_code_context` |
| Not sure where to start? | `plan_investigation` |
| Need offline/fast repeat analysis? | `sync_docker_logs`, then re-run other tools |
| About to reproduce a bug live? | `capture_logs` |
| Check cache coverage/size before analyzing? | `cache_info` |
| Force fresh logs instead of cached? | `clear_cache` |

For broad incidents, chain tools rather than guessing: `analyze_error_spikes`
→ `analyze_correlations` → `map_service_dependencies` → `analyze_root_causes`.
`plan_investigation` will generate this chain automatically from a
free-text symptom description and save it to `.cache/plans/`.

Natural-language prompt examples for each tool are catalogued in
[doc/WIKI_COPILOT_PROMPTS.md](../doc/WIKI_COPILOT_PROMPTS.md).

## Test containers

Four synthetic log-generator containers (`test-web-app`, `test-database`,
`test-cache`, `test-gateway`) are the standard target for trying tools out —
different log formats/languages per container, staggered error-spike timing
so `analyze_correlations` has something real to find. Start them with
`start_test_containers`, defined in
[docker-compose.test.yml](../docker-compose.test.yml).

## Working on this repo's own code

- New tools: implement `tool_<name>()` in `tools.py`, register with
  `@mcp.tool()` in `mcp_server.py`, keep it stateless, add unit tests before
  functional tests (see CLAUDE.md §4.0 — wiring bugs are invisible to
  pure-module tests; a test that exercises `tool_<name>()` directly catches
  mis-unpacked returns and wrong argument types that unit tests on the
  underlying module alone would miss).
- Run `uv run pytest tests/ -m "not integration"` for the fast CI-safe suite;
  full suite (`pytest tests/`) needs Docker running.
- Never introduce LLM calls, message brokers, background schedulers, or raw
  log persistence to disk — see CLAUDE.md §11 for the full "what not to do"
  list.
