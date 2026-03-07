"""
Unit tests for docker_log_analyzer.root_cause_analyzer.

All tests are self-contained (no Docker required).
"""

import pytest

from docker_log_analyzer.root_cause_analyzer import (
    WEIGHT_CASCADE,
    WEIGHT_DEPENDENCY,
    WEIGHT_DEPENDENT,
    WEIGHT_SPIKE_FIRST,
    rank_root_causes,
)

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────────────────

def _edge(target, confidence="high", inferred_from="http_url", hit_count=1):
    return {"target": target, "confidence": confidence,
            "inferred_from": inferred_from, "hit_count": hit_count}


def _cascade(origin, receiver, score=0.8):
    return {"from": origin, "to": receiver, "correlation_score": score,
            "dependency_type": "http_url", "confidence": "high",
            "evidence": "test"}


def _spike(container, ts):
    return {"container": container, "first_spike_ts": ts,
            "spike_count": 1, "max_error_rate": 10.0}


def _scores(result):
    """Return {container: score} dict from rank_root_causes output."""
    return {r["container"]: r["score"] for r in result}


# ── Empty / degenerate inputs ──────────────────────────────────────────────────

class TestEmptyInputs:

    def test_all_empty_returns_empty_list(self):
        assert rank_root_causes({}, [], []) == []

    def test_graph_only_no_cascades_no_spikes(self):
        graph = {"web": [_edge("db")], "db": []}
        result = rank_root_causes(graph, [], [])
        scores = _scores(result)
        # db has 1 fan-in: +WEIGHT_DEPENDENT; web has 1 outbound: WEIGHT_DEPENDENCY
        assert scores["db"] == WEIGHT_DEPENDENT
        assert scores["web"] == WEIGHT_DEPENDENCY

    def test_cascades_only_no_graph_no_spikes(self):
        result = rank_root_causes({}, [_cascade("db", "web", 1.0)], [])
        scores = _scores(result)
        assert scores["db"] == pytest.approx(1.0 * WEIGHT_CASCADE)
        assert "web" not in scores

    def test_spikes_only_no_graph_no_cascades(self):
        # Spikes alone don't score anything — timing bonus requires a cascade pair.
        result = rank_root_causes({}, [], [_spike("db", "2026-03-07T10:00:00Z")])
        assert result == []

    def test_single_container_no_deps(self):
        graph = {"solo": []}
        result = rank_root_causes(graph, [], [])
        # No fan-in, no cascades, 0 outbound edges → score = 0.0
        assert len(result) == 1
        assert result[0]["container"] == "solo"
        assert result[0]["score"] == 0.0


# ── Fan-in scoring ─────────────────────────────────────────────────────────────

class TestFanInScoring:

    def test_single_dependent_adds_weight(self):
        graph = {"web": [_edge("db")]}
        result = rank_root_causes(graph, [], [])
        scores = _scores(result)
        assert scores["db"] == WEIGHT_DEPENDENT

    def test_three_dependents_triples_weight(self):
        graph = {
            "web":     [_edge("db")],
            "api":     [_edge("db")],
            "worker":  [_edge("db")],
        }
        result = rank_root_causes(graph, [], [])
        scores = _scores(result)
        assert scores["db"] == pytest.approx(3 * WEIGHT_DEPENDENT)

    def test_fan_in_score_proportional_to_dependents(self):
        graph = {
            "a": [_edge("shared")],
            "b": [_edge("shared")],
            "c": [_edge("leaf")],
        }
        result = rank_root_causes(graph, [], [])
        scores = _scores(result)
        assert scores["shared"] > scores["leaf"]


# ── Fan-out penalty ────────────────────────────────────────────────────────────

class TestFanOutPenalty:

    def test_single_outbound_edge_applies_penalty(self):
        graph = {"web": [_edge("db")]}
        scores = _scores(rank_root_causes(graph, [], []))
        assert scores["web"] == WEIGHT_DEPENDENCY  # -1.0

    def test_two_outbound_edges_doubles_penalty(self):
        graph = {"web": [_edge("db"), _edge("cache")]}
        scores = _scores(rank_root_causes(graph, [], []))
        assert scores["web"] == pytest.approx(2 * WEIGHT_DEPENDENCY)

    def test_fan_out_can_make_score_negative(self):
        # No fan-in, one outbound dep → negative score is expected (Issue E tracks floor)
        graph = {"leaf": [_edge("external")]}
        scores = _scores(rank_root_causes(graph, [], []))
        assert scores["leaf"] < 0


# ── Cascade scoring ────────────────────────────────────────────────────────────

class TestCascadeScoring:

    def test_cascade_origin_scored_by_correlation(self):
        cascades = [_cascade("db", "web", score=0.8)]
        scores = _scores(rank_root_causes({}, cascades, []))
        assert scores["db"] == pytest.approx(0.8 * WEIGHT_CASCADE)

    def test_cascade_receiver_not_scored(self):
        cascades = [_cascade("db", "web", score=0.8)]
        scores = _scores(rank_root_causes({}, cascades, []))
        assert "web" not in scores

    def test_multiple_cascades_accumulate_on_same_origin(self):
        cascades = [
            _cascade("db", "web",    score=0.8),
            _cascade("db", "worker", score=0.5),
        ]
        scores = _scores(rank_root_causes({}, cascades, []))
        assert scores["db"] == pytest.approx((0.8 + 0.5) * WEIGHT_CASCADE)


# ── Spike timing ───────────────────────────────────────────────────────────────

