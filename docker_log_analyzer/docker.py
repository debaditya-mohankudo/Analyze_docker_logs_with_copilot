"""docker.py – Docker client and log-fetching helpers.

Low-level wrappers around python-on-whales: client construction, log fetching,
container name normalisation, time argument parsing, and cache-first log retrieval.
"""

from datetime import datetime, timezone
from pathlib import Path

from python_on_whales import DockerClient
from python_on_whales.exceptions import DockerException, NoSuchContainer

from .cache_manager import read_cached_logs_for_window
from .logger import logger

# ── Module-level paths ──────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = _REPO_ROOT / "docker-compose.test.yml"


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


def _fetch_logs_with_cache(
    container,
    container_name: str,
    since: datetime,
    until: datetime,
    use_cache: bool = True,
) -> tuple[list[str], bool]:
    """
    Cache-first log fetching.

    1. Check .cache/logs/<container>/<YYYY-MM-DD>.parquet
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
