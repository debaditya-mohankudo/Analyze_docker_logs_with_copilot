"""
MCP Server for Docker Log Pattern Analysis (non-LLM).

Exposes 7 tools to VSCode Copilot Agent Mode via .vscode/mcp.json:

  list_containers        – discover running Docker containers
  analyze_patterns       – PatternDetector per container (timestamps, language, log levels)
  detect_error_spikes    – Polars rolling-window spike detection
  correlate_containers   – pairwise cross-container temporal error correlation
  detect_data_leaks      – SecretDetector for API keys, credentials, PII, sensitive data
  start_test_containers  – build & start test log-generator containers (docker-compose.test.yml)
  stop_test_containers   – stop and remove test log-generator containers

All tools are stateless (fetch → analyse → return JSON). No external API calls.
Uses python-on-whales (CLI wrapper) instead of docker-py; compose is native, no subprocess.
"""

import asyncio
import json
import re
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Optional

from python_on_whales import DockerClient
from python_on_whales.exceptions import DockerException, NoSuchContainer
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .cache_manager import (
    read_cached_logs_for_window,
    write_cached_logs_for_date,
    get_cache_info,
)
from .config import settings
from .correlator import correlate
from .dependency_mapper import build_graph, find_cascade_candidates
from .log_pattern_analyzer import PatternDetector
from .logger import logger
from .secret_detector import SecretDetector
from .spike_detector import DOCKER_TS_RE, detect_spikes

# Path to the test compose file (repo root / docker-compose.test.yml)
_REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = _REPO_ROOT / "docker-compose.test.yml"

# Pattern analysis cache (keyed by container name + short_id)
_CACHE_DIR = _REPO_ROOT / ".cache" / "patterns"

# ── Logging (stderr only; stdout is the MCP JSON stream) ───────────────────
# Logger is initialized from logger.py singleton (with run_id tracking)


# ── Pattern cache helpers ────────────────────────────────────────────────────

def _cache_path(container_name: str) -> Path:
    """Cache file path for a container (keyed by name only, not short_id).
    
    This persists across container restarts. Users should manually clean .cache/patterns
    if they change the log pattern of a container.
    """
    safe = container_name.replace("/", "_")
    return _CACHE_DIR / f"{safe}.json"


def _read_cache(container_name: str) -> Optional[dict]:
    path = _cache_path(container_name)
    if path.exists():
        return json.loads(path.read_text())
    return None


