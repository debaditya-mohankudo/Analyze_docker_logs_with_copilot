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

import subprocess
import time
from pathlib import Path

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


_containers_started = False

# ── ssh-target keypair (used by test_remote_docker_integration.py) ──────────
# Ephemeral, gitignored under .cache/ — never baked into the repo. Generated
# fresh per session if missing, so `ssh://root@localhost:2222` (the DinD
# target defined in docker-compose.test.yml) is reachable non-interactively
# without touching the developer's real ~/.ssh/config.
_SSH_TEST_DIR = Path(__file__).parent.parent / ".cache" / "ssh_test"
_SSH_TEST_PORT = 2222
_SSH_TEST_HOST_ALIAS = f"[localhost]:{_SSH_TEST_PORT}"


@pytest.fixture(scope="session")
def ssh_test_keypair(request):
    """Generate (or reuse) an ed25519 keypair for the ssh-target container and
    register it with the running ssh-agent so key-based auth succeeds without
    prompting. docker's ssh:// transport does not itself pass -o BatchMode, so
    agent + known_hosts must already be satisfied before any connection is
    attempted (see caller in setup_integration_containers for the known_hosts
    half of this).

    No-ops when no integration tests are selected — pytest resolves fixture
    dependencies eagerly, so without this check ssh-keygen would still run
    (and race under parallel xdist workers) on every unit-only run, even
    though setup_integration_containers itself skips its own body.
    """
    has_integration = any(
        item.get_closest_marker("integration")
        for item in request.session.items
    )
    if not has_integration:
        yield None
        return

    _SSH_TEST_DIR.mkdir(parents=True, exist_ok=True)
    key_path = _SSH_TEST_DIR / "id_ed25519"
    pub_path = _SSH_TEST_DIR / "id_ed25519.pub"
    auth_keys_path = _SSH_TEST_DIR / "authorized_keys"

    if not key_path.exists():
        subprocess.run(
            [
                "ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path),
                "-C", "docker-log-analyzer-test",
            ],
            check=True, capture_output=True,
        )
    key_path.chmod(0o600)
    auth_keys_path.write_text(pub_path.read_text())

    add_result = subprocess.run(["ssh-add", str(key_path)], capture_output=True)

    yield {"key_path": key_path, "port": _SSH_TEST_PORT}

    if add_result.returncode == 0:
        subprocess.run(["ssh-add", "-d", str(key_path)], capture_output=True)


