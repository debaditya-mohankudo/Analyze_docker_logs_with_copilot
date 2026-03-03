"""
Unit tests for docker_log_analyzer.spike_detector.

All tests are self-contained (no Docker required).
"""

import pytest

from docker_log_analyzer.spike_detector import detect_spikes, DOCKER_TS_RE


pytestmark = pytest.mark.unit


class TestDetectSpikes:

    def test_single_spike_detected(self, spike_logs_single):
        spikes = detect_spikes(spike_logs_single, "test", spike_threshold=2.0)
        assert len(spikes) == 1
        assert spikes[0]["bucket_minute"] == "2024-03-02T21:13"
        assert spikes[0]["container"] == "test"
        assert spikes[0]["error_count"] == 8
        assert spikes[0]["ratio"] > 2.0

    def test_spike_fields_present(self, spike_logs_single):
        spikes = detect_spikes(spike_logs_single, "mycontainer", spike_threshold=2.0)
        assert len(spikes) == 1
        s = spikes[0]
        assert set(s.keys()) == {"container", "bucket_minute", "error_count", "baseline", "ratio"}

    def test_no_spike_on_uniform_errors(self, spike_logs_uniform):
        spikes = detect_spikes(spike_logs_uniform, "test", spike_threshold=2.0)
        assert spikes == []

    def test_no_timestamps_returns_empty(self, spike_logs_no_timestamps):
        spikes = detect_spikes(spike_logs_no_timestamps, "test", spike_threshold=2.0)
        assert spikes == []

    def test_empty_input_returns_empty(self):
        assert detect_spikes([], "test") == []

    def test_single_bucket_returns_empty(self):
        """One minute bucket cannot establish a baseline – no spikes possible."""
        lines = ["2024-03-02T21:10:00.000Z ERROR foo"] * 20
        assert detect_spikes(lines, "test", spike_threshold=2.0) == []

    def test_threshold_respected(self, spike_logs_single):
        """High threshold (10.0) should not flag a 4× spike."""
        spikes = detect_spikes(spike_logs_single, "test", spike_threshold=10.0)
        assert spikes == []

    def test_lower_threshold_catches_more(self, spike_logs_single):
        """threshold=1.5 flags the spike that 2.0 also catches."""
        spikes = detect_spikes(spike_logs_single, "test", spike_threshold=1.5)
        assert len(spikes) >= 1

    def test_baseline_not_none(self, spike_logs_single):
        """baseline must be a float, never None."""
        spikes = detect_spikes(spike_logs_single, "test", spike_threshold=2.0)
        for s in spikes:
            assert isinstance(s["baseline"], float)
            assert isinstance(s["ratio"], float)

    def test_ratio_equals_count_over_baseline(self, spike_logs_single):
        spikes = detect_spikes(spike_logs_single, "test", spike_threshold=2.0)
        s = spikes[0]
        assert abs(s["ratio"] - s["error_count"] / s["baseline"]) < 0.01

    def test_no_errors_in_logs(self):
        """Logs with no error lines produce no spikes."""
        lines = [
            "2024-03-02T21:10:00.000Z INFO all good",
            "2024-03-02T21:11:00.000Z INFO still good",
        ]
        assert detect_spikes(lines, "test", spike_threshold=2.0) == []

    def test_mixed_containers_spike_labelled_correctly(self):
        lines = (
            ["2024-03-02T21:10:00.000Z ERROR e"] * 2 +
            ["2024-03-02T21:11:00.000Z ERROR e"] * 2 +
            ["2024-03-02T21:12:00.000Z ERROR e"] * 2 +
            ["2024-03-02T21:13:00.000Z ERROR spike"] * 10
        )
        spikes = detect_spikes(lines, "web-app", spike_threshold=2.0)
        assert all(s["container"] == "web-app" for s in spikes)


class TestDockerTsRegex:

    def test_matches_standard_rfc3339(self):
        line = "2024-03-02T21:19:41.123456789Z ERROR something"
        assert DOCKER_TS_RE.match(line.strip())

    def test_matches_without_fractional(self):
        line = "2024-03-02T21:19:41Z INFO ok"
        assert DOCKER_TS_RE.match(line.strip())

    def test_no_match_on_syslog(self):
        line = "Mar  2 21:19:41 myhost app: ERROR"
        assert not DOCKER_TS_RE.match(line.strip())

    def test_no_match_on_plain_text(self):
        assert not DOCKER_TS_RE.match("just a plain log line")
