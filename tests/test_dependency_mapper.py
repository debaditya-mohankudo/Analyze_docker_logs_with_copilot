"""
Unit tests for docker_log_analyzer.dependency_mapper.

All tests are self-contained (no Docker required).
"""

import pytest

from docker_log_analyzer.dependency_mapper import (
    build_graph,
    extract_dependencies,
    find_cascade_candidates,
)

pytestmark = pytest.mark.unit


# ── extract_dependencies ──────────────────────────────────────────────────────

class TestExtractDependencies:

    def test_http_url_high_confidence(self):
        lines = ["2024-03-02T21:10:00Z INFO GET http://payment-service:8080/api/charge 200"]
        deps = extract_dependencies(lines, {"payment-service"})
        targets = [(t, src, conf) for t, src, conf in deps]
        assert any(t == "payment-service" and src == "http_url" and conf == "high"
                   for t, src, conf in targets)

    def test_https_url_high_confidence(self):
        lines = ["2024-03-02T21:10:00Z INFO POST https://auth-service/token 201"]
        deps = extract_dependencies(lines, {"auth-service"})
        assert any(t == "auth-service" and conf == "high" for t, _, conf in deps)

    def test_postgres_connection_string(self):
        lines = ["2024-03-02T21:10:00Z INFO Connecting postgres://db-host:5432/mydb"]
        deps = extract_dependencies(lines, set())
        assert any(t == "db-host" and "postgres" in src for t, src, _ in deps)

    def test_redis_connection_string(self):
        lines = ["2024-03-02T21:10:00Z INFO redis://cache:6379 connected"]
        deps = extract_dependencies(lines, set())
        assert any(t == "cache" and "redis" in src for t, src, _ in deps)

    def test_mongodb_connection_string(self):
        lines = ["2024-03-02T21:10:00Z INFO mongodb://mongo-host:27017/mydb connected"]
        deps = extract_dependencies(lines, set())
        assert any(t == "mongo-host" and "mongodb" in src for t, src, _ in deps)

    def test_grpc_call_medium_confidence(self):
        lines = ["2024-03-02T21:10:00Z INFO dialing grpc://order-service:50051"]
        deps = extract_dependencies(lines, {"order-service"})
        assert any(src == "grpc_call" and conf == "medium" for _, src, conf in deps)

    def test_container_name_mention_low_confidence(self):
        lines = ["2024-03-02T21:10:00Z ERROR failed to reach inventory-svc"]
        deps = extract_dependencies(lines, {"inventory-svc"})
        assert any(t == "inventory-svc" and src == "name_mention" and conf == "low"
                   for t, src, conf in deps)

    def test_skips_localhost(self):
        lines = ["2024-03-02T21:10:00Z INFO http://localhost:8080/health"]
        deps = extract_dependencies(lines, set())
        assert all(t != "localhost" for t, _, _ in deps)

    def test_skips_127_0_0_1(self):
        lines = ["2024-03-02T21:10:00Z INFO redis://127.0.0.1:6379"]
        deps = extract_dependencies(lines, set())
        assert all(t != "127.0.0.1" for t, _, _ in deps)

    def test_deduplication(self):
        lines = [
            "2024-03-02T21:10:00Z INFO http://db:5432/",
            "2024-03-02T21:10:01Z INFO http://db:5432/",
            "2024-03-02T21:10:02Z INFO http://db:5432/",
        ]
        deps = extract_dependencies(lines, set())
        http_db = [(t, s) for t, s, _ in deps if t == "db" and s == "http_url"]
        assert len(http_db) == 1

    def test_empty_lines_returns_empty(self):
        assert extract_dependencies([], set()) == []

    def test_no_signals_returns_empty(self):
        lines = ["2024-03-02T21:10:00Z INFO all good, nothing happening"]
        assert extract_dependencies(lines, set()) == []

    def test_connection_string_with_credentials_stripped(self):
        lines = ["2024-03-02T21:10:00Z INFO postgres://user:secret@db-primary:5432/mydb"]
        deps = extract_dependencies(lines, set())
        assert any(t == "db-primary" for t, _, _ in deps)
        # credentials must not appear as a target
        assert all(t not in ("user", "secret") for t, _, _ in deps)

    def test_name_mention_requires_min_length(self):
        # 3-char names should not trigger name_mention (min=4)
        lines = ["2024-03-02T21:10:00Z INFO error in db service"]
        deps = extract_dependencies(lines, {"db"})
        assert not any(t == "db" and src == "name_mention" for t, src, _ in deps)

    def test_multiple_signals_in_one_line(self):
        lines = [
            "2024-03-02T21:10:00Z INFO calling http://api:8080 and redis://cache:6379"
        ]
        deps = extract_dependencies(lines, set())
        targets = {t for t, _, _ in deps}
        assert "api" in targets
        assert "cache" in targets


