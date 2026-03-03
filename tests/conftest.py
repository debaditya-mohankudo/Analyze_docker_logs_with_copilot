"""
Shared pytest fixtures and markers.

Markers:
  unit        – fast, no external dependencies (no Docker required)
  integration – requires Docker daemon running with test containers

Run only unit tests:
  uv run pytest tests/ -m unit

Run all (unit + integration, Docker must be running):
  uv run pytest tests/
"""

import pytest

from python_on_whales import DockerClient
from python_on_whales.exceptions import DockerException


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: fast tests, no Docker required")
    config.addinivalue_line("markers", "integration: requires Docker daemon and running test containers")
    config.addinivalue_line("markers", "serial: tests that must run sequentially")


def pytest_collection_modifyitems(config, items):
    """Use xdist_group to prevent parallel execution of serial-marked tests."""
    for item in items:
        if item.get_closest_marker("serial"):
            item.add_marker(pytest.mark.xdist_group(name="serial"))


# ── Docker availability ───────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def docker_client():
    """Return a DockerClient, or skip the test if Docker is unavailable."""
    try:
        client = DockerClient()
        client.system.info()
        return client
    except DockerException as exc:
        pytest.skip(f"Docker unavailable: {exc}")


@pytest.fixture(scope="session", autouse=True)
def setup_test_containers(request):
    """Start test containers before integration tests, stop after they complete."""
    # Check if integration tests are in the collected items
    has_integration = any(
        item.get_closest_marker("integration") 
        for item in request.session.items
    )
    
    if not has_integration:
        yield
        return
    
    from docker_log_analyzer.mcp_server import tool_start_test_containers, tool_stop_test_containers
    
    # Start containers before any tests
    result = tool_start_test_containers(rebuild=False)
    if result["status"] == "error":
        pytest.skip(f"Failed to start test containers: {result.get('error')}")
    
    yield  # Run all tests
    
    # Stop containers after all tests
    tool_stop_test_containers()


# ── Synthetic log helpers ─────────────────────────────────────────────────────

def _iso(minute: str, level: str, msg: str) -> str:
    """Build a Docker-prepended ISO-8601 log line."""
    return f"{minute}:00.000Z {level} {msg}"


# ── Spike-detector fixtures ───────────────────────────────────────────────────

@pytest.fixture
def spike_logs_single():
    """
    3 baseline buckets (2 errors each) → 1 spike bucket (8 errors).
    Expected spike at 2024-03-02T21:13, ratio = 4.0.
    """
    base = "2024-03-02T21:"
    return (
        [_iso(f"{base}10", "ERROR", "db timeout")] * 2 +
        [_iso(f"{base}11", "ERROR", "timeout")] * 2 +
        [_iso(f"{base}12", "ERROR", "timeout")] * 2 +
        [_iso(f"{base}13", "ERROR", "cascade failure")] * 8
    )


@pytest.fixture
def spike_logs_uniform():
    """2 errors per minute, no spike expected."""
    base = "2024-03-02T21:"
    lines = []
    for minute in range(10, 16):
        lines += [_iso(f"{base}{minute}", "ERROR", "steady error")] * 2
    return lines


@pytest.fixture
def spike_logs_no_timestamps():
    """Lines without any RFC3339 timestamp prefix."""
    return [
        "ERROR database connection failed",
        "CRITICAL service down",
        "ERROR timeout after 30s",
    ]


# ── Correlator fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def corr_aligned_logs():
    """
    web errors at T=00 and T=10; db errors at T=05 and T=12.
    All within 30s of each other → high correlation.
    """
    return {
        "web": [
            "2024-03-02T21:10:00.000Z ERROR connection refused",
            "2024-03-02T21:10:10.000Z ERROR timeout",
            "2024-03-02T21:10:20.000Z INFO request ok",
        ],
        "db": [
            "2024-03-02T21:10:05.000Z ERROR database down",
            "2024-03-02T21:10:12.000Z ERROR query failed",
            "2024-03-02T21:10:30.000Z INFO health ok",
        ],
    }


