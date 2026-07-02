"""Unit tests for logger.py — JsonlFormatter and file-logging wiring."""

import json
import logging

import pytest

from docker_log_analyzer.logger import JsonlFormatter, LoggerDecorator, LoggerWithRunID, RunIDFilter


pytestmark = pytest.mark.unit


def _make_record(msg="hello", level=logging.INFO, exc_info=None):
    record = logging.LogRecord(
        name="test-logger",
        level=level,
        pathname=__file__,
        lineno=42,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    record.run_id = "run-123"
    return record


class TestJsonlFormatter:
    def test_produces_valid_single_line_json(self):
        formatter = JsonlFormatter()
        line = formatter.format(_make_record())

        assert "\n" not in line
        parsed = json.loads(line)
        assert parsed["message"] == "hello"
        assert parsed["level"] == "INFO"
        assert parsed["run_id"] == "run-123"
        assert parsed["logger"] == "test-logger"
        assert parsed["line"] == 42

    def test_includes_exception_when_present(self):
        formatter = JsonlFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = _make_record(msg="failed", level=logging.ERROR, exc_info=sys.exc_info())

        line = formatter.format(record)
        parsed = json.loads(line)
        assert "exception" in parsed
        assert "ValueError: boom" in parsed["exception"]

    def test_missing_run_id_serializes_as_null(self):
        record = logging.LogRecord(
            name="test-logger", level=logging.INFO, pathname=__file__,
            lineno=1, msg="no run id", args=(), exc_info=None,
        )
        line = JsonlFormatter().format(record)
        parsed = json.loads(line)
        assert parsed["run_id"] is None


class TestEnableFileLogging:
    def test_writes_jsonl_lines_to_file(self, tmp_path):
        logger = logging.getLogger("test-file-logging")
        logger.handlers.clear()
        decorator = LoggerDecorator(logger, run_id="run-abc")

        log_path = tmp_path / "app.jsonl"
        decorator.enable_file_logging(str(log_path), max_bytes=10_000_000, backup_count=1)
        decorator.info("first message")
        decorator.error("second message")

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert first["message"] == "first message"
        assert first["run_id"] == "run-abc"
        assert second["level"] == "ERROR"

    def test_idempotent_does_not_duplicate_handler(self, tmp_path):
        logger = logging.getLogger("test-file-logging-idempotent")
        logger.handlers.clear()
        decorator = LoggerDecorator(logger, run_id="run-xyz")

        log_path = tmp_path / "app.jsonl"
        decorator.enable_file_logging(str(log_path), max_bytes=10_000_000, backup_count=1)
        decorator.enable_file_logging(str(log_path), max_bytes=10_000_000, backup_count=1)
        decorator.info("only once")

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1

    def test_creates_parent_directories(self, tmp_path):
        logger = logging.getLogger("test-file-logging-mkdir")
        logger.handlers.clear()
        decorator = LoggerDecorator(logger, run_id="run-mkdir")

        log_path = tmp_path / "nested" / "dirs" / "app.jsonl"
        decorator.enable_file_logging(str(log_path), max_bytes=10_000_000, backup_count=1)
        decorator.info("created nested dirs")

        assert log_path.exists()


class TestCallerAttribution:
    """LoggerWithRunID wraps LoggerDecorator wraps logging.Logger — without
    stacklevel correction, every JSONL line would report logger.py itself as
    the caller (module/func/line), which defeats the point of file logging
    for debugging this repo. These pin the fix."""

    def test_logger_decorator_attributes_to_direct_caller(self, tmp_path):
        logging.getLogger("test-attrib-decorator").handlers.clear()
        decorator = LoggerDecorator(logging.getLogger("test-attrib-decorator"), run_id="r1")
        log_path = tmp_path / "app.jsonl"
        decorator.enable_file_logging(str(log_path), max_bytes=10_000_000, backup_count=1)

        def caller():
            decorator.info("from caller")

        this_line = caller.__code__.co_firstlineno + 1  # the decorator.info(...) line
        caller()

        entry = json.loads(log_path.read_text().strip())
        assert entry["func"] == "caller"
        assert entry["line"] == this_line

    def test_logger_with_run_id_attributes_to_external_caller_not_logger_py(self, tmp_path, monkeypatch):
        # LoggerWithRunID is a process-wide singleton (_instance class attr);
        # reset it so this test builds its own instance instead of mutating
        # the shared module-level `logger` used everywhere else. monkeypatch
        # restores the original _instance automatically on teardown.
        monkeypatch.setattr(LoggerWithRunID, "_instance", None)
        logging.getLogger("test-attrib-facade").handlers.clear()
        facade = LoggerWithRunID(name="test-attrib-facade")
        log_path = tmp_path / "app.jsonl"
        facade.enable_file_logging(str(log_path), max_bytes=10_000_000, backup_count=1)

        def caller():
            facade.info("from external caller")

        this_line = caller.__code__.co_firstlineno + 1
        caller()

        entry = json.loads(log_path.read_text().strip())
        assert entry["module"] != "logger"
        assert entry["func"] == "caller"
        assert entry["line"] == this_line
