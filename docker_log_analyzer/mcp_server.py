"""
MCP Server for Docker Log Pattern Analysis (non-LLM).

Exposes 10 tools to VSCode Copilot Agent Mode via .vscode/mcp.json:

  list_containers           – discover running Docker containers
  analyze_patterns          – PatternDetector per container (timestamps, language, log levels)
  detect_error_spikes       – Polars rolling-window spike detection
  correlate_containers      – pairwise cross-container temporal error correlation
  detect_data_leaks         – SecretDetector for API keys, credentials, PII, sensitive data
  map_service_dependencies  – infer service dependency graph from log patterns
  sync_docker_logs          – cache logs for offline / instant analysis
  capture_and_analyze       – live capture + spike + correlation report
  start_test_containers     – build & start test log-generator containers
  stop_test_containers      – stop and remove test log-generator containers

All tools are stateless (fetch → analyse → return JSON). No external API calls.
Tool implementations live in tools.py; this file is registry + MCP wiring only.
"""

import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .config import settings
from .logger import logger
from .tools import (
    COMPOSE_FILE,
    PATTERN_CACHE_DIR,
    tool_list_containers,
    tool_analyze_patterns,
    tool_detect_error_spikes,
    tool_correlate_containers,
    tool_start_test_containers,
    tool_stop_test_containers,
    tool_sync_docker_logs,
    tool_capture_and_analyze,
    tool_detect_data_leaks,
    tool_map_service_dependencies,
)


# ── Wrappers (argument unpacking + type coercion) ───────────────────────────

def _wrap_analyze_patterns(**kwargs) -> dict:
    """Wrapper for analyze_patterns with argument unpacking."""
    return tool_analyze_patterns(
        container_name=kwargs.get("container_name"),
        tail=int(kwargs.get("tail", settings.default_tail_lines)),
        force_refresh=bool(kwargs.get("force_refresh", False)),
    )


def _wrap_detect_error_spikes(**kwargs) -> dict:
    """Wrapper for detect_error_spikes with argument unpacking."""
    return tool_detect_error_spikes(
        container_name=kwargs.get("container_name"),
        tail=int(kwargs.get("tail", settings.default_spike_tail_lines)),
        window_minutes=int(kwargs.get("window_minutes", settings.default_window_minutes)),
        spike_threshold=float(kwargs.get("spike_threshold", settings.default_spike_threshold)),
    )


def _wrap_correlate_containers(**kwargs) -> dict:
    """Wrapper for correlate_containers with argument unpacking."""
    return tool_correlate_containers(
        time_window_seconds=int(
            kwargs.get("time_window_seconds", settings.default_correlation_window_seconds)
        ),
        tail=int(kwargs.get("tail", settings.default_tail_lines)),
    )


def _wrap_start_test_containers(**kwargs) -> dict:
    """Wrapper for start_test_containers with argument unpacking."""
    return tool_start_test_containers(
        rebuild=bool(kwargs.get("rebuild", False)),
    )


def _wrap_sync_docker_logs(**kwargs) -> dict:
    """Wrapper for sync_docker_logs with argument unpacking."""
    return tool_sync_docker_logs(
        container_names=kwargs.get("container_names"),
        since=str(kwargs.get("since", "24 hours ago")),
        until=str(kwargs.get("until", "now")),
        force_refresh=bool(kwargs.get("force_refresh", False)),
    )


def _wrap_capture_and_analyze(**kwargs) -> dict:
    """Wrapper for capture_and_analyze with argument unpacking."""
    return asyncio.run(tool_capture_and_analyze(
        container_names=kwargs.get("container_names"),
        duration_seconds=int(kwargs.get("duration_seconds", 120)),
        spike_threshold=float(kwargs.get("spike_threshold", 2.0)),
        time_window_seconds=int(kwargs.get("time_window_seconds", 30)),
    ))


def _wrap_detect_data_leaks(**kwargs) -> dict:
    """Wrapper for detect_data_leaks with argument unpacking."""
    return asyncio.run(tool_detect_data_leaks(
        duration_seconds=int(kwargs.get("duration_seconds", 60)),
        container_names=kwargs.get("container_names"),
        severity_filter=str(kwargs.get("severity_filter", "all")),
    ))


def _wrap_map_service_dependencies(**kwargs) -> dict:
    """Wrapper for map_service_dependencies with argument unpacking."""
    return tool_map_service_dependencies(
        containers=kwargs.get("containers"),
        tail=int(kwargs.get("tail", 500)),
        include_transitive=bool(kwargs.get("include_transitive", False)),
    )


# ── Tool registry (replaces if/elif dispatch) ──────────────────────────────

