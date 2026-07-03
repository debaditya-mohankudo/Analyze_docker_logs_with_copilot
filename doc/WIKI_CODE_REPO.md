---
tags: [code-repo, stack-trace, analyze_code_context, coderepo, source, deep-dive]
last_updated: 2026-07-03
---

# Code Repository Context — analyze_code_context

`analyze_code_context` is a deterministic MCP tool that bridges container error
logs and your actual source code. After identifying which container is failing,
this tool parses the stack trace, locates the relevant files in your local
repository, and returns the surrounding code lines — giving you immediate context
without leaving your Copilot chat.

No LLM. No network calls. All analysis is local file I/O + regex.

---

## What It Does

1. Fetches the last `tail` log lines from the target container
2. Parses stack traces using language-specific regex (Python, Java, Go, Node.js)
3. Resolves each frame's file path against a configured repository root
4. Extracts `context_lines` of source code around each error location
5. Returns structured frames with inline code context

---

## Tool Signature

```
analyze_code_context(
    container_name: str,
    tail:           int         = 200,
    context_lines:  int | None  = None,   # default: config.code_context_lines (10)
    max_frames:     int | None  = None,   # default: config.max_stack_frames (10)
    repo_path:      str | None  = None,   # explicit override
    language:       str | None  = None,   # auto-detected if omitted
) -> dict
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `container_name` | `str` | required | Target container |
| `tail` | `int` | `200` | Log lines to scan for stack traces |
| `context_lines` | `int` | `10` (config) | Source lines before/after the error line |
| `max_frames` | `int` | `10` (config) | Maximum stack frames to return |
| `repo_path` | `str` | — | Explicit repository root. Overrides all config. |
| `language` | `str` | auto | Force parser: `python`, `java`, `go`, `nodejs` |

---

## Configuration

Set in `.env` or environment variables:

```env
# List of repo roots to search (colon-separated or JSON array)
REPO_PATHS=["/home/user/myapp", "/srv/services/api"]

# Explicit container → repo mapping (JSON object)
CONTAINER_REPO_MAP={"api-service": "/home/user/api", "worker": "/home/user/worker"}

# Code context window
CODE_CONTEXT_LINES=10

# Max stack frames per call
MAX_STACK_FRAMES=10
```

### Repository Resolution Order

1. `repo_path` parameter (call-time override — highest priority)
2. `CONTAINER_REPO_MAP` exact match on container name
3. `CONTAINER_REPO_MAP` prefix match (e.g. `"api"` matches `"api-gateway"`)
4. First valid path in `REPO_PATHS` list

If no repo is configured, the tool still returns parsed frames but with
`code_context: null` and a warning message.

---

## Supported Stack Trace Formats

| Language | Example frame |
|----------|--------------|
| Python | `File "app/server.py", line 42, in handle_request` |
| Java | `at com.example.Service.process(Service.java:123)` |
| Go | `/home/user/app/main.go:42 +0x1a3` |
| Node.js | `at handleRequest (/app/server.js:42:10)` |

Language is auto-detected by running `PatternDetector.detect_language()` directly
on the fetched log lines. Use the `language` parameter to force a specific parser.

---

## Return Value

```json
{
  "status": "success",
  "container": "payment-service",
  "language": "python",
  "repo_root": "/home/user/payment-service",
  "frames_found": 3,
  "frames": [
    {
      "language": "python",
      "raw_frame": "  File \"app/payments.py\", line 87, in charge_card",
      "function": "charge_card",
      "file_in_log": "app/payments.py",
      "line_no": 87,
      "resolved_file": "/home/user/payment-service/app/payments.py",
      "code_context": {
        "file": "/home/user/payment-service/app/payments.py",
        "error_line": 87,
        "before": [
          [85, "    amount = request.json['amount']"],
          [86, "    card = stripe.Customer.retrieve(customer_id)"]
        ],
        "at": [87, "    result = stripe.Charge.create(amount=amount, customer=card.id)"],
        "after": [
          [88, "    return jsonify({'status': 'ok', 'charge_id': result.id})"]
        ]
      }
    }
  ],
  "unresolved_files": [],
  "warnings": []
}
```

### Status values

| Status | Meaning |
|--------|---------|
| `success` | Frames found and returned (code context may be null if no repo) |
| `no_frames` | No stack traces detected in the error logs |
| `error` | Container not found or Docker unavailable |

---

## Example Copilot Prompts

```
After investigating the errors in payment-service, show me the code around each stack frame.
```

```
Parse the stack traces from api-gateway and show me the source code.
Repo is at /home/user/api.
```

```
The worker container is crashing — analyze its stack trace and show me the failing code.
```

---

## Integration with plan_investigation

When `plan_investigation` is called with specific `containers`, it automatically
adds an `analyze_code_context` step as the final deep-dive action:

```
PLAN:
...
8. action: analyze_root_causes
   target: all containers
   reason: Score containers by root-cause likelihood...

9. action: analyze_code_context
   target: payment-service
   reason: Parse stack traces from payment-service error logs and surface
           the source code around each error line — requires REPO_PATHS or
           CONTAINER_REPO_MAP to be configured in .env
```

---

## Implementation

| File | Role |
|------|------|
| `docker_log_analyzer/coderepo.py` | Core: `parse_frames()`, `resolve_repo_for_container()`, `find_file_in_repo()`, `extract_code_context()`, `analyse_code_context()` |
| `docker_log_analyzer/config.py` | `repo_paths`, `container_repo_map`, `code_context_lines`, `max_stack_frames` settings |
| `docker_log_analyzer/tools.py` | `tool_analyze_code_context()` wrapper |
| `docker_log_analyzer/mcp_server.py` | `@mcp.tool()` registration |
| `docker_log_analyzer/investigation_planner.py` | Step 9: code context deep-dive |
| `tests/test_coderepo.py` | Unit tests (no Docker required) |

---

## Design Decisions

- **Stateless** — no cross-call state; every call is a fresh fetch + parse
- **No LLM** — pure regex parsing + file I/O; deterministic and reproducible offline
- **Graceful degradation** — works without a repo configured (returns frames, warns about missing context)
- **Language auto-detection** — `PatternDetector.detect_language()` is called on the fetched lines; no prior `analyze_patterns` call required
- **Basename fallback** — Java's short file names (e.g. `Service.java`) are found via `rglob` when relative resolution fails

---

## Retrieval keywords

code context, stack trace, analyze_code_context, coderepo, repo_paths, container_repo_map, parse_frames, find_file_in_repo, extract_code_context, source code, deep dive, repository resolution

**[negative keywords / not-this-doc]**
path confinement security rules, secret detection, CI, coverage, cache strategy

---

## See also

- Tool parameter reference: [WIKI_TOOLS.md § analyze_code_context](WIKI_TOOLS.md#14-analyze_code_context)
- Path confinement security rules: [WIKI_SECURITY.md § Repository Path Confinement](WIKI_SECURITY.md#2-repository-path-confinement-coderepopy)
- Architecture hub: [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md)
- Home: [WIKI_HOME.md](WIKI_HOME.md)
