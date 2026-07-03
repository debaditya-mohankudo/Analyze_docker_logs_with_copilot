---
tags: [skill, add-new-tool, scaffold, contribution]
last_updated: 2026-07-03
---

# /add-new-tool

Scaffolds a new MCP tool into this repository following all architectural
constraints defined in CLAUDE.md.

> This file must stay in sync with [.claude/commands/add-new-tool.md](.claude/commands/add-new-tool.md)
> — that's the file Claude Code actually loads for the `/add-new-tool` slash
> command; this repo-root copy exists for visibility/portability. They had
> drifted (wrong `"ok"` status string, wrong import paths, wrong Docker mock
> library) until 2026-07-03 — keep both updated together.

---

## Prerequisites

Before running this skill, confirm:
- `uv sync` has been run and the virtualenv is active
- Docker daemon is reachable (`docker ps` succeeds)
- Existing unit tests pass: `uv run pytest tests/ -m "not integration"`

If any of the above fail, stop and fix them before proceeding.

---

## Step 1 — Ask the user for tool details

Ask the user the following questions before writing any code.
Do not proceed until all answers are collected.

1. **Tool name** — what should it be called?
   - Must be snake_case
   - Will become `tool_<name>()` in `tools.py`
   - Will be registered as `@mcp.tool()` in `mcp_server.py`

2. **What does this tool do?** — one sentence description.

3. **Input parameters** — list each parameter with:
   - Name (snake_case)
   - Type (str, int, float, bool, Optional[str], etc.)
   - Default value if optional
   - Description (used in the docstring)

4. **Which existing modules will it use?**
   - docker.py (log fetching / cache)
   - spike_detector.py
   - correlator.py
   - dependency_mapper.py
   - root_cause_analyzer.py
   - log_pattern_analyzer.py
   - secret_detector.py
   - request_tracer.py
   - error_classifier.py
   - investigation_planner.py
   - coderepo.py
   - cache_manager.py
   - patterns.py
   - None of the above (new logic entirely)

5. **Does it need Docker access?**
   - Yes → integration test required in addition to unit test
   - No → unit test only

---

## Step 2 — Verify the design is stateless

Before writing any code, validate the proposed tool against CLAUDE.md §1.1.

Check ALL of the following. If any answer is "yes", stop and redesign with the user:

- [ ] Does it hold any variable in module-level scope between calls?
- [ ] Does it spawn a background thread?
- [ ] Does it open a connection and keep it open past the return statement?
- [ ] Does it write to an in-memory structure that persists across calls?
- [ ] Does it depend on another `tool_*()` having been called first?

If all answers are "no", proceed.

---

## Step 3 — Implement `tool_<name>()` in tools.py

Add the function at the end of `tools.py`, following this exact structure:

```python
def tool_<name>(
    <param1>: <type1>,
    <param2>: <type2> = <default>,
) -> dict:
    """
    <One-sentence description from Step 1>.

    Args:
        <param1>: <description>
        <param2>: <description>

    Returns:
        dict with keys:
            status     - "success" | "error"
            <key>      - <description of each output field>
    """
    try:
        # --- fetch logs (if Docker-dependent) ---
        # Use cache-first pattern from docker.py (CLAUDE.md §3.1)
        # Example:
        #   logs = fetch_logs_with_cache(container, window_hours=24)

        # --- analysis ---
        # Perform all analysis here.
        # No external API calls.
        # No mutation of shared state.

        return {
            "status": "success",
            # ... result fields
        }

    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "error": str(exc),
        }
```

Rules enforced by this structure (cross-referenced to CLAUDE.md):
- All state is local to this function call (§1.1)
- All failures return structured JSON, never raise (§5)
- No stdout prints (§5)
- No external network calls (§1.2)

---

## Step 4 — Register in mcp_server.py

Add the registration directly below the last existing `@mcp.tool()` block:

```python
@mcp.tool()
def <name>(
    <param1>: <type1>,
    <param2>: <type2> = <default>,
) -> dict:
    """<Same one-sentence description — this is what VSCode Copilot shows the user>."""
    return tool_<name>(<param1>, <param2>)
```

Rules:
- `mcp_server.py` contains ONLY wiring — no logic (CLAUDE.md §2)
- Add `tool_<name>` to the existing `from .tools import (...)` block at the top of the file (relative import, matching every other tool)

---

## Step 5 — Write tests (do this before any manual testing)

Follow CLAUDE.md §4.0 test priority order exactly.
Write tests in this sequence — do not skip ahead to feature tests.

### 5a. Design-principle tests (always required)

Create `tests/test_tool_<name>.py`:

