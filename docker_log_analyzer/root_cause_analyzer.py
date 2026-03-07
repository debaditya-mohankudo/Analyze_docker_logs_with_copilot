"""
root_cause_analyzer.py – Score-based root cause ranking for container failures.

Algorithm:
  1. Fan-in score:    For each container C, count services that depend on C → score += count × WEIGHT_DEPENDENT
  2. Cascade score:   For each cascade candidate where C is the origin ("from"),
                      add correlation_score × WEIGHT_CASCADE
  3. Spike timing:    Parse first_spike_ts for each container. For each cascade pair,
                      if origin spiked before receiver → score += WEIGHT_SPIKE_FIRST
  4. Fan-out penalty: For each outbound dependency C has, subtract WEIGHT_DEPENDENCY
                      (services depending on many others are followers, not leaders)
  5. Sort by score descending.

Input contracts:
  graph:    Output of dependency_mapper.build_graph()
            {container: [{target, inferred_from, confidence, hit_count}]}
  cascades: Output of dependency_mapper.find_cascade_candidates()
            [{from, to, dependency_type, correlation_score, confidence, evidence}]
  spikes:   Output of spike_detector.detect_spikes()
            [{container, first_spike_ts, ...}]

Output: [{container: str, score: float, evidence: [str, ...]}]
        Sorted by score descending.

All analysis is local – no external API calls.
"""

from collections import defaultdict
from typing import Dict, List

# ── Scoring weights ────────────────────────────────────────────────────────────
# Issue B fix: named constants with documented rationale (was bare magic numbers).
# Tuned for a 4-service test stack; may need adjustment for larger topologies.

WEIGHT_DEPENDENT = 2.0   # +score per service that depends on this container (fan-in)
WEIGHT_CASCADE = 3.0     # +score per cascade candidate where this container is the origin
WEIGHT_SPIKE_FIRST = 4.0 # +score when this container's error spike preceded a dependent's spike
WEIGHT_DEPENDENCY = -1.0 # -score per outbound dependency (followers, not leaders; fan-out penalty)


# ── Core ranking function ──────────────────────────────────────────────────────

def rank_root_causes(
    graph: Dict[str, List[dict]],
    cascades: List[dict],
    spikes: List[dict],
) -> List[dict]:
    """
    Rank containers by root-cause likelihood using dependency graph, cascade
    candidates, and error spike timing.

    Args:
        graph:    Dependency graph from ``dependency_mapper.build_graph()``.
        cascades: Cascade candidates from ``dependency_mapper.find_cascade_candidates()``.
        spikes:   Spike records from ``spike_detector.detect_spikes()``.

    Returns:
        List of ``{"container": str, "score": float}`` dicts, sorted by score
        descending.

    # TODO (Issue C – MISSING, MEDIUM): Add evidence list alongside score accumulation.
    #   Each score contribution should append a human-readable string to an evidence
    #   dict keyed by container name, e.g.:
    #     evidence[c].append(f"{dependents[c]} services depend on {c}")
    #     evidence[origin].append(f"cascade correlation with {target} ({score:.2f})")
    #     evidence[origin].append(f"error spike occurred before {target}")
    #   Return shape becomes: {"container": str, "score": float, "evidence": [str, ...]}
    #   Reference: WIKI_PROPOSAL_ROOT_CAUSE_ANALYZER.md § Issue C

    # TODO (Issue D – ROBUSTNESS, MEDIUM): Return structured result when inputs are empty.
    #   If `scores` is empty after processing, return:
    #     {"status": "success", "root_causes": [], "message": "No root cause signals found"}
    #   Currently returns an empty list, which is technically correct but unhelpful.
    #   Reference: WIKI_PROPOSAL_ROOT_CAUSE_ANALYZER.md § Issue D

    # TODO (Issue E – UX, LOW): Floor scores at 0.0.
    #   The fan-out penalty (WEIGHT_DEPENDENCY = -1.0) can make leaf-service scores
    #   negative. Negative scores are confusing in a ranking context.
    #   Fix: {"container": k, "score": round(max(v, 0.0), 3), ...}
    #   Reference: WIKI_PROPOSAL_ROOT_CAUSE_ANALYZER.md § Issue E

    # TODO (Issue F – ENHANCE, MEDIUM): Incorporate error density (spike magnitude).
    #   All spikes currently contribute equally to spike timing regardless of severity.
    #   A container with 500 errors/min spiking first is stronger evidence than one
    #   with 5 errors/min. Incorporate max_error_rate from detect_spikes() output:
    #     from math import log1p
    #     spike_magnitude = {s["container"]: s.get("max_error_rate", 1.0) for s in spikes}
    #     if origin_ts and target_ts and origin_ts < target_ts:
    #         scores[origin] += WEIGHT_SPIKE_FIRST * log1p(spike_magnitude.get(origin, 1.0))
    #   Use log1p to avoid a single large spike overwhelming all other signals.
    #   Reference: WIKI_REVIEW_ROOT_CAUSE_ANALYZER.md § Issue 3

    # TODO (Issue G – ROBUSTNESS, LOW): Guard against external (unresolved) hostnames
    #   appearing as cascade origins. _resolve_target() in dependency_mapper passes
    #   through raw hostnames when no container matches, which can propagate into
    #   cascade["from"]. Add a known_containers guard before scoring cascade origins:
    #     known_containers = set(graph.keys())
    #     if known_containers and origin not in known_containers:
    #         continue
    #   Reference: WIKI_REVIEW_ROOT_CAUSE_ANALYZER.md § Issue 5
    """
    scores: Dict[str, float] = defaultdict(float)

    # ── Step 1: Fan-in score ───────────────────────────────────────────────────
    # Count how many containers have each target as a dependency.
    dependents: Dict[str, int] = defaultdict(int)
    for edges in graph.values():
        for edge in edges:
            dependents[edge["target"]] += 1

    for container, count in dependents.items():
        scores[container] += count * WEIGHT_DEPENDENT

    # ── Step 2: Cascade score ──────────────────────────────────────────────────
    # Cascade candidates: "from" is the origin (dependency), "to" is the receiver.
    # Origin containers score higher — they are the likely root cause.
    for cascade in cascades:
        origin = cascade["from"]
        corr_score = cascade.get("correlation_score", 0.0)
        scores[origin] += corr_score * WEIGHT_CASCADE

    # ── Step 3: Spike timing ───────────────────────────────────────────────────
    # Issue A fix: use None sentinel; skip comparison when either container has no spike.
    # (Was: spike_time.get(origin, 0) which compared ISO-8601 string with int 0 — TypeError)
    spike_time: Dict[str, str] = {
        s["container"]: s["first_spike_ts"]
        for s in spikes
        if s.get("first_spike_ts")
    }

    for cascade in cascades:
        origin = cascade["from"]
        target = cascade["to"]
        origin_ts = spike_time.get(origin)
        target_ts = spike_time.get(target)
        # ISO-8601 strings sort lexicographically — comparison is valid when both present.
        if origin_ts and target_ts and origin_ts < target_ts:
            scores[origin] += WEIGHT_SPIKE_FIRST

    # ── Step 4: Fan-out penalty ────────────────────────────────────────────────
    # Containers with many outbound dependencies are likely followers, not leaders.
    for container, edges in graph.items():
        outbound = len(edges)
        scores[container] += outbound * WEIGHT_DEPENDENCY

    # ── Step 5: Sort and return ────────────────────────────────────────────────
    return sorted(
        [{"container": k, "score": round(v, 3)} for k, v in scores.items()],
        key=lambda x: x["score"],
        reverse=True,
    )
