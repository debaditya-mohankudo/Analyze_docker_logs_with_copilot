"""
tools.py – Docker log analysis tool implementations.

Shared infrastructure helpers (Docker client, log fetching, pattern cache)
and all tool_* functions callable directly or via the MCP registry.

All tools are stateless: fetch logs from Docker SDK → analyse → return JSON.
No background threads, no persistent in-memory state, no external API calls.
"""

import hashlib
import time
import json
import re
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Optional

from python_on_whales.exceptions import DockerException, NoSuchContainer

from .cache_manager import (
    read_cached_logs_for_window,
    write_cached_logs_for_date,
    get_cache_info,
    clear_cache,
)
from .investigation_planner import generate_plan
from .coderepo import analyse_code_context
from .config import settings
from .correlator import correlate
from .dependency_mapper import build_graph, find_cascade_candidates
from .log_pattern_analyzer import PatternDetector
from .logger import logger
from .error_classifier import classify_lines, aggregate_stats, VALID_CATEGORIES
from .request_tracer import RequestIdPattern, extract_ids, cross_container_timelines
from .api_query_extractor import extract_api_calls, extract_queries
from .root_cause_analyzer import rank_root_causes
from .secret_detector import SecretDetector
from .patterns import DOCKER_TS_RE, ERROR_PATTERN_RE
from .spike_detector import detect_spikes
from .docker import (
    COMPOSE_FILE,
    _docker_client,
    _compose_client,
    _fetch_logs,
    _fetch_logs_window,
    _container_name,
    _fetch_logs_with_cache,
)

# ── Module-level paths ──────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent

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


# ── Error level detector ─────────────────────────────────────────────────────

_LEVEL_RE = re.compile(r"\b(fatal|critical|error|panic|exception|traceback|severe)\b", re.IGNORECASE)


def _detect_level(line: str) -> str:
    """Classify an error log line as fatal, critical, or error."""
    m = _LEVEL_RE.search(line)
    if not m:
        return "error"
    word = m.group(1).lower()
    if word in ("fatal", "panic"):
        return "fatal"
    if word == "critical":
        return "critical"
    return "error"


# ── ISO-8601 time parser ────────────────────────────────────────────────────

def _parse_iso(s: Optional[str]) -> datetime:
    """Parse an ISO-8601 UTC string. None or empty → current UTC time."""
    if not s:
        return datetime.now(timezone.utc)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


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
    1. Check the SQLite log cache for the last settings.log_lookback_minutes minutes
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
        since = now - timedelta(minutes=settings.log_lookback_minutes)
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
        framework = detector.detect_framework(language, lines)
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
            "framework": framework,
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


def tool_analyze_error_spikes(
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
        since = now - timedelta(minutes=settings.log_lookback_minutes)
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


def tool_analyze_correlations(
    time_window_seconds: int = 30,
    tail: int = 500,
    use_cache: bool = True,
    container_names: list[str] | None = None,
) -> dict:
    """Compute pairwise temporal error correlation across running containers."""
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    running = client.container.list()
    parameters = {"time_window_seconds": time_window_seconds, "tail": tail}

    # Filter to user-selected containers if provided
    if container_names:
        running = [c for c in running if _container_name(c) in container_names]

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
    since = now - timedelta(minutes=settings.log_lookback_minutes)

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
                "or analyze_patterns / analyze_error_spikes once logs accumulate."
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
    since: Optional[str] = None,
    until: Optional[str] = None,
    force_refresh: bool = False,
) -> dict:
    """Sync Docker logs to local cache (.cache/logs/) for a time window.

    Enables fast offline analysis and bug reproduction by caching logs locally.
    All analysis tools use cache-first strategy when fetching logs.

    Args:
        container_names: Specific containers to sync. Omit for all running.
        since: Start time as ISO-8601 UTC (e.g. "2026-03-04T10:00:00Z"). Defaults to settings.log_lookback_minutes ago.
        until: End time as ISO-8601 UTC. Defaults to now.
        force_refresh: Skip cache, re-fetch everything
    """
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    since_dt = _parse_iso(since) if since else datetime.now(timezone.utc) - timedelta(minutes=settings.log_lookback_minutes)
    until_dt = _parse_iso(until)

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


