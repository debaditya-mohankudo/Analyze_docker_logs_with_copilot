"""
Configuration for Docker Log Analyzer MCP Server (non-LLM).
Loads settings from environment variables / .env file.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

from .logger import logger

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logger.set_level(getattr(logging, LOG_LEVEL))

# Docker
DOCKER_HOST = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")
CONTAINER_LABEL_FILTER = os.getenv("CONTAINER_LABEL_FILTER", "")

# Tool parameter defaults (overridable per call)
DEFAULT_TAIL_LINES = int(os.getenv("DEFAULT_TAIL_LINES", "500"))
DEFAULT_SPIKE_TAIL_LINES = int(os.getenv("DEFAULT_SPIKE_TAIL_LINES", "1000"))
DEFAULT_WINDOW_MINUTES = int(os.getenv("DEFAULT_WINDOW_MINUTES", "5"))
DEFAULT_SPIKE_THRESHOLD = float(os.getenv("DEFAULT_SPIKE_THRESHOLD", "2.0"))
DEFAULT_CORRELATION_WINDOW_SECONDS = int(os.getenv("DEFAULT_CORRELATION_WINDOW_SECONDS", "30"))

# Error patterns used by spike_detector and correlator
ERROR_PATTERNS = [
    r"ERROR",
    r"CRITICAL",
    r"FATAL",
    r"Exception",
    r"Traceback",
    r"5\d{2}",   # HTTP 5xx
    r"4\d{2}",   # HTTP 4xx (optional, can be noisy)
    r"panic:",   # Go
    r"SEVERE",   # Java
    # SQL failure patterns – Java
    r"SQLException",
    r"HibernateException",
    r"JDBCException",
    r"could not execute statement",
    r"ORA-\d+",             # Oracle error codes
    r"PSQLException",        # PostgreSQL via JDBC
    r"SQLSyntaxErrorException",
    # SQL failure patterns – PHP
    r"PDOException",
    r"mysqli_error",
    r"mysql_error",
    r"SQLSTATE\[",           # PDO SQLSTATE prefix
    r"Query failed",
    r"Deadlock found",
    r"Table .+ doesn't exist",
]
