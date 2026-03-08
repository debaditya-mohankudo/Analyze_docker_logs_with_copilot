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


_containers_started = False


@pytest.fixture(scope="session", autouse=True)
def setup_integration_containers(request):
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
    
    from docker_log_analyzer.tools import tool_start_test_containers, tool_stop_test_containers
    
    # Start containers
    result = tool_start_test_containers(rebuild=False)
    if result["status"] == "error":
        pytest.skip(f"Failed to start test containers: {result.get('error')}")
    
    _containers_started = True
    yield
    
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