def tool_capture_logs(
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
        "capture_logs: watching %d containers for %ds",
        len(targets), duration_seconds,
    )

    time.sleep(duration_seconds)

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
        # extract_error_patterns only matches a narrow set of known error
        # *shapes* (settings.error_patterns + a few regexes) and can come
        # back empty even when error_count > 0 — ERROR_PATTERN_RE is the
        # same broad line-level match tool_get_last_errors uses, so this
        # always surfaces the actual offending lines, not just recognized
        # templates. Same {timestamp, level, message} shape as
        # tool_get_last_errors's "errors" entries, for consistency.
        error_lines = [
            {
                "timestamp": (m := DOCKER_TS_RE.match(line.strip())) and m.group(1),
                "level": _detect_level(line),
                "message": line,
            }
            for line in lines if ERROR_PATTERN_RE.search(line)
        ][-20:]
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
            "error_lines": error_lines,
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
        # Raw lines are included (not just the analysis) so the TUI can
        # write them out to a plain-text file for handoff/audit purposes —
        # separate from the .cache/logs/ Parquet cache, which this tool
        # deliberately does not write to (analysis-only tool by design).
        "raw_logs": container_logs,
    }


def tool_detect_data_leaks(
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

    time.sleep(duration_seconds)

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
    since = now - timedelta(minutes=settings.log_lookback_minutes)

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


def tool_analyze_root_causes(
    containers: Optional[list[str]] = None,
    tail: int = 500,
    time_window_seconds: int = 3600,
    include_transitive: bool = False,
    use_cache: bool = True,
) -> dict:
    """Rank containers by root-cause likelihood using dependency graph, cascade candidates, and spike timing."""
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

    params = {
        "containers": containers,
        "tail": tail,
        "time_window_seconds": time_window_seconds,
        "include_transitive": include_transitive,
    }

    if not targets:
        return {
            "status": "success",
            "root_causes": [],
            "analysis_inputs": {
                "containers_analyzed": 0,
                "spikes_detected": 0,
                "cascade_candidates": 0,
                "dependency_edges": 0,
            },
            "cache_hits": {},
            "parameters": params,
            "message": "No running containers.",
        }

    now = datetime.now(timezone.utc)
    since = now - timedelta(seconds=time_window_seconds)

    container_logs: dict[str, list[str]] = {}
    cache_hits: dict[str, bool] = {}

    for c in targets:
        name = _container_name(c)
        logs, was_cached = _fetch_logs_with_cache(c, name, since, now, use_cache=use_cache)
        if logs:
            container_logs[name] = logs
            cache_hits[name] = was_cached

    # Collect spikes per container
    all_spikes: list[dict] = []
    for c in targets:
        name = _container_name(c)
        if name not in container_logs:
            continue
        spikes = detect_spikes(container_logs[name], name)
        all_spikes.extend(spikes)

    # Build dependency graph and cascade candidates
    graph = build_graph(container_logs, include_transitive=include_transitive)

    cascade_candidates: list[dict] = []
    if len(container_logs) >= 2:
        correlations = correlate(container_logs, time_window_seconds=30)
        cascade_candidates = find_cascade_candidates(graph, correlations)

    root_causes = rank_root_causes(graph, cascade_candidates, all_spikes)

    dependency_edges = sum(len(edges) for edges in graph.values())

    return {
        "status": "success",
        "root_causes": root_causes,
        "analysis_inputs": {
            "containers_analyzed": len(container_logs),
            "spikes_detected": len(all_spikes),
            "cascade_candidates": len(cascade_candidates),
            "dependency_edges": dependency_edges,
        },
        "cache_hits": cache_hits,
        "parameters": params,
    }


def tool_get_last_errors(
    container_name: str,
    tail: int = 200,
    limit: int = 10,
) -> dict:
    """Return the last `limit` error/fatal lines from a single container's logs.

    Scans the most recent `tail` log lines, filters by ERROR_PATTERN_RE, and
    returns the last `limit` matches in chronological order with parsed timestamp
    and classified severity level.
    """
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    try:
        c = client.container.inspect(container_name)
    except NoSuchContainer:
        return {"status": "error", "error": f"Container '{container_name}' not found."}

    lines = _fetch_logs(c, tail=tail)
    error_lines = [line for line in lines if ERROR_PATTERN_RE.search(line)]

    entries = []
    for line in error_lines[-limit:]:
        m = DOCKER_TS_RE.match(line.strip())
        entries.append({
            "timestamp": m.group(1) if m else None,
            "level": _detect_level(line),
            "message": line,
        })

    return {
        "status": "success",
        "container": container_name,
        "errors_found": len(error_lines),
        "limit": limit,
        "errors": entries,
    }


def tool_trace_request_flow(
    container_names: Optional[list[str]] = None,
    tail: int = 500,
    use_cache: bool = True,
    min_events: int = 2,
    max_requests: int = 50,
) -> dict:
    """Trace request flows by correlating request/trace IDs in logs.

    Scans log lines for configurable request ID patterns (set via REQUEST_ID_PATTERNS
    in config / .env), groups matched lines by ID, and builds per-request chronological
    timelines per container. Call with multiple container_names to collect timelines from
    each; the same request ID will appear as separate timeline entries per container —
    compare them manually to trace a transaction across service boundaries.

    Parameters
    ----------
    container_names : Containers to scan. Omit for all running containers.
    tail            : Log lines to fetch per container (default 500).
    use_cache       : Use cached logs when available (default True).
    min_events      : Minimum events per request ID to include (default 2).
                      Filters out IDs seen only once (likely noise).
    max_requests    : Maximum number of request timelines to return (default 50).
                      Sorted by event_count descending before truncation.

    Returns
    -------
    JSON with:
        status              – 'success' or 'error'
        timelines           – list of per-request timeline dicts
        request_count       – total matched requests before max_requests truncation
        containers_scanned  – container names that were scanned
        cache_hits          – dict of container → bool (True = served from cache)
        parameters          – effective tail / min_events / max_requests values
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
        return {
            "status": "success",
            "timelines": [],
            "request_count": 0,
            "containers_scanned": [],
            "cache_hits": {},
            "parameters": {"tail": tail, "min_events": min_events, "max_requests": max_requests},
        }

    # Build RequestIdPattern list from settings.
    patterns = []
    for name, pat in settings.request_id_patterns.items():
        try:
            patterns.append(RequestIdPattern(name=name, pattern=pat))
        except ValueError as exc:
            logger.warning("Skipping invalid request_id pattern '%s': %s", name, exc)

    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=settings.log_lookback_minutes)

    cache_hits: dict[str, bool] = {}
    # Collect all matches globally — (id_value, pattern_name, unix_ts, line, container)
    all_matches: list[tuple[str, str, float | None, str, str]] = []

    for c in targets:
        cname = _container_name(c)
        lines, was_cached = _fetch_logs_with_cache(c, cname, since, now, use_cache=use_cache)
        cache_hits[cname] = was_cached
        if tail <= 0:
            lines = []
        else:
            lines = lines[-tail:]
        if not lines or not patterns:
            continue
        for id_value, pattern_name, unix_ts, line in extract_ids(lines, patterns):
            all_matches.append((id_value, pattern_name, unix_ts, line, cname))

    # Cross-container grouping by ID value within the trace time window.
    timelines = cross_container_timelines(all_matches, settings.trace_window_seconds)

    # Filter by min_events, sort by event_count descending, truncate.
    filtered = [t for t in timelines if t["event_count"] >= min_events]
    filtered.sort(key=lambda t: t["event_count"], reverse=True)
    total = len(filtered)

    return {
        "status": "success",
        "timelines": filtered[:max_requests],
        "request_count": total,
        "containers_scanned": [_container_name(c) for c in targets],
        "cache_hits": cache_hits,
        "parameters": {
            "tail": tail,
            "min_events": min_events,
            "max_requests": max_requests,
            "trace_window_seconds": settings.trace_window_seconds,
        },
    }


def tool_extract_apis_and_queries(
    container_names: Optional[list[str]] = None,
    tail: int = 500,
    use_cache: bool = True,
) -> dict:
    """Extract HTTP API calls and DB queries from container logs.

    Detects each container's language via PatternDetector.detect_language and,
    when the container is Java, prefers Spring/Hibernate-specific patterns
    before falling back to generic HTTP-access-log / SQL patterns.

    Parameters
    ----------
    container_names : Containers to scan. Omit for all running containers.
    tail            : Log lines to fetch per container (default 500).
    use_cache       : Use cached logs when available (default True).

    Returns
    -------
    JSON with:
        status              – 'success' or 'error'
        api_calls           – list of {container, method, path, status, timestamp, line}
        queries             – list of {container, query, timestamp, line}
        summary             – endpoint_counts, query_counts, detected_language per container
        containers_scanned  – container names that were scanned
        cache_hits          – dict of container → bool (True = served from cache)
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
        return {
            "status": "success",
            "api_calls": [],
            "queries": [],
            "summary": {"endpoint_counts": {}, "query_counts": {}, "detected_language": {}},
            "containers_scanned": [],
            "cache_hits": {},
        }

    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=settings.log_lookback_minutes)

    cache_hits: dict[str, bool] = {}
    detected_language: dict[str, str] = {}
    api_calls: list[dict] = []
    queries: list[dict] = []
    endpoint_counts: dict[str, int] = {}
    query_counts: dict[str, int] = {}

    for c in targets:
        cname = _container_name(c)
        lines, was_cached = _fetch_logs_with_cache(c, cname, since, now, use_cache=use_cache)
        cache_hits[cname] = was_cached
        lines = lines[-tail:] if tail > 0 else []
        if not lines:
            detected_language[cname] = "unknown"
            continue

        language, _confidence = PatternDetector.detect_language(lines)
        detected_language[cname] = language

        for method, path, status, unix_ts, line in extract_api_calls(lines, language=language):
            api_calls.append({
                "container": cname,
                "method": method,
                "path": path,
                "status": status,
                "timestamp": (
                    datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()
                    if unix_ts is not None else None
                ),
                "line": line[:500],
            })
            endpoint_key = f"{method} {path}"
            endpoint_counts[endpoint_key] = endpoint_counts.get(endpoint_key, 0) + 1

        for query_text, unix_ts, line in extract_queries(lines, language=language):
            queries.append({
                "container": cname,
                "query": query_text[:500],
                "timestamp": (
                    datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()
                    if unix_ts is not None else None
                ),
                "line": line[:500],
            })
            verb = query_text.strip().split()[0].upper() if query_text.strip() else "UNKNOWN"
            query_counts[verb] = query_counts.get(verb, 0) + 1

    return {
        "status": "success",
        "api_calls": api_calls,
        "queries": queries,
        "summary": {
            "endpoint_counts": endpoint_counts,
            "query_counts": query_counts,
            "detected_language": detected_language,
        },
        "containers_scanned": [_container_name(c) for c in targets],
        "cache_hits": cache_hits,
    }


