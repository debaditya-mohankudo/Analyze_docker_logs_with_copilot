"""
MCP Server for Docker Log Pattern Analysis (non-LLM).

Exposes 6 tools to VSCode Copilot Agent Mode via .vscode/mcp.json:

  list_containers        – discover running Docker containers
  analyze_patterns       – PatternDetector per container (timestamps, language, log levels)
  detect_error_spikes    – Polars rolling-window spike detection
  correlate_containers   – pairwise cross-container temporal error correlation
  start_test_containers  – build & start test log-generator containers (docker-compose.test.yml)
  stop_test_containers   – stop and remove test log-generator containers

All tools are stateless (fetch → analyse → return JSON). No external API calls.
Uses python-on-whales (CLI wrapper) instead of docker-py; compose is native, no subprocess.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from python_on_whales import DockerClient
from python_on_whales.exceptions import DockerException, NoSuchContainer
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from . import config
from .correlator import correlate
from .log_pattern_analyzer import PatternDetector
from .spike_detector import DOCKER_TS_RE, detect_spikes

# Path to the test compose file (repo root / docker-compose.test.yml)
_REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = _REPO_ROOT / "docker-compose.test.yml"

# ── Logging (stderr only; stdout is the MCP JSON stream) ───────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


# ── Docker helpers ──────────────────────────────────────────────────────────

def _docker_client() -> DockerClient:
    """Connect to Docker daemon; raise RuntimeError with readable message on failure."""
    try:
        client = DockerClient()
        client.system.info()   # connectivity check – raises DockerException if daemon is down
        return client
    except DockerException as exc:
        raise RuntimeError(
            f"Cannot connect to Docker daemon – is Docker running? ({exc})"
        ) from exc


def _compose_client() -> DockerClient:
    """Return a DockerClient pre-configured with the test compose file."""
    return DockerClient(compose_files=[str(COMPOSE_FILE)])


def _fetch_logs(container, tail: int) -> list[str]:
    """Fetch last `tail` lines from a container with Docker-prepended timestamps."""
    try:
        logs = container.logs(tail=tail, timestamps=True)
        if isinstance(logs, bytes):
            logs = logs.decode("utf-8", errors="replace")
        return logs.splitlines() if logs else []
    except DockerException:
        return []


def _container_name(c) -> str:
    """Return clean container name (strip leading slash if present)."""
    return c.name.lstrip("/")


# ── Tool implementations ────────────────────────────────────────────────────

def tool_list_containers() -> dict:
    """List all running Docker containers."""
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    containers = [
        {
            "name": _container_name(c),
            "short_id": c.id[:12],
            "image": c.config.image,
            "status": c.state.status,
            "labels": c.config.labels,
        }
        for c in client.container.list()
    ]
    return {"status": "success", "containers": containers, "count": len(containers)}


def tool_analyze_patterns(
    container_name: Optional[str] = None,
    tail: int = 500,
) -> dict:
    """Fetch logs and run PatternDetector against one or all containers."""
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    if container_name:
        try:
            targets = [client.container.inspect(container_name)]
        except NoSuchContainer:
            return {"status": "error", "error": f"Container '{container_name}' not found."}
    else:
        targets = client.container.list()

    if not targets:
        return {"status": "success", "results": {}, "message": "No running containers."}

    detector = PatternDetector()
    results = {}

    for c in targets:
        name = _container_name(c)
        lines = _fetch_logs(c, tail)
        if not lines:
            results[name] = {"status": "no_logs"}
            continue

        ts_format = "unknown"
        ts_sample = ""
        for line in lines[:100]:
            detected = detector.detect_timestamp_format(line)
            if detected:
                ts_format, ts_sample, _ = detected
                break

        language, lang_confidence = detector.detect_language(lines)
        log_levels = detector.extract_log_levels(lines)
        health_check = detector.detect_health_checks(lines)
        common_errors = detector.extract_error_patterns(lines)

        results[name] = {
            "container_id": c.id[:12],
            "total_lines": len(lines),
            "timestamp_format": ts_format,
            "timestamp_sample": ts_sample[:60],
            "language": language,
            "language_confidence": round(lang_confidence, 3),
            "log_levels": log_levels,
            "health_check": {
                "detected": health_check is not None,
                "pattern": health_check.pattern if health_check else None,
                "frequency_per_minute": (
                    round(health_check.frequency_per_minute, 2) if health_check else None
                ),
            },
            "common_errors": [{"pattern": p, "count": n} for p, n in common_errors],
        }

    return {"status": "success", "results": results}


def tool_detect_error_spikes(
    container_name: Optional[str] = None,
    tail: int = 1000,
    window_minutes: int = 5,
    spike_threshold: float = 2.0,
) -> dict:
    """Detect error spikes using Polars rolling-window analysis."""
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    if container_name:
        try:
            targets = [client.container.inspect(container_name)]
        except NoSuchContainer:
            return {"status": "error", "error": f"Container '{container_name}' not found."}
    else:
        targets = client.container.list()

    all_spikes = []
    no_timestamp_containers = []

    for c in targets:
        name = _container_name(c)
        lines = _fetch_logs(c, tail)
        if not lines:
            continue
        spikes = detect_spikes(lines, name, window_minutes, spike_threshold)
        if spikes:
            all_spikes.extend(spikes)
        else:
            has_timestamps = any(DOCKER_TS_RE.match(l.strip()) for l in lines[:20])
            if not has_timestamps:
                no_timestamp_containers.append(name)

    all_spikes.sort(key=lambda x: (x["bucket_minute"], x["container"]))

    return {
        "status": "success",
        "spikes": all_spikes,
        "spike_count": len(all_spikes),
        "parameters": {
            "tail": tail,
            "window_minutes": window_minutes,
            "spike_threshold": spike_threshold,
        },
        "warnings": (
            [f"No timestamps found in logs for: {', '.join(no_timestamp_containers)}"]
            if no_timestamp_containers else []
        ),
    }


def tool_correlate_containers(
    time_window_seconds: int = 30,
    tail: int = 500,
) -> dict:
    """Compute pairwise temporal error correlation across all running containers."""
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    running = client.container.list()
    if len(running) < 2:
        return {
            "status": "success",
            "correlations": [],
            "message": "Need at least 2 running containers to correlate.",
        }

    container_logs = {_container_name(c): _fetch_logs(c, tail) for c in running}
    correlations = correlate(container_logs, time_window_seconds)

    return {
        "status": "success",
        "correlations": correlations,
        "parameters": {"time_window_seconds": time_window_seconds, "tail": tail},
    }


def tool_start_test_containers(rebuild: bool = False) -> dict:
    """Build (if needed) and start the test log-generator containers in detached mode."""
    if not COMPOSE_FILE.exists():
        return {"status": "error", "error": f"Compose file not found: {COMPOSE_FILE}"}
    try:
        client = _compose_client()
        client.compose.up(detach=True, build=rebuild)
        return {
            "status": "success",
            "output": "Containers started successfully.",
            "message": (
                "Test containers started. Use list_containers to see them, "
                "or analyze_patterns / detect_error_spikes once logs accumulate."
            ),
            "compose_file": str(COMPOSE_FILE),
        }
    except DockerException as exc:
        return {"status": "error", "error": str(exc)}


def tool_stop_test_containers() -> dict:
    """Stop and remove the test log-generator containers."""
    if not COMPOSE_FILE.exists():
        return {"status": "error", "error": f"Compose file not found: {COMPOSE_FILE}"}
    try:
        client = _compose_client()
        client.compose.down()
        return {
            "status": "success",
            "message": "Test containers stopped and removed.",
            "compose_file": str(COMPOSE_FILE),
        }
    except DockerException as exc:
        return {"status": "error", "error": str(exc)}


# ── MCP tool registry ───────────────────────────────────────────────────────

TOOLS = [
    Tool(
        name="list_containers",
        description="List all running Docker containers with name, image, status, and labels.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="analyze_patterns",
        description=(
            "Fetch Docker container logs and detect patterns: timestamp format, "
            "programming language, log level distribution, health check frequency, "
            "and common error patterns. No LLM required."
        ),
        inputSchema={
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
            },
            "required": [],
        },
    ),
    Tool(
        name="detect_error_spikes",
        description=(
            "Detect error spikes in Docker container logs using Polars rolling-window analysis. "
            "Flags 1-minute buckets where error count exceeds spike_threshold × rolling baseline. "
            "No LLM required."
        ),
        inputSchema={
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
    ),
    Tool(
        name="correlate_containers",
        description=(
            "Compute pairwise temporal correlation of errors across all running containers. "
            "Returns container pairs sorted by correlation score (0–1) with example "
            "co-occurring error lines. No LLM required."
        ),
        inputSchema={
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
    ),
    Tool(
        name="start_test_containers",
        description=(
            "Build and start the test log-generator containers defined in docker-compose.test.yml. "
            "Spins up 4 containers (web-app, database, cache, gateway) that emit random logs "
            "in different formats (ISO-8601, syslog, epoch, Apache) and languages "
            "(Python, Java, Go, Node.js) with periodic error spikes for testing."
        ),
        inputSchema={
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
    ),
    Tool(
        name="stop_test_containers",
        description="Stop and remove the test log-generator containers started by start_test_containers.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
]


# ── MCP server wiring ───────────────────────────────────────────────────────

def create_mcp_server() -> Server:
    server = Server("docker-log-analyzer")

    @server.list_tools()
    async def list_tools():
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        logger.debug("Tool called: %s, args: %s", name, arguments)
        try:
            if name == "list_containers":
                result = tool_list_containers()
            elif name == "analyze_patterns":
                result = tool_analyze_patterns(
                    container_name=arguments.get("container_name"),
                    tail=int(arguments.get("tail", config.DEFAULT_TAIL_LINES)),
                )
            elif name == "detect_error_spikes":
                result = tool_detect_error_spikes(
                    container_name=arguments.get("container_name"),
                    tail=int(arguments.get("tail", config.DEFAULT_SPIKE_TAIL_LINES)),
                    window_minutes=int(arguments.get("window_minutes", config.DEFAULT_WINDOW_MINUTES)),
                    spike_threshold=float(arguments.get("spike_threshold", config.DEFAULT_SPIKE_THRESHOLD)),
                )
            elif name == "correlate_containers":
                result = tool_correlate_containers(
                    time_window_seconds=int(
                        arguments.get("time_window_seconds", config.DEFAULT_CORRELATION_WINDOW_SECONDS)
                    ),
                    tail=int(arguments.get("tail", config.DEFAULT_TAIL_LINES)),
                )
            elif name == "start_test_containers":
                result = tool_start_test_containers(
                    rebuild=bool(arguments.get("rebuild", False)),
                )
            elif name == "stop_test_containers":
                result = tool_stop_test_containers()
            else:
                result = {"status": "error", "error": f"Unknown tool: {name}"}
        except Exception as exc:
            logger.exception("Unhandled error in tool '%s'", name)
            result = {"status": "error", "error": str(exc)}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return server


async def _main_async() -> None:
    server = create_mcp_server()
    logger.info("Docker Log Analyzer MCP Server starting (non-LLM mode)...")
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
