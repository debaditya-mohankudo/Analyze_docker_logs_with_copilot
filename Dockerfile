FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies and uv
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

# Add uv to PATH
ENV PATH="/root/.cargo/bin:$PATH"

# Copy project files
COPY pyproject.toml .
COPY src/ ./src/

# Install Python dependencies using uv
RUN uv pip install --system --no-cache -e .

# Use unbuffered Python output for immediate Docker logs
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('kafka', 9092), timeout=5)" || exit 1

# Run as non-root user for security (may need to override for Docker socket access)
# USER nobody

# Start the application
CMD ["python", "-u", "src/main.py"]