def tool_classify_errors(
    container_names: Optional[list[str]] = None,
    tail: int = 1000,
    categories: Optional[list[str]] = None,
    use_cache: bool = True,
) -> dict:
    """Classify errors in container logs into semantic categories.

    Scans log lines for error patterns, then categorises each error into one of:
    database, network, timeout, auth, oom, disk, rate_limit, configuration,
    application, or unknown.  Returns per-container breakdowns with counts,
    percentage, timestamps, sample lines, and a minute-level category timeline.

    Parameters
    ----------
    container_names : Containers to scan. Omit for all running containers.
    tail            : Log lines to fetch per container (default 1000).
    categories      : Optional filter — only include these categories in results.
                      Example: ["database", "timeout"].
    use_cache       : Use cached logs when available (default True).

    Returns
    -------
    JSON with:
        status              – 'success' or 'error'
        containers          – dict of container → classification breakdown
        summary             – aggregate stats across all containers
        containers_scanned  – list of container names scanned
        cache_hits          – dict of container → bool
        parameters          – effective parameter values
    """
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    # Validate category filter.
    if categories:
        invalid = set(categories) - VALID_CATEGORIES
        if invalid:
            return {
                "status": "error",
                "error": f"Invalid categories: {sorted(invalid)}. Valid: {sorted(VALID_CATEGORIES)}",
            }

    if container_names:
        targets = []
        for name in container_names:
            try:
                targets.append(client.container.inspect(name))
            except NoSuchContainer:
                return {"status": "error", "error": f"Container '{name}' not found."}
    else:
        targets = client.container.list()

    params = {
        "tail": tail,
        "categories": categories,
    }

    if not targets:
        return {
            "status": "success",
            "containers": {},
            "summary": {"total_errors": 0, "dominant_category": None},
            "containers_scanned": [],
            "cache_hits": {},
            "parameters": params,
        }

    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=settings.log_lookback_minutes)

    cache_hits: dict[str, bool] = {}
    per_container: dict[str, dict] = {}
    all_classified: list[tuple[str, float | None, str]] = []

    for c in targets:
        cname = _container_name(c)
        lines, was_cached = _fetch_logs_with_cache(c, cname, since, now, use_cache=use_cache)
        cache_hits[cname] = was_cached
        if tail <= 0:
            lines = []
        else:
            lines = lines[-tail:]
        if not lines:
            per_container[cname] = {
                "total_errors": 0,
                "categories": {},
                "dominant_category": None,
                "category_timeline": [],
            }
            continue

        classified = classify_lines(lines)

        # Apply category filter if specified.
        if categories:
            classified = [(cat, ts, line) for cat, ts, line in classified if cat in categories]

        stats = aggregate_stats(classified)
        per_container[cname] = stats
        all_classified.extend(classified)

    # Cross-container summary.
    summary_stats = aggregate_stats(all_classified)

    return {
        "status": "success",
        "containers": per_container,
        "summary": {
            "total_errors": summary_stats["total_errors"],
            "dominant_category": summary_stats["dominant_category"],
            "categories": summary_stats["categories"],
        },
        "containers_scanned": [_container_name(c) for c in targets],
        "cache_hits": cache_hits,
        "parameters": params,
    }


