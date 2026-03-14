"""
MCP Server for Docker Log Pattern Analysis (non-LLM).

Exposes 16 tools to VSCode Copilot Agent Mode via .vscode/mcp.json:

  list_containers           – discover running Docker containers
  analyze_patterns          – PatternDetector per container (timestamps, language, log levels)
  analyze_error_spikes       – Polars rolling-window spike detection
  analyze_correlations      – pairwise cross-container temporal error correlation
  detect_data_leaks         – SecretDetector for API keys, credentials, PII, sensitive data
  map_service_dependencies  – infer service dependency graph from log patterns
  analyze_root_causes          – score containers by root-cause likelihood
  sync_docker_logs          – cache logs for offline / instant analysis
  capture_logs       – live capture + spike + correlation report
  get_last_errors           – last N error/fatal lines from a single container
  plan_investigation        – generate a structured investigation plan from symptoms
  analyze_code_context      – parse stack traces + surface source code around error lines
  trace_request_flow        – trace individual request IDs across containers via log correlation
  classify_errors           – categorise errors into semantic classes (database, network, timeout, etc.)
  start_test_containers     – build & start test log-generator containers
  stop_test_containers      – stop and remove test log-generator containers

All tools are stateless (fetch → analyse → return JSON). No external API calls.
Tool implementations live in tools.py; this file is FastMCP wiring.
"""

from __future__ import annotations

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "MCP dependency is missing. Install project dependencies with 'uv sync'."
    ) from exc

from .config import settings
from .logger import logger
from .tools import (
    PATTERN_CACHE_DIR,
    tool_list_containers,
    tool_analyze_patterns,
    tool_analyze_error_spikes,
    tool_analyze_correlations,
    tool_start_test_containers,
    tool_stop_test_containers,
    tool_sync_docker_logs,
    tool_capture_logs,
    tool_detect_data_leaks,
    tool_map_service_dependencies,
    tool_analyze_root_causes,
    tool_get_last_errors,
    tool_plan_investigation,
    tool_analyze_code_context,
    tool_trace_request_flow,
    tool_classify_errors,
)


# ── FastMCP server ────────────────────────────────────────────────────────────

mcp = FastMCP("docker-log-analyzer")


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def list_containers() -> dict:
    """List all running Docker containers with name, image, status, and labels."""
    return tool_list_containers()


@mcp.tool()
async def analyze_patterns(
    container_name: str | None = None,
    tail: int = 500,
    force_refresh: bool = False,
) -> dict:
    """Fetch Docker container logs and detect patterns: timestamp format, programming language,
    log level distribution, health check frequency, and common error patterns. No LLM required.

    Args:
        container_name: Target container name. Omit to analyze all running containers.
        tail: Number of recent log lines to fetch (default 500).
        force_refresh: Bypass the on-disk cache and re-analyse live logs. Use when the
            service has changed significantly since the last analysis.
    """
    return tool_analyze_patterns(
        container_name=container_name,
        tail=tail,
        force_refresh=force_refresh,
    )


@mcp.tool()
async def analyze_error_spikes(
    container_name: str | None = None,
    tail: int = 1000,
    window_minutes: int = 5,
    spike_threshold: float = 2.0,
) -> dict:
    """Detect error spikes in Docker container logs using Polars rolling-window analysis.
    Flags 1-minute buckets where error count exceeds spike_threshold × rolling baseline.
    No LLM required.

    Args:
        container_name: Target container name. Omit to check all containers.
        tail: Log lines to fetch per container (default 1000).
        window_minutes: Spike detection window in minutes (default 5).
        spike_threshold: Ratio of current bucket to rolling baseline that triggers a
            spike (default 2.0 = 2× baseline).
    """
    return tool_analyze_error_spikes(
        container_name=container_name,
        tail=tail,
        window_minutes=window_minutes,
        spike_threshold=spike_threshold,
    )


@mcp.tool()
async def analyze_correlations(
    container_names: list[str] | None = None,
    time_window_seconds: int = 30,
    tail: int = 500,
) -> dict:
    """Compute pairwise temporal correlation of errors across containers.

    Args:
        container_names: Container names to correlate. Omit to correlate all running containers.
        time_window_seconds: Co-occurrence window in seconds (default 30).
        tail: Log lines to fetch per container (default 500).
    """
    return tool_analyze_correlations(
        time_window_seconds=time_window_seconds,
        tail=tail,
        container_names=container_names,
    )


@mcp.tool()
async def start_test_containers(rebuild: bool = False) -> dict:
    """Build and start the test log-generator containers defined in docker-compose.test.yml.
    Spins up 4 containers (web-app, database, cache, gateway) that emit random logs in
    different formats (ISO-8601, syslog, epoch, Apache) and languages (Python, Java, Go,
    Node.js) with periodic error spikes for testing.

    Args:
        rebuild: Force rebuild of the Docker image before starting (default false).
    """
    return tool_start_test_containers(rebuild=rebuild)


@mcp.tool()
async def stop_test_containers() -> dict:
    """Stop and remove the test log-generator containers started by start_test_containers."""
    return tool_stop_test_containers()