# ── build_graph ────────────────────────────────────────────────────────────────

class TestBuildGraph:

    def test_basic_dependency_detected(self):
        logs = {
            "web": ["2024-03-02T21:10:00Z INFO http://database:5432/ SELECT ok"],
            "database": ["2024-03-02T21:10:00Z INFO ready"],
        }
        graph = build_graph(logs)
        assert "web" in graph
        assert any(e["target"] == "database" for e in graph["web"])

    def test_self_loop_excluded(self):
        logs = {
            "web": ["2024-03-02T21:10:00Z INFO http://web:8080/health 200"],
        }
        graph = build_graph(logs)
        # web should not depend on itself
        if "web" in graph:
            assert all(e["target"] != "web" for e in graph["web"])

    def test_no_deps_container_absent(self):
        logs = {
            "worker": ["2024-03-02T21:10:00Z INFO processing job"],
        }
        graph = build_graph(logs)
        assert "worker" not in graph

    def test_hit_count_reflects_occurrences(self):
        logs = {
            "web": [
                "2024-03-02T21:10:00Z INFO http://cache:6379/",
                "2024-03-02T21:10:01Z INFO http://cache:6379/",
                "2024-03-02T21:10:02Z INFO http://cache:6379/",
            ],
            "cache": [],
        }
        graph = build_graph(logs)
        assert "web" in graph
        cache_edge = next((e for e in graph["web"] if e["target"] == "cache"), None)
        assert cache_edge is not None
        assert cache_edge["hit_count"] == 3

    def test_edge_fields_present(self):
        logs = {
            "web": ["2024-03-02T21:10:00Z INFO http://db:5432/ ok"],
            "db": [],
        }
        graph = build_graph(logs)
        for src, edges in graph.items():
            for edge in edges:
                assert "target" in edge
                assert "inferred_from" in edge
                assert "confidence" in edge
                assert "hit_count" in edge

    def test_include_transitive_adds_hop(self):
        logs = {
            "gateway": ["2024-03-02T21:10:00Z INFO http://web:8080/"],
            "web": ["2024-03-02T21:10:00Z INFO http://database:5432/"],
            "database": ["2024-03-02T21:10:00Z INFO ready"],
        }
        graph = build_graph(logs, include_transitive=True)
        # gateway → web (direct) and gateway → database (transitive)
        if "gateway" in graph:
            targets = {e["target"] for e in graph["gateway"]}
            assert "database" in targets
            transitive = [e for e in graph["gateway"] if e["target"] == "database"]
            assert transitive[0]["inferred_from"] == "transitive"
            assert transitive[0]["confidence"] == "low"

    def test_no_transitive_by_default(self):
        logs = {
            "gateway": ["2024-03-02T21:10:00Z INFO http://web:8080/"],
            "web": ["2024-03-02T21:10:00Z INFO http://database:5432/"],
            "database": ["2024-03-02T21:10:00Z INFO ready"],
        }
        graph = build_graph(logs, include_transitive=False)
        if "gateway" in graph:
            assert all(e["target"] != "database" for e in graph["gateway"])

    def test_empty_logs_returns_empty_graph(self):
        assert build_graph({}) == {}

    def test_all_empty_lines_returns_empty_graph(self):
        logs = {"web": [], "db": []}
        assert build_graph(logs) == {}


# ── find_cascade_candidates ────────────────────────────────────────────────────

