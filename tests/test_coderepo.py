"""
Unit tests for docker_log_analyzer.coderepo.

All tests are self-contained (no Docker required).
Uses tmp_path fixtures to create temporary repo structures.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from docker_log_analyzer.coderepo import (
    StackFrame,
    parse_python_frames,
    parse_java_frames,
    parse_go_frames,
    parse_nodejs_frames,
    parse_frames,
    resolve_repo_for_container,
    find_file_in_repo,
    extract_code_context,
    analyse_code_context,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Python stack trace parser
# --------------------------------------------------------------------------- #

class TestParsePythonFrames:
    def test_basic_frame(self):
        lines = ['  File "app/server.py", line 42, in handle_request']
        frames = parse_python_frames(lines)
        assert len(frames) == 1
        assert frames[0].file_path == "app/server.py"
        assert frames[0].line_no == 42
        assert frames[0].function_name == "handle_request"
        assert frames[0].language == "python"

    def test_multiple_frames(self):
        lines = [
            'Traceback (most recent call last):',
            '  File "app/server.py", line 42, in handle_request',
            '  File "app/db.py", line 10, in query',
            'Exception: connection failed',
        ]
        frames = parse_python_frames(lines)
        assert len(frames) == 2
        assert frames[0].file_path == "app/server.py"
        assert frames[1].file_path == "app/db.py"

    def test_no_frames_in_plain_log(self):
        lines = ["INFO starting server", "ERROR something went wrong"]
        assert parse_python_frames(lines) == []

    def test_absolute_path(self):
        lines = ['  File "/usr/local/lib/python3.11/site-packages/pkg.py", line 5, in run']
        frames = parse_python_frames(lines)
        assert frames[0].file_path == "/usr/local/lib/python3.11/site-packages/pkg.py"


# --------------------------------------------------------------------------- #
# Java stack trace parser
# --------------------------------------------------------------------------- #

class TestParseJavaFrames:
    def test_basic_frame(self):
        lines = ["\tat com.example.Service.process(Service.java:123)"]
        frames = parse_java_frames(lines)
        assert len(frames) == 1
        assert frames[0].file_path == "Service.java"
        assert frames[0].line_no == 123
        assert frames[0].function_name == "process"
        assert frames[0].language == "java"

    def test_inner_class(self):
        lines = ["\tat com.example.Service$Inner.run(Service.java:50)"]
        frames = parse_java_frames(lines)
        assert len(frames) == 1
        assert frames[0].file_path == "Service.java"

    def test_multiple_frames(self):
        lines = [
            "java.lang.NullPointerException",
            "\tat com.example.A.foo(A.java:10)",
            "\tat com.example.B.bar(B.java:20)",
        ]
        frames = parse_java_frames(lines)
        assert len(frames) == 2

    def test_no_frames(self):
        assert parse_java_frames(["INFO: server started"]) == []


# --------------------------------------------------------------------------- #
# Go stack trace parser
# --------------------------------------------------------------------------- #

class TestParseGoFrames:
    def test_basic_frame(self):
        lines = [
            "goroutine 1 [running]:",
            "main.handleRequest(...)",
            "\t/home/user/app/main.go:42 +0x1a3",
        ]
        frames = parse_go_frames(lines)
        assert len(frames) == 1
        assert frames[0].file_path == "/home/user/app/main.go"
        assert frames[0].line_no == 42
        assert frames[0].language == "go"

    def test_no_frames(self):
        assert parse_go_frames(["level=error msg=something"]) == []


# --------------------------------------------------------------------------- #
# Node.js stack trace parser
# --------------------------------------------------------------------------- #

class TestParseNodejsFrames:
    def test_named_function(self):
        lines = ["    at handleRequest (/app/server.js:42:10)"]
        frames = parse_nodejs_frames(lines)
        assert len(frames) == 1
        assert frames[0].file_path == "/app/server.js"
        assert frames[0].line_no == 42
        assert frames[0].function_name is not None
        assert frames[0].language == "nodejs"

    def test_anonymous(self):
        lines = ["    at /app/router.js:15:5"]
        frames = parse_nodejs_frames(lines)
        assert len(frames) == 1
        assert frames[0].file_path == "/app/router.js"
        assert frames[0].line_no == 15

    def test_typescript_extension(self):
        lines = ["    at compile (/app/build.ts:7:3)"]
        frames = parse_nodejs_frames(lines)
        assert len(frames) == 1
        assert frames[0].file_path == "/app/build.ts"

    def test_no_frames(self):
        assert parse_nodejs_frames(["INFO server listening on :3000"]) == []


# --------------------------------------------------------------------------- #
# parse_frames dispatcher
# --------------------------------------------------------------------------- #

class TestParseFrames:
    def test_dispatches_python(self):
        lines = ['  File "app.py", line 1, in main']
        frames = parse_frames(lines, "python")
        assert all(f.language == "python" for f in frames)

    def test_dispatches_java(self):
        lines = ["\tat com.A.run(A.java:5)"]
        frames = parse_frames(lines, "java")
        assert all(f.language == "java" for f in frames)

    def test_unknown_tries_all(self):
        lines = [
            '  File "app.py", line 1, in main',
            "\tat com.A.run(A.java:5)",
        ]
        frames = parse_frames(lines, "unknown")
        assert len(frames) == 2

    def test_unknown_language_returns_results(self):
        lines = ['  File "app.py", line 1, in main']
        frames = parse_frames(lines, "ruby")  # unsupported → tries all
        assert len(frames) >= 1


# --------------------------------------------------------------------------- #
# resolve_repo_for_container
# --------------------------------------------------------------------------- #

class TestResolveRepoForContainer:
    def test_exact_match(self, tmp_path):
        result = resolve_repo_for_container(
            "api-service",
            container_repo_map={"api-service": str(tmp_path)},
            repo_paths=[],
        )
        assert result == tmp_path

    def test_prefix_match(self, tmp_path):
        result = resolve_repo_for_container(
            "api-gateway",
            container_repo_map={"api": str(tmp_path)},
            repo_paths=[],
        )
        assert result == tmp_path

    def test_falls_back_to_repo_paths(self, tmp_path):
        result = resolve_repo_for_container(
            "unknown-service",
            container_repo_map={},
            repo_paths=[str(tmp_path)],
        )
        assert result == tmp_path

    def test_returns_none_when_no_config(self):
        result = resolve_repo_for_container("svc", {}, [])
        assert result is None

    def test_nonexistent_path_returns_none(self):
        result = resolve_repo_for_container(
            "svc",
            container_repo_map={"svc": "/nonexistent/path/xyz"},
            repo_paths=[],
        )
        assert result is None

    def test_exact_takes_priority_over_prefix(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        result = resolve_repo_for_container(
            "api",
            container_repo_map={"api": str(tmp_path), "a": str(other)},
            repo_paths=[],
        )
        assert result == tmp_path


# --------------------------------------------------------------------------- #
# find_file_in_repo
# --------------------------------------------------------------------------- #

class TestFindFileInRepo:
    def test_relative_path(self, tmp_path):
        (tmp_path / "app").mkdir()
        f = tmp_path / "app" / "server.py"
        f.write_text("# server")
        result = find_file_in_repo(tmp_path, "app/server.py")
        assert result == f

    def test_absolute_path_within_repo(self, tmp_path):
        f = tmp_path / "main.go"
        f.write_text("package main")
        result = find_file_in_repo(tmp_path, str(f))
        assert result == f

    def test_absolute_path_outside_repo_is_rejected(self, tmp_path):
        # A file that exists on the host but is outside repo_root must not be returned.
        repo = tmp_path / "repo"
        repo.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("sensitive")
        result = find_file_in_repo(repo, str(outside))
        assert result is None

    def test_absolute_path_inside_relative_repo_root(self, tmp_path, monkeypatch):
        # Regression: when repo_root is a relative path (e.g. REPO_PATHS="."),
        # find_file_in_repo must still resolve absolute frame paths that point
        # inside the repo — it must not silently return None for valid configs.
        f = tmp_path / "app" / "main.py"
        f.parent.mkdir()
        f.write_text("# main")
        # CWD set to tmp_path so that Path(".") resolves to tmp_path
        monkeypatch.chdir(tmp_path)
        result = find_file_in_repo(Path("."), str(f))
        assert result == f.resolve()

    def test_absolute_path_outside_relative_repo_root_rejected(self, tmp_path, monkeypatch):
        # Security: even with a relative repo_root, absolute paths outside it
        # must still be rejected after the resolve() fix.
        repo = tmp_path / "repo"
        repo.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("sensitive")
        monkeypatch.chdir(repo)
        result = find_file_in_repo(Path("."), str(outside))
        assert result is None

    def test_basename_search(self, tmp_path):
        (tmp_path / "pkg" / "server").mkdir(parents=True)
        f = tmp_path / "pkg" / "server" / "Service.java"
        f.write_text("class Service {}")
        result = find_file_in_repo(tmp_path, "Service.java")
        assert result == f

    def test_returns_none_when_not_found(self, tmp_path):
        result = find_file_in_repo(tmp_path, "nonexistent.py")
        assert result is None


# --------------------------------------------------------------------------- #
# extract_code_context
# --------------------------------------------------------------------------- #

class TestExtractCodeContext:
    def _make_file(self, tmp_path, lines):
        f = tmp_path / "code.py"
        f.write_text("\n".join(lines))
        return f

    def test_returns_correct_line(self, tmp_path):
        f = self._make_file(tmp_path, ["line1", "line2", "line3", "line4", "line5"])
        ctx = extract_code_context(f, line_no=3, context_lines=1)
        assert ctx["at"] == (3, "line3")
        assert ctx["before"] == [(2, "line2")]
        assert ctx["after"] == [(4, "line4")]

    def test_clamps_at_file_start(self, tmp_path):
        f = self._make_file(tmp_path, ["a", "b", "c"])
        ctx = extract_code_context(f, line_no=1, context_lines=5)
        assert ctx["before"] == []
        assert ctx["at"] == (1, "a")

    def test_clamps_at_file_end(self, tmp_path):
        f = self._make_file(tmp_path, ["a", "b", "c"])
        ctx = extract_code_context(f, line_no=3, context_lines=5)
        assert ctx["after"] == []
        assert ctx["at"] == (3, "c")

    def test_out_of_range_returns_error(self, tmp_path):
        f = self._make_file(tmp_path, ["only one line"])
        ctx = extract_code_context(f, line_no=99, context_lines=2)
        assert "error" in ctx

    def test_unreadable_path_returns_error(self, tmp_path):
        ctx = extract_code_context(tmp_path / "no_file.py", line_no=1)
        assert "error" in ctx


# --------------------------------------------------------------------------- #
# analyse_code_context (full pipeline)
# --------------------------------------------------------------------------- #

class TestAnalyseCodeContext:
    def test_no_frames_returns_no_frames_status(self):
        result = analyse_code_context(
            log_lines=["INFO: all good"],
            language="python",
            container_name="svc",
            container_repo_map={},
            repo_paths=[],
        )
        assert result["status"] == "no_frames"

    def test_warns_when_no_repo_configured(self, tmp_path):
        lines = ['  File "app.py", line 1, in main', "Exception: boom"]
        result = analyse_code_context(
            log_lines=lines,
            language="python",
            container_name="svc",
            container_repo_map={},
            repo_paths=[],
        )
        assert result["status"] == "success"
        assert len(result["warnings"]) > 0
        assert result["repo_root"] is None

    def test_resolves_code_context_when_repo_configured(self, tmp_path):
        # Create a real source file
        src = tmp_path / "app.py"
        src.write_text("\n".join(f"line{i}" for i in range(1, 20)))

        lines = ['  File "app.py", line 5, in main', "Exception: boom"]
        result = analyse_code_context(
            log_lines=lines,
            language="python",
            container_name="svc",
            container_repo_map={"svc": str(tmp_path)},
            repo_paths=[],
        )
        assert result["status"] == "success"
        assert result["repo_root"] == str(tmp_path)
        frame = result["frames"][0]
        assert frame["resolved_file"] == str(src)
        assert frame["code_context"] is not None
        assert frame["code_context"]["at"][0] == 5

    def test_unresolved_file_tracked(self, tmp_path):
        lines = ['  File "missing_module.py", line 1, in func']
        result = analyse_code_context(
            log_lines=lines,
            language="python",
            container_name="svc",
            container_repo_map={"svc": str(tmp_path)},
            repo_paths=[],
        )
        assert "missing_module.py" in result["unresolved_files"]

    def test_max_frames_limit(self, tmp_path):
        lines = [f'  File "f{i}.py", line {i}, in fn' for i in range(1, 20)]
        result = analyse_code_context(
            log_lines=lines,
            language="python",
            container_name="svc",
            container_repo_map={},
            repo_paths=[],
            max_frames=3,
        )
        assert result["frames_found"] == 3

    def test_returns_container_name(self):
        result = analyse_code_context(
            log_lines=["INFO: ok"],
            language="python",
            container_name="my-service",
            container_repo_map={},
            repo_paths=[],
        )
        assert result["container"] == "my-service"
