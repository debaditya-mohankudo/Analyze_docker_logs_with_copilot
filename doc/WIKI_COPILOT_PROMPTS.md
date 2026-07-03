---
tags: [copilot, prompt, natural-language, agent-mode, vscode, workflow, triage]
last_updated: 2026-07-03
---

# Wiki Hub: Copilot Prompts

Natural language prompts for VSCode Copilot Chat (Agent mode). Each section maps to one or more MCP tools.

For tool parameter details, see [WIKI_TOOLS.md](WIKI_TOOLS.md).

---

## Discovery

- "What Docker containers are currently running?"
- "List all my running containers and their status."

**Tools used:** `list_containers`

---

## Quick triage — last error in a container

- "What was the last error in test-cache?"
- "Show me the last 5 fatal errors from test-web-app."
- "Did test-database log any panics recently?"
- "What broke in test-gateway? Show me the last errors."

**Tools used:** `get_last_errors`

---

## Pattern analysis

- "Analyze the log patterns for the test-database container."
- "What log format and programming language is test-web-app using?"
- "Show me the log level distribution and top errors for test-gateway."
- "Are there any health check endpoints being hit frequently in test-cache?"

**Tools used:** `analyze_patterns`

---

## Error spike detection

- "Check for error spikes across all containers in the last 1000 lines."
- "Detect error spikes in test-database with a threshold of 1.5."
- "Are there any error rate anomalies in my containers right now?"
- "Which containers had the worst error spikes in the last few minutes?"

**Tools used:** `analyze_error_spikes`

---

## Cross-container correlation

- "Are there any correlated errors between my containers?"
- "Correlate container errors using a 60-second time window."
- "Which containers are failing together? Use a 30-second co-occurrence window."
- "Is test-gateway causing failures in test-web-app and test-database?"

**Tools used:** `analyze_correlations`

---

## Service dependency mapping

- "Map the service dependencies across all my containers."
- "Which containers depend on the database?"
- "Show me the full dependency graph including transitive hops."
- "Are there any likely error cascade paths between my services?"
- "What services does test-web-app call based on its logs?"

**Tools used:** `map_service_dependencies`

---

## Root cause ranking

- "Find the root cause of my system failure."
- "Which container is most likely causing the cascade of errors?"
- "Rank my containers by how likely they are to be the source of this incident."
- "Score all containers by root-cause likelihood — something is wrong but I don't know where."

**Tools used:** `analyze_root_causes`

> Copilot may chain: `analyze_error_spikes` → `analyze_correlations` → `map_service_dependencies` → `analyze_root_causes` for a full investigation.

---

## Sensitive data detection

- "Scan all containers for sensitive data like API keys and credentials."
- "Check test-database logs for data leaks in the last 60 seconds."
- "Detect critical-level secrets (API keys, tokens) in test-web-app."
- "Are there any passwords or credit card numbers in my container logs?"

**Tools used:** `detect_data_leaks`

---

## Log caching and offline analysis

- "Sync logs from the last 4 hours for all containers."
- "Cache test-web-app logs from 2026-03-07T10:00:00Z to 2026-03-07T12:00:00Z."
- "I'm about to stop the containers — sync their logs first so I can analyze offline."

**Tools used:** `sync_docker_logs`

---

## Bug reproduction capture

- "Watch test-web-app and test-database for the next 2 minutes — I'm about to reproduce the bug."
- "Capture all container logs for 90 seconds, then tell me what happened."
- "Monitor only test-gateway and test-cache for 1 minute with a spike threshold of 1.5."

**Tools used:** `capture_logs`

---

## Code context — deep-dive into failing source code

- "Show me the source code around the stack trace in payment-service."
- "Parse the stack traces from api-gateway and show the failing lines. Repo is at /home/user/api."
- "The worker container is crashing — analyze its stack trace and show me the code."
- "After finding the root cause, show me the code context for the top container."

**Tools used:** `analyze_code_context`

> Requires `REPO_PATHS` or `CONTAINER_REPO_MAP` set in `.env`, or pass `repo_path` directly.

---

## Investigation planning

- "Plan how to investigate payment-service 500 errors and high latency in checkout."
- "Create an investigation plan for connection refused errors. Focus on root cause."
- "Plan a security investigation for auth-service — I suspect credentials are leaking."
- "Give me a full investigation plan scoped to api-gateway and database containers."

**Tools used:** `plan_investigation` → saves Markdown plan to `.cache/plans/`

---

## Request flow tracing

- "Trace request flows across all containers — show me how requests propagate."
- "Which request IDs appear in both test-gateway and test-web-app?"
- "Show me the timeline for requests that touched at least 3 containers."
- "Trace request IDs in test-web-app and test-database — are any requests failing end-to-end?"

**Tools used:** `trace_request_flow`

> Configure ID patterns via `REQUEST_ID_PATTERNS` in `.env` (e.g. `request_id`, `trace_id`, `correlation_id`).

---

## Error classification

- "What kinds of errors are happening in test-web-app — database, network, or application?"
- "Classify all errors across my containers and tell me the dominant category."
- "Are the errors in test-database mostly timeouts or connection failures?"
- "Show me only database and network errors across all containers."
- "What category of errors is spiking in test-gateway right now?"

**Tools used:** `classify_errors`

> Use after `analyze_error_spikes` to understand *what kind* of errors are spiking, or before `analyze_root_causes` to prioritize infrastructure errors over application errors.

---

## Combined investigation workflows

- "List my containers, then check for error spikes and tell me which ones are most correlated."
- "My app seems unhealthy — analyze patterns and detect spikes across all containers."
- "Check if test-web-app and test-database are failing at the same time."
- "Something is broken — start from scratch and give me a full system health report."

**Tools used:** `list_containers` → `get_last_errors` → `analyze_error_spikes` → `analyze_correlations` → `analyze_root_causes`

---

## Test containers

- "Start the test log-generator containers."
- "Stop and remove the test containers."
- "Rebuild and restart the test containers."

**Tools used:** `start_test_containers`, `stop_test_containers`

---

## Cache management

- "How much log data is cached right now?"
- "Show me cache coverage for test-database."
- "Clear the log cache for test-web-app — I want fresh logs."
- "Wipe the entire log cache and start over."

**Tools used:** `cache_info`, `clear_cache`

---

## Retrieval keywords

copilot, prompt, natural language, agent mode, vscode, workflow, triage, investigation, discovery, spike, correlation, dependency, root cause, secret, cache, capture, test containers, get_last_errors, analyze_root_causes, analyze_patterns, analyze_error_spikes, trace_request_flow, classify_errors, request tracing, error classification, request ID, trace ID, database errors, network errors, cache_info, clear_cache, cache management

**[negative keywords / not-this-doc]**
parameters, return shapes, algorithm internals, module design, CI, coverage, configuration, environment variables

---

## See also

- Full tool reference: [WIKI_TOOLS.md](WIKI_TOOLS.md)
- Operations & setup: [WIKI_OPERATIONS.md](WIKI_OPERATIONS.md)
- Architecture: [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md)
- Home: [WIKI_HOME.md](WIKI_HOME.md)
