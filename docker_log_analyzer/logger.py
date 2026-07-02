"""
Logger module with run_id tracking for distributed system tracing.
Each run of the analyzer gets a unique run_id for log correlation.
"""

import json
import logging
import logging.handlers
import uuid
from pathlib import Path
from typing import Any


class RunIDFilter(logging.Filter):
    """Filter that injects run_id into log records."""

    def __init__(self, run_id: str):
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        return True


class JsonlFormatter(logging.Formatter):
    """Formats each record as one JSON object per line (JSONL).

    For debugging this repo's own server process — separate from the
    Docker container logs the tools analyze. Structured fields make it
    greppable/parseable without regex, unlike the stderr text format.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "run_id": getattr(record, "run_id", None),
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


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

    def enable_file_logging(self, path: str, max_bytes: int, backup_count: int) -> None:
        """Add a rotating JSONL file handler alongside the stderr handler.

        Idempotent — safe to call more than once (e.g. Settings reloaded in
        tests) without stacking duplicate handlers on the same path.
        """
        target = str(Path(path).resolve())
        for h in self._logger.handlers:
            if isinstance(h, logging.handlers.RotatingFileHandler) and h.baseFilename == target:
                return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count
        )
        handler.setFormatter(JsonlFormatter())
        self._logger.addHandler(handler)

    # Delegate logging methods to wrapped logger.
    #
    # stacklevel defaults to 2 here: one frame for this method, so the
    # JsonlFormatter's module/func/line fields report the code that actually
    # called .info()/.error()/etc, not this wrapper. LoggerWithRunID (below)
    # calls through this class, so it passes stacklevel=3 explicitly —
    # setdefault() below only applies when that key is absent (direct use of
    # LoggerDecorator, as in its own unit tests).
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 2)
        self._logger.info(msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 2)
        self._logger.error(msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 2)
        self._logger.warning(msg, *args, **kwargs)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 2)
        self._logger.debug(msg, *args, **kwargs)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 2)
        self._logger.exception(msg, *args, **kwargs)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 2)
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

    def enable_file_logging(self, path: str, max_bytes: int, backup_count: int) -> None:
        """Add a rotating JSONL file handler for debugging this repo's own
        server process. Separate from the Docker container logs the tools
        analyze — see JsonlFormatter."""
        self._decorator.enable_file_logging(path, max_bytes, backup_count)

    # Delegate all methods to the decorator. stacklevel=3 accounts for this
    # extra wrapping layer (LoggerWithRunID.info -> LoggerDecorator.info ->
    # Logger.info), so JsonlFormatter attributes each line to the real
    # call site rather than logger.py itself.
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 3)
        self._decorator.info(msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 3)
        self._decorator.error(msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 3)
        self._decorator.warning(msg, *args, **kwargs)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 3)
        self._decorator.debug(msg, *args, **kwargs)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 3)
        self._decorator.exception(msg, *args, **kwargs)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stacklevel", 3)
        self._decorator.critical(msg, *args, **kwargs)
    
    def get_run_id(self) -> str:
        return self._decorator.get_run_id()


__all__ = ["LoggerWithRunID", "logger"]

# Module-level singleton instance
logger = LoggerWithRunID()
