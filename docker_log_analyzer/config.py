"""
Configuration for Docker Log Analyzer MCP Server (non-LLM).
Uses Pydantic Settings with environment variable and .env file support.
"""

import logging
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


class Settings(BaseSettings):
    """Application configuration loaded from environment and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL",
    )

    # Docker Configuration
    docker_host: str = Field(
        default="unix:///var/run/docker.sock",
        description="Docker daemon socket/host URL",
    )
    container_label_filter: str = Field(
        default="",
        description="Optional label filter for listing containers (e.g., 'env=prod')",
    )

    # Tool Parameter Defaults (overridable per call)
    default_tail_lines: int = Field(
        default=500,
        description="Default number of log lines to fetch for pattern analysis",
    )
    default_spike_tail_lines: int = Field(
        default=1000,
        description="Default number of log lines to fetch for spike detection",
    )
    default_window_minutes: int = Field(
        default=5,
        description="Default rolling window size in minutes for spike detection",
    )
    default_spike_threshold: float = Field(
        default=2.0,
        description="Default spike detection threshold (multiplier of baseline)",
    )
    default_correlation_window_seconds: int = Field(
        default=30,
        description="Default time window in seconds for container correlation",
    )

    # Error Patterns for spike and correlation detection
    error_patterns: List[str] = Field(
        default=[
            r"ERROR",
            r"CRITICAL",
            r"FATAL",
            r"Exception",
            r"Traceback",
            r"5\d{2}",  # HTTP 5xx
            r"4\d{2}",  # HTTP 4xx
            r"panic:",  # Go
            r"SEVERE",  # Java
            # SQL failure patterns – Java
            r"SQLException",
            r"HibernateException",
            r"JDBCException",
            r"could not execute statement",
            r"ORA-\d+",  # Oracle error codes
            r"PSQLException",  # PostgreSQL via JDBC
            r"SQLSyntaxErrorException",
            # SQL failure patterns – PHP
            r"PDOException",
            r"mysqli_error",
            r"mysql_error",
            r"SQLSTATE\[",  # PDO SQLSTATE prefix
            r"Query failed",
            r"Deadlock found",
            r"Table .+ doesn't exist",
        ],
        description="Regex patterns to detect error/failure lines",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate that log level is a valid Python logging level."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v.upper()

    @field_validator("default_tail_lines", "default_spike_tail_lines")
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        """Ensure positive integer values."""
        if v <= 0:
            raise ValueError("must be a positive integer")
        return v

    @field_validator("default_spike_threshold")
    @classmethod
    def validate_positive_float(cls, v: float) -> float:
        """Ensure positive float threshold."""
        if v <= 0:
            raise ValueError("must be a positive float")
        return v


# Singleton instance for application-wide use
settings = Settings()

# Configure logger with loaded settings
logger_module = logging.getLogger("docker_log_analyzer")
logger_module.setLevel(getattr(logging, settings.log_level))
