"""
tools.py – Docker log analysis tool implementations.

Shared infrastructure helpers (Docker client, log fetching, pattern cache)
and all tool_* functions callable directly or via the MCP registry.

All tools are stateless: fetch logs from Docker SDK → analyse → return JSON.
No background threads, no persistent in-memory state, no external API calls.
"""

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Optional

from python_on_whales import DockerClient
from python_on_whales.exceptions import DockerException, NoSuchContainer

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

# ── Module-level paths ──────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = _REPO_ROOT / "docker-compose.test.yml"

# Pattern analysis cache directory (keyed by container name)
PATTERN_CACHE_DIR = _REPO_ROOT / ".cache" / "patterns"

# Correlation result cache directory (keyed by MD5 of inputs)
CORRELATION_CACHE_DIR = _REPO_ROOT / ".cache" / "correlations"


# ── Pattern cache helpers ────────────────────────────────────────────────────

def _cache_path(container_name: str) -> Path:
    """Cache file path for a container's pattern analysis result."""
    safe = container_name.replace("/", "_")
    return PATTERN_CACHE_DIR / f"{safe}.json"


def _read_cache(container_name: str) -> Optional[dict]:
    path = _cache_path(container_name)
    if path.exists():
        return json.loads(path.read_text())
    return None


def _write_cache(container_name: str, data: dict) -> None:
    PATTERN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(container_name).write_text(json.dumps(data, indent=2))


# ── Correlation cache helpers ────────────────────────────────────────────────

def _correlation_cache_key(container_names: list[str], time_window_seconds: int, tail: int) -> str:
    """MD5 of sorted container names + parameters → stable cache filename."""
    key_str = ",".join(sorted(container_names)) + f"|{time_window_seconds}|{tail}"
    return hashlib.md5(key_str.encode()).hexdigest()


def _read_correlation_cache(cache_key: str) -> Optional[dict]:
    """Return cached correlation result if within TTL, else None."""
    path = CORRELATION_CACHE_DIR / f"{cache_key}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    ttl_seconds = settings.correlation_cache_ttl_minutes * 60
    if ttl_seconds <= 0:
        return None
    cached_at = datetime.fromisoformat(data["cached_at"])
    if (datetime.now(timezone.utc) - cached_at).total_seconds() > ttl_seconds:
        return None
    return data


def _write_correlation_cache(cache_key: str, result: dict) -> None:
    """Atomically write correlation result to cache."""
    CORRELATION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CORRELATION_CACHE_DIR / f"{cache_key}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result))
    tmp.rename(path)


# ── Docker helpers ──────────────────────────────────────────────────────────

def _docker_client() -> DockerClient:
    """Connect to Docker daemon; raise RuntimeError with readable message on failure."""
    try:
        client = DockerClient()
        client.system.info()
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
    Parse a time argument into a UTC datetime.

    Supported formats:
        "2 hours ago", "30 minutes ago", "7 days ago"
        "now"
        "2026-03-04T10:00:00Z"  (ISO-8601)
    """
    now = datetime.now(timezone.utc)

    if time_str.lower() == "now":
        return now

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

    try:
        if time_str.endswith("Z"):
            time_str = time_str[:-1] + "+00:00"
        return datetime.fromisoformat(time_str)
    except ValueError:
        logger.warning("Could not parse time: %s, using now", time_str)
        return now


def _fetch_logs_with_cache(
    container,
    container_name: str,
    since: datetime,
    until: datetime,
    use_cache: bool = True,
) -> tuple[list[str], bool]:
    """
    Cache-first log fetching.

    1. Check .cache/logs/<container>/<YYYY-MM-DD>.jsonl
    2. If cache covers the window, return cached logs
    3. Otherwise, fetch fresh from Docker API

    Returns: (logs, was_cached)
    """
    if use_cache:
        cached_logs = read_cached_logs_for_window(container_name, since, until)
        if cached_logs is not None:
            logger.debug("Cache hit: %s (%d lines)", container_name, len(cached_logs))
            return cached_logs, True

    logger.debug("Cache miss: %s, fetching from Docker API", container_name)
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

    Results are cached per container by name. Pass force_refresh=True to
    bypass the cache and re-analyse.
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

        if not force_refresh:
            cached = _read_cache(name)
            if cached is not None:
                results[name] = cached
                logger.debug("Pattern cache hit for container '%s'", name)
                continue

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
            "logs_cache_hit": was_cached,
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
    cache_hits = {}

    for c in targets:
        name = _container_name(c)
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
    """Compute pairwise temporal error correlation across all running containers."""
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

    running_names = [_container_name(c) for c in running]
    corr_cache_key = _correlation_cache_key(running_names, time_window_seconds, tail)

    if use_cache:
        cached = _read_correlation_cache(corr_cache_key)
        if cached is not None:
            logger.debug("Correlation cache hit (key=%s)", corr_cache_key)
            return {**cached, "correlation_cache_hit": True}

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

    result = {
        "status": "success",
        "correlations": correlations,
        "cache_hits": cache_hits,
        "correlation_cache_hit": False,
        "cached_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parameters": parameters,
    }
    _write_correlation_cache(corr_cache_key, result)
    return result


def tool_start_test_containers(rebuild: bool = False) -> dict:
    """Build (if needed) and start the test log-generator containers."""
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
    """Sync Docker logs to local cache (.cache/logs/) for a time window.

    Enables fast offline analysis and bug reproduction by caching logs locally.
    All analysis tools use cache-first strategy when fetching logs.

    Args:
        container_names: Specific containers to sync. Omit for all running.
        since: Start time ("2 hours ago", "2026-03-04T10:00:00Z", etc.)
        until: End time (default "now")
        force_refresh: Skip cache, re-fetch everything
    """
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    since_dt = _parse_time_arg(since)
    until_dt = _parse_time_arg(until)

    if since_dt > until_dt:
        return {"status": "error", "error": "since must be before until"}

    logger.info("Syncing logs from %s to %s", since_dt, until_dt)

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

    while current_date <= until_dt.date():
        day_start = datetime.combine(current_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        day_end = datetime.combine(current_date, datetime.max.time()).replace(
            tzinfo=timezone.utc
        )
        day_start = max(day_start, since_dt)
        day_end = min(day_end, until_dt)

        for c in targets:
            name = _container_name(c)
            if name not in synced:
                synced[name] = {"dates": [], "total_lines": 0}
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
    """Capture live logs for `duration_seconds`, then return a combined analysis.

    Designed for bug reproduction: call this, reproduce the issue, and get a
    unified report of error spikes, cross-container correlation, and per-container
    log level breakdown for exactly the window you care about.
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

    container_logs: dict[str, list[str]] = {}
    cache_hits = {}
    for c in targets:
        name = _container_name(c)
        logs, was_cached = _fetch_logs_with_cache(
            c, name, start_time, end_time, use_cache=use_cache
        )
        container_logs[name] = logs
        cache_hits[name] = was_cached

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
        "cache_hits": cache_hits,
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
    """Detect sensitive data (API keys, credentials, PII) in container logs."""
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

    from .secret_detector import Finding

    finding_objs = [
        Finding(
            severity=f["severity"],
            pattern_name=f["pattern_name"],
            line_number=f["line_number"],
            timestamp=f["timestamp"],
            context_before=f["context_before"],
            context_after=f["context_after"],
            matched_text_redacted=f["matched_text"],
        )
        for f in all_findings
    ]

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

    Note: Dependencies are inferred best-effort from log content.
    HTTP URL matches are high-confidence; container name mentions are low-confidence.
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
