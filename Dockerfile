FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PATH="/root/.cargo/bin:$PATH"
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml .
COPY docker_log_analyzer/ ./docker_log_analyzer/

RUN uv pip install --system --no-cache -e .

CMD ["docker-log-analyzer-mcp"]
