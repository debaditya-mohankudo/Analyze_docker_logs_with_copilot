#!/usr/bin/env python3
"""
Test log generator for Docker Log Analyzer.

Emits continuous random logs in configurable formats with periodic error spikes.
Designed to exercise all 4 MCP tools: list_containers, analyze_patterns,
detect_error_spikes, and correlate_containers.

Environment Variables:
  LOG_FORMAT       iso8601 | syslog | epoch | apache | mixed   (default: mixed)
  LOG_LANGUAGE     python | java | go | nodejs | generic | mixed (default: mixed)
  SERVICE_NAME     Label used in log messages             (default: test-service)
  LOG_INTERVAL     Seconds between normal log lines       (default: 0.3)
  ERROR_RATE       0.0-1.0 base error probability         (default: 0.05)
  SPIKE_INTERVAL   Seconds between error spikes           (default: 60)
  SPIKE_DURATION   Seconds each spike lasts               (default: 8)
  SPIKE_ERROR_RATE 0.0-1.0 error probability during spike (default: 0.9)
"""

import os
import random
import sys
import time
from datetime import datetime, timezone

# ── Configuration ────────────────────────────────────────────────────────────

LOG_FORMAT       = os.getenv("LOG_FORMAT", "mixed")
LOG_LANGUAGE     = os.getenv("LOG_LANGUAGE", "mixed")
SERVICE_NAME     = os.getenv("SERVICE_NAME", "test-service")
LOG_INTERVAL     = float(os.getenv("LOG_INTERVAL", "0.3"))
ERROR_RATE       = float(os.getenv("ERROR_RATE", "0.05"))
SPIKE_INTERVAL   = float(os.getenv("SPIKE_INTERVAL", "60"))
SPIKE_DURATION   = float(os.getenv("SPIKE_DURATION", "8"))
SPIKE_ERROR_RATE = float(os.getenv("SPIKE_ERROR_RATE", "0.9"))

# ── Timestamp formatters ─────────────────────────────────────────────────────

def fmt_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def fmt_syslog() -> str:
    return datetime.now().strftime("%b %d %H:%M:%S") + f" {SERVICE_NAME}[{os.getpid()}]:"

def fmt_epoch() -> str:
    return f"{time.time():.3f}"

def fmt_apache() -> str:
    return datetime.now().strftime("%d/%b/%Y:%H:%M:%S +0000")

FORMATTERS = {
    "iso8601": fmt_iso8601,
    "syslog":  fmt_syslog,
    "epoch":   fmt_epoch,
    "apache":  fmt_apache,
}

# ── Normal log messages by language ─────────────────────────────────────────

NORMAL_MESSAGES = {
    "python": [
        'INFO Starting application server on port 8080',
        'INFO Request GET /api/v1/users 200 12ms',
        'INFO Request POST /api/v1/orders 201 45ms',
        'DEBUG Cache hit for key=user:1234',
        'INFO Health check passed',
        'DEBUG Processing batch job batch_id=7892',
        'INFO User authenticated user_id=42',
        'DEBUG Database query executed in 3ms rows=15',
        'INFO Scheduled task completed task=cleanup',
        'INFO Listening on 0.0.0.0:8080',
    ],
    "java": [
        'INFO  [main] o.s.boot.SpringApplication - Started in 3.421 seconds',
        'INFO  [http-nio-8080] c.example.Controller - GET /health 200',
        'DEBUG [pool-1-thread-1] c.example.Service - Processing request id=8821',
        'INFO  [scheduler-1] c.example.Job - Scheduled job completed successfully',
        'DEBUG [main] o.h.SQL - select user0_.id from users user0_',
        'INFO  [http-nio-8080] c.example.Filter - Request completed in 24ms',
        'INFO  [main] c.example.CacheManager - Loaded 1024 entries from cache',
        'DEBUG [pool-2-thread-4] c.example.Worker - Worker thread idle',
    ],
    "go": [
        'time=2024-03-02T21:19:41Z level=info msg="Server started" addr=:8080',
        'time=2024-03-02T21:19:41Z level=debug msg="Cache lookup" key=user:99 hit=true',
        'time=2024-03-02T21:19:41Z level=info msg="Request handled" method=GET path=/health status=200 latency=2ms',
        'time=2024-03-02T21:19:41Z level=debug msg="Worker spawned" goroutines=12',
        'time=2024-03-02T21:19:41Z level=info msg="Config loaded" file=config.yaml',
        'time=2024-03-02T21:19:41Z level=info msg="Health probe OK" uptime=3600s',
    ],
    "nodejs": [
        'info: Server listening on port 3000',
        'debug: Cache hit for session abc123',
        'info: GET /api/status 200 5ms',
        'debug: Database pool size: 10/20',
        'info: Worker process spawned pid=1234',
        'info: Scheduled cleanup completed removed=42',
        'debug: WebSocket connection established client=192.168.1.10',
    ],
    "generic": [
        'INFO  Service healthy',
        'INFO  Request processed successfully',
        'DEBUG Cache refreshed',
        'INFO  Connection pool: 5/10 active',
        'INFO  Background worker running',
        'INFO  Metrics exported',
        'DEBUG Heartbeat sent',
        'INFO  /health 200 OK 1ms',
        'INFO  Configuration reloaded',
    ],
}

