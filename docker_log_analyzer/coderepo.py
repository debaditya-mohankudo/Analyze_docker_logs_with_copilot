"""
coderepo.py – Code repository access for post-error deep-dive analysis.

Given a container name and recent error logs, this module:
  1. Parses stack traces (Python, Java, Go, Node.js) into structured frames
  2. Resolves frame file paths against configured local repository roots
  3. Extracts code context (N lines before/after each error line)
  4. Optionally scans the repo for the error pattern to find all call sites

No LLM. No network calls. All analysis is local file I/O + regex.
Stateless: every function is a pure transformation (input → output).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Stack frame dataclass
# --------------------------------------------------------------------------- #

@dataclass
class StackFrame:
    language: str
    file_path: str          # as it appears in the log (relative or absolute)
    line_no: int | None
    function_name: str | None
    raw_line: str


# --------------------------------------------------------------------------- #
# Per-language stack trace patterns
# --------------------------------------------------------------------------- #

# Python:  File "path/to/module.py", line 42, in function_name
_PY_FRAME_RE = re.compile(
    r'File "(?P<file>[^"]+)",\s+line\s+(?P<line>\d+),\s+in\s+(?P<func>\S+)'
)

# Java:    at com.example.Service.method(Service.java:123)
_JAVA_FRAME_RE = re.compile(
    r'at\s+[\w.$]+\.(?P<func>[\w$<>]+)\((?P<file>[\w$]+\.java):(?P<line>\d+)\)'
)

# Go:      goroutine N [running]:
#          github.com/org/repo/pkg.FuncName(...)
#                  /home/user/go/src/.../file.go:42 +0x...
_GO_FRAME_RE = re.compile(
    r'^\s+(?P<file>[^\s]+\.go):(?P<line>\d+)'
)
_GO_FUNC_RE = re.compile(
    r'^(?P<func>[\w./\-@${}()\[\]*]+)\('
)

# Node.js:     at FunctionName (/path/to/file.js:42:10)
#              at /path/to/file.js:42:10
_NODE_FRAME_RE = re.compile(
    r'at\s+(?:(?P<func>[^(]+?)\s+)?\((?P<file>[^)]+\.(?:js|ts|mjs|cjs)):(?P<line>\d+):\d+\)'
    r'|at\s+(?P<file2>[^\s]+\.(?:js|ts|mjs|cjs)):(?P<line2>\d+):\d+'
)

# --------------------------------------------------------------------------- #
# Stack trace parsers
# --------------------------------------------------------------------------- #

def parse_python_frames(lines: list[str]) -> list[StackFrame]:
    frames: list[StackFrame] = []
    for line in lines:
        m = _PY_FRAME_RE.search(line)
        if m:
            frames.append(StackFrame(
                language="python",
                file_path=m.group("file"),
                line_no=int(m.group("line")),
                function_name=m.group("func"),
                raw_line=line.rstrip(),
            ))
    return frames


def parse_java_frames(lines: list[str]) -> list[StackFrame]:
    frames: list[StackFrame] = []
    for line in lines:
        m = _JAVA_FRAME_RE.search(line)
        if m:
            frames.append(StackFrame(
                language="java",
                file_path=m.group("file"),
                line_no=int(m.group("line")),
                function_name=m.group("func"),
                raw_line=line.rstrip(),
            ))
    return frames


def parse_go_frames(lines: list[str]) -> list[StackFrame]:
    frames: list[StackFrame] = []
    pending_func: str | None = None
    for line in lines:
        func_m = _GO_FUNC_RE.match(line.strip())
        if func_m and not line.startswith("\t"):
            pending_func = func_m.group("func")
            continue
        file_m = _GO_FRAME_RE.match(line)
        if file_m:
            frames.append(StackFrame(
                language="go",
                file_path=file_m.group("file"),
                line_no=int(file_m.group("line")),
                function_name=pending_func,
                raw_line=line.rstrip(),
            ))
            pending_func = None
    return frames


def parse_nodejs_frames(lines: list[str]) -> list[StackFrame]:
    frames: list[StackFrame] = []
    for line in lines:
        m = _NODE_FRAME_RE.search(line)
        if not m:
            continue
        file_path = m.group("file") or m.group("file2") or ""
        line_no_str = m.group("line") or m.group("line2")
        func = m.group("func")
        frames.append(StackFrame(
            language="nodejs",
            file_path=file_path.strip(),
            line_no=int(line_no_str) if line_no_str else None,
            function_name=func.strip() if func else None,
            raw_line=line.rstrip(),
        ))
    return frames


def parse_frames(lines: list[str], language: str) -> list[StackFrame]:
    """Dispatch to the right parser based on detected language."""
    parsers = {
        "python": parse_python_frames,
        "java":   parse_java_frames,
        "go":     parse_go_frames,
        "nodejs": parse_nodejs_frames,
    }
    parser = parsers.get(language.lower())
    if parser is None:
        # Try all parsers and merge results — useful for "unknown" language
        all_frames: list[StackFrame] = []
        for p in parsers.values():
            all_frames.extend(p(lines))
        return all_frames
    return parser(lines)


# --------------------------------------------------------------------------- #
# Repository resolution
# --------------------------------------------------------------------------- #

def resolve_repo_for_container(
    container_name: str,
    container_repo_map: dict[str, str],
    repo_paths: list[str],
) -> Path | None:
    """
    Return the best repo root for a container.

    Priority:
    1. Exact match in container_repo_map
    2. Prefix match in container_repo_map (e.g. "api" matches "api-gateway")
    3. First existing path in repo_paths
    """
    # Exact match
    if container_name in container_repo_map:
        p = Path(container_repo_map[container_name])
        return p if p.is_dir() else None

    # Prefix match (longest first)
    matches = [
        (k, v) for k, v in container_repo_map.items()
        if container_name.startswith(k) or k.startswith(container_name)
    ]
    if matches:
        matches.sort(key=lambda x: len(x[0]), reverse=True)
        p = Path(matches[0][1])
        if p.is_dir():
            return p

    # Fall back: first valid path from repo_paths list
    for rp in repo_paths:
        p = Path(rp)
        if p.is_dir():
            return p

    return None


def find_file_in_repo(repo_root: Path, frame_path: str) -> Path | None:
    """
    Resolve a stack-frame file path to an absolute path within repo_root.

    Handles:
    - Absolute paths that start with repo_root
    - Relative paths (e.g. "pkg/server/handler.go")
    - Basename-only paths (Java: "Service.java")
    - Paths with internal repo prefix stripped by Docker builds
    """
    # Absolute path — only accept if it resolves within repo_root.
    # Early-return in all cases: an absolute frame_path must never fall
    # through to the relative checks below (Path("/repo") / "/abs" = "/abs").
    candidate = Path(frame_path)
    if candidate.is_absolute():
        try:
            candidate.relative_to(repo_root)  # raises ValueError if outside
            if candidate.is_file():
                return candidate
        except ValueError:
            pass
        # Strip anchor and re-root under repo (handles Docker build paths)
        try:
            rel = candidate.relative_to(candidate.anchor)
            rooted = repo_root / rel
            if rooted.is_file():
                return rooted
        except ValueError:
            pass
        return None  # absolute path not resolvable within repo_root

    # Relative path directly from repo root
    rooted = repo_root / frame_path
    if rooted.is_file():
        return rooted

    # Basename search (slow but handles Java short names like "Service.java")
    basename = Path(frame_path).name
    if not basename:
        return None
    for found in repo_root.rglob(basename):
        if found.is_file():
            return found

    return None


# --------------------------------------------------------------------------- #
# Code context extraction
# --------------------------------------------------------------------------- #

def extract_code_context(
    file_path: Path,
    line_no: int,
    context_lines: int = 10,
) -> dict[str, Any]:
    """
    Read `context_lines` lines before and after `line_no` from `file_path`.

    Returns:
        {
            "file": str,
            "error_line": int,
            "before": [(lineno, text), ...],
            "at":     (lineno, text),
            "after":  [(lineno, text), ...],
        }
    """
    try:
        raw_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"error": f"Cannot read {file_path}"}

    total = len(raw_lines)
    idx = line_no - 1  # 0-based

    if idx < 0 or idx >= total:
        return {"error": f"Line {line_no} out of range (file has {total} lines)"}

    start = max(0, idx - context_lines)
    end = min(total, idx + context_lines + 1)

    before = [(n + 1, raw_lines[n]) for n in range(start, idx)]
    at = (line_no, raw_lines[idx])
    after = [(n + 1, raw_lines[n]) for n in range(idx + 1, end)]

    return {
        "file": str(file_path),
        "error_line": line_no,
        "before": before,
        "at": at,
        "after": after,
    }


# --------------------------------------------------------------------------- #
# High-level: analyse frames for a container
# --------------------------------------------------------------------------- #

def analyse_code_context(
    log_lines: list[str],
    language: str,
    container_name: str,
    container_repo_map: dict[str, str],
    repo_paths: list[str],
    context_lines: int = 10,
    max_frames: int = 10,
) -> dict[str, Any]:
    """
    Full pipeline: log lines → stack frames → resolved files → code context.

    Returns a structured dict suitable for MCP tool response.
    """
    repo_root = resolve_repo_for_container(
        container_name, container_repo_map, repo_paths
    )

    frames = parse_frames(log_lines, language)
    frames = frames[:max_frames]

    if not frames:
        return {
            "status": "no_frames",
            "message": "No stack frames detected in the provided log lines.",
            "container": container_name,
            "language": language,
            "repo_root": str(repo_root) if repo_root else None,
        }

    results: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for frame in frames:
        entry: dict[str, Any] = {
            "language": frame.language,
            "raw_frame": frame.raw_line,
            "function": frame.function_name,
            "file_in_log": frame.file_path,
            "line_no": frame.line_no,
        }

        if repo_root and frame.line_no is not None:
            resolved = find_file_in_repo(repo_root, frame.file_path)
            if resolved:
                entry["resolved_file"] = str(resolved)
                entry["code_context"] = extract_code_context(
                    resolved, frame.line_no, context_lines
                )
            else:
                unresolved.append(frame.file_path)
                entry["resolved_file"] = None
                entry["code_context"] = None
        else:
            entry["resolved_file"] = None
            entry["code_context"] = None

        results.append(entry)

    return {
        "status": "success",
        "container": container_name,
        "language": language,
        "repo_root": str(repo_root) if repo_root else None,
        "frames_found": len(frames),
        "frames": results,
        "unresolved_files": unresolved,
        "warnings": (
            ["No repo configured — code context unavailable. "
             "Set REPO_PATHS or CONTAINER_REPO_MAP in your .env file."]
            if repo_root is None else []
        ),
    }