class ToolRegistry:
    """Registry pattern for MCP tools."""

    def __init__(self):
        self._tools: dict[str, dict] = {}

    def register(self, name: str, handler, schema: dict):
        """Register a tool with its handler and JSON schema."""
        self._tools[name] = {
            "handler": handler,
            "schema": schema,
        }

    def get_handler(self, name: str):
        """Get the handler function for a tool."""
        if name not in self._tools:
            return None
        return self._tools[name]["handler"]

    def get_schema(self, name: str) -> dict:
        """Get the JSON schema for a tool."""
        if name not in self._tools:
            return {}
        return self._tools[name]["schema"]

    def list_tools(self) -> list[Tool]:
        """Generate Tool objects for all registered tools."""
        tools = []
        for name, tool_def in self._tools.items():
            schema = tool_def["schema"]
            tools.append(
                Tool(
                    name=name,
                    description=schema.get("description", ""),
                    inputSchema=schema.get("inputSchema", {"type": "object", "properties": {}, "required": []}),
                )
            )
        return tools


# Create the global registry
_registry = ToolRegistry()


# Register all tools
_registry.register(
    "list_containers",
    tool_list_containers,
    {
        "description": "List all running Docker containers with name, image, status, and labels.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    }
)

_registry.register(
    "analyze_patterns",
    _wrap_analyze_patterns,
    {
        "description": (
            "Fetch Docker container logs and detect patterns: timestamp format, "
            "programming language, log level distribution, health check frequency, "
            "and common error patterns. No LLM required."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "container_name": {
                    "type": "string",
                    "description": "Target container name. Omit to analyze all running containers.",
                },
                "tail": {
                    "type": "integer",
                    "description": "Number of recent log lines to fetch (default 500).",
                    "default": 500,
                },
                "force_refresh": {
                    "type": "boolean",
                    "description": (
                        "Bypass the on-disk cache and re-analyse live logs. "
                        "Use when the service has changed significantly since the last analysis."
                    ),
                    "default": False,
                },
            },
            "required": [],
        },
    }
)

_registry.register(
    "detect_error_spikes",
    _wrap_detect_error_spikes,
    {
        "description": (
            "Detect error spikes in Docker container logs using Polars rolling-window analysis. "
            "Flags 1-minute buckets where error count exceeds spike_threshold × rolling baseline. "
            "No LLM required."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "container_name": {
                    "type": "string",
                    "description": "Target container name. Omit to check all containers.",
                },
                "tail": {
                    "type": "integer",
                    "description": "Log lines to fetch per container (default 1000).",
                    "default": 1000,
                },
                "window_minutes": {
                    "type": "integer",
                    "description": "Spike detection window in minutes (default 5).",
                    "default": 5,
                },
                "spike_threshold": {
                    "type": "number",
                    "description": (
                        "Ratio of current bucket to rolling baseline that triggers a spike "
                        "(default 2.0 = 2× baseline)."
                    ),
                    "default": 2.0,
                },
            },
            "required": [],
        },
    }
)

_registry.register(
    "correlate_containers",
    _wrap_correlate_containers,
    {
        "description": (
            "Compute pairwise temporal correlation of errors across all running containers. "
            "Returns container pairs sorted by correlation score (0–1) with example "
            "co-occurring error lines. No LLM required."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "time_window_seconds": {
                    "type": "integer",
                    "description": "Co-occurrence window in seconds (default 30).",
                    "default": 30,
                },
                "tail": {
                    "type": "integer",
                    "description": "Log lines to fetch per container (default 500).",
                    "default": 500,
                },
            },
            "required": [],
        },
    }
)

_registry.register(
    "start_test_containers",
    _wrap_start_test_containers,
    {
        "description": (
            "Build and start the test log-generator containers defined in docker-compose.test.yml. "
            "Spins up 4 containers (web-app, database, cache, gateway) that emit random logs "
            "in different formats (ISO-8601, syslog, epoch, Apache) and languages "
            "(Python, Java, Go, Node.js) with periodic error spikes for testing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "rebuild": {
                    "type": "boolean",
                    "description": "Force rebuild of the Docker image before starting (default false).",
                    "default": False,
                },
            },
            "required": [],
        },
    }
)