```python
"""
Design-principle tests for tool_<name>.
Order follows CLAUDE.md §4.0: wiring -> isolation -> error contract -> determinism -> features.
"""
from unittest.mock import MagicMock
import pytest
from docker_log_analyzer.tools import tool_<name>


class TestStatelessWiring:
    """§4.0 check 1 — Docker -> fetch -> analyse -> return, no state leaks."""

    def test_returns_dict(self, mock_docker):
        result = tool_<name>(<minimal_valid_args>)
        assert isinstance(result, dict)

    def test_no_state_between_calls(self, mock_docker):
        r1 = tool_<name>(<args>)
        r2 = tool_<name>(<args>)
        assert r1 == r2


class TestToolIsolation:
    """§4.0 check 2 — callable without any other tool having been called first."""

    def test_callable_standalone(self, mock_docker):
        # Do NOT call any other tool_*() before this
        result = tool_<name>(<args>)
        assert result["status"] in ("success", "error")


class TestErrorContract:
    """§4.0 check 3 — every failure returns structured JSON, never raises."""

    def test_invalid_container_returns_error_json(self):
        result = tool_<name>(container="__nonexistent__")
        assert result["status"] == "error"
        assert "error" in result
        assert isinstance(result["error"], str)

    def test_never_raises(self):
        try:
            result = tool_<name>(container="")
            assert "status" in result
        except Exception as exc:
            pytest.fail(f"tool_<name> raised unexpectedly: {exc}")


class TestDeterminism:
    """§4.0 check 4 — same inputs produce same outputs."""

    def test_deterministic(self, mock_docker):
        results = [tool_<name>(<args>) for _ in range(3)]
        assert all(r == results[0] for r in results)


class TestFeatures:
    """Feature-level tests — only added after all four design checks above pass."""

    def test_success_status_on_valid_input(self, mock_docker):
        result = tool_<name>(<valid_args>)
        assert result["status"] == "success"

    def test_output_fields_present(self, mock_docker):
        result = tool_<name>(<valid_args>)
        for key in (<expected_output_keys>):
            assert key in result, f"Missing field: {key}"
```

This project uses `python_on_whales.DockerClient`, **not** the `docker-py`
SDK (`docker.from_env()`) — mocking the wrong library produces tests that
pass without ever exercising the real code path. If `mock_docker` is not
already in `tests/conftest.py`, add it patching
`docker_log_analyzer.tools._docker_client` (the actual import point used by
`tool_*()` functions):

```python
from unittest.mock import patch, MagicMock

@pytest.fixture
def mock_docker():
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.logs.return_value = (
        "2026-03-04T10:00:00.000Z INFO service started\n"
        "2026-03-04T10:00:01.000Z ERROR connection refused\n"
    )
    fake_client.container.list.return_value = [fake_container]
    with patch("docker_log_analyzer.tools._docker_client", return_value=fake_client):
        yield fake_client
```

See `tests/test_tools_unit.py` for real examples of this pattern already in use.

### 5b. Integration test (only if Docker-dependent)

Create `tests/test_tool_<name>_integration.py`:

```python
import pytest
from docker_log_analyzer.tools import tool_<name>


@pytest.mark.integration
class TestIntegration:
    def test_live_container(self, live_container):
        result = tool_<name>(container=live_container)
        assert result["status"] == "success"
```

---

## Step 6 — Run the tests

```bash
# Unit tests for the new tool
uv run pytest tests/test_tool_<name>.py -v

# Full suite — confirm no regressions
uv run pytest tests/ -m "not integration" -v
```

All tests must pass before proceeding. Fix failures before moving on.

---

## Step 7 — Update README

Add the new tool to the tools table in README.md using the existing row format.

Minimum columns:
| Tool name | Description | Key parameters | Returns |

---

## Step 8 — Update CLAUDE.md

Only update CLAUDE.md if the new tool introduces a pattern or constraint not
already documented — for example, a new external module dependency, a new cache
key scheme, or a new security consideration.

Do not update CLAUDE.md for routine tool additions that follow existing patterns.
Refer to §10 (contribution guidelines) to decide.

---

## Step 9 — Final checklist

- [ ] `tool_<name>()` implemented in `tools.py` with correct signature and try/except
- [ ] `@mcp.tool()` wiring added to `mcp_server.py` — no logic there
- [ ] All §4.0 design-principle tests written and passing
- [ ] `uv run pytest tests/ -m "not integration"` passes with no regressions
- [ ] README tools table updated
- [ ] CLAUDE.md updated only if architectural impact

---

## What NOT to do

When following this skill, never:

- Add LLM/API calls inside the tool (CLAUDE.md §11)
- Add module-level mutable state (CLAUDE.md §1.1)
- Put logic in `mcp_server.py` (CLAUDE.md §2)
- Skip the §4.0 test order and jump straight to feature tests
- Print to stdout inside the tool function (CLAUDE.md §5)
- Write logs to disk outside `.cache/` (CLAUDE.md §11)
- Add background threads or schedulers (CLAUDE.md §11)
- Call external services or APIs (CLAUDE.md §1.2)
