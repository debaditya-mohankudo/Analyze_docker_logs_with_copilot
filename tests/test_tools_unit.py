"""
Unit tests for tools.py helper functions and tool contract/error branches.

These tests are Docker-free and rely on mocks/fakes to maximize branch coverage
for tools.py without integration overhead.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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


class _FakePatternDetector:
    def detect_timestamp_format(self, line: str):
        if "ts" in line:
            return ("iso8601", line, 1.0)
        return None

    def detect_language(self, lines):
        return ("python", 0.8123)

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
# tool_list_containers / analyze_patterns / detect_error_spikes
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

    def test_tool_detect_error_spikes_not_found(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_detect_error_spikes(container_name="missing")
        assert out["status"] == "error"

    def test_tool_detect_error_spikes_warning_for_no_timestamps(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c], inspect_map={"svc": c})
        lines = ["no ts here", "still no ts"]
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(lines, False)
        ), patch("docker_log_analyzer.tools.detect_spikes", return_value=[]):
            out = tools.tool_detect_error_spikes()
        assert out["status"] == "success"
        assert out["warnings"]


# ---------------------------------------------------------------------------
# tool_correlate_containers / lifecycle / sync
# ---------------------------------------------------------------------------


class TestCorrelateLifecycleAndSync:
    def test_tool_correlate_containers_docker_error(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("x")):
            out = tools.tool_correlate_containers()
        assert out["status"] == "error"

    def test_tool_correlate_containers_need_two(self):
        client = _mock_client(list_containers=[_FakeContainer("/one")])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_correlate_containers()
        assert out["status"] == "success"
        assert out["correlations"] == []

    def test_tool_correlate_containers_cache_hit(self):
        client = _mock_client(list_containers=[_FakeContainer("/a"), _FakeContainer("/b")])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools._read_correlation_cache", return_value={"status": "success", "correlations": []}
        ):
            out = tools.tool_correlate_containers()
        assert out["correlation_cache_hit"] is True

    def test_tool_correlate_containers_filters_names(self):
        client = _mock_client(list_containers=[_FakeContainer("/a"), _FakeContainer("/b")])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = tools.tool_correlate_containers(container_names=["only-this"])
        assert out["correlations"] == []

    def test_tool_correlate_containers_populates_cache_hits_for_nonempty_logs(self):
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
            out = tools.tool_correlate_containers()
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
# async tools
# ---------------------------------------------------------------------------


class TestAsyncTools:
    def test_capture_and_analyze_docker_error(self):
        with patch("docker_log_analyzer.tools._docker_client", side_effect=RuntimeError("x")):
            out = asyncio.run(tools.tool_capture_and_analyze(duration_seconds=0))
        assert out["status"] == "error"

    def test_capture_and_analyze_no_targets(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = asyncio.run(tools.tool_capture_and_analyze(duration_seconds=0))
        assert out["status"] == "success"
        assert "No running containers" in out["message"]

    def test_capture_and_analyze_success_summary(self):
        c = _FakeContainer("/svc")
        client = _mock_client(list_containers=[c], inspect_map={"svc": c})
        with patch("docker_log_analyzer.tools._docker_client", return_value=client), patch(
            "docker_log_analyzer.tools.asyncio.sleep", new=AsyncMock()
        ), patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["x"], False)), patch(
            "docker_log_analyzer.tools.PatternDetector", return_value=_FakePatternDetector()
        ), patch("docker_log_analyzer.tools.detect_spikes", return_value=[{"container": "svc"}]), patch(
            "docker_log_analyzer.tools.correlate", return_value=[]
        ):
            out = asyncio.run(tools.tool_capture_and_analyze(duration_seconds=0))
        assert out["status"] == "success"
        assert out["summary"]["total_log_lines"] == 1
        assert out["summary"]["spike_count"] == 1

    def test_detect_data_leaks_not_found(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = asyncio.run(
                tools.tool_detect_data_leaks(duration_seconds=0, container_names=["missing"])
            )
        assert out["status"] == "error"

    def test_detect_data_leaks_no_targets(self):
        client = _mock_client(list_containers=[])
        with patch("docker_log_analyzer.tools._docker_client", return_value=client):
            out = asyncio.run(tools.tool_detect_data_leaks(duration_seconds=0))
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
            "docker_log_analyzer.tools.asyncio.sleep", new=AsyncMock()
        ), patch("docker_log_analyzer.tools._fetch_logs_with_cache", return_value=(["line"], True)), patch(
            "docker_log_analyzer.tools.SecretDetector", return_value=_FakeSecretDetector([finding])
        ):
            out = asyncio.run(tools.tool_detect_data_leaks(duration_seconds=0))
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