class TestFindCascadeCandidates:

    def _make_graph(self, src, target, confidence="high", inferred_from="http_url"):
        return {src: [{"target": target, "inferred_from": inferred_from,
                       "confidence": confidence, "hit_count": 5}]}

    def _make_corr(self, a, b, score):
        return [{"container_a": a, "container_b": b, "correlation_score": score,
                 "co_occurrences": 3, "errors_a": 3, "errors_b": 3, "example_pairs": []}]

    def test_high_confidence_high_correlation(self):
        graph = self._make_graph("web", "db", confidence="high")
        corr = self._make_corr("web", "db", 0.8)
        candidates = find_cascade_candidates(graph, corr)
        assert len(candidates) == 1
        assert candidates[0]["confidence"] == "high"

    def test_high_dep_low_correlation_is_medium(self):
        graph = self._make_graph("web", "db", confidence="high")
        corr = self._make_corr("web", "db", 0.3)
        candidates = find_cascade_candidates(graph, corr)
        assert candidates[0]["confidence"] == "medium"

    def test_low_dep_any_correlation_is_low(self):
        graph = self._make_graph("web", "db", confidence="low", inferred_from="name_mention")
        corr = self._make_corr("web", "db", 0.9)
        candidates = find_cascade_candidates(graph, corr)
        assert candidates[0]["confidence"] == "low"

    def test_no_correlation_no_candidates(self):
        graph = self._make_graph("web", "db")
        candidates = find_cascade_candidates(graph, [])
        assert candidates == []

    def test_zero_score_excluded(self):
        graph = self._make_graph("web", "db")
        corr = self._make_corr("web", "db", 0.0)
        candidates = find_cascade_candidates(graph, corr)
        assert candidates == []

    def test_candidate_fields_present(self):
        graph = self._make_graph("web", "db")
        corr = self._make_corr("web", "db", 0.7)
        candidates = find_cascade_candidates(graph, corr)
        assert len(candidates) == 1
        c = candidates[0]
        assert "from" in c
        assert "to" in c
        assert "dependency_type" in c
        assert "correlation_score" in c
        assert "confidence" in c
        assert "evidence" in c

    def test_from_is_dependency_target(self):
        # web depends on db → error origin should be db
        graph = self._make_graph("web", "db")
        corr = self._make_corr("web", "db", 0.8)
        candidates = find_cascade_candidates(graph, corr)
        assert candidates[0]["from"] == "db"
        assert candidates[0]["to"] == "web"

    def test_no_duplicate_pairs(self):
        # Both web→db and db←web edges present (bidirectional graph)
        graph = {
            "web": [{"target": "db", "inferred_from": "http_url",
                     "confidence": "high", "hit_count": 3}],
            "db": [{"target": "web", "inferred_from": "name_mention",
                    "confidence": "low", "hit_count": 1}],
        }
        corr = self._make_corr("web", "db", 0.8)
        candidates = find_cascade_candidates(graph, corr)
        assert len(candidates) == 1

    def test_sorted_by_confidence_then_score(self):
        graph = {
            "a": [{"target": "b", "inferred_from": "http_url", "confidence": "high", "hit_count": 5}],
            "c": [{"target": "d", "inferred_from": "name_mention", "confidence": "low", "hit_count": 1}],
        }
        corr = [
            {"container_a": "a", "container_b": "b", "correlation_score": 0.6,
             "co_occurrences": 3, "errors_a": 3, "errors_b": 3, "example_pairs": []},
            {"container_a": "c", "container_b": "d", "correlation_score": 0.9,
             "co_occurrences": 3, "errors_a": 3, "errors_b": 3, "example_pairs": []},
        ]
        candidates = find_cascade_candidates(graph, corr)
        confidences = [c["confidence"] for c in candidates]
        order = {"high": 0, "medium": 1, "low": 2}
        assert order[confidences[0]] <= order[confidences[-1]]

    def test_empty_graph_returns_empty(self):
        corr = self._make_corr("a", "b", 0.9)
        assert find_cascade_candidates({}, corr) == []

    def test_empty_correlations_returns_empty(self):
        graph = self._make_graph("web", "db")
        assert find_cascade_candidates(graph, []) == []