def _write_cache(container_name: str, data: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(container_name).write_text(json.dumps(data, indent=2))


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


def _fetch_logs_window(container, since: datetime, until: datetime) -> list[str]:
    """Fetch logs produced between `since` and `until` (exact time range)."""
    try:
        logs = container.logs(since=since, until=until, timestamps=True)
        if isinstance(logs, bytes):
            logs = logs.decode("utf-8", errors="replace")
        return logs.splitlines() if logs else []
    except DockerException:
        return []


def _container_name(c) -> str:
    """Return clean container name (strip leading slash if present)."""
    return c.name.lstrip("/")


def _parse_time_arg(time_str: str) -> datetime:
    """
    Parse time argument like "2 hours ago", "now", or ISO-8601 timestamp.

    Examples:
        "2 hours ago" → datetime 2 hours before now
        "now" → current UTC time
        "2026-03-04T10:00:00Z" → parsed ISO-8601
    """
    now = datetime.now(timezone.utc)

    # Handle "now"
    if time_str.lower() == "now":
        return now

    # Handle relative times like "2 hours ago", "1 day ago", etc.
    match = re.match(r"(\d+)\s+(second|minute|hour|day|week)s?\s+ago", time_str)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()

        delta_map = {
            "second": timedelta(seconds=amount),
            "minute": timedelta(minutes=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "week": timedelta(weeks=amount),
        }
        return now - delta_map[unit]

    # Try parsing as ISO-8601
    try:
        if time_str.endswith("Z"):
            time_str = time_str[:-1] + "+00:00"
        return datetime.fromisoformat(time_str)
    except ValueError:
        logger.warning(f"Could not parse time: {time_str}, using now")
        return now


def _fetch_logs_with_cache(
    container,
    container_name: str,
    since: datetime,
    until: datetime,
    use_cache: bool = True,
) -> tuple[list[str], bool]:
    """
    Fetch logs: CACHE-FIRST strategy.

    1. Check .cache/logs/<container>/<YYYY-MM-DD>.jsonl
    2. If cache hit and covers window, return cached logs
    3. Otherwise, fetch fresh from Docker API

    Returns: (logs, was_cached)
    """
    # STEP 1: Try cache first
    if use_cache:
        cached_logs = read_cached_logs_for_window(container_name, since, until)
        if cached_logs is not None:
            logger.debug(f"Cache hit: {container_name} ({len(cached_logs)} lines)")
            return cached_logs, True

    # STEP 2: Fallback to Docker API
    logger.debug(f"Cache miss or fallback: {container_name}, fetching from Docker API")
    logs = _fetch_logs_window(container, since, until)
    return logs, False


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
    force_refresh: bool = False,
    use_cache: bool = True,
) -> dict:
    """Fetch logs and run PatternDetector against one or all containers.

    Logs fetching strategy (cache-first):
    1. Check .cache/logs/<container>/<YYYY-MM-DD>.jsonl (24-hour window)
    2. If cache hit, use cached logs (instant)
    3. Otherwise, fetch fresh from Docker API

    Results are cached per container by name and persisted across restarts.
    Pass force_refresh=True to bypass the cache and re-analyse. If you change
    the log pattern of a container, see README for cache cleanup instructions.
    """
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
        short_id = c.id[:12]

        # Return cached result if available and not forcing a refresh
        if not force_refresh:
            cached = _read_cache(name)
            if cached is not None:
                results[name] = cached
                logger.debug("Cache hit for container '%s'", name)
                continue

        # Fetch logs: cache-first (last 24 hours)
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)
        lines, was_cached = _fetch_logs_with_cache(c, name, since, now, use_cache=use_cache)
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

        entry = {
            "container_id": short_id,
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
            "logs_cache_hit": was_cached,  # Track if logs came from cache
            "analyzed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        _write_cache(name, entry)
        results[name] = entry

    return {"status": "success", "results": results}


def tool_detect_error_spikes(
    container_name: Optional[str] = None,
    tail: int = 1000,
    window_minutes: int = 5,
    spike_threshold: float = 2.0,
    use_cache: bool = True,
) -> dict:
    """Detect error spikes using Polars rolling-window analysis.

    Logs fetching strategy (cache-first):
    1. Check .cache/logs/<container>/<YYYY-MM-DD>.jsonl (24-hour window)
    2. If cache hit, use cached logs (instant)
    3. Otherwise, fetch fresh from Docker API
    """
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
    cache_hits = {}

    for c in targets:
        name = _container_name(c)
        # Fetch logs: cache-first (last 24 hours)
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)
        lines, was_cached = _fetch_logs_with_cache(c, name, since, now, use_cache=use_cache)
        cache_hits[name] = was_cached
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
        "cache_hits": cache_hits,
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
    use_cache: bool = True,
) -> dict:
    """Compute pairwise temporal error correlation across all running containers.

    Logs fetching strategy (cache-first):
    1. Check .cache/logs/<container>/<YYYY-MM-DD>.jsonl (24-hour window)
    2. If cache hit, use cached logs (instant)
    3. Otherwise, fetch fresh from Docker API
    """
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    running = client.container.list()
    parameters = {"time_window_seconds": time_window_seconds, "tail": tail}

    if len(running) < 2:
        return {
            "status": "success",
            "correlations": [],
            "message": "Need at least 2 running containers to correlate.",
            "parameters": parameters,
        }

    # Fetch logs: cache-first (last 24 hours)
    container_logs = {}
    cache_hits = {}
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    for c in running:
        name = _container_name(c)
        logs, was_cached = _fetch_logs_with_cache(c, name, since, now, use_cache=use_cache)
        if logs:
            container_logs[name] = logs
            cache_hits[name] = was_cached

    correlations = correlate(container_logs, time_window_seconds)

    return {
        "status": "success",
        "correlations": correlations,
        "cache_hits": cache_hits,
        "parameters": parameters,
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


def tool_sync_docker_logs(
    container_names: Optional[list[str]] = None,
    since: str = "24 hours ago",
    until: str = "now",
    force_refresh: bool = False,
) -> dict:
    """
    Sync Docker logs to local cache (.cache/logs/) for time window.

    Cache organized by container and date:
    .cache/logs/
      ├── web-app/2026-03-04.jsonl
      ├── database/2026-03-04.jsonl
      └── metadata.json

    Enables fast offline analysis and bug reproduction.

    Args:
        container_names: Specific containers to sync. Omit for all.
        since: Start time ("2 hours ago", "2026-03-04T10:00:00Z", etc.)
        until: End time (default "now")
        force_refresh: Skip cache, re-fetch everything

    Returns:
        {
            "status": "success",
            "synced_containers": {
                "web-app": {"dates": ["2026-03-04"], "total_lines": 5000}
            },
            "time_window": {"since": "...", "until": "..."},
            "cache_path": ".cache/logs/"
        }
    """
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    # Parse time window
    since_dt = _parse_time_arg(since)
    until_dt = _parse_time_arg(until)

    if since_dt > until_dt:
        return {"status": "error", "error": "since must be before until"}

    logger.info(f"Syncing logs from {since_dt} to {until_dt}")

    # Get target containers
    if container_names:
        targets = []
        for name in container_names:
            try:
                targets.append(client.container.inspect(name))
            except NoSuchContainer:
                return {"status": "error", "error": f"Container '{name}' not found."}
    else:
        targets = client.container.list()

    if not targets:
        return {
            "status": "success",
            "message": "No running containers to sync.",
            "time_window": {
                "since": since_dt.isoformat(),
                "until": until_dt.isoformat(),
            },
        }

    synced = {}
    current_date = since_dt.date()

    # Fetch and cache for each date in window
    while current_date <= until_dt.date():
        day_start = datetime.combine(current_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        day_end = datetime.combine(current_date, datetime.max.time()).replace(
            tzinfo=timezone.utc
        )

        # Clamp to requested window
        day_start = max(day_start, since_dt)
        day_end = min(day_end, until_dt)

        for c in targets:
            name = _container_name(c)

            if name not in synced:
                synced[name] = {"dates": [], "total_lines": 0}

            # Fetch logs for this date
            logs = _fetch_logs_window(c, day_start, day_end)

            if logs:
                write_cached_logs_for_date(name, logs, current_date)
                synced[name]["dates"].append(str(current_date))
                synced[name]["total_lines"] += len(logs)

        current_date += timedelta(days=1)

    return {
        "status": "success",
        "synced_containers": synced,
        "time_window": {
            "since": since_dt.isoformat(),
            "until": until_dt.isoformat(),
        },
        "cache_path": ".cache/logs/",
        "message": f"Synced {len(synced)} containers to cache",
    }


async def tool_capture_and_analyze(
    container_names: Optional[list[str]] = None,
    duration_seconds: int = 120,
    spike_threshold: float = 2.0,
    time_window_seconds: int = 30,
    use_cache: bool = True,
) -> dict:
    """
    Capture live logs for `duration_seconds`, then return a combined analysis.

    Designed for bug reproduction: call this, reproduce the issue, and get a
    unified report of error spikes, cross-container correlation, and per-container
    log level breakdown for exactly the window you care about.

    If use_cache=True, checks if logs already cached for the capture window
    (e.g., from sync_docker_logs). Can use cached logs to analyze instantly
    if window already synced, avoiding the wait time.
    """
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    if container_names:
        targets = []
        for name in container_names:
            try:
                targets.append(client.container.inspect(name))
            except NoSuchContainer:
                return {"status": "error", "error": f"Container '{name}' not found."}
    else:
        targets = client.container.list()

    if not targets:
        return {"status": "success", "message": "No running containers to monitor."}

    start_time = datetime.now(timezone.utc)
    logger.info(
        "capture_and_analyze: watching %d containers for %ds",
        len(targets), duration_seconds,
    )

    await asyncio.sleep(duration_seconds)

    end_time = datetime.now(timezone.utc)

    # Fetch logs (cache-first strategy)
    container_logs: dict[str, list[str]] = {}
    cache_hits = {}
    for c in targets:
        name = _container_name(c)
        logs, was_cached = _fetch_logs_with_cache(
            c, name, start_time, end_time, use_cache=use_cache
        )
        container_logs[name] = logs
        cache_hits[name] = was_cached

    # Run all analysers on the captured lines
    detector = PatternDetector()
    all_spikes: list[dict] = []
    per_container: dict[str, dict] = {}
    total_lines = 0
    total_errors = 0
    containers_with_errors = 0

    for name, lines in container_logs.items():
        spikes = detect_spikes(lines, name, window_minutes=1, spike_threshold=spike_threshold)
        all_spikes.extend(spikes)

        log_levels = detector.extract_log_levels(lines)
        top_errors = detector.extract_error_patterns(lines)
        error_count = sum(
            v for k, v in log_levels.items()
            if k in ("ERROR", "CRITICAL", "FATAL", "SEVERE")
        )

        total_lines += len(lines)
        total_errors += error_count
        if error_count > 0:
            containers_with_errors += 1

        per_container[name] = {
            "lines_captured": len(lines),
            "log_levels": log_levels,
            "top_errors": [{"pattern": p, "count": n} for p, n in top_errors],
        }

    correlations = correlate(container_logs, time_window_seconds)

    return {
        "status": "success",
        "capture_window": {
            "start": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end":   end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_seconds": duration_seconds,
        },
        "containers_monitored": list(container_logs.keys()),
        "cache_hits": cache_hits,  # Show which containers used cache
        "summary": {
            "total_log_lines": total_lines,
            "total_errors": total_errors,
            "containers_with_errors": containers_with_errors,
            "spike_count": len(all_spikes),
        },
        "error_spikes": all_spikes,
        "correlations": correlations,
        "per_container": per_container,
    }


async def tool_detect_data_leaks(
    duration_seconds: int = 60,
    container_names: Optional[list[str]] = None,
    severity_filter: str = "all",
    use_cache: bool = True,
) -> dict:
    """
    Detect sensitive data (API keys, credentials, PII) in container logs.

    Designed for security audits: call this to scan logs for accidental secret leaks,
    then review findings and apply remediation (key rotation, etc.).

    Logs fetching strategy (cache-first):
    1. Check .cache/logs/<container>/<YYYY-MM-DD>.jsonl (24-hour window)
    2. If cache hit, use cached logs (instant)
    3. Otherwise, fetch fresh from Docker API
    """
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    if container_names:
        targets = []
        for name in container_names:
            try:
                targets.append(client.container.inspect(name))
            except NoSuchContainer:
                return {"status": "error", "error": f"Container '{name}' not found."}
    else:
        targets = client.container.list()

    if not targets:
        return {"status": "success", "message": "No running containers to scan."}

    start_time = datetime.now(timezone.utc)
    logger.info("detect_data_leaks: scanning %d containers for %ds", len(targets), duration_seconds)

    await asyncio.sleep(duration_seconds)

    end_time = datetime.now(timezone.utc)

    # Fetch logs during the scan window (cache-first)
    detector = SecretDetector()
    all_findings = []
    per_container_summary = {}
    cache_hits = {}

    for c in targets:
        name = _container_name(c)
        lines, was_cached = _fetch_logs_with_cache(c, name, start_time, end_time, use_cache=use_cache)
        cache_hits[name] = was_cached

        if not lines:
            per_container_summary[name] = {"lines_scanned": 0, "findings": 0}
            continue

        findings = detector.scan_logs(lines, severity_filter=severity_filter)
        per_container_summary[name] = {
            "lines_scanned": len(lines),
            "findings": len(findings),
        }

        # Attach container name to each finding
        for f in findings:
            all_findings.append(
                {
                    "container": name,
                    "severity": f.severity,
                    "pattern_name": f.pattern_name,
                    "matched_text": f.matched_text_redacted,
                    "line_number": f.line_number,
                    "timestamp": f.timestamp,
                    "context_before": f.context_before,
                    "context_after": f.context_after,
                }
            )

    # Generate summary statistics
    summary = detector.get_findings_summary(
        [f for f in detector.scan_logs([], severity_filter=severity_filter)]
    )
    # Re-calculate summary from actual findings
    finding_objs = []
    for finding in all_findings:
        from .secret_detector import Finding

        finding_objs.append(
            Finding(
                severity=finding["severity"],
                pattern_name=finding["pattern_name"],
                line_number=finding["line_number"],
                timestamp=finding["timestamp"],
                context_before=finding["context_before"],
                context_after=finding["context_after"],
                matched_text_redacted=finding["matched_text"],
            )
        )

    summary = detector.get_findings_summary(finding_objs)
    recommendations = detector.get_recommendations(finding_objs)

    return {
        "status": "success",
        "scan_window": {
            "start": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_seconds": duration_seconds,
        },
        "containers_scanned": list(per_container_summary.keys()),
        "cache_hits": cache_hits,
        "findings": all_findings,
        "summary": summary,
        "per_container": per_container_summary,
        "recommendations": recommendations,
    }

def tool_map_service_dependencies(
    containers: Optional[list[str]] = None,
    tail: int = 500,
    include_transitive: bool = False,
    use_cache: bool = True,
) -> dict:
    """Map service dependencies inferred from container log analysis.

    Scans logs for HTTP URLs, database connection strings, gRPC dial calls, and
    container name mentions to build a directed dependency graph. Joins with
    temporal error correlation to surface likely error cascade candidates.

    Logs fetching strategy (cache-first):
    1. Check .cache/logs/<container>/<YYYY-MM-DD>.jsonl (24-hour window)
    2. If cache hit, use cached logs (instant)
    3. Otherwise, fetch fresh from Docker API

    Note: Dependencies are inferred best-effort from log content.
    HTTP URL matches are high-confidence; container name mentions are low-confidence.
    gRPC/event-driven architectures may have limited coverage.
    """
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    if containers:
        try:
            targets = [client.container.inspect(name) for name in containers]
        except NoSuchContainer as exc:
            return {"status": "error", "error": f"Container not found: {exc}"}
    else:
        targets = client.container.list()

    if not targets:
        return {
            "status": "success",
            "dependencies": {},
            "cascade_candidates": [],
            "cache_hits": {},
            "message": "No running containers.",
        }

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    container_logs: dict[str, list[str]] = {}
    cache_hits: dict[str, bool] = {}

    for c in targets:
        name = _container_name(c)
        logs, was_cached = _fetch_logs_with_cache(c, name, since, now, use_cache=use_cache)
        if logs:
            container_logs[name] = logs
            cache_hits[name] = was_cached

    if not container_logs:
        return {
            "status": "success",
            "dependencies": {},
            "cascade_candidates": [],
            "cache_hits": cache_hits,
            "message": "No logs found in any container.",
        }

    graph = build_graph(container_logs, include_transitive=include_transitive)

    cascade_candidates: list[dict] = []
    if len(container_logs) >= 2:
        correlations = correlate(container_logs, time_window_seconds=30)
        cascade_candidates = find_cascade_candidates(graph, correlations)

    return {
        "status": "success",
        "dependencies": graph,
        "cascade_candidates": cascade_candidates,
        "cache_hits": cache_hits,
        "parameters": {
            "tail": tail,
            "include_transitive": include_transitive,
        },
    }


# ── Tool wrapper functions (for registry) ──────────────────────────────────

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
    logger.info(f"  Cache Directory:          {_CACHE_DIR}")
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
