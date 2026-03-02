# Multi-Container Docker Log Correlation System with Kafka

A Python event-driven system running in Docker that monitors sibling containers. Uses Kafka for log streaming, detects errors across containers, and performs LLM correlation analysis.

## Architecture Overview

- **Log Producers**: Stream Docker container logs to Kafka (one thread per container)
- **Kafka**: Central message broker for log streaming (3-minute retention)
- **Error Consumer**: Detects errors in real-time from Kafka stream
- **Buffer Manager**: In-memory time-windowed storage (±60 seconds)
- **LLM Analyzer**: Correlates logs across containers using GPT-4o-mini

## Tech Stack

- Python 3.11
- Docker SDK for Python
- kafka-python
- OpenAI API (GPT-4o-mini)
- Docker & Docker Compose
- Confluent Kafka

## Quick Start

```bash
# Setup environment
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY

# Start the system
docker-compose up -d

# View logs
docker logs -f log-analyzer

# Stop the system
docker-compose down
```

## How It Works

1. **Log Collection**: Monitors all Docker containers on the host via Docker socket
2. **Streaming**: Publishes logs to Kafka topic in real-time
3. **Error Detection**: Consumes from Kafka, detecting ERROR/CRITICAL/FATAL patterns
4. **Context Aggregation**: When error found, captures ±60 seconds from ALL containers
5. **LLM Analysis**: Sends context to GPT-4o-mini to identify correlations and root cause
6. **Output**: Prints analysis to console (visible via `docker logs`)

## Integration with Existing Projects

Add to your existing docker-compose.yml:

```yaml
services:
  # ... your existing services ...
  
  log-analyzer:
    image: docker-log-analyzer:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
    depends_on:
      - kafka
    networks:
      - your-network
```