@mcp.tool()
async def sync_docker_logs(
    container_names: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """Sync Docker logs to local cache (.cache/logs/) for a time window.
    Enables fast offline analysis and bug reproduction by caching logs locally.
    All tools use cache-first strategy when analyzing logs.

    Args:
        container_names: Specific containers to sync. Omit to sync all running containers.
        since: Start of time window as ISO-8601 UTC string (e.g. '2026-03-04T10:00:00Z').
            Omit to default to 24 hours ago.
        until: End of time window as ISO-8601 UTC string. Omit to default to now.
        force_refresh: Skip cache, re-fetch all logs (default false).
    """
    return tool_sync_docker_logs(
        container_names=container_names,
        since=since,
        until=until,
        force_refresh=force_refresh,
    )


@mcp.tool()
async def capture_logs(
    container_names: list[str] | None = None,
    duration_seconds: int = 120,
    spike_threshold: float = 2.0,
    time_window_seconds: int = 30,
) -> dict:
    """Capture live logs for a specified duration then return a combined analysis: error
    spikes, cross-container correlation, and per-container log level breakdown. Designed
    for bug reproduction — call this, reproduce the issue, and get a unified report of
    exactly what happened across your services during the window.

    Args:
        container_names: Containers to monitor. Omit to watch all running containers.
        duration_seconds: Capture window in seconds (default 120 = 2 minutes).
        spike_threshold: Error rate multiplier to flag as a spike (default 2.0).
        time_window_seconds: Co-occurrence window for cross-container correlation
            (default 30).
    """
    return await tool_capture_logs(
        container_names=container_names,
        duration_seconds=duration_seconds,
        spike_threshold=spike_threshold,
        time_window_seconds=time_window_seconds,
    )


@mcp.tool()
async def detect_data_leaks(
    duration_seconds: int = 60,
    container_names: list[str] | None = None,
    severity_filter: str = "all",
) -> dict:
    """Detect sensitive data (API keys, credentials, tokens, PII) in container logs over a
    specified time window. Returns findings sorted by severity with remediation
    recommendations. Designed for security audits and compliance checks.

    Args:
        duration_seconds: Scan window in seconds (default 60).
        container_names: Containers to scan. Omit to scan all running containers.
        severity_filter: Filter by minimum severity — 'critical' (API keys), 'high'
            (tokens, DB URLs), 'all' (includes PII). Default 'all'.
    """
    return await tool_detect_data_leaks(
        duration_seconds=duration_seconds,
        container_names=container_names,
        severity_filter=severity_filter,
    )


@mcp.tool()
async def map_service_dependencies(
    containers: list[str] | None = None,
    tail: int = 500,
    include_transitive: bool = False,
) -> dict:
    """Infer service dependency graph from container log analysis. Parses HTTP URLs,
    database connection strings, gRPC dial calls, and container name mentions to build
    a directed graph. Joins with temporal error correlation to surface likely error
    cascade paths. Best for HTTP-heavy microservices; gRPC/event-driven coverage is
    limited. No LLM required.

    Args:
        containers: Specific containers to analyse. Omit for all running containers.
        tail: Log lines to fetch per container (default 500).
        include_transitive: Add one-hop transitive edges (A→B + B→C → A→C). Transitive
            edges are marked confidence='low' and inferred_from='transitive'.
            Default false.
    """
    return tool_map_service_dependencies(
        containers=containers,
        tail=tail,
        include_transitive=include_transitive,
    )


@mcp.tool()
async def analyze_root_causes(
    containers: list[str] | None = None,
    tail: int = 500,
    time_window_seconds: int = 3600,
    include_transitive: bool = False,
) -> dict:
    """Rank containers by root-cause likelihood using dependency fan-in, error cascade
    paths, and spike timing. Internally runs spike detection, correlation, and dependency
    graph analysis in a single call. Best used after `analyze_error_spikes` or
    `map_service_dependencies` confirm a system-wide failure.

    Args:
        containers: Specific containers to analyse. Omit for all running containers.
        tail: Log lines to fetch per container (default 500).
        time_window_seconds: Analysis window in seconds (default 3600).
        include_transitive: Include transitive edges in the dependency graph (default false).
    """
    return tool_analyze_root_causes(
        containers=containers,
        tail=tail,
        time_window_seconds=time_window_seconds,
        include_transitive=include_transitive,
    )


@mcp.tool()
async def get_last_errors(
    container_name: str,
    tail: int = 200,
    limit: int = 10,
) -> dict:
    """Return the last N error, fatal, or panic lines from a single container's logs.
    Fast triage tool: answers "what was the last error in <container>?" without
    requiring full spike or pattern analysis.

    Args:
        container_name: Target container name (required).
        tail: Number of recent log lines to scan (default 200).
        limit: Maximum number of error entries to return (default 10).
    """
    return tool_get_last_errors(
        container_name=container_name,
        tail=tail,
        limit=limit,
    )


@mcp.tool()
async def plan_investigation(
    symptoms: list[str],
    containers: list[str] | None = None,
    focus: str | None = None,
) -> dict:
    """Generate a structured, step-by-step DevOps investigation plan from observed symptoms.

    Classifies symptoms into signal categories (crash, spike, cascade, security, pattern)
    and maps them to an ordered sequence of MCP tool calls to execute. The full plan is
    written to a Markdown file under .cache/plans/ — the response returns the file path,
    not the plan text itself.

    Args:
        symptoms: Observed problem descriptions, e.g.
            ["payment-service returning 500s", "high latency in checkout"].
        containers: Container names to scope the investigation. Omit to cover all containers.
        focus: Investigation focus — one of 'root_cause', 'security', 'performance',
            or 'general' (default).
    """
    return tool_plan_investigation(
        symptoms=symptoms,
        containers=containers,
        focus=focus,
    )


@mcp.tool()
async def analyze_code_context(
    container_name: str,
    tail: int = 200,
    context_lines: int | None = None,
    max_frames: int | None = None,
    repo_path: str | None = None,
    language: str | None = None,
) -> dict:
    """Parse stack traces from a container's recent error logs and surface the relevant
    source code context from the linked code repository. Use this after get_last_errors
    or analyze_error_spikes to dive into the actual code that caused the failure.

    Workflow: fetch error logs → parse stack frames (Python/Java/Go/Node.js) →
    resolve file paths against the configured repo root → return N lines of code
    around each error location.

    Args:
        container_name: Target container (required).
        tail: Log lines to scan for stack traces (default 200).
        context_lines: Source lines before/after the error line (default: config value 10).
        max_frames: Maximum stack frames to return (default: config value 10).
        repo_path: Explicit repository root path — overrides CONTAINER_REPO_MAP and REPO_PATHS
            config. Use when the repo isn't pre-configured.
        language: Force parser — 'python', 'java', 'go', or 'nodejs'. Auto-detected if omitted.
    """
    return tool_analyze_code_context(
        container_name=container_name,
        tail=tail,
        context_lines=context_lines,
        max_frames=max_frames,
        repo_path=repo_path,
        language=language,
    )


@mcp.tool()
async def trace_request_flow(
    container_names: list[str] | None = None,
    tail: int = 500,
    min_events: int = 2,
    max_requests: int = 50,
) -> dict:
    """Trace individual request flows across containers by correlating request/trace/
    correlation IDs found in log lines. Builds per-request chronological timelines
    showing how a single transaction propagated through your services.

    Configure which ID patterns to search for via REQUEST_ID_PATTERNS in your .env
    (e.g. REQUEST_ID_PATTERNS='{"request_id": "requestId=([\\\\w-]+)"}').

    Args:
        container_names: Containers to scan. Omit for all running containers.
        tail:            Log lines to fetch per container (default 500).
        min_events:      Minimum events per request ID to include (default 2).
        max_requests:    Maximum timelines to return, sorted by event count (default 50).
    """
    return tool_trace_request_flow(
        container_names=container_names,
        tail=tail,
        min_events=min_events,
        max_requests=max_requests,
    )


@mcp.tool()
async def classify_errors(
    container_names: list[str] | None = None,
    tail: int = 1000,
    categories: list[str] | None = None,
) -> dict:
    """Classify errors in container logs into semantic categories: database,
    network, timeout, auth, oom, disk, rate_limit, configuration, application,
    or unknown. Returns per-container breakdowns with counts, percentages,
    timestamps, sample lines, recommendations, and a minute-level timeline.

    Use this after analyze_error_spikes to understand *what kind* of errors are
    spiking, or before analyze_root_causes to prioritize infrastructure errors
    (database, network) over application-level errors.

    Args:
        container_names: Containers to scan. Omit for all running containers.
        tail:            Log lines to fetch per container (default 1000).
        categories:      Optional filter — only include these categories in results.
    """
    return await tool_classify_errors(
        container_names=container_names,
        tail=tail,
        categories=categories,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def _log_startup_config() -> None:
    """Log active configuration settings for user visibility."""
    logger.info("─" * 70)
    logger.info("Docker Log Analyzer – MCP Server Configuration")
    logger.info("─" * 70)
    logger.info(f"  Log Level:                {settings.log_level}")
    logger.info(f"  Docker Host:              {settings.docker_host}")
    if settings.container_label_filter:
        logger.info(f"  Container Label Filter:   {settings.container_label_filter}")
    logger.info(f"  Default Tail Lines:       {settings.default_tail_lines}")
    logger.info(f"  Spike Window (minutes):   {settings.default_window_minutes}")
    logger.info(f"  Spike Threshold:          {settings.default_spike_threshold}x")
    logger.info(f"  Correlation Window (s):   {settings.default_correlation_window_seconds}")
    logger.info(f"  Cache Directory:          {PATTERN_CACHE_DIR}")
    logger.info("─" * 70)


def run() -> None:
    """Synchronous entry point registered in pyproject.toml."""
    logger.info("Docker Log Analyzer MCP Server starting (FastMCP / non-LLM mode)...")
    _log_startup_config()
    mcp.run()


if __name__ == "__main__":
    run()