def _wait_for_ssh_target_and_trust_host_key(port: int, timeout: int = 30) -> None:
    """Poll the ssh-target container's SSH port and seed known_hosts once it's
    up. The container's host key is baked in at image build time (Dockerfile
    runs ssh-keygen -A), so it's stable across up/down cycles but changes on
    rebuild — stale known_hosts entries are dropped first to avoid a
    'REMOTE HOST IDENTIFICATION HAS CHANGED' failure blocking the whole suite.
    """
    subprocess.run(
        ["ssh-keygen", "-R", _SSH_TEST_HOST_ALIAS],
        capture_output=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scan = subprocess.run(
            ["ssh-keyscan", "-p", str(port), "-T", "2", "localhost"],
            capture_output=True, text=True,
        )
        if scan.returncode == 0 and scan.stdout.strip():
            known_hosts = Path.home() / ".ssh" / "known_hosts"
            known_hosts.parent.mkdir(parents=True, exist_ok=True)
            with open(known_hosts, "a") as f:
                f.write(scan.stdout)
            return
        time.sleep(1)


# Services started *inside* the ssh-target's inner DinD daemon (over SSH),
# so `docker -H ssh://root@localhost:2222 ps` shows real, independent
# containers with the same names the analyzer tools look for — not the
# host's own copies. ssh-target itself is deliberately excluded to avoid
# recursively composing itself inside its own inner daemon.
_LOG_GENERATOR_SERVICES = ["web-app", "database", "cache", "gateway"]


def _start_log_generators_over_ssh(compose_file: Path, port: int, timeout: int = 90) -> bool:
    """Bring up the log-generator services inside ssh-target's inner Docker
    daemon, reachable only via SSH. Returns True on success, False if the
    inner daemon isn't ready yet or the build/up fails — callers should treat
    False as "SSH integration tests will skip" rather than a hard failure,
    since ssh-target is not required for the rest of the integration suite.
    """
    deadline = time.monotonic() + timeout
    last_exc = None
    while time.monotonic() < deadline:
        try:
            inner = DockerClient(
                host=f"ssh://root@localhost:{port}",
                compose_files=[str(compose_file)],
            )
            inner.compose.up(services=_LOG_GENERATOR_SERVICES, detach=True, build=True)
            return True
        except DockerException as exc:
            last_exc = exc
            time.sleep(2)
    if last_exc is not None:
        print(f"ssh-target inner daemon not ready, SSH integration tests will skip: {last_exc}")
    return False


def _stop_log_generators_over_ssh(compose_file: Path, port: int) -> None:
    try:
        inner = DockerClient(
            host=f"ssh://root@localhost:{port}",
            compose_files=[str(compose_file)],
        )
        inner.compose.down()
    except DockerException:
        pass


@pytest.fixture(scope="session", autouse=True)
def setup_integration_containers(request, ssh_test_keypair):
    """Start test containers at session start for integration tests, stop at end.

    Integration tests are run locally before commits. This fixture is for local development.
    """
    global _containers_started

    # Check if integration tests are being run
    has_integration = any(
        item.get_closest_marker("integration")
        for item in request.session.items
    )

    if not has_integration:
        yield
        return

    from docker_log_analyzer.docker import COMPOSE_FILE
    from docker_log_analyzer.tools import tool_start_test_containers, tool_stop_test_containers

    # Start containers (ssh_test_keypair fixture has already written
    # .cache/ssh_test/authorized_keys, which ssh-target bind-mounts)
    result = tool_start_test_containers(rebuild=False)
    if result["status"] == "error":
        pytest.skip(f"Failed to start test containers: {result.get('error')}")

    port = ssh_test_keypair["port"]
    _wait_for_ssh_target_and_trust_host_key(port)

    # Best-effort: populate the ssh-target's own inner daemon with the same
    # log-generator containers, so SSH-based tests see real, independently
    # reachable "test-web-app" etc. rather than the host's copies. Failure
    # here only disables SSH-specific tests (they skip individually); it
    # must not block the rest of the integration suite.
    global _ssh_target_ready
    _ssh_target_ready = _start_log_generators_over_ssh(COMPOSE_FILE, port)

    _containers_started = True
    yield

    if _ssh_target_ready:
        _stop_log_generators_over_ssh(COMPOSE_FILE, port)

    # Stop containers after all tests
    tool_stop_test_containers()


_ssh_target_ready = False


@pytest.fixture(scope="session")
def ssh_target_ready():
    """True once the ssh-target inner daemon has the log-generator containers
    running and reachable over SSH. SSH-dependent tests should skip (not
    fail) when this is False — e.g. privileged containers unavailable."""
    return _ssh_target_ready


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
    # Spring-specific errors
    "2024-03-02T21:10:04Z ERROR [main] o.s.boot.SpringApplication - APPLICATION FAILED TO START",
    "2024-03-02T21:10:04Z ERROR [main] o.s.context.AnnotationConfigApplicationContext - Error starting ApplicationContext",
    "2024-03-02T21:10:05Z ERROR [main] o.s.b.SpringApplication - org.springframework.beans.factory.BeanCreationException: Error creating bean 'dataSource'",
    "2024-03-02T21:10:05Z ERROR [main] o.s.b.SpringApplication - Caused by: org.springframework.beans.factory.NoSuchBeanDefinitionException: No bean named 'userRepo'",
    "2024-03-02T21:10:06Z ERROR [main] o.s.b.SpringApplication - Caused by: org.springframework.beans.factory.UnsatisfiedDependencyException",
    "2024-03-02T21:10:07Z ERROR [http-nio] o.s.web.servlet.DispatcherServlet - Failed to complete request",
    "2024-03-02T21:10:08Z ERROR [http-nio] o.s.w.s.m.HttpMessageNotReadableException: Required request body is missing",
    "2024-03-02T21:10:09Z ERROR [http-nio] o.s.v.m.MethodArgumentNotValidException: Validation failed for argument",
    "2024-03-02T21:10:10Z ERROR [http-nio] o.s.d.DataAccessException: could not execute statement",
    "2024-03-02T21:10:11Z ERROR [http-nio] org.hibernate.HibernateException: identifier of an instance was altered",
    "2024-03-02T21:10:12Z ERROR [http-nio] org.springframework.security.AuthenticationException: Bad credentials",
    # Cassandra / DataStax driver errors
    "2024-03-02T21:10:13Z ERROR [main] c.example.App - com.datastax.oss.driver.api.core.NoHostAvailableException: No node was available",
    "2024-03-02T21:10:14Z ERROR [main] c.example.App - com.datastax.oss.driver.api.core.AllNodesFailedException: Could not reach any contact point",
    "2024-03-02T21:10:15Z ERROR [main] c.example.App - com.datastax.driver.core.exceptions.ReadTimeoutException: Cassandra timeout during read query",
    "2024-03-02T21:10:16Z ERROR [main] c.example.App - com.datastax.driver.core.exceptions.WriteTimeoutException: Cassandra timeout during write query",
    "2024-03-02T21:10:17Z ERROR [main] c.example.App - com.datastax.driver.core.exceptions.UnavailableException: Not enough replicas available",
    "2024-03-02T21:10:18Z ERROR [main] c.example.App - com.datastax.driver.core.exceptions.InvalidQueryException: unconfigured table users",
    # Kafka errors
    "2024-03-02T21:10:19Z ERROR [main] o.a.k.c.p.KafkaProducer - org.apache.kafka.common.errors.TimeoutException: Topic orders not present in metadata",
    "2024-03-02T21:10:20Z ERROR [main] c.example.App - org.apache.kafka.common.errors.SerializationException: Error deserializing key/value for partition orders-0",
    "2024-03-02T21:10:21Z ERROR [main] c.example.App - org.apache.kafka.clients.consumer.CommitFailedException: Commit cannot be completed due to group rebalance",
    "2024-03-02T21:10:22Z WARN  [main] o.a.k.c.NetworkClient - Broker: Leader not available for partition orders-2",
    "2024-03-02T21:10:23Z ERROR [main] c.example.App - org.apache.kafka.common.errors.RecordTooLargeException: Message exceeds max message size",
]