def tool_plan_investigation(
    symptoms: list[str],
    containers: list[str] | None = None,
    focus: str | None = None,
) -> dict:
    """
    Generate a structured, step-by-step investigation plan from observed symptoms.

    Classifies symptoms into signal categories (crash, spike, cascade, security,
    pattern) and maps them to an ordered sequence of MCP tool calls to execute.
    The structured plan (list of steps) is returned in the JSON response AND
    saved as a human-readable Markdown table to .cache/plans/. Open plan_file
    for the formatted version; use the plan list to drive automated execution.

    Parameters
    ----------
    symptoms    : Observed problem descriptions, e.g.
                  ["payment-service returning 500s", "high latency in checkout"].
    containers  : Container names to scope the investigation. Omit for all.
    focus       : One of 'root_cause', 'security', 'performance', 'general'.
                  Defaults to 'general'.

    Returns
    -------
    JSON with:
        status             – 'success' or 'error'
        signals_detected   – inferred signal categories
        focus              – effective focus used
        containers_in_scope
        step_count         – number of planned steps
        plan               – list of {step, action, target, reason, parameters}
        plan_file          – path to the saved Markdown plan
    """
    return generate_plan(symptoms=symptoms, containers=containers, focus=focus)


def tool_analyze_code_context(
    container_name: str,
    tail: int = 200,
    context_lines: int | None = None,
    max_frames: int | None = None,
    repo_path: str | None = None,
    language: str | None = None,
) -> dict:
    """
    Parse stack traces from a container's recent error logs and surface the
    relevant source code context from the linked code repository.

    Workflow:
      1. Fetch the last `tail` log lines from `container_name`
      2. Filter to error/fatal lines
      3. Parse stack frames (Python / Java / Go / Node.js)
      4. Resolve each frame's file path against the configured repo root
      5. Extract `context_lines` of source code around each error line

    Repository resolution order:
      1. `repo_path` parameter (explicit override)
      2. `container_repo_map` in config (e.g. {"api": "/home/user/api"})
      3. First valid path in `repo_paths` config list

    Parameters
    ----------
    container_name : Target container (required).
    tail           : Log lines to scan for stack traces (default 200).
    context_lines  : Source lines before/after the error line
                     (default: config.code_context_lines = 10).
    max_frames     : Max stack frames per error event
                     (default: config.max_stack_frames = 10).
    repo_path      : Explicit path to the repository root. Overrides config.
    language       : Force a language parser: python, java, go, nodejs.
                     Auto-detected from analyze_patterns if omitted.

    Returns
    -------
    JSON with:
        status         – 'success' | 'no_frames' | 'error'
        container      – container name
        language       – detected or forced language
        repo_root      – resolved repo path (null if not configured)
        frames_found   – total stack frames extracted
        frames         – list of {raw_frame, function, file_in_log, line_no,
                         resolved_file, code_context}
        unresolved_files – frame paths that could not be found in the repo
        warnings       – informational messages (e.g. no repo configured)
    """
    try:
        client = _docker_client()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    try:
        c = client.container.inspect(container_name)
    except NoSuchContainer:
        return {"status": "error", "error": f"Container '{container_name}' not found."}

    lines = _fetch_logs(c, tail=tail)

    # Auto-detect language if not forced
    detected_lang = language or "unknown"
    if language is None:
        lang, _conf = PatternDetector.detect_language(lines)
        detected_lang = lang if lang else "unknown"

    # Build repo map — explicit repo_path overrides everything
    container_repo_map: dict[str, str] = dict(settings.container_repo_map)
    repo_paths: list[str] = list(settings.repo_paths)
    if repo_path:
        container_repo_map[container_name] = repo_path

    ctx_lines = context_lines if context_lines is not None else settings.code_context_lines
    max_f = max_frames if max_frames is not None else settings.max_stack_frames

    return analyse_code_context(
        log_lines=lines,
        language=detected_lang,
        container_name=container_name,
        container_repo_map=container_repo_map,
        repo_paths=repo_paths,
        context_lines=ctx_lines,
        max_frames=max_f,
    )


def tool_cache_info(container_name: Optional[str] = None) -> dict:
    """Return a summary of the local log cache (.cache/logs/).

    Reports per-container: number of cached parquet files, dates covered, total log
    lines, disk size, and last sync time. Useful for checking cache staleness before
    running analysis tools.

    Args:
        container_name: Specific container to inspect, or None for all containers.
    """
    try:
        info = get_cache_info(container_name=container_name)
        return {"status": "success", **info}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def tool_clear_cache(container_name: Optional[str] = None) -> dict:
    """Clear the local log cache (.cache/logs/).

    Removes cached Parquet files and updates metadata. After clearing, the next
    analysis tool call will fetch fresh logs from the Docker API.

    Args:
        container_name: Specific container to clear, or None to clear all containers.
    """
    try:
        result = clear_cache(container_name=container_name)
        return {"status": "success", **result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
