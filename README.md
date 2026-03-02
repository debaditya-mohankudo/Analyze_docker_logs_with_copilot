# Docker Log Analyzer with LLM Correlation

A Python-based real-time log monitoring system that streams logs from multiple Docker containers, detects errors, captures contextual information from all containers within a time window, and uses LLM analysis to identify correlated failures and root causes.

## 🎯 Features

- **Real-time Log Streaming**: Monitors multiple Docker containers simultaneously via Docker socket
- **Distributed Error Detection**: Detects ERROR/CRITICAL/FATAL patterns across all containers
- **Time-Window Context**: Captures ±60 seconds of logs from ALL containers when an error occurs
- **LLM-Powered Analysis**: Uses GPT-4o-mini to correlate logs and identify root causes
- **Event-Driven Architecture**: Kafka-based streaming for scalability and persistence
- **Dockerized Deployment**: Runs as a container alongside your services

## 🏗️ Architecture

```
┌─────────────────┐
│ Docker          │
│ Containers      │──┐
│ (Your Apps)     │  │
└─────────────────┘  │
                     │ Docker Socket
                     ▼
┌─────────────────────────────────────────────────┐
│              Log Analyzer Container              │
│                                                  │
│  ┌──────────────┐    ┌──────────────────────┐  │
│  │ Log Producer │───▶│  Kafka (3min TTL)   │  │
│  │  (Threads)   │    └──────────────────────┘  │
│  └──────────────┘              │                │
│                                 ▼                │
│  ┌──────────────────────────────────────────┐  │
│  │  Error Consumer + Buffer Manager         │  │
│  │  (In-Memory ±60s Context Window)         │  │
│  └──────────────────────────────────────────┘  │
│                     │                            │
│                     ▼ Error Detected             │
│  ┌──────────────────────────────────────────┐  │
│  │  LLM Analyzer (OpenAI GPT-4o-mini)       │  │
│  │  • Root Cause Analysis                   │  │
│  │  • Cross-Container Correlation           │  │
│  │  • Actionable Recommendations            │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose installed
- OpenAI API key ([Get one here](https://platform.openai.com/api-keys))
- At least 1.5GB RAM available for Kafka/Zookeeper

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd Analyze_docker_log_w_llm
   ```

2. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env and add your OPENAI_API_KEY
   nano .env
   ```

3. **Start the system**
   ```bash
   docker-compose up -d
   ```

4. **View logs**
   ```bash
   docker logs -f log-analyzer
   ```

### Stopping

```bash
docker-compose down
```

## 📋 Configuration

Edit `.env` file to customize behavior:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (required) | Your OpenAI API key |
| `MODEL_NAME` | `gpt-4o-mini` | OpenAI model to use |
| `CONTEXT_WINDOW_SECONDS` | `60` | Seconds of context before/after error |
| `DEBOUNCE_SECONDS` | `10` | Wait time to group multiple errors |
| `LLM_ERROR_THRESHOLD` | `10` | Minimum error count in 60s window to trigger LLM (cost optimization) |
| `ANALYTICS_ENABLED` | `true` | Enable Polars-based real-time analytics and smart LLM filtering |
| `ANALYTICS_INTERVAL` | `10.0` | Seconds between background analytics snapshots |
| `CONTAINER_LABEL_FILTER` | `""` | Filter containers by label (e.g., `app=myservice`) |
| `BUFFER_SIZE_PER_CONTAINER` | `1000` | Max log entries kept in memory per container |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG` for buffer statistics) |

## � Logging with Run ID Tracking

Every analyzer run generates a unique `run_id` (UUID) that appears in all log messages, enabling:

- **Distributed Tracing**: Correlate logs across components in complex deployments
- **Multi-Instance Support**: Distinguish between multiple analyzer instances
- **Debugging**: Easily filter logs for a specific execution

**Example Log Output:**
```
2026-03-02 20:38:09 [e0021d2b-377b-41f4-9f1e-a5c967455051] INFO docker-log-analyzer — Configuration validated successfully
2026-03-02 20:38:09 [e0021d2b-377b-41f4-9f1e-a5c967455051] INFO docker-log-analyzer — Model: gpt-4o-mini
2026-03-02 20:38:09 [e0021d2b-377b-41f4-9f1e-a5c967455051] INFO docker-log-analyzer — Monitoring 5 containers
```

The run ID is displayed in:
- Startup banner
- All log messages
- Configuration output

