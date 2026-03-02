"""
Logger module with run_id tracking for distributed system tracing.
Each run of the analyzer gets a unique run_id for log correlation.
"""

import logging
import uuid
from typing import Any


class RunIDFilter(logging.Filter):
    """Filter that injects run_id into log records."""
    
    def __init__(self, run_id: str):
        super().__init__()
        self.run_id = run_id
    
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        return True


class LoggerDecorator:
    """
    Decorator that wraps a standard logger with run_id tracking.
    
    Uses the decorator pattern to extend logging.Logger behavior
    without inheritance, maintaining loose coupling.
    """
    
    def __init__(self, logger: logging.Logger, run_id: str):
        self._logger = logger
        self.run_id = run_id
        self._configure()
    
    def _configure(self) -> None:
        """Configure the wrapped logger with run_id filter and formatter."""
        self._logger.setLevel(logging.INFO)
        
        # Add run_id filter
        run_id_filter = RunIDFilter(self.run_id)
        self._logger.addFilter(run_id_filter)
        
        # Configure handler with run_id in format
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(run_id)s] %(levelname)s %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        self._logger.addHandler(handler)
    
    # Delegate logging methods to wrapped logger
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.info(msg, *args, **kwargs)
    
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.error(msg, *args, **kwargs)
    
    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.warning(msg, *args, **kwargs)
    
    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.debug(msg, *args, **kwargs)
    
    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.exception(msg, *args, **kwargs)
    
    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.critical(msg, *args, **kwargs)
    
    def get_run_id(self) -> str:
        return self.run_id


class LoggerWithRunID:
    """
    Singleton facade for LoggerDecorator.
    
    Ensures single run_id across the application lifecycle.
    """
    _instance: "LoggerWithRunID | None" = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, name: str = "docker-log-analyzer"):
        if not hasattr(self, "initialized"):
            run_id = str(uuid.uuid4())
            self.logger = logging.getLogger(name)
            self._decorator = LoggerDecorator(self.logger, run_id)
            self.initialized = True
    
    def set_level(self, level: int) -> None:
        """Set logging level dynamically."""
        self._decorator._logger.setLevel(level)
    
    # Delegate all methods to the decorator
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._decorator.info(msg, *args, **kwargs)
    
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._decorator.error(msg, *args, **kwargs)
    
    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._decorator.warning(msg, *args, **kwargs)
    
    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._decorator.debug(msg, *args, **kwargs)
    
    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._decorator.exception(msg, *args, **kwargs)
    
    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._decorator.critical(msg, *args, **kwargs)
    
    def get_run_id(self) -> str:
        return self._decorator.get_run_id()


__all__ = ["LoggerWithRunID", "logger"]

# Module-level singleton instance
logger = LoggerWithRunID()
