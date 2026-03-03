# Docker Log Analyzer MCP Server

This directory contains an MCP (Model Context Protocol) server that exposes Docker log analysis capabilities to Claude and other MCP clients.

## Quick Start

### Option 1: Using CLI Entry Point (Recommended)
```bash
uv run docker-log-analyzer-mcp
```

### Option 2: Using Module Execution
```bash
uv run python -m src.mcp_server
```

### Option 3: Using Wrapper Script
```bash
uv run python run_mcp_server.py
```

### Option 4: Direct Python (add sys.path)
```bash
cd src
python mcp_server.py
```

## Available Tools

The MCP server exposes 3 tools for Claude/Copilot:

### 1. `analyze_logs`
Analyze Docker container logs to identify errors, correlations, and root causes using AI.

**Input:**
- `logs` (string): Docker container logs

**Output:**
- JSON with error analysis, correlations, timeline, root cause, impact, and recommendations

### 2. `discover_patterns`
Discover patterns in Docker logs including timestamp format, programming language, log levels, and common errors.

**Input:**
- `logs` (string): Docker container logs to analyze

**Output:**
- JSON with discovered patterns (timestamp format, language, log levels, error samples)

### 3. `correlate_errors`
Analyze multi-container logs and find correlations between errors across containers.

**Input:**
- `logs` (string): Multi-container Docker logs
- `container_name` (optional): Focus analysis on specific container

**Output:**
- JSON with correlation analysis including timeline and affected services

## Example Usage

**Analyzing sample Docker logs:**
```bash
cat << 'EOF' | uv run docker-log-analyzer-mcp
2024-03-02T21:20:01.111Z [backend] ERROR Database connection refused: timeout 30s
2024-03-02T21:20:01.222Z [backend] ERROR Failed to execute query: no active connection
2024-03-02T21:20:01.333Z [cache] ERROR Unable to cache result: upstream unavailable
2024-03-02T21:20:01.444Z [frontend] ERROR Failed to fetch /api/users: 503 Service Unavailable
2024-03-02T21:20:02.555Z [backend] ERROR Maximum retries exceeded
2024-03-02T21:20:03.666Z [db] CRITICAL Database service down
EOF
```

## Integration with Claude

To integrate with Claude Desktop or other MCP clients, configure in your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "docker-log-analyzer": {
      "command": "uv",
      "args": ["run", "docker-log-analyzer-mcp"],
      "cwd": "/path/to/Analyze_docker_log_w_llm"
    }
  }
}
```

## Architecture

- **mcp_server.py** - MCP server implementation with tool handlers
- **llm_analyzer.py** - LLM analysis using OpenAI API
- **log_pattern_analyzer.py** - Pattern detection (timestamps, languages, errors)
- **buffer_manager.py** - Log storage and retrieval
- **error_consumer.py** - Error detection patterns

## Configuration

Set these environment variables:

```bash
export OPENAI_API_KEY="your-api-key"
export MODEL_NAME="gpt-4o-mini"  # or your preferred model
export CONTEXT_WINDOW_SECONDS="60"
export DEBOUNCE_SECONDS="10"
```

## Development

Install dependencies:
```bash
uv sync
```

Run tests:
```bash
uv run pytest tests/
```

## Troubleshooting

**ImportError: attempted relative import with no known parent package**
- Use `uv run docker-log-analyzer-mcp` or `uv run python -m src.mcp_server`
- Avoid running `python src/mcp_server.py` directly

**OPENAI_API_KEY not set**
- Create a `.env` file with your API key or export it as an environment variable

**MCP connection issues**
- Ensure stdio_server is properly initialized
- Check that MCP client supports the protocol version