This is based on the logging pattern from the [ACME Cert Lifecycle Agent](https://github.com/debaditya-mohankudo/ACME_Cert_Life_Cycle_Agent_By_Claude_Cowork) project.
## 🔬 Intelligent Log Pattern Discovery

Before running full analysis, **analyze container log patterns** to understand their characteristics:

```bash
# Collect logs for 30 seconds and analyze patterns
uv run python src/main.py --analyze --collection-time 30
```

This generates `container_patterns.json` with insights:

```json
{
  "Flask-API": {
    "language": {
      "name": "python",
      "confidence": 0.95
    },
    "timestamp_format": {
      "type": "iso8601",
      "sample": "2024-03-02T21:19:41.123Z"
    },
    "health_check": {
      "detected": true,
      "pattern": "GET /health 200 OK",
      "frequency_per_minute": 15.0
    },
    "log_levels": {
      "INFO": 1250,
      "DEBUG": 340,
      "ERROR": 23,
      "WARNING": 18
    },
    "common_errors": [
      {
        "pattern": "Error: Connection refused",
        "count": 5
      }
    ]
  }
}
```

**Benefits:**

| Feature | Use Case |
|---------|----------|
| **Timestamp Detection** | Normalize timestamps across different log formats |
| **Language Identification** | Adjust error patterns for Python vs Java vs PHP |
| **Health Check Discovery** | Filter out repeating health checks to reduce noise |
| **Error Pattern Learning** | Pre-compile error regexes for faster detection |
| **Buffer Optimization** | Size buffers based on actual log volume per container |

**Supported Timestamp Formats:**
- ISO-8601: `2024-03-02T21:19:41.123Z`
- Syslog: `Mar 2 21:19:41`
- Unix Epoch: `1709432381`
- Apache: `02/Mar/2024:21:19:41`

**Supported Languages:**
- Python (Flask, Django, FastAPI)
- Java (Spring Boot, Tomcat)
- PHP (Laravel, WordPress)
- Go (net/http, Gin)
- Node.js (Express, Fastify)
## �🔧 Integration with Existing Projects

### Option 1: Add to Existing docker-compose.yml

```yaml
services:
  # ... your existing services ...
  
  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_TICK_TIME: 2000
    networks:
      - your-network

  kafka:
    image: confluentinc/cp-kafka:7.5.0
    depends_on:
      - zookeeper
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_LOG_RETENTION_MS: 180000
    networks:
      - your-network

  log-analyzer:
    build: ./docker-log-analyzer
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    env_file:
      - ./docker-log-analyzer/.env
    environment:
      - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
    depends_on:
      - kafka
    networks:
      - your-network
```

### Option 2: Use Separate Compose File

```bash
docker-compose -f docker-compose.yml -f log-analyzer/docker-compose.yml up -d
```

### Filtering Containers

To monitor only specific containers, use labels:

**In your services:**
```yaml
services:
  my-app:
    image: my-app:latest
    labels:
      - "monitor=true"
```

**In log-analyzer .env:**
```
CONTAINER_LABEL_FILTER=monitor=true
```

## 📊 Usage Example

### Scenario: Database Connection Error

1. **Your app container** logs an error:
   ```
   ERROR: Database connection timeout after 30s
   ```

2. **Log analyzer detects** the error pattern and starts the debounce timer (`DEBOUNCE_SECONDS`)

3. **Smart LLM filtering** evaluates whether analysis is worth triggering. LLM analysis runs only if, within the last 60 seconds, **either**:
   - total error count across all containers ≥ `LLM_ERROR_THRESHOLD` (default: 10), **or**
   - errors appear in ≥ 2 containers simultaneously (potential cascade)

   A single isolated error below the threshold is logged but skipped to reduce API cost.

4. **Captures context** from all containers (±60 seconds):
   - **web-server**: HTTP requests timing out
   - **database**: Connection pool exhausted warning
   - **cache-service**: Memory pressure alerts

5. **LLM analyzes** the correlated logs and outputs:
   ```
   ROOT CAUSE: Database connection pool exhaustion
   
   TIMELINE:
   1. [10:14:45] cache-service: Memory usage spike to 95%
   2. [10:15:10] database: Connection pool size reached max (100)
   3. [10:15:30] web-server: Multiple timeout errors
   
   CORRELATIONS:
   - Cache service OOM caused increased DB queries
   - DB connection pool insufficient for load spike
   
   RECOMMENDATIONS:
   1. Increase cache-service memory allocation
   2. Expand database connection pool size
   3. Implement connection pooling retry logic
   4. Add circuit breaker to prevent cascade failures
   ```

## 🔍 Monitoring and Debugging

### View Real-Time Logs
```bash
docker logs -f log-analyzer
```

### Check Buffer Statistics
Buffer statistics are logged at DEBUG level. Set `LOG_LEVEL=DEBUG` in `.env` to see them:
```
Buffer statistics:
  web-server: 243 logs
  database: 156 logs
  cache-service: 89 logs
```
At the default `INFO` level, the periodic analytics summary (error counts per container) is logged instead, every `ANALYTICS_INTERVAL` seconds.

### Test Error Detection
Inject a test error into any container:
```bash
docker exec <container-name> sh -c 'echo "ERROR: Test failure" >&2'
```

### Kafka Message Inspection
```bash
docker exec kafka kafka-console-consumer \
  --topic docker-logs \
  --from-beginning \
  --bootstrap-server localhost:9092
```

## 🛠️ Development

### VS Code Copilot Extension (Local Development)

For local development and testing **without API costs**, use the built-in VS Code Copilot extension:

```bash
# Navigate to extension directory
cd vscode-extension

# Install dependencies
npm install

# Compile TypeScript
npm run compile

# Open in VS Code and press F5 to start debug session
# Or open this directory in VS Code: code .
```

The extension provides three commands (accessible via `Ctrl+Shift+P`):

1. **Docker Logs: Analyze Container Logs** - Paste your logs to get AI-powered analysis
2. **Docker Logs: Discover Patterns** - Auto-detect timestamp formats, languages, health checks
3. **Docker Logs: Test with Sample Data** - Test with realistic multi-container failure scenario

**Benefits over cloud LLM:**
- ✅ Free (uses VS Code Copilot subscription)
- ✅ Local execution (no API latency)
- ✅ Perfect for development and testing
- ✅ No authentication key management
- ✅ Real-time iteration and refinement

For detailed setup instructions, see [vscode-extension/QUICKSTART.md](vscode-extension/QUICKSTART.md).

### Local Testing (Without Docker)

1. **Install uv** (if not already installed)
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Install dependencies**
   ```bash
   uv sync
   ```

3. **Start Kafka locally** (or use Docker)
   ```bash
   docker-compose up -d kafka zookeeper
   ```

4. **Run the analyzer**
   ```bash
   # Using uv run (recommended - no venv activation needed)
   export OPENAI_API_KEY="your-key"
   export KAFKA_BOOTSTRAP_SERVERS="localhost:9092"
   export DOCKER_HOST="unix:///var/run/docker.sock"
   uv run python src/main.py
   
   # Or activate venv manually
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   python src/main.py
   ```

### Running Tests

Test individual modules using `uv run`:
```bash
# Test error detection
uv run python src/error_consumer.py

# Test buffer management
uv run python src/buffer_manager.py

# Test LLM formatting
uv run python src/llm_analyzer.py

# Test config validation
uv run python src/config.py
```

## ⚙️ System Requirements

- **CPU**: 2 cores minimum
- **RAM**: 2GB minimum (1GB for Kafka/Zookeeper, 512MB for analyzer)
- **Disk**: 1GB for Kafka logs (auto-pruned after 3 minutes)
- **Network**: Outbound HTTPS for OpenAI API

## 🐛 Troubleshooting

### "Permission denied" on Docker socket
**Solution**: Run analyzer as root or add user to docker group
```yaml
# In docker-compose.yml, uncomment:
# user: root
```

### "Failed to connect to Kafka"
**Solution**: Ensure Kafka is fully started (takes ~30s)
```bash
docker logs kafka
# Wait for: "Kafka Server started"
```

### "OpenAI API rate limit exceeded"
**Solution**: Increase `DEBOUNCE_SECONDS` to reduce analysis frequency
```
DEBOUNCE_SECONDS=30
```

### High memory usage
**Solution**: Reduce buffer size per container
```
BUFFER_SIZE_PER_CONTAINER=500
```

### Missing logs in context window
**Cause**: Container clocks are out of sync
**Solution**: Acceptable ±5s skew is normal. For precise sync, use NTP in containers.

## 📝 Error Patterns Detected

Default patterns (configurable in `src/config.py`):
- `ERROR`, `CRITICAL`, `FATAL`
- `Exception`, `Traceback`
- HTTP 5xx errors: `500`, `502`, `503`, etc.
- `panic:` (Go applications)
- `SEVERE` (Java applications)

## 🔐 Security Considerations

- **Docker socket**: Mounted read-only (`:ro`) to prevent container manipulation
- **API keys**: Never commit `.env` file (included in `.gitignore`)
- **Network isolation**: Uses dedicated bridge network
- **Non-root user**: Commented out in Dockerfile (enable if possible)

For production, consider using [docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy) to further restrict Docker API access.

## 📄 License

MIT License - see LICENSE file

## 🤝 Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Submit a pull request

## 📧 Support

For issues and questions, please open a GitHub issue.

---

**Built with ❤️ using Python, Docker, Kafka, OpenAI, and VS Code Copilot**

### Architecture Options

| Scenario | Solution | Cost | Latency | Effort |
|----------|----------|------|---------|--------|
| **Local Development** | VS Code Copilot Extension | Free | Low (~1s) | ⭐ Low |
| **Development Testing** | Pattern Analyzer Module | Free | Medium (~100ms) | ⭐⭐ Medium |
| **Production Deployment** | Cloud LLM (OpenAI/Azure) | $$ | Low | ⭐⭐⭐ High |
| **Hybrid** | Extension + Cloud LLM | $ | Mixed | ⭐⭐⭐ High |