# ── Error messages by language ───────────────────────────────────────────────

ERROR_MESSAGES = {
    "python": [
        'ERROR Traceback (most recent call last):\n  File "app.py", line 42, in handle_request\n    result = db.query(sql)\nException: Connection refused',
        'ERROR Failed to connect to database: ConnectionRefusedError: [Errno 111] Connection refused',
        'CRITICAL Unhandled exception in worker thread\nTraceback (most recent call last):\n  File "worker.py", line 78\nAttributeError: NoneType object has no attribute "id"',
        'ERROR ImportError: cannot import name "config" from "settings"',
        'ERROR HTTP 503 Service Unavailable: upstream connect error',
        'ERROR Request timeout after 30000ms GET /api/data',
        'FATAL Database pool exhausted max_connections=20',
    ],
    "java": [
        'ERROR [http-nio-8080] c.example.Handler - Exception in thread "main" java.lang.NullPointerException\n\tat com.example.Service.process(Service.java:42)\n\tat com.example.Controller.handle(Controller.java:18)',
        'ERROR [main] o.s.boot.SpringApplication - Application run failed\nCaused by: java.net.ConnectException: Connection refused',
        'FATAL [main] c.example.DataSource - Cannot acquire JDBC Connection\njava.sql.SQLTransientConnectionException: Unable to acquire connection',
        'ERROR [pool-3-thread-1] c.example.Worker - java.lang.OutOfMemoryError: Java heap space',
        'ERROR [http-nio-8080] c.example.Filter - HTTP 500 Internal Server Error',
        'SEVERE [scheduler-1] c.example.Job - Scheduled job failed after 3 retries',
    ],
    "go": [
        'time=2024-03-02T21:19:41Z level=error msg="Database connection failed" err="dial tcp: connection refused"',
        'time=2024-03-02T21:19:41Z level=fatal msg="panic: runtime error: index out of range [5] with length 3"\ngoroutine 1 [running]:\nmain.processData(0xc0000b4000, 0x5, 0x5)',
        'time=2024-03-02T21:19:41Z level=error msg="HTTP 503 upstream unavailable" service=database',
        'time=2024-03-02T21:19:41Z level=error msg="Cache write failed" err="connection reset by peer"',
        'time=2024-03-02T21:19:41Z level=error msg="Request timeout" path=/api/data timeout=30s',
        'panic: interface conversion: interface {} is nil, not string\n\ngoroutine 17 [running]:\nmain.handler(0xc0001a4000)',
    ],
    "nodejs": [
        'error: UnhandledPromiseRejectionWarning: Error: connect ECONNREFUSED 127.0.0.1:5432\n    at TCPConnectWrap.afterConnect [as oncomplete] (net.js:1141:16)',
        'error: TypeError: Cannot read property "id" of undefined\n    at processRequest (/app/src/handler.js:42:15)\n    at Layer.handle [as handle_request] (/app/node_modules/express/lib/router/layer.js:95:5)',
        'error: HTTP 500 Internal Server Error: upstream service unavailable',
        'npm ERR! code ENOENT npm ERR! syscall open',
        'error: Database query timeout after 5000ms',
        'error: Memory usage exceeded threshold: 512MB / 512MB',
    ],
    "generic": [
        'ERROR Connection timeout after 30s',
        'CRITICAL Service unavailable: upstream returned HTTP 503',
        'ERROR Failed to write to disk: no space left on device',
        'FATAL Out of memory',
        'ERROR Database connection pool exhausted',
        'ERROR Retry limit exceeded after 3 attempts',
        'CRITICAL Unexpected shutdown signal received',
        'ERROR HTTP 500 downstream dependency failed',
    ],
}

