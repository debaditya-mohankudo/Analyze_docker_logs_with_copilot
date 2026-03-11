# Investigation Planner

`plan_investigation` is a deterministic, rule-based MCP tool that acts as a
**DevOps investigation planner**. Given symptom descriptions, it classifies
the problem signals and produces an ordered investigation plan mapped to the
available analysis tools — with no LLM required.

---

## What It Does

1. Accepts free-text symptom descriptions and optional container scope / focus
2. Classifies symptoms into signal categories using regex heuristics
3. Maps signals to an ordered sequence of MCP tool calls
4. Writes the full plan to a Markdown file under `.cache/plans/`
5. Returns the file path (not the plan inline)

---

## Tool Signature

```
plan_investigation(
    symptoms:   list[str],
    containers: list[str] | None = None,
    focus:      str | None = None,
) -> dict
```

### Parameters

| Parameter    | Type             | Default    | Description |
|--------------|------------------|------------|-------------|
| `symptoms`   | `list[str]`      | required   | Observed problem descriptions in plain English |
| `containers` | `list[str]`      | `None`     | Limit scope to specific containers; omit for all |
| `focus`      | `str`            | `"general"`| One of `root_cause`, `security`, `performance`, `general` |

### Focus Modes

| Focus         | Signal categories used          | When to use |
|---------------|---------------------------------|-------------|
| `root_cause`  | crash, cascade, spike           | System-wide failure, cascading errors |
| `security`    | security                        | Suspected secret/credential exposure |
| `performance` | spike                           | Latency, throughput, or resource pressure |
| `general`     | all detected                    | Unknown issue, broad sweep |

---

## Signal Detection

Symptoms are classified into categories by keyword matching:

| Signal     | Example keywords |
|------------|-----------------|
| `crash`    | error, 500, exception, traceback, panic, fatal, OOM |
| `spike`    | latency, slow, timeout, burst, high load, CPU, memory |
| `cascade`  | connection refused, downstream, upstream, circuit-breaker |
| `security` | token, credential, API key, password, PII, 401, 403 |
| `pattern`  | log level, timestamp, health-check, format |

If no signals match, `crash` is assumed as a safe default.

---

## Investigation Plan Steps

Steps are generated in a fixed priority order:

| Priority | Action                    | Triggered by |
|----------|---------------------------|--------------|
| 1        | `list_containers`         | Always |
| 2        | `analyze_patterns`        | crash, pattern, root_cause, general |
| 3        | `get_last_errors`         | crash, cascade, root_cause, general |
| 4        | `analyze_error_spikes`    | spike, crash, performance, root_cause, general |
| 5        | `analyze_correlations`    | cascade, spike, root_cause, general |
| 6        | `map_service_dependencies`| cascade, root_cause, general |
| 7        | `detect_data_leaks`       | security focus |
| 8        | `analyze_root_causes`     | root_cause, general, cascade |

---

## Return Value

```json
{
  "status": "success",
  "signals_detected": ["crash", "cascade"],
  "focus": "root_cause",
  "containers_in_scope": ["payment-service", "api-gateway"],
  "step_count": 7,
  "plan": [
    {
      "step": 1,
      "action": "list_containers",
      "reason": "Discover which containers are running to scope the investigation"
    },
    {
      "step": 2,
      "action": "analyze_patterns",
      "target": "payment-service",
      "reason": "Detect log format, language, error level distribution in payment-service",
      "parameters": {"container_name": "payment-service"}
    }
  ],
  "plan_file": ".cache/plans/20260311T120000Z_root_cause_payment-service_api-gateway.md"
}
```

The full human-readable plan (with a Markdown table of steps, tool reference,
and symptom summary) is saved to `plan_file`. The JSON response does not echo
the plan text — open the file to view it.

---

## Example Copilot Prompts

```
Plan how to investigate payment-service 500 errors and high latency in checkout.
```

```
Create an investigation plan for connection refused errors between api-gateway and database.
Focus on root cause.
```

```
Plan a security investigation for the auth-service — I suspect credentials are leaking.
```

---

## Plan File Location

Plans are written to:

```
.cache/plans/<timestamp>_<focus>_<scope>.md
```

Example: `.cache/plans/20260311T120000Z_root_cause_payment-service.md`

The `.cache/` directory is gitignored. Plans are ephemeral — regenerate as needed.

---

## Implementation

| File | Role |
|------|------|
| `docker_log_analyzer/investigation_planner.py` | Core planner: `classify_symptoms()`, `generate_plan()`, `_save_plan_md()` |
| `docker_log_analyzer/tools.py` | `tool_plan_investigation()` wrapper |
| `docker_log_analyzer/mcp_server.py` | `@mcp.tool()` registration |
| `tests/test_investigation_planner.py` | Unit tests (no Docker required) |

---

## Design Decisions

- **Deterministic** — same symptoms + focus always produce the same plan (no LLM, no randomness)
- **File output, not inline** — plans can be long; writing to `.md` avoids bloating the MCP response
- **Stateless** — no cross-call state; each call generates an independent plan file
- **Composable** — the returned `plan` list of dicts can be iterated by Copilot to execute each step automatically
