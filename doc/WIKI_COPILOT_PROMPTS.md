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

**Tools used:** `detect_error_spikes`

---

## Cross-container correlation

- "Are there any correlated errors between my containers?"
- "Correlate container errors using a 60-second time window."
- "Which containers are failing together? Use a 30-second co-occurrence window."
- "Is test-gateway causing failures in test-web-app and test-database?"

**Tools used:** `correlate_containers`

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

**Tools used:** `rank_root_causes`

> Copilot may chain: `detect_error_spikes` → `correlate_containers` → `map_service_dependencies` → `rank_root_causes` for a full investigation.

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

**Tools used:** `capture_and_analyze`

---

## Combined investigation workflows

- "List my containers, then check for error spikes and tell me which ones are most correlated."
- "My app seems unhealthy — analyze patterns and detect spikes across all containers."
- "Check if test-web-app and test-database are failing at the same time."
- "Something is broken — start from scratch and give me a full system health report."

**Tools used:** `list_containers` → `get_last_errors` → `detect_error_spikes` → `correlate_containers` → `rank_root_causes`

---

## Test containers

- "Start the test log-generator containers."
- "Stop and remove the test containers."
- "Rebuild and restart the test containers."

**Tools used:** `start_test_containers`, `stop_test_containers`

---

## Retrieval keywords

copilot, prompt, natural language, agent mode, vscode, workflow, triage, investigation, discovery, spike, correlation, dependency, root cause, secret, cache, capture, test containers, get_last_errors, rank_root_causes, analyze_patterns, detect_error_spikes

**[negative keywords / not-this-doc]**
parameters, return shapes, algorithm internals, module design, CI, coverage, configuration, environment variables

---

## See also

- Full tool reference: [WIKI_TOOLS.md](WIKI_TOOLS.md)
- Operations & setup: [WIKI_OPERATIONS.md](WIKI_OPERATIONS.md)
- Architecture: [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md)
- Home: [WIKI_HOME.md](WIKI_HOME.md)