# ── Health check messages ────────────────────────────────────────────────────

HEALTH_CHECK_MESSAGES = [
    'INFO  /health 200 OK 1ms',
    'DEBUG Health check passed',
    'INFO  Liveness probe OK',
    'DEBUG Ping pong',
    'INFO  Status: healthy uptime=99.9%',
    'INFO  Readiness probe: ready',
]

# ── Log line builders ────────────────────────────────────────────────────────

def pick_format() -> str:
    if LOG_FORMAT == "mixed":
        return random.choice(list(FORMATTERS.keys()))
    return LOG_FORMAT if LOG_FORMAT in FORMATTERS else "iso8601"

def pick_language() -> str:
    if LOG_LANGUAGE == "mixed":
        return random.choice(list(NORMAL_MESSAGES.keys()))
    return LOG_LANGUAGE if LOG_LANGUAGE in NORMAL_MESSAGES else "generic"

_language = pick_language()   # fixed per container (unless "mixed" requested)

def build_line(is_error: bool) -> str:
    lang = pick_language() if LOG_LANGUAGE == "mixed" else _language
    fmt  = pick_format()
    ts   = FORMATTERS[fmt]()

    if is_error:
        msg = random.choice(ERROR_MESSAGES.get(lang, ERROR_MESSAGES["generic"]))
    elif random.random() < 0.08:
        msg = random.choice(HEALTH_CHECK_MESSAGES)
    else:
        msg = random.choice(NORMAL_MESSAGES.get(lang, NORMAL_MESSAGES["generic"]))

    # Apache format wraps the message differently
    if fmt == "apache":
        return f'[{ts}] [{SERVICE_NAME}] {msg}'
    return f'{ts} {msg}'

# ── Main loop ────────────────────────────────────────────────────────────────

def main() -> None:
    print(
        f'{fmt_iso8601()} INFO Log generator started '
        f'service={SERVICE_NAME} format={LOG_FORMAT} language={LOG_LANGUAGE} '
        f'error_rate={ERROR_RATE} spike_interval={SPIKE_INTERVAL}s',
        flush=True,
    )

    spike_start = time.time() + SPIKE_INTERVAL   # first spike after SPIKE_INTERVAL seconds
    in_spike = False
    spike_end = 0.0

    while True:
        now = time.time()

        # ── Spike state machine ──────────────────────────────────────────────
        if not in_spike and now >= spike_start:
            in_spike = True
            spike_end = now + SPIKE_DURATION
            spike_start = spike_end + SPIKE_INTERVAL
            print(
                f'{fmt_iso8601()} WARNING Error spike started for {SPIKE_DURATION}s '
                f'service={SERVICE_NAME}',
                flush=True,
            )

        if in_spike and now >= spike_end:
            in_spike = False
            print(
                f'{fmt_iso8601()} INFO Error spike ended service={SERVICE_NAME}',
                flush=True,
            )

        # ── Emit a log line ──────────────────────────────────────────────────
        rate = SPIKE_ERROR_RATE if in_spike else ERROR_RATE
        is_error = random.random() < rate
        line = build_line(is_error)
        print(line, flush=True)

        time.sleep(LOG_INTERVAL + random.uniform(-LOG_INTERVAL * 0.2, LOG_INTERVAL * 0.2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
