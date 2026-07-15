"""
Unit tests for tools.py helper functions and tool contract/error branches.

These tests are Docker-free and rely on mocks/fakes to maximize branch coverage
for tools.py without integration overhead.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import docker_log_analyzer.tools as tools


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _patch_tool_exceptions(monkeypatch):
    """Replace SDK exception classes with lightweight test doubles."""
    import docker_log_analyzer.docker as _docker_mod

    class _FakeDockerException(Exception):
        pass

    class _FakeNoSuchContainer(_FakeDockerException):
        pass

    monkeypatch.setattr(tools, "DockerException", _FakeDockerException)
    monkeypatch.setattr(tools, "NoSuchContainer", _FakeNoSuchContainer)
    monkeypatch.setattr(_docker_mod, "DockerException", _FakeDockerException)
    monkeypatch.setattr(_docker_mod, "NoSuchContainer", _FakeNoSuchContainer)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeFinding:
    severity: str
    pattern_name: str
    line_number: int
    timestamp: str
    context_before: str
    context_after: str
    matched_text_redacted: str
    recommendation: str = ""


class _FakePatternDetector:
    def detect_timestamp_format(self, line: str):
        if "ts" in line:
            return ("iso8601", line, 1.0)
        return None

    def detect_language(self, lines):
        return ("python", 0.8123)

    def detect_framework(self, language, lines):
        return None

    def extract_log_levels(self, lines):
        return {"INFO": 2, "ERROR": 1, "CRITICAL": 1}

    def detect_health_checks(self, lines):
        return SimpleNamespace(pattern="/health", frequency_per_minute=2.345)

    def extract_error_patterns(self, lines):
        return [("timeout", 3)]


class _FakeSecretDetector:
    def __init__(self, findings=None):
        self._findings = findings or []

    def scan_logs(self, lines, severity_filter="all"):
        return self._findings

    def get_findings_summary(self, finding_objs):
        return {
            "total_findings": len(finding_objs),
            "by_severity": {"critical": 1, "high": 0, "medium": 0, "low": 0},
        }

    def get_recommendations(self, finding_objs):
        return ["Rotate credentials immediately"] if finding_objs else []


class _FakeContainer:
    def __init__(self, name: str, logs_result="", cid="abcdef1234567890"):
        self.name = name
        self.id = cid
        self._logs_result = logs_result
        self.config = SimpleNamespace(image="img:latest", labels={"a": "b"})
        self.state = SimpleNamespace(status="running")

    def logs(self, **kwargs):
        return self._logs_result


def _mock_client(list_containers=None, inspect_map=None):
    list_containers = list_containers or []
    inspect_map = inspect_map or {}
    client = MagicMock()
    client.system.info.return_value = {"ok": True}
    client.container.list.return_value = list_containers

    def _inspect(name):
        if name not in inspect_map:
            raise tools.NoSuchContainer(name)
        return inspect_map[name]

    client.container.inspect.side_effect = _inspect
    return client


# ---------------------------------------------------------------------------
# Pattern cache helpers
# ---------------------------------------------------------------------------


class TestPatternCacheHelpers:
    def test_cache_path_sanitizes_slashes(self, tmp_path):
        with patch("docker_log_analyzer.tools.PATTERN_CACHE_DIR", tmp_path):
            p = tools._cache_path("ns/service")
        assert p.name == "ns_service.json"

    def test_read_cache_missing_returns_none(self, tmp_path):
        with patch("docker_log_analyzer.tools.PATTERN_CACHE_DIR", tmp_path):
            assert tools._read_cache("missing") is None

    def test_write_and_read_cache_round_trip(self, tmp_path):
        payload = {"status": "ok", "n": 1}
        with patch("docker_log_analyzer.tools.PATTERN_CACHE_DIR", tmp_path):
            tools._write_cache("svc", payload)
            loaded = tools._read_cache("svc")
        assert loaded == payload


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------


class TestDockerHelpers:
    def test_docker_client_success(self):
        fake = _mock_client()
        with patch("docker_log_analyzer.docker.DockerClient", return_value=fake):
            got = tools._docker_client()
        assert got is fake

    def test_docker_client_failure_raises_runtime_error(self):
        with patch(
            "docker_log_analyzer.docker.DockerClient",
            side_effect=tools.DockerException("down"),
        ):
            with pytest.raises(RuntimeError, match="Cannot connect to Docker daemon"):
                tools._docker_client()

    def test_compose_client_uses_compose_file(self):
        fake = _mock_client()
        with patch("docker_log_analyzer.docker.DockerClient", return_value=fake) as dc:
            tools._compose_client()
        assert "compose_files" in dc.call_args.kwargs

    def test_fetch_logs_decodes_bytes(self):
        c = _FakeContainer("svc", logs_result=b"a\nb")
        assert tools._fetch_logs(c, tail=10) == ["a", "b"]

    def test_fetch_logs_handles_exception(self):
        c = MagicMock()
        c.logs.side_effect = tools.DockerException("boom")
        assert tools._fetch_logs(c, tail=1) == []

    def test_fetch_logs_window_decodes_bytes(self):
        c = _FakeContainer("svc", logs_result=b"x\ny")
        now = datetime.now(timezone.utc)
        assert tools._fetch_logs_window(c, now - timedelta(minutes=1), now) == ["x", "y"]

    def test_fetch_logs_window_handles_exception(self):
        c = MagicMock()
        c.logs.side_effect = tools.DockerException("boom")
        now = datetime.now(timezone.utc)
        assert tools._fetch_logs_window(c, now - timedelta(minutes=1), now) == []

    def test_container_name_strips_leading_slash(self):
        c = SimpleNamespace(name="/svc")
        assert tools._container_name(c) == "svc"


# ---------------------------------------------------------------------------
# Time/cache helpers
# ---------------------------------------------------------------------------


class TestTimeAndCacheHelpers:
    def test_parse_iso_none_returns_now(self):
        before = datetime.now(timezone.utc)
        parsed = tools._parse_iso(None)
        after = datetime.now(timezone.utc)
        assert before <= parsed <= after

    def test_parse_iso_empty_string_returns_now(self):
        before = datetime.now(timezone.utc)
        parsed = tools._parse_iso("")
        after = datetime.now(timezone.utc)
        assert before <= parsed <= after

    def test_parse_iso_z_suffix(self):
        parsed = tools._parse_iso("2026-03-04T10:00:00Z")
        assert parsed.year == 2026
        assert parsed.hour == 10
        assert parsed.tzinfo is not None

    def test_parse_iso_with_offset(self):
        parsed = tools._parse_iso("2026-03-04T10:00:00+00:00")
        assert parsed.year == 2026
        assert parsed.hour == 10

    def test_fetch_logs_with_cache_hit(self):
        c = _FakeContainer("svc", logs_result="fresh")
        now = datetime.now(timezone.utc)
        with patch("docker_log_analyzer.docker.read_cached_logs_for_window", return_value=["cached"]):
            logs, was_cached = tools._fetch_logs_with_cache(c, "svc", now, now)
        assert logs == ["cached"]
        assert was_cached is True

    def test_fetch_logs_with_cache_miss(self):
        c = _FakeContainer("svc", logs_result="")
        now = datetime.now(timezone.utc)
        with patch("docker_log_analyzer.docker.read_cached_logs_for_window", return_value=None), patch(
            "docker_log_analyzer.docker._fetch_logs_window", return_value=["fresh"]
        ):
            logs, was_cached = tools._fetch_logs_with_cache(c, "svc", now, now)
        assert logs == ["fresh"]
        assert was_cached is False


# ---------------------------------------------------------------------------
# tool_list_containers / analyze_patterns / analyze_error_spikes
# ---------------------------------------------------------------------------


class TestCoreToolsSync:
    def test_tool_list_containers_docker_error(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("nope")):
            out = tools.tool_list_containers()
        assert out["status"] == "error"

    def test_tool_list_containers_success(self):
        containers = [_FakeContainer("/svc-a"), _FakeContainer("/svc-b")]
        client = _mock_client(list_containers=containers)
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_list_containers()
        assert out["status"] == "success"
        assert out["count"] == 2

    def test_tool_analyze_patterns_docker_error(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("x")):
            out = tools.tool_analyze_patterns()
        assert out["status"] == "error"

    def test_tool_analyze_patterns_no_running_containers(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_analyze_patterns()
        assert out["status"] == "success"
        assert out["results"] == {}

    def test_tool_analyze_patterns_cache_hit_short_circuit(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools._read_cache", return_value={"cached": True}
        ):
            out = tools.tool_analyze_patterns()
        assert out["results"]["svc"]["cached"] is True

    def test_tool_analyze_patterns_no_logs_branch(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools._read_cache", return_value=None
        ), patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], False)):
            out = tools.tool_analyze_patterns(force_refresh=True)
        assert out["results"]["svc"]["status"] == "no_logs"

    def test_tool_analyze_patterns_success_writes_cache(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools.PatternDetector", return_value=_FakePatternDetector()
        ), patch("docker_log_analyzer.tools._read_cache", return_value=None), patch(
            "docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["ts line"], True)
        ), patch("docker_log_analyzer.tools._write_cache") as write_cache:
            out = tools.tool_analyze_patterns(force_refresh=True)
        assert out["status"] == "success"
        assert out["results"]["svc"]["logs_cache_hit"] is True
        write_cache.assert_called_once()

    def test_tool_analyze_error_spikes_not_found(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_analyze_error_spikes(container_name="missing")
        assert out["status"] == "error"

    def test_tool_analyze_error_spikes_warning_for_no_timestamps(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c], inspect_map={"svc": c})
        lines = ["no ts here", "still no ts"]
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(lines, False)
        ), patch("docker_log_analyzer.tools.detect_spikes", return_value=[]):
            out = tools.tool_analyze_error_spikes()
        assert out["status"] == "success"
        assert out["warnings"]


# ---------------------------------------------------------------------------
# tool_analyze_correlations / lifecycle / sync
# ---------------------------------------------------------------------------


class TestCorrelateLifecycleAndSync:
    def test_tool_analyze_correlations_docker_error(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("x")):
            out = tools.tool_analyze_correlations()
        assert out["status"] == "error"

    def test_tool_analyze_correlations_need_two(self):
        client = _mock_client(list_containers=[_FakeContainer("/one")])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_analyze_correlations()
        assert out["status"] == "success"
        assert out["correlations"] == []

    def test_tool_analyze_correlations_cache_hit(self):
        client = _mock_client(list_containers=[_FakeContainer("/a"), _FakeContainer("/b")])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools._read_correlation_cache", return_value={"status": "success", "correlations": []}
        ):
            out = tools.tool_analyze_correlations()
        assert out["correlation_cache_hit"] is True

    def test_tool_analyze_correlations_filters_names(self):
        client = _mock_client(list_containers=[_FakeContainer("/a"), _FakeContainer("/b")])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_analyze_correlations(container_names=["only-this"])
        assert out["correlations"] == []

    def test_tool_analyze_correlations_populates_cache_hits_for_nonempty_logs(self):
        a = _FakeContainer("/a")
        b = _FakeContainer("/b")
        client = _mock_client(list_containers=[a, b])

        def _fetch(_c, name, *_args, **_kwargs):
            if name == "a":
                return (["line"], True)
            return ([], False)

        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools._read_correlation_cache", return_value=None
        ), patch("docker_log_analyzer.tools._fetch_logs_with_cache", side_effect=_fetch), patch(
            "docker_log_analyzer.tools.correlate", return_value=[]
        ), patch("docker_log_analyzer.tools._write_correlation_cache"):
            out = tools.tool_analyze_correlations()
        assert out["cache_hits"] == {"a": True}

    def test_start_test_containers_missing_compose(self, tmp_path):
        with patch("docker_log_analyzer.tools.COMPOSE_FILE", tmp_path / "missing.yml"):
            out = tools.tool_start_test_containers()
        assert out["status"] == "error"

    def test_start_test_containers_docker_exception(self, tmp_path):
        compose_client = MagicMock()
        compose_client.compose.up.side_effect = tools.DockerException("boom")
        compose_file = tmp_path / "docker-compose.test.yml"
        compose_file.write_text("services: {}\n")
        with patch("docker_log_analyzer.tools.COMPOSE_FILE", compose_file), patch(
            "docker_log_analyzer.tools._compose_client", return_value=compose_client
        ):
            out = tools.tool_start_test_containers(rebuild=True)
        assert out["status"] == "error"

    def test_stop_test_containers_missing_compose(self, tmp_path):
        with patch("docker_log_analyzer.tools.COMPOSE_FILE", tmp_path / "missing.yml"):
            out = tools.tool_stop_test_containers()
        assert out["status"] == "error"

    def test_stop_test_containers_docker_exception(self, tmp_path):
        compose_client = MagicMock()
        compose_client.compose.down.side_effect = tools.DockerException("boom")
        compose_file = tmp_path / "docker-compose.test.yml"
        compose_file.write_text("services: {}\n")
        with patch("docker_log_analyzer.tools.COMPOSE_FILE", compose_file), patch(
            "docker_log_analyzer.tools._compose_client", return_value=compose_client
        ):
            out = tools.tool_stop_test_containers()
        assert out["status"] == "error"

    def test_tool_sync_docker_logs_since_after_until(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_sync_docker_logs(since="2026-03-07T10:00:00Z", until="2026-03-06T10:00:00Z")
        assert out["status"] == "error"

    def test_tool_sync_docker_logs_not_found(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_sync_docker_logs(container_names=["missing"])
        assert out["status"] == "error"

    def test_tool_sync_docker_logs_no_targets(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_sync_docker_logs()
        assert out["status"] == "success"
        assert "No running containers" in out["message"]

    def test_tool_sync_docker_logs_writes_logs_for_day(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c], inspect_map={"svc": c})
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools._fetch_logs_window", return_value=["l1", "l2"]
        ), patch("docker_log_analyzer.tools.write_cached_logs_for_date") as write_cache:
            out = tools.tool_sync_docker_logs(
                since="2026-03-06T10:00:00Z",
                until="2026-03-06T11:00:00Z",
            )
        assert out["status"] == "success"
        write_cache.assert_called()


# ---------------------------------------------------------------------------
# sync tools (formerly async)
# ---------------------------------------------------------------------------


class TestAsyncTools:
    def test_capture_logs_docker_error(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("x")):
            out = tools.tool_capture_logs(duration_seconds=0)
        assert out["status"] == "error"

    def test_capture_logs_no_targets(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_capture_logs(duration_seconds=0)
        assert out["status"] == "success"
        assert "No running containers" in out["message"]

    def test_capture_logs_success_summary(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c], inspect_map={"svc": c})
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools.time.sleep"
        ), patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["x"], False)), patch(
            "docker_log_analyzer.tools.PatternDetector", return_value=_FakePatternDetector()
        ), patch("docker_log_analyzer.tools.detect_spikes", return_value=[{"container": "svc"}]), patch(
            "docker_log_analyzer.tools.correlate", return_value=[]
        ):
            out = tools.tool_capture_logs(duration_seconds=0)
        assert out["status"] == "success"
        assert out["summary"]["total_log_lines"] == 1
        assert out["summary"]["spike_count"] == 1

    def test_detect_data_leaks_not_found(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_detect_data_leaks(duration_seconds=0, container_names=["missing"])
        assert out["status"] == "error"

    def test_detect_data_leaks_no_targets(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_detect_data_leaks(duration_seconds=0)
        assert out["status"] == "success"
        assert "No running containers" in out["message"]

    def test_detect_data_leaks_success_transforms_findings(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c], inspect_map={"svc": c})
        finding = _FakeFinding(
            severity="critical",
            pattern_name="api_key",
            line_number=1,
            timestamp="2026-03-06T10:00:00Z",
            context_before="a",
            context_after="b",
            matched_text_redacted="sk-****",
        )
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools.time.sleep"
        ), patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["line"], True)), patch(
            "docker_log_analyzer.tools.SecretDetector", return_value=_FakeSecretDetector([finding])
        ):
            out = tools.tool_detect_data_leaks(duration_seconds=0)
        assert out["status"] == "success"
        assert out["findings"][0]["matched_text"] == "sk-****"

    def test_map_service_dependencies_docker_error(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("x")):
            out = tools.tool_map_service_dependencies()
        assert out["status"] == "error"

    def test_map_service_dependencies_no_targets(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_map_service_dependencies()
        assert out["status"] == "success"
        assert out["dependencies"] == {}

    def test_map_service_dependencies_no_logs(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], False)
        ):
            out = tools.tool_map_service_dependencies()
        assert out["status"] == "success"
        assert "No logs found" in out["message"]

    def test_map_service_dependencies_success_with_cascade(self):
        a = _FakeContainer("/a")
        b = _FakeContainer("/b")
        client = _mock_client(list_containers=[a, b], inspect_map={"a": a, "b": b})

        def _fetch(_c, name, *_args, **_kwargs):
            return ([f"{name} log"], False)

        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools._fetch_logs_with_cache", side_effect=_fetch
        ), patch("docker_log_analyzer.tools.build_graph", return_value={"a": []}), patch(
            "docker_log_analyzer.tools.correlate", return_value=[{"container_a": "a", "container_b": "b"}]
        ), patch("docker_log_analyzer.tools.find_cascade_candidates", return_value=[{"from": "a", "to": "b"}]):
            out = tools.tool_map_service_dependencies()
        assert out["status"] == "success"
        assert out["cascade_candidates"] == [{"from": "a", "to": "b"}]


# ---------------------------------------------------------------------------
# tool_analyze_code_context – wrapper-level tests
# ---------------------------------------------------------------------------


_PY_TRACEBACK = [
    "2026-03-07T10:00:00Z ERROR app Traceback (most recent call last):",
    '2026-03-07T10:00:00Z ERROR app   File "app.py", line 42, in handle_request',
    "2026-03-07T10:00:00Z ERROR app ValueError: bad input",
]


class TestToolAnalyzeCodeContextWrapper:
    def test_docker_error_returns_error_status(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("down")):
            out = tools.tool_analyze_code_context("api")
        assert out["status"] == "error"

    def test_container_not_found_returns_error(self):
        client = _mock_client(inspect_map={})
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_analyze_code_context("missing")
        assert out["status"] == "error"
        assert "missing" in out["error"]

    def test_no_logs_returns_no_frames(self):
        c = _FakeContainer("/api", logs_result=b"")
        client = _mock_client(inspect_map={"api": c})
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_analyze_code_context("api")
        assert out["status"] == "no_frames"

    def test_stack_frames_parsed_from_all_lines(self, tmp_path):
        # Verifies that frame lines (which don't match ERROR_PATTERN_RE) reach the parser.
        src = tmp_path / "app.py"
        src.write_text("\n".join(f"line{i}" for i in range(1, 60)))
        logs = "\n".join(_PY_TRACEBACK).encode()
        c = _FakeContainer("/api", logs_result=logs)
        client = _mock_client(inspect_map={"api": c})
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools.settings.container_repo_map", {"api": str(tmp_path)}
        ), patch("docker_log_analyzer.tools.settings.repo_paths", []):
            out = tools.tool_analyze_code_context("api", language="python")
        assert out["status"] == "success"
        assert out["frames_found"] >= 1
        assert out["frames"][0]["file_in_log"] == "app.py"

    def test_language_auto_detected_as_string(self, tmp_path):
        # Regression: detect_language returns (str, float); wrapper must unpack to str.
        src = tmp_path / "app.py"
        src.write_text("\n".join(f"line{i}" for i in range(1, 60)))
        logs = "\n".join(_PY_TRACEBACK).encode()
        c = _FakeContainer("/api", logs_result=logs)
        client = _mock_client(inspect_map={"api": c})
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools.settings.container_repo_map", {"api": str(tmp_path)}
        ), patch("docker_log_analyzer.tools.settings.repo_paths", []):
            out = tools.tool_analyze_code_context("api")  # language=None → auto-detect
        # If detected_lang were a tuple, coderepo.py:147 would raise AttributeError
        assert out["status"] in ("success", "no_frames")


# ---------------------------------------------------------------------------
# tool_analyze_root_causes — return-structure contract
# ---------------------------------------------------------------------------

# Required top-level keys for BOTH return paths (empty containers + normal).
# If you need to add or rename a key, update this set AND the tool together —
# the test is the canonical contract for what callers can rely on.
_ROOT_CAUSES_REQUIRED_KEYS = frozenset({
    "status",
    "root_causes",
    "analysis_inputs",
    "cache_hits",
    "parameters",
})

_ANALYSIS_INPUTS_REQUIRED_KEYS = frozenset({
    "containers_analyzed",
    "spikes_detected",
    "cascade_candidates",
    "dependency_edges",
})

_PARAMETERS_REQUIRED_KEYS = frozenset({
    "containers",
    "tail",
    "time_window_seconds",
    "include_transitive",
})


class TestToolAnalyzeRootCausesContract:
    """
    These tests guard the return-structure contract of tool_analyze_root_causes.
    They do NOT test correctness of scoring — only that callers can rely on the
    documented key set across every return path.
    """

    def _make_client_with_containers(self, containers):
        client = MagicMock()
        client.system.info.return_value = {"ok": True}
        client.container.list.return_value = containers
        client.container.inspect.side_effect = lambda name: next(
            (c for c in containers if c.name.lstrip("/") == name), None
        )
        return client

    def test_empty_containers_has_required_keys(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_analyze_root_causes()
        assert _ROOT_CAUSES_REQUIRED_KEYS <= out.keys(), (
            f"Missing keys: {_ROOT_CAUSES_REQUIRED_KEYS - out.keys()}"
        )
        assert _ANALYSIS_INPUTS_REQUIRED_KEYS <= out["analysis_inputs"].keys()
        assert _PARAMETERS_REQUIRED_KEYS <= out["parameters"].keys()
        assert out["status"] == "success"
        assert out["root_causes"] == []

    def test_normal_path_has_required_keys(self):
        c = _FakeContainer("/svc-a", logs_result=b"2024-01-01T00:00:00Z ERROR boom\n")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_analyze_root_causes()
        assert _ROOT_CAUSES_REQUIRED_KEYS <= out.keys(), (
            f"Missing keys: {_ROOT_CAUSES_REQUIRED_KEYS - out.keys()}"
        )
        assert _ANALYSIS_INPUTS_REQUIRED_KEYS <= out["analysis_inputs"].keys()
        assert _PARAMETERS_REQUIRED_KEYS <= out["parameters"].keys()
        assert out["status"] == "success"

    def test_docker_error_returns_error_status(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("down")):
            out = tools.tool_analyze_root_causes()
        assert out["status"] == "error"
        assert "error" in out

    def test_parameters_reflect_call_arguments(self):
        c = _FakeContainer("/svc-a", logs_result=b"")
        client = _mock_client(list_containers=[c], inspect_map={"svc-a": c})
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_analyze_root_causes(
                containers=["svc-a"],
                tail=100,
                time_window_seconds=300,
                include_transitive=True,
            )
        p = out["parameters"]
        assert p["containers"] == ["svc-a"]
        assert p["tail"] == 100
        assert p["time_window_seconds"] == 300
        assert p["include_transitive"] is True
        assert isinstance(out.get("language", ""), str)


# ---------------------------------------------------------------------------
# tool_plan_investigation — return-structure contract
# ---------------------------------------------------------------------------

# Top-level keys present on every successful call.
# If you add or rename a key, update this set AND the tool/planner together.
_PLAN_REQUIRED_KEYS = frozenset({
    "status",
    "signals_detected",
    "focus",
    "containers_in_scope",
    "step_count",
    "plan",
    "plan_file",
})

# Each step dict must always carry at least these three keys.
# 'target' and 'parameters' are optional (omitted when not applicable).
_STEP_REQUIRED_KEYS = frozenset({"step", "action", "reason"})


class TestToolPlanInvestigationContract:
    """
    Guards the return-structure contract of tool_plan_investigation.
    Tests run without Docker — generate_plan is pure (no I/O beyond file write).
    """

    def test_success_path_has_required_keys(self, tmp_path):
        with patch("docker_log_analyzer.tools.generate_plan",
                   wraps=lambda **kw: __import__(
                       "docker_log_analyzer.investigation_planner",
                       fromlist=["generate_plan"]
                   ).generate_plan(**kw, plans_dir=tmp_path)):
            out = tools.tool_plan_investigation(["payment-service returning 500s"])
        assert _PLAN_REQUIRED_KEYS <= out.keys(), (
            f"Missing keys: {_PLAN_REQUIRED_KEYS - out.keys()}"
        )

    def test_every_step_has_required_keys(self, tmp_path):
        with patch("docker_log_analyzer.tools.generate_plan",
                   wraps=lambda **kw: __import__(
                       "docker_log_analyzer.investigation_planner",
                       fromlist=["generate_plan"]
                   ).generate_plan(**kw, plans_dir=tmp_path)):
            out = tools.tool_plan_investigation(["high error rate"])
        for step in out["plan"]:
            assert _STEP_REQUIRED_KEYS <= step.keys(), (
                f"Step missing keys: {_STEP_REQUIRED_KEYS - step.keys()}"
            )

    def test_step_count_matches_plan_length(self, tmp_path):
        with patch("docker_log_analyzer.tools.generate_plan",
                   wraps=lambda **kw: __import__(
                       "docker_log_analyzer.investigation_planner",
                       fromlist=["generate_plan"]
                   ).generate_plan(**kw, plans_dir=tmp_path)):
            out = tools.tool_plan_investigation(["crash in api"])
        assert out["step_count"] == len(out["plan"])

    def test_focus_and_containers_reflected_in_output(self, tmp_path):
        with patch("docker_log_analyzer.tools.generate_plan",
                   wraps=lambda **kw: __import__(
                       "docker_log_analyzer.investigation_planner",
                       fromlist=["generate_plan"]
                   ).generate_plan(**kw, plans_dir=tmp_path)):
            out = tools.tool_plan_investigation(
                ["leaked credentials"], containers=["api"], focus="security"
            )
        assert out["focus"] == "security"
        assert "api" in out["containers_in_scope"]
        assert out["status"] == "success"


# ---------------------------------------------------------------------------
# tool_detect_data_leaks — return-structure contract
# ---------------------------------------------------------------------------

# Top-level keys present on every successful scan (with ≥1 container).
# The "no running containers" path intentionally returns only status+message
# and is excluded from this contract (it is a no-op sentinel, not a scan).
_DATA_LEAKS_REQUIRED_KEYS = frozenset({
    "status",
    "scan_window",
    "containers_scanned",
    "cache_hits",
    "findings",
    "summary",
    "per_container",
    "recommendations",
})

_SCAN_WINDOW_REQUIRED_KEYS = frozenset({"start", "end", "duration_seconds"})

# Keys present on every finding dict emitted by the tool.
_FINDING_REQUIRED_KEYS = frozenset({
    "container",
    "severity",
    "pattern_name",
    "matched_text",
    "line_number",
    "timestamp",
    "context_before",
    "context_after",
})


class TestToolDetectDataLeaksContract:
    """Guards the return-structure contract of tool_detect_data_leaks."""

    def _base_patches(self, client, logs=None, findings=None):
        from unittest.mock import patch
        return [
            patch("docker_log_analyzer.tools._docker_client", return_value=client),
            patch("docker_log_analyzer.tools.time.sleep"),
            patch("docker_log_analyzer.tools._fetch_logs_with_cache",
                  return_value=(logs or ["log line"], False)),
            patch("docker_log_analyzer.tools.SecretDetector",
                  return_value=_FakeSecretDetector(findings or [])),
        ]

    def test_success_path_has_required_keys(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools.time.sleep"), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["line"], False)), \
             patch("docker_log_analyzer.tools.SecretDetector", return_value=_FakeSecretDetector()):
            out = tools.tool_detect_data_leaks(duration_seconds=0)
        assert _DATA_LEAKS_REQUIRED_KEYS <= out.keys(), (
            f"Missing keys: {_DATA_LEAKS_REQUIRED_KEYS - out.keys()}"
        )

    def test_scan_window_has_required_keys(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools.time.sleep"), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["line"], False)), \
             patch("docker_log_analyzer.tools.SecretDetector", return_value=_FakeSecretDetector()):
            out = tools.tool_detect_data_leaks(duration_seconds=0)
        assert _SCAN_WINDOW_REQUIRED_KEYS <= out["scan_window"].keys(), (
            f"Missing scan_window keys: {_SCAN_WINDOW_REQUIRED_KEYS - out['scan_window'].keys()}"
        )

    def test_each_finding_has_required_keys(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        finding = _FakeFinding(
            severity="critical", pattern_name="AWS Access Key ID",
            line_number=1, timestamp="2026-01-01T00:00:00Z",
            context_before="before", context_after="after",
            matched_text_redacted="AK**",
        )
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools.time.sleep"), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["line"], False)), \
             patch("docker_log_analyzer.tools.SecretDetector", return_value=_FakeSecretDetector([finding])):
            out = tools.tool_detect_data_leaks(duration_seconds=0)
        assert len(out["findings"]) == 1
        assert _FINDING_REQUIRED_KEYS <= out["findings"][0].keys(), (
            f"Missing finding keys: {_FINDING_REQUIRED_KEYS - out['findings'][0].keys()}"
        )

    def test_docker_error_returns_error_status(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("down")):
            out = tools.tool_detect_data_leaks(duration_seconds=0)
        assert out["status"] == "error"
        assert "error" in out


# ---------------------------------------------------------------------------
# tool_trace_request_flow – return-structure contract tests
# ---------------------------------------------------------------------------

_TRACE_FLOW_REQUIRED_KEYS = frozenset({
    "status",
    "timelines",
    "request_count",
    "containers_scanned",
    "cache_hits",
    "parameters",
})

_TRACE_FLOW_PARAMETERS_REQUIRED_KEYS = frozenset({
    "tail",
    "min_events",
    "max_requests",
    "trace_window_seconds",
})

_TIMELINE_REQUIRED_KEYS = frozenset({
    "id_value",
    "id_patterns",
    "containers",
    "event_count",
    "first_seen",
    "last_seen",
    "duration_ms",
    "events",
})

_TIMELINE_EVENT_REQUIRED_KEYS = frozenset({
    "container",
    "timestamp",
    "pattern_name",
    "message",
})


class TestToolTraceRequestFlowContract:
    """Guards the return-structure contract of tool_trace_request_flow.

    All tests are Docker-free; request_tracer module functions are mocked or
    exercised via lightweight fakes.
    """

    def test_docker_error_returns_error_status(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("no docker")):
            out = tools.tool_trace_request_flow()
        assert out["status"] == "error"
        assert "error" in out

    def test_container_not_found_returns_error_status(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            client.container.inspect = MagicMock(side_effect=tools.NoSuchContainer("x"))
            out = tools.tool_trace_request_flow(container_names=["missing"])
        assert out["status"] == "error"
        assert "error" in out

    def test_no_running_containers_returns_success(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_trace_request_flow()
        assert out["status"] == "success"
        assert out["timelines"] == []
        assert out["request_count"] == 0

    def _fake_timeline(self, id_value="abc-123", event_count=3, events=None):
        return {
            "id_value": id_value,
            "id_patterns": ["request_id"],
            "containers": ["svc"],
            "event_count": event_count,
            "first_seen": "2026-03-13T10:00:00.000Z",
            "last_seen": "2026-03-13T10:00:01.000Z",
            "duration_ms": 1000.0,
            "events": events or [],
        }

    def test_success_path_has_required_top_level_keys(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], False)), \
             patch("docker_log_analyzer.tools.extract_ids", return_value=[]), \
             patch("docker_log_analyzer.tools.cross_container_timelines", return_value=[]):
            out = tools.tool_trace_request_flow()
        assert _TRACE_FLOW_REQUIRED_KEYS <= out.keys(), (
            f"Missing keys: {_TRACE_FLOW_REQUIRED_KEYS - out.keys()}"
        )

    def test_parameters_sub_dict_has_required_keys(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], False)), \
             patch("docker_log_analyzer.tools.extract_ids", return_value=[]), \
             patch("docker_log_analyzer.tools.cross_container_timelines", return_value=[]):
            out = tools.tool_trace_request_flow()
        assert _TRACE_FLOW_PARAMETERS_REQUIRED_KEYS <= out["parameters"].keys(), (
            f"Missing parameters keys: {_TRACE_FLOW_PARAMETERS_REQUIRED_KEYS - out['parameters'].keys()}"
        )

    def test_each_timeline_has_required_keys(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["line"], False)), \
             patch("docker_log_analyzer.tools.extract_ids", return_value=[]), \
             patch("docker_log_analyzer.tools.cross_container_timelines", return_value=[self._fake_timeline()]):
            out = tools.tool_trace_request_flow(min_events=1)
        assert len(out["timelines"]) == 1
        assert _TIMELINE_REQUIRED_KEYS <= out["timelines"][0].keys(), (
            f"Missing timeline keys: {_TIMELINE_REQUIRED_KEYS - out['timelines'][0].keys()}"
        )

    def test_each_timeline_event_has_required_keys(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        fake_event = {
            "container": "svc",
            "timestamp": "2026-03-13T10:00:00.000Z",
            "pattern_name": "request_id",
            "message": "GET /api/order request_id=abc-123",
        }
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["line"], False)), \
             patch("docker_log_analyzer.tools.extract_ids", return_value=[]), \
             patch("docker_log_analyzer.tools.cross_container_timelines",
                   return_value=[self._fake_timeline(events=[fake_event])]):
            out = tools.tool_trace_request_flow(min_events=1)
        assert len(out["timelines"][0]["events"]) == 1
        assert _TIMELINE_EVENT_REQUIRED_KEYS <= out["timelines"][0]["events"][0].keys(), (
            f"Missing event keys: {_TIMELINE_EVENT_REQUIRED_KEYS - out['timelines'][0]['events'][0].keys()}"
        )

    def test_min_events_filters_single_occurrence_requests(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["line"], False)), \
             patch("docker_log_analyzer.tools.extract_ids", return_value=[]), \
             patch("docker_log_analyzer.tools.cross_container_timelines",
                   return_value=[self._fake_timeline(event_count=1)]):
            out = tools.tool_trace_request_flow(min_events=2)
        assert out["timelines"] == []
        assert out["request_count"] == 0

    def test_max_requests_truncates_output(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        timelines = [self._fake_timeline(id_value=f"req-{i}") for i in range(10)]
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["line"], False)), \
             patch("docker_log_analyzer.tools.extract_ids", return_value=[]), \
             patch("docker_log_analyzer.tools.cross_container_timelines", return_value=timelines):
            out = tools.tool_trace_request_flow(min_events=1, max_requests=3)
        assert len(out["timelines"]) == 3
        assert out["request_count"] == 10

    def test_cache_hits_populated_for_nonempty_container_list(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], True)), \
             patch("docker_log_analyzer.tools.extract_ids", return_value=[]), \
             patch("docker_log_analyzer.tools.cross_container_timelines", return_value=[]):
            out = tools.tool_trace_request_flow()
        assert "svc" in out["cache_hits"]
        assert out["cache_hits"]["svc"] is True

    def test_tail_limits_lines_passed_to_extract_ids(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        lines = ["line-1", "line-2", "line-3", "line-4", "line-5"]
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(lines, False)), \
             patch("docker_log_analyzer.tools.extract_ids", return_value=[]) as extract_mock, \
             patch("docker_log_analyzer.tools.cross_container_timelines", return_value=[]):
            tools.tool_trace_request_flow(tail=2, min_events=1)
        passed_lines = extract_mock.call_args.args[0]
        assert passed_lines == ["line-4", "line-5"]


# ---------------------------------------------------------------------------
# tool_classify_errors – return-structure contract tests
# ---------------------------------------------------------------------------

_CLASSIFY_ERRORS_REQUIRED_KEYS = frozenset({
    "status",
    "containers",
    "summary",
    "containers_scanned",
    "cache_hits",
    "parameters",
})

_CLASSIFY_ERRORS_SUMMARY_REQUIRED_KEYS = frozenset({
    "total_errors",
    "dominant_category",
})

_CLASSIFY_ERRORS_PARAMS_REQUIRED_KEYS = frozenset({
    "tail",
    "categories",
})

_CATEGORY_STAT_REQUIRED_KEYS = frozenset({
    "count",
    "percentage",
    "first_seen",
    "last_seen",
    "samples",
    "recommendation",
})


class TestToolClassifyErrorsContract:
    """Guards the return-structure contract of tool_classify_errors."""

    def test_docker_error_returns_error_status(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("down")):
            out = tools.tool_classify_errors()
        assert out["status"] == "error"
        assert "error" in out

    def test_container_not_found_returns_error_status(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            client.container.inspect = MagicMock(side_effect=tools.NoSuchContainer("x"))
            out = tools.tool_classify_errors(container_names=["missing"])
        assert out["status"] == "error"
        assert "error" in out

    def test_invalid_category_filter_returns_error(self):
        client = _mock_client(list_containers=[_FakeContainer("/svc")])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_classify_errors(categories=["bogus_cat"])
        assert out["status"] == "error"
        assert "Invalid categories" in out["error"]

    def test_no_running_containers_returns_success(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_classify_errors()
        assert out["status"] == "success"
        assert out["containers"] == {}
        assert out["summary"]["total_errors"] == 0

    def test_success_path_has_required_top_level_keys(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], False)):
            out = tools.tool_classify_errors()
        assert _CLASSIFY_ERRORS_REQUIRED_KEYS <= out.keys(), (
            f"Missing keys: {_CLASSIFY_ERRORS_REQUIRED_KEYS - out.keys()}"
        )

    def test_summary_has_required_keys(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], False)):
            out = tools.tool_classify_errors()
        assert _CLASSIFY_ERRORS_SUMMARY_REQUIRED_KEYS <= out["summary"].keys(), (
            f"Missing summary keys: {_CLASSIFY_ERRORS_SUMMARY_REQUIRED_KEYS - out['summary'].keys()}"
        )

    def test_parameters_has_required_keys(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], False)):
            out = tools.tool_classify_errors()
        assert _CLASSIFY_ERRORS_PARAMS_REQUIRED_KEYS <= out["parameters"].keys(), (
            f"Missing parameter keys: {_CLASSIFY_ERRORS_PARAMS_REQUIRED_KEYS - out['parameters'].keys()}"
        )

    def test_per_container_stats_with_errors(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        error_lines = [
            "2026-03-13T10:00:00Z ERROR deadlock detected",
            "2026-03-13T10:00:01Z ERROR read timeout",
        ]
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(error_lines, False)):
            out = tools.tool_classify_errors()
        container_stats = out["containers"]["svc"]
        assert container_stats["total_errors"] == 2
        assert container_stats["dominant_category"] is not None
        # Verify at least one category has the right shape.
        for cat_name, cat_stat in container_stats["categories"].items():
            assert _CATEGORY_STAT_REQUIRED_KEYS <= cat_stat.keys(), (
                f"Missing category stat keys for '{cat_name}': {_CATEGORY_STAT_REQUIRED_KEYS - cat_stat.keys()}"
            )

    def test_category_filter_applied(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        error_lines = [
            "2026-03-13T10:00:00Z ERROR deadlock detected",
            "2026-03-13T10:00:01Z ERROR read timeout",
        ]
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(error_lines, False)):
            out = tools.tool_classify_errors(categories=["database"])
        container_stats = out["containers"]["svc"]
        # Only "database" category should appear.
        for cat_name in container_stats["categories"]:
            assert cat_name == "database"

    def test_cache_hits_populated(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], True)):
            out = tools.tool_classify_errors()
        assert "svc" in out["cache_hits"]


# ---------------------------------------------------------------------------
# tool_extract_apis_and_queries – return-structure contract tests
# ---------------------------------------------------------------------------

_EXTRACT_REQUIRED_KEYS = frozenset({
    "status",
    "api_calls",
    "queries",
    "summary",
    "containers_scanned",
    "cache_hits",
})

_EXTRACT_SUMMARY_REQUIRED_KEYS = frozenset({
    "endpoint_counts",
    "query_counts",
    "detected_language",
})


class TestToolExtractApisAndQueriesContract:
    """Guards the return-structure contract of tool_extract_apis_and_queries.

    All tests are Docker-free; PatternDetector.detect_language is mocked or
    exercised via lightweight fakes, matching TestToolTraceRequestFlowContract's style.
    """

    def test_docker_error_returns_error_status(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("no docker")):
            out = tools.tool_extract_apis_and_queries()
        assert out["status"] == "error"
        assert "error" in out

    def test_container_not_found_returns_error_status(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_extract_apis_and_queries(container_names=["missing"])
        assert out["status"] == "error"
        assert "error" in out

    def test_no_running_containers_returns_success(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_extract_apis_and_queries()
        assert out["status"] == "success"
        assert out["api_calls"] == []
        assert out["queries"] == []
        assert _EXTRACT_SUMMARY_REQUIRED_KEYS <= out["summary"].keys()

    def test_success_path_has_required_top_level_keys(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], False)):
            out = tools.tool_extract_apis_and_queries()
        assert _EXTRACT_REQUIRED_KEYS <= out.keys(), (
            f"Missing keys: {_EXTRACT_REQUIRED_KEYS - out.keys()}"
        )

    def test_no_logs_records_unknown_language_and_skips_extraction(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], False)):
            out = tools.tool_extract_apis_and_queries()
        assert out["summary"]["detected_language"]["svc"] == "unknown"
        assert out["api_calls"] == []
        assert out["queries"] == []

    def test_java_container_uses_detected_language_for_extraction(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        lines = ["Mapped [{GET [/api/pets/1]}] to public ...Owner"]
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(lines, False)), \
             patch.object(tools.PatternDetector, "detect_language", return_value=("java", 0.9)):
            out = tools.tool_extract_apis_and_queries()
        assert out["summary"]["detected_language"]["svc"] == "java"
        assert len(out["api_calls"]) == 1
        assert out["api_calls"][0]["method"] == "GET"
        assert out["api_calls"][0]["path"] == "/api/pets/1"

    def test_endpoint_and_query_counts_aggregated(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        lines = [
            '"GET /api/pets/1 HTTP/1.1" 200',
            '"GET /api/pets/1 HTTP/1.1" 200',
            "SELECT * FROM pets",
        ]
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(lines, False)), \
             patch.object(tools.PatternDetector, "detect_language", return_value=("unknown", 0.0)):
            out = tools.tool_extract_apis_and_queries()
        assert out["summary"]["endpoint_counts"]["GET /api/pets/1"] == 2
        assert out["summary"]["query_counts"]["SELECT"] == 1

    def test_cache_hits_populated(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=([], True)):
            out = tools.tool_extract_apis_and_queries()
        assert out["cache_hits"]["svc"] is True
        assert out["cache_hits"]["svc"] is True


class TestLogLookbackWindowRespected:
    """settings.log_lookback_minutes (set by WindowScreen in the TUI) must be
    re-read at call time by every time-windowed tool, not captured once at
    import — otherwise picking a different window in the TUI would silently
    have no effect on what gets fetched. See task:a638ca4b / [[docker-log-
    analyzer-log-window-must-be-live]]."""

    def test_tool_sync_docker_logs_uses_current_log_lookback_minutes(self, monkeypatch):
        monkeypatch.setattr(tools.settings, "log_lookback_minutes", 7)
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])

        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_sync_docker_logs()

        assert out["status"] == "success"
        since = datetime.fromisoformat(out["time_window"]["since"])
        until = datetime.fromisoformat(out["time_window"]["until"])
        assert (until - since).total_seconds() == pytest.approx(7 * 60, abs=1)

        monkeypatch.setattr(tools.settings, "log_lookback_minutes", 45)
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_sync_docker_logs()
        since = datetime.fromisoformat(out["time_window"]["since"])
        until = datetime.fromisoformat(out["time_window"]["until"])
        assert (until - since).total_seconds() == pytest.approx(45 * 60, abs=1)

    def test_tool_sync_docker_logs_explicit_since_overrides_window(self, monkeypatch):
        """An explicit `since` argument (not the TUI path, but used by callers
        that want a specific window) must win over settings.log_lookback_minutes."""
        monkeypatch.setattr(tools.settings, "log_lookback_minutes", 7)
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])

        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_sync_docker_logs(since="2020-01-01T00:00:00Z")

        assert datetime.fromisoformat(out["time_window"]["since"]).year == 2020

    def test_tool_analyze_patterns_uses_current_log_lookback_minutes(self, monkeypatch):
        monkeypatch.setattr(tools.settings, "log_lookback_minutes", 12)
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c])
        captured = {}

        def _fake_fetch_with_cache(container, name, since, until, **kwargs):
            captured["since"] = since
            captured["until"] = until
            return [], False

        with patch("docker_log_analyzer.tools._docker_client", return_value=client), \
             patch("docker_log_analyzer.tools._read_cache", return_value=None), \
             patch("docker_log_analyzer.tools._fetch_logs_with_cache", side_effect=_fake_fetch_with_cache):
            out = tools.tool_analyze_patterns(force_refresh=True)

        assert out["status"] == "success"
        elapsed = (captured["until"] - captured["since"]).total_seconds()
        assert elapsed == pytest.approx(12 * 60, abs=1)
