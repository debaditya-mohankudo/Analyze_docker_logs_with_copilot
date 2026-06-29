"""
root_cause_analyzer.py – Score-based root cause ranking for container failures.

Algorithm:
  1. Fan-in score:    For each container C, count services that depend on C → score += count × WEIGHT_DEPENDENT
  2. Cascade score:   For each cascade candidate where C is the origin ("from"),
                      add correlation_score × WEIGHT_CASCADE
  3. Spike timing:    Parse first spike bucket_minute per container. For each cascade pair,
                      if origin spiked before receiver → score += WEIGHT_SPIKE_FIRST × log1p(ratio)
  4. Fan-out penalty: For each outbound dependency C has, subtract WEIGHT_DEPENDENCY
                      (services depending on many others are followers, not leaders)
  5. Floor scores at 0.0 — negative scores are confusing in a ranking context.
  6. Sort by score descending.

Input contracts:
  graph:    Output of dependency_mapper.build_graph()
            {container: [{target, inferred_from, confidence, hit_count}]}
  cascades: Output of dependency_mapper.find_cascade_candidates()
            [{from, to, dependency_type, correlation_score, confidence, evidence}]
  spikes:   Output of spike_detector.detect_spikes()
            [{container, bucket_minute, error_count, baseline, ratio}]

Output: [{container: str, score: float, evidence: [str, ...]}]
        Sorted by score descending.

All analysis is local – no external API calls.
"""

from collections import defaultdict
from math import log1p
from typing import Dict, List

# ── Scoring weights ────────────────────────────────────────────────────────────
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
        List of ``{"container": str, "score": float, "evidence": [str, ...]}`` dicts,
        sorted by score descending. Scores are floored at 0.0.
    """
    scores: Dict[str, float] = defaultdict(float)
    evidence: Dict[str, List[str]] = defaultdict(list)

    # Initialise every graph container at 0.0 so containers with no signals still appear.
    for container in graph:
        scores[container]  # touch to ensure key exists in defaultdict

    # ── Step 1: Fan-in score ───────────────────────────────────────────────────
    # Count how many containers have each target as a dependency.
    dependents: Dict[str, int] = defaultdict(int)
    for edges in graph.values():
        for edge in edges:
            dependents[edge["target"]] += 1

    for container, count in dependents.items():
        scores[container] += count * WEIGHT_DEPENDENT
        evidence[container].append(f"{count} service(s) depend on {container} (fan-in)")

    # ── Step 2: Cascade score ──────────────────────────────────────────────────
    # Cascade candidates: "from" is the origin (dependency), "to" is the receiver.
    # Guard against external/unresolved hostnames appearing as cascade origins.
    # Use all known containers: graph keys + all edge targets.
    known_containers = set(graph.keys())
    for edges in graph.values():
        for edge in edges:
            known_containers.add(edge["target"])
    for cascade in cascades:
        origin = cascade["from"]
        if known_containers and origin not in known_containers:
            continue
        target = cascade["to"]
        corr_score = cascade.get("correlation_score", 0.0)
        scores[origin] += corr_score * WEIGHT_CASCADE
        evidence[origin].append(
            f"cascade correlation with {target} (score={corr_score:.2f})"
        )

    # ── Step 3: Spike timing + magnitude ──────────────────────────────────────
    # Derive first spike bucket and max ratio per container from spike records.
    # bucket_minute is ISO-8601 prefix (YYYY-MM-DDTHH:MM) — sorts lexicographically.
    first_spike: Dict[str, str] = {}
    max_ratio: Dict[str, float] = {}
    for s in spikes:
        c = s["container"]
        bm = s.get("bucket_minute", "")
        ratio = float(s.get("ratio", 1.0))
        if bm and (c not in first_spike or bm < first_spike[c]):
            first_spike[c] = bm
        if ratio > max_ratio.get(c, 0.0):
            max_ratio[c] = ratio

    for cascade in cascades:
        origin = cascade["from"]
        target = cascade["to"]
        if known_containers and origin not in known_containers:
            continue
        origin_ts = first_spike.get(origin)
        target_ts = first_spike.get(target)
        if origin_ts and target_ts and origin_ts < target_ts:
            magnitude = log1p(max_ratio.get(origin, 1.0))
            scores[origin] += WEIGHT_SPIKE_FIRST * magnitude
            evidence[origin].append(
                f"error spike before {target} (ratio={max_ratio.get(origin, 1.0):.1f}x, "
                f"magnitude weight={magnitude:.2f})"
            )

    # ── Step 4: Fan-out penalty ────────────────────────────────────────────────
    # Containers with many outbound dependencies are likely followers, not leaders.
    for container, edges in graph.items():
        outbound = len(edges)
        if outbound:
            scores[container] += outbound * WEIGHT_DEPENDENCY
            evidence[container].append(f"{outbound} outbound dependency(s) (fan-out penalty)")

    # ── Step 5: Sort and return ────────────────────────────────────────────────
    # Floor scores at 0.0 — negative values are confusing in a ranking context.
    if not scores:
        return []

    return sorted(
        [
            {
                "container": k,
                "score": round(max(v, 0.0), 3),
                "evidence": evidence[k],
            }
            for k, v in scores.items()
        ],
        key=lambda x: x["score"],
        reverse=True,
    )