_registry.register(
    "stop_test_containers",
    tool_stop_test_containers,
    {
        "description": "Stop and remove the test log-generator containers started by start_test_containers.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    }
)

_registry.register(
    "sync_docker_logs",
    _wrap_sync_docker_logs,
    {
        "description": (
            "Sync Docker logs to local cache (.cache/logs/) for a time window. "
            "Enables fast offline analysis and bug reproduction by caching logs locally. "
            "All tools use cache-first strategy when analyzing logs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "container_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific containers to sync. Omit to sync all running containers.",
                },
                "since": {
                    "type": "string",
                    "description": (
                        "Start of time window (default '24 hours ago'). "
                        "Examples: '2 hours ago', '7 days ago', '2026-03-04T10:00:00Z'"
                    ),
                    "default": "24 hours ago",
                },
                "until": {
                    "type": "string",
                    "description": "End of time window (default 'now'). Same format as 'since'.",
                    "default": "now",
                },
                "force_refresh": {
                    "type": "boolean",
                    "description": "Skip cache, re-fetch all logs (default false).",
                    "default": False,
                },
            },
            "required": [],
        },
    }
)

_registry.register(
    "capture_and_analyze",
    _wrap_capture_and_analyze,
    {
        "description": (
            "Capture live logs for a specified duration (default 2 minutes) then return a combined "
            "analysis: error spikes, cross-container correlation, and per-container log level "
            "breakdown. Designed for bug reproduction — call this, reproduce the issue, and get a "
            "unified report of exactly what happened across your services during the window."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "container_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Containers to monitor. Omit to watch all running containers.",
                },
                "duration_seconds": {
                    "type": "integer",
                    "description": "Capture window in seconds (default 120 = 2 minutes).",
                    "default": 120,
                },
                "spike_threshold": {
                    "type": "number",
                    "description": "Error rate multiplier to flag as a spike (default 2.0).",
                    "default": 2.0,
                },
                "time_window_seconds": {
                    "type": "integer",
                    "description": "Co-occurrence window for cross-container correlation (default 30).",
                    "default": 30,
                },
            },
            "required": [],
        },
    }
)

_registry.register(
    "detect_data_leaks",
    _wrap_detect_data_leaks,
    {
        "description": (
            "Detect sensitive data (API keys, credentials, tokens, PII) in container logs over a "
            "specified time window (default 60s). Returns findings sorted by severity with "
            "remediation recommendations. Designed for security audits and compliance checks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "duration_seconds": {
                    "type": "integer",
                    "description": "Scan window in seconds (default 60).",
                    "default": 60,
                },
                "container_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Containers to scan. Omit to scan all running containers.",
                },
                "severity_filter": {
                    "type": "string",
                    "enum": ["all", "high", "critical"],
                    "description": "Filter by minimum severity: 'critical' (API keys), 'high' (tokens, DB URLs), 'all' (includes PII). Default 'all'.",
                    "default": "all",
                },
            },
            "required": [],
        },
    }
)

_registry.register(
    "map_service_dependencies",
    _wrap_map_service_dependencies,
    {
        "description": (
            "Infer service dependency graph from container log analysis. "
            "Parses HTTP URLs, database connection strings, gRPC dial calls, and "
            "container name mentions to build a directed graph. Joins with temporal "
            "error correlation to surface likely error cascade paths. "
            "Best for HTTP-heavy microservices; gRPC/event-driven coverage is limited. "
            "No LLM required."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "containers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific containers to analyse. Omit for all running containers.",
                },
                "tail": {
                    "type": "integer",
                    "description": "Log lines to fetch per container (default 500).",
                    "default": 500,
                },
                "include_transitive": {
                    "type": "boolean",
                    "description": (
                        "Add one-hop transitive edges (A→B + B→C → A→C). "
                        "Transitive edges are marked confidence='low' and inferred_from='transitive'. "
                        "Default false."
                    ),
                    "default": False,
                },
            },
            "required": [],
        },
    }
)


# Backward compatibility: export TOOLS list from registry
TOOLS = _registry.list_tools()


# ── MCP server wiring ───────────────────────────────────────────────────────

def create_mcp_server() -> Server:
    server = Server("docker-log-analyzer")

    @server.list_tools()
    async def list_tools():
        return _registry.list_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        logger.debug("Tool called: %s, args: %s", name, arguments)
        try:
            handler = _registry.get_handler(name)
            if handler is None:
                result = {"status": "error", "error": f"Unknown tool: {name}"}
            else:
                result = handler(**arguments)
        except Exception as exc:
            logger.exception("Unhandled error in tool '%s'", name)
            result = {"status": "error", "error": str(exc)}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return server


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


async def _main_async() -> None:
    server = create_mcp_server()
    logger.info("Docker Log Analyzer MCP Server starting (non-LLM mode)...")
    _log_startup_config()
    async with stdio_server() as (read_stream, write_stream):
        logger.info("MCP Server ready – waiting for tool calls via stdio.")
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run() -> None:
    """Synchronous entry point registered in pyproject.toml."""
    asyncio.run(_main_async())


if __name__ == "__main__":
    run()