@pytest.fixture
def corr_distant_logs():
    """
    web errors at T=00; db errors 2 minutes later.
    No overlap within 30s → correlation_score = 0.
    """
    return {
        "web": ["2024-03-02T21:10:00.000Z ERROR connection refused"],
        "db":  ["2024-03-02T21:12:05.000Z ERROR database down"],
    }


@pytest.fixture
def corr_single_container():
    """Only one container — cannot compute pairwise correlation."""
    return {"web": ["2024-03-02T21:10:00.000Z ERROR connection refused"]}


# ── PatternDetector fixtures ──────────────────────────────────────────────────

PYTHON_LOGS = [
    "2024-03-02T21:10:00Z INFO Starting application server on port 8080",
    "2024-03-02T21:10:01Z INFO Request GET /api/users 200 12ms",
    "2024-03-02T21:10:02Z ERROR Traceback (most recent call last):",
    '2024-03-02T21:10:02Z ERROR   File "app.py", line 42, in handle',
    "2024-03-02T21:10:02Z ERROR ConnectionRefusedError: [Errno 111] Connection refused",
    "2024-03-02T21:10:03Z DEBUG Cache hit for key=user:99",
    "2024-03-02T21:10:04Z INFO /health 200 OK 1ms",
    "2024-03-02T21:10:05Z INFO Health check passed",
]

JAVA_LOGS = [
    "2024-03-02T21:10:00Z INFO  [main] o.s.boot.SpringApplication - Started in 3.4s",
    "2024-03-02T21:10:01Z INFO  [http-nio] c.example.Controller - GET /health 200",
    "2024-03-02T21:10:02Z ERROR [main] c.example.App - Exception in thread main java.lang.NullPointerException",
    "2024-03-02T21:10:02Z ERROR   at com.example.Service.process(Service.java:42)",
    "2024-03-02T21:10:02Z ERROR Caused by: java.net.ConnectException: Connection refused",
    "2024-03-02T21:10:03Z DEBUG [pool-1] c.example.Worker - Worker idle",
]

GO_LOGS = [
    '2024-03-02T21:10:00Z time=2024-03-02T21:10:00Z level=info msg="Server started" addr=:8080',
    '2024-03-02T21:10:01Z time=2024-03-02T21:10:01Z level=info msg="Request handled" status=200',
    '2024-03-02T21:10:02Z panic: runtime error: index out of range [5] with length 3',
    '2024-03-02T21:10:02Z goroutine 1 [running]:',
    '2024-03-02T21:10:03Z time=2024-03-02T21:10:03Z level=error msg="Cache write failed"',
]

NODEJS_LOGS = [
    "2024-03-02T21:10:00Z info: Server listening on port 3000",
    "2024-03-02T21:10:01Z info: GET /api/status 200 5ms",
    "2024-03-02T21:10:02Z error: TypeError: Cannot read property 'id' of undefined",
    "2024-03-02T21:10:02Z error:     at processRequest (/app/src/handler.js:42:15)",
    "2024-03-02T21:10:03Z debug: Cache hit for session abc123",
]

SYSLOG_LINES = [
    "Mar  2 21:10:00 myhost app[1234]: INFO service started",
    "Mar  2 21:10:01 myhost app[1234]: ERROR connection failed",
]

EPOCH_LINES = [
    "1709432381 INFO service started",
    "1709432382 ERROR connection failed",
]

APACHE_LINES = [
    '02/Mar/2024:21:10:00 +0000 "GET /index.html HTTP/1.1" 200 1234',
    '02/Mar/2024:21:10:01 +0000 "POST /api/data HTTP/1.1" 500 89',
]


@pytest.fixture
def python_logs():
    return PYTHON_LOGS


@pytest.fixture
def java_logs():
    return JAVA_LOGS


@pytest.fixture
def go_logs():
    return GO_LOGS


@pytest.fixture
def nodejs_logs():
    return NODEJS_LOGS