PHP_LOGS = [
    "[02-Mar-2024 21:10:00 UTC] PHP Fatal error: Uncaught PDOException: SQLSTATE[HY000] [2002] Can't connect to MySQL server on 'db:3306' in /var/www/html/db.php:15",
    "[02-Mar-2024 21:10:01 UTC] PHP Warning: mysqli_connect_error(): Access denied for user 'app'@'%' (using password: YES) MySQL",
    "[02-Mar-2024 21:10:02 UTC] PHP Fatal error: SQLSTATE[42S02]: Table 'app.users' doesn't exist in /var/www/html/model.php:38",
    "[02-Mar-2024 21:10:03 UTC] PHP Warning: MySQL server has gone away in /var/www/html/db.php:82",
    "[02-Mar-2024 21:10:04 UTC] PHP Warning: Lost connection to MySQL server during query in /var/www/html/db.php:99",
    "[02-Mar-2024 21:10:05 UTC] PHP Fatal error: SQLSTATE[23000]: Duplicate entry '42' for key 'PRIMARY' in /var/www/html/repo.php:55",
    "[02-Mar-2024 21:10:06 UTC] PHP Warning: Deadlock found when trying to get lock; try restarting transaction in /var/www/html/repo.php:71",
    "[02-Mar-2024 21:10:07 UTC] PHP Fatal error: Too many connections in /var/www/html/db.php:12",
    "[02-Mar-2024 21:10:08 UTC] PHP Notice: On line 42 in file /var/www/html/index.php",
    # Slim Framework errors
    "[02-Mar-2024 21:10:09 UTC] PHP Fatal error: Uncaught Slim\\Exception\\HttpNotFoundException: Not found in /var/www/html/vendor/slim/slim/Slim/Routing/RouteRunner.php:62",
    "[02-Mar-2024 21:10:10 UTC] PHP Fatal error: Uncaught Slim\\Exception\\HttpMethodNotAllowedException: Method not allowed in /var/www/html/app/routes.php:28",
    "[02-Mar-2024 21:10:11 UTC] PHP Fatal error: Uncaught Slim\\Exception\\HttpUnauthorizedException: Unauthorized in /var/www/html/app/middleware/AuthMiddleware.php:45",
    "[02-Mar-2024 21:10:12 UTC] PHP Fatal error: Uncaught Slim\\Exception\\HttpInternalServerErrorException in /var/www/html/app/handlers.php:33",
    "[02-Mar-2024 21:10:13 UTC] PHP Fatal error: Uncaught Psr\\Container\\ContainerExceptionInterface: Service not found in /var/www/html/app/dependencies.php:19",
    "[02-Mar-2024 21:10:14 UTC] PHP Fatal error: FastRoute\\BadRouteException: Cannot register two routes matching '/users/{id}' in /var/www/html/vendor/nikic/fast-route/src/RouteCollector.php:88",
    "[02-Mar-2024 21:10:15 UTC] PHP Warning: Slim\\Middleware\\BodyParsingMiddleware: Unable to parse JSON body in /var/www/html/vendor/slim/slim/Slim/Middleware/BodyParsingMiddleware.php:104",
    "[02-Mar-2024 21:10:16 UTC] PHP Fatal error: Slim\\Factory\\AppFactory::create() called before container was set in /var/www/html/app/bootstrap.php:12",
    # Kafka (php-rdkafka) errors
    "[02-Mar-2024 21:10:17 UTC] PHP Fatal error: Uncaught RdKafka\\Exception: Local: Broker transport failure in /var/www/html/kafka/producer.php:34",
    "[02-Mar-2024 21:10:18 UTC] PHP Warning: rdkafka: Local: Queue full — producer queue is full, message dropped in /var/www/html/kafka/producer.php:58",
    "[02-Mar-2024 21:10:19 UTC] PHP Fatal error: Uncaught RdKafka\\KafkaErrorException: Broker: Unknown topic or partition in /var/www/html/kafka/consumer.php:72",
    "[02-Mar-2024 21:10:20 UTC] PHP Warning: rdkafka: Local: Message timed out — delivery timeout exceeded in /var/www/html/kafka/producer.php:91",
    "[02-Mar-2024 21:10:21 UTC] PHP Fatal error: rdkafka consumer error UNKNOWN_TOPIC_OR_PART on topic orders in /var/www/html/kafka/consumer.php:45",
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

NGINX_LOGS = [
    "2024/03/02 21:10:00 [notice] 1#1: nginx/1.25.3",
    "2024/03/02 21:10:01 [error] 1#1: *42 connect() failed (111: Connection refused) while connecting to upstream, client: 10.0.0.1, upstream: http://app:8080/api",
    "2024/03/02 21:10:02 [error] 1#1: *43 upstream timed out (110: Connection timed out) while reading response header from upstream",
    "2024/03/02 21:10:03 [error] 1#1: *44 no live upstreams while connecting to upstream, client: 10.0.0.2, upstream: app_pool",
    "2024/03/02 21:10:04 [warn]  1#1: *45 upstream prematurely closed connection while reading response header from upstream",
    "2024/03/02 21:10:05 [error] 1#1: *46 upstream sent invalid header while reading response header from upstream",
    "2024/03/02 21:10:06 [error] 1#1: *47 SSL_do_handshake() failed (SSL: error:14094416) while SSL handshaking to upstream",
    "2024/03/02 21:10:07 [warn]  1#1: *48 client intended to send too large body: 15728640 bytes",
    '2024/03/02 21:10:08 [error] 1#1: *49 open() "/var/www/html/missing.html" failed (2: No such file or directory)',
    "2024/03/02 21:10:09 [error] 1#1: *50 FastCGI sent in stderr: PHP Fatal error: Uncaught TypeError in /var/www/html/index.php:12",
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


@pytest.fixture
def php_logs():
    return PHP_LOGS


@pytest.fixture
def nginx_logs():
    return NGINX_LOGS