class TestSpikeTiming:

    def test_origin_spiked_first_adds_weight(self):
        cascades = [_cascade("db", "web")]
        spikes = [
            _spike("db",  "2026-03-07T10:00:00Z"),
            _spike("web", "2026-03-07T10:05:00Z"),
        ]
        scores = _scores(rank_root_causes({}, cascades, spikes))
        # Cascade score + spike timing bonus
        expected = 0.8 * WEIGHT_CASCADE + WEIGHT_SPIKE_FIRST
        assert scores["db"] == pytest.approx(expected)

    def test_origin_spiked_after_target_no_bonus(self):
        cascades = [_cascade("db", "web")]
        spikes = [
            _spike("db",  "2026-03-07T10:10:00Z"),  # later
            _spike("web", "2026-03-07T10:05:00Z"),  # earlier
        ]
        scores = _scores(rank_root_causes({}, cascades, spikes))
        # Only cascade score, no spike timing bonus
        assert scores["db"] == pytest.approx(0.8 * WEIGHT_CASCADE)

    def test_missing_origin_spike_skips_comparison(self):
        """Issue A fix: None sentinel — no TypeError when origin has no spike."""
        cascades = [_cascade("db", "web")]
        spikes = [_spike("web", "2026-03-07T10:05:00Z")]  # db has no spike
        scores = _scores(rank_root_causes({}, cascades, spikes))
        # No crash; only cascade score contributes
        assert scores["db"] == pytest.approx(0.8 * WEIGHT_CASCADE)

    def test_missing_target_spike_skips_comparison(self):
        """Issue A fix: None sentinel — no TypeError when target has no spike."""
        cascades = [_cascade("db", "web")]
        spikes = [_spike("db", "2026-03-07T10:00:00Z")]  # web has no spike
        scores = _scores(rank_root_causes({}, cascades, spikes))
        assert scores["db"] == pytest.approx(0.8 * WEIGHT_CASCADE)

    def test_spike_with_none_first_spike_ts_excluded(self):
        """Issue A fix: spikes with first_spike_ts=None are excluded from lookup."""
        cascades = [_cascade("db", "web")]
        spikes = [
            {"container": "db",  "first_spike_ts": None},
            {"container": "web", "first_spike_ts": "2026-03-07T10:05:00Z"},
        ]
        # Must not raise; None is excluded from spike_time dict
        scores = _scores(rank_root_causes({}, cascades, spikes))
        assert scores["db"] == pytest.approx(0.8 * WEIGHT_CASCADE)

    def test_spike_with_missing_first_spike_ts_key_excluded(self):
        """Issue A fix: spikes missing the first_spike_ts key entirely are excluded."""
        cascades = [_cascade("db", "web")]
        spikes = [
            {"container": "db"},  # no first_spike_ts key
            _spike("web", "2026-03-07T10:05:00Z"),
        ]
        scores = _scores(rank_root_causes({}, cascades, spikes))
        assert scores["db"] == pytest.approx(0.8 * WEIGHT_CASCADE)


# ── Sort order and output shape ────────────────────────────────────────────────

class TestOutputShape:

    def test_result_sorted_descending_by_score(self):
        graph = {
            "web": [_edge("db"), _edge("cache")],
            "api": [_edge("db")],
        }
        result = rank_root_causes(graph, [], [])
        scores = [r["score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_each_result_has_container_and_score_keys(self):
        graph = {"web": [_edge("db")]}
        result = rank_root_causes(graph, [], [])
        for item in result:
            assert "container" in item
            assert "score" in item

    def test_scores_are_rounded_to_three_decimal_places(self):
        cascades = [_cascade("db", "web", score=1/3)]
        result = rank_root_causes({}, cascades, [])
        for item in result:
            # round(x, 3) means at most 3 decimal digits
            assert item["score"] == round(item["score"], 3)


# ── Combined 4-service scenario ────────────────────────────────────────────────

class TestFourServiceScenario:
    """
    Realistic scenario: web-app and gateway both depend on database and cache.
    Database should rank #1 due to high fan-in + cascade origin + earliest spike.
    """

    @pytest.fixture
    def graph(self):
        return {
            "web-app": [_edge("database"), _edge("cache")],
            "gateway": [_edge("web-app")],
        }

    @pytest.fixture
    def cascades(self):
        return [
            _cascade("database", "web-app", score=0.81),
            _cascade("cache",    "web-app", score=0.65),
        ]

    @pytest.fixture
    def spikes(self):
        return [
            _spike("database", "2026-03-07T10:00:00Z"),
            _spike("cache",    "2026-03-07T10:02:00Z"),
            _spike("web-app",  "2026-03-07T10:05:00Z"),
        ]

    def test_database_ranks_first(self, graph, cascades, spikes):
        result = rank_root_causes(graph, cascades, spikes)
        assert result[0]["container"] == "database"

    def test_gateway_ranks_last(self, graph, cascades, spikes):
        result = rank_root_causes(graph, cascades, spikes)
        assert result[-1]["container"] == "gateway"

    def test_database_score_includes_all_signals(self, graph, cascades, spikes):
        scores = _scores(rank_root_causes(graph, cascades, spikes))
        # database: fan-in=1 (+2.0) + cascade(0.81) × 3.0 (+2.43) + spike_first (+4.0)
        expected = WEIGHT_DEPENDENT + 0.81 * WEIGHT_CASCADE + WEIGHT_SPIKE_FIRST
        assert scores["database"] == pytest.approx(expected, rel=1e-3)

    def test_web_app_fan_out_penalty_applied(self, graph, cascades, spikes):
        scores = _scores(rank_root_causes(graph, cascades, spikes))
        # web-app: fan-in=1 (+2.0), no cascade origin, 2 outbound edges (-2.0)
        assert scores["web-app"] == pytest.approx(WEIGHT_DEPENDENT + 2 * WEIGHT_DEPENDENCY)
