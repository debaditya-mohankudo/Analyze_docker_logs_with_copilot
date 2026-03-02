# Log Pattern Analysis for Container Optimization

## Overview

The **Log Pattern Analyzer** intelligently discovers characteristics of logs from each container before running the full Docker log analyzer. This allows the system to optimize buffer management, error detection, and LLM analysis.

## Workflow

### Step 1: Pattern Discovery (30-60 seconds)

```bash
# Collect logs and analyze patterns
uv run python src/main.py --analyze --collection-time 60

# Output: container_patterns.json
```

During this phase:
1. **Log Producer** streams logs from all containers to Kafka
2. **Error Consumer** collects logs into buffers
3. **Pattern Analyzer** examines accumulated logs and:
   - Detects timestamp formats
   - Identifies programming language
   - Discovers repeating patterns (health checks)
   - Extracts error patterns
   - Calculates log level distributions

### Step 2: Pattern Export

Results saved to `container_patterns.json`:

```json
{
  "container_name": {
    "language": {
      "name": "python",
      "confidence": 0.95
    },
    "timestamp_format": {
      "type": "iso8601",
      "sample": "2024-03-02T21:19:41.123Z",
      "confidence": 0.9
    },
    "log_levels": {
      "INFO": 5000,
      "DEBUG": 1200,
      "ERROR": 45,
      "WARNING": 120
    },
    "health_check": {
      "detected": true,
      "pattern": "GET /health 200",
      "frequency_per_minute": 15.0,
      "example_logs": [...]
    },
    "common_errors": [
      {
        "pattern": "Connection refused",
        "count": 12
      }
    ]
  }
}
```

### Step 3: Buffer Manager Optimization

Using `container_patterns.json`, the buffer manager can:

1. **Pre-compile Error Patterns**: Only look for errors relevant to each container's language
2. **Filter Health Checks**: Ignore repeating health check logs to reduce noise
3. **Size Buffers Appropriately**: Adjust buffer sizes based on observed log volume
4. **Normalize Timestamps**: Parse timestamps correctly for each container
5. **Tune LLM Thresholds**: Adjust error thresholds based on error distribution

## Pattern Detection Details

### Timestamp Format Detection

| Format | Pattern | Example |
|--------|---------|---------|
| ISO-8601 | `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}` | `2024-03-02T21:19:41.123Z` |
| Syslog | `^[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}` | `Mar 2 21:19:41` |
| Epoch | `^\d{10}(\.\d+)?` | `1709432381` |
| Apache | `^\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}` | `02/Mar/2024:21:19:41` |

### Language Identification

Uses heuristic pattern matching:

**Python:**
- `Traceback (most recent call last)`
- `File "...", line \d+`
- `ImportError|ModuleNotFoundError`

**Java:**
- `Exception in thread`
- `at java.`
- `Caused by:`

**PHP:**
- `PHP Fatal|Warning|Notice`
- `On line \d+ in file`
- `.php:`

**Go:**
- `panic:`
- `goroutine \d+ \[`

**Node.js:**
- `at .*\.js:\d+:\d+`
- `Error: `
- `npm ERR!`

### Health Check Patterns

Automatically detects repeating patterns:
- `health check (passed|ok|successful)`
- `GET /health 200`
- `ping pong`, `heartbeat`
- `liveness probe`, `readiness probe`
- `/status`, `/ping` endpoints

## Integration with LLM Analysis

Once patterns are discovered, the system can:

1. **Skip Obvious Non-Errors**: Filter out INFO/DEBUG logs known to be safe
2. **Context-Aware Aggregation**: Group related errors by language/container type
3. **Smarter LLM Prompting**: Include language-specific error context
4. **Cost Optimization**: Skip LLM analysis for known health check patterns

## Usage Example

```bash
# 1. Analyze patterns (no LLM analysis yet)
docker-compose up -d
uv run python src/main.py --analyze --collection-time 60

# Review container_patterns.json
cat container_patterns.json | jq '.'

# 2. Now run full analyzer with pattern awareness
uv run python src/main.py
```

## Pattern Analysis Results

The test suite demonstrates detection across multiple languages:

```
📦 Python Flask
   Language: python (95% confidence)
   Timestamp: iso8601
   Health Check: YES (15.0/minute)
   Log Levels: INFO, DEBUG, ERROR, WARNING
   Common Errors: Connection refused (12x)

📦 Java Spring Boot
   Language: java (87% confidence)
   Timestamp: custom (space-separated)
   Health Check: NO
   Log Levels: INFO, DEBUG, ERROR, WARN
   Common Errors: SQLException (8x)

📦 Node.js Express
   Language: nodejs (82% confidence)
   Timestamp: iso8601
   Health Check: YES (/health endpoint)
   Log Levels: INFO, DEBUG, ERROR
   Common Errors: ENOENT (5x)
```

## Files

- **src/log_pattern_analyzer.py** - Main analyzer module (530 lines)
  - `PatternDetector` - Pattern detection algorithms
  - `LogPatternAnalyzer` - Orchestration and export
  - `TimestampPattern`, `HealthCheckPattern`, `ContainerPattern` - Data models

- **test_pattern_analyzer.py** - Comprehensive test suite
  - Tests detection with 4 language types
  - Validates JSON export format
  - Demonstrates real-world log samples

## Next Steps

After pattern analysis, the buffer manager can:
1. Load `container_patterns.json` on startup
2. Pre-configure error filters per container
3. Adjust buffer sizes based on observed volumes
4. Skip known health check logs in error detection
5. Provide language-specific context to LLM analyzer

This creates a fully optimized, context-aware log correlation system!
