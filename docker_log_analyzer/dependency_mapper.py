"""
dependency_mapper.py – Log-based service dependency inference.

Algorithm:
  1. For each container, scan log lines for service dependency signals:
     - HTTP/HTTPS URLs:          http(s)://hostname:port/...
     - DB connection strings:    postgres://, redis://, mongodb://, mysql://...
     - gRPC/dial calls:          "calling", "dialing", "connecting to <name>"
     - Container name mentions:  bare name appearing in free-text log body
  2. Build a directed dependency graph: {container → [{target, inferred_from, confidence, hit_count}]}
  3. Optionally add one level of transitive closure (A→B + B→C → A→C, labelled speculative).
  4. Join with correlator output to surface cascade candidates.

Confidence levels:
  - "high"   – explicit URL or connection string in log line
  - "medium" – structured gRPC/dial pattern match
  - "low"    – container name found in free-form log text, or transitive edge

All analysis is local – no external API calls.
"""

import re
from collections import defaultdict
from typing import Dict, List, Set, Tuple

# ── Dependency signal patterns ─────────────────────────────────────────────────

# HTTP/HTTPS calls – capture the hostname before optional port/path
_HTTP_RE = re.compile(
    r"https?://([a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*)(?::\d+)?(?:/[^\s\"']*)?",
    re.IGNORECASE,
)

# DB / message-bus connection strings – capture protocol and hostname
_DB_HOST_RE = re.compile(
    r"(postgres(?:ql)?|redis|mongodb?|mysql|mariadb|cassandra|elasticsearch"
    r"|amqps?|rabbitmq|kafka)://"
    r"(?:[^@\s]+@)?"                         # optional user:pass@
    r"([a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*)"  # hostname
    r"(?::\d+)?",
    re.IGNORECASE,
)

# gRPC / service-mesh dial patterns
_GRPC_RE = re.compile(
    r'(?:calling|dial(?:ing)?|connecting\s+to|grpc(?:\.|\s+)?(?:call|dial))'
    r'[\s\'":/=]+'
    r'([a-zA-Z0-9_-]{3,})',
    re.IGNORECASE,
)

# Protocol → canonical label for inferred_from field
_PROTOCOL_LABEL = {
    "postgres": "postgres_connection",
    "postgresql": "postgres_connection",
    "redis": "redis_connection",
    "mongodb": "mongodb_connection",
    "mongo": "mongodb_connection",
    "mysql": "mysql_connection",
    "mariadb": "mysql_connection",
    "cassandra": "cassandra_connection",
    "elasticsearch": "elasticsearch_connection",
    "amqp": "amqp_connection",
    "amqps": "amqp_connection",
    "rabbitmq": "amqp_connection",
    "kafka": "kafka_connection",
}

# Hostnames that never represent a real dependency
_SKIP_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


# ── Core extraction ────────────────────────────────────────────────────────────

def _normalize_host(raw: str) -> str:
    """Lowercase, strip port and trailing dots from a raw hostname string."""
    return raw.split(":")[0].lower().strip(".")


def _resolve_target(target: str, known: Set[str]) -> str:
    """Map a hostname to a known container name when possible."""
    if target in known:
        return target
    for name in known:
        if target.startswith(name) or name.startswith(target):
            return name
    return target


def extract_dependencies(
    lines: List[str],
    known_containers: Set[str],
) -> List[Tuple[str, str, str]]:
    """
    Scan log lines from one container for dependency signals.

    Args:
        lines:             Raw log lines (may have Docker-prepended timestamp).
        known_containers:  Names of all running containers for name-mention detection.

    Returns:
        Deduplicated list of ``(target, inferred_from, confidence)`` tuples.
        ``target`` is the raw hostname or container name found in the log.
    """
    found: List[Tuple[str, str, str]] = []
    seen: Set[Tuple[str, str]] = set()  # (target, inferred_from) dedup per scan

    def _add(host: str, source: str, confidence: str) -> None:
        target = _normalize_host(host)
        if not target or target in _SKIP_HOSTS:
            return
        key = (target, source)
        if key not in seen:
            seen.add(key)
            found.append((target, source, confidence))

    for line in lines:
        # HTTP/HTTPS URLs
        for m in _HTTP_RE.finditer(line):
            _add(m.group(1), "http_url", "high")

        # DB / message-bus connection strings
        for m in _DB_HOST_RE.finditer(line):
            protocol = m.group(1).lower()
            label = _PROTOCOL_LABEL.get(protocol, f"{protocol}_connection")
            _add(m.group(2), label, "high")

        # gRPC / dial calls
        for m in _GRPC_RE.finditer(line):
            _add(m.group(1), "grpc_call", "medium")

        # Container name mentions in log body (strip Docker timestamp prefix first)
        body = re.sub(r"^\S+Z\s+", "", line)
        for name in known_containers:
            if len(name) >= 4 and re.search(
                r"\b" + re.escape(name) + r"\b", body, re.IGNORECASE
            ):
                _add(name, "name_mention", "low")

    return found


# ── Graph builder ──────────────────────────────────────────────────────────────

def build_graph(
    container_logs: Dict[str, List[str]],
    include_transitive: bool = False,
) -> Dict[str, List[dict]]:
    """
    Build a directed dependency graph from per-container log lines.

    Args:
        container_logs:      {container_name: [raw_log_line, ...]}
        include_transitive:  If True, add one hop of transitive edges (labelled "low").

    Returns:
        {
            "container_name": [
                {
                    "target":       str,   # service being depended upon
                    "inferred_from": str,  # signal type (http_url, redis_connection, …)
                    "confidence":   str,   # "high" | "medium" | "low"
                    "hit_count":    int,   # how many log lines contained the signal
                }
            ]
        }
        Containers with no outbound dependencies are omitted.
    """
    known = set(container_logs.keys())

    # Accumulate (target, source, confidence) → count per container
    raw: Dict[str, Dict[Tuple[str, str, str], int]] = {
        name: defaultdict(int) for name in known
    }

    for container, lines in container_logs.items():
        for line in lines:
            for target, source, confidence in extract_dependencies([line], known):
                raw[container][(target, source, confidence)] += 1

    graph: Dict[str, List[dict]] = {}
    for container, dep_counts in raw.items():
        edges = []
        for (target, source, confidence), count in sorted(
            dep_counts.items(), key=lambda x: -x[1]
        ):
            resolved = _resolve_target(target, known)
            if resolved == container:
                continue  # skip self-loops
            edges.append({
                "target": resolved,
                "inferred_from": source,
                "confidence": confidence,
                "hit_count": count,
            })
        if edges:
            graph[container] = edges

    if include_transitive:
        _apply_transitive(graph)

    return graph


def _apply_transitive(graph: Dict[str, List[dict]]) -> None:
    """
    Add one hop of transitive edges in-place.

    For every A→B and B→C in ``graph``, add A→C with
    inferred_from="transitive" and confidence="low" if A→C does not already exist.
    """
    direct: Dict[str, Set[str]] = {
        src: {e["target"] for e in edges}
        for src, edges in graph.items()
    }

    for src, edges in list(graph.items()):
        existing = {e["target"] for e in edges}
        for edge in list(edges):
            hop = edge["target"]
            for second_hop in direct.get(hop, set()):
                if second_hop != src and second_hop not in existing:
                    graph[src].append({
                        "target": second_hop,
                        "inferred_from": "transitive",
                        "confidence": "low",
                        "hit_count": 0,
                    })
                    existing.add(second_hop)


# ── Cascade candidate finder ───────────────────────────────────────────────────

def find_cascade_candidates(
    graph: Dict[str, List[dict]],
    correlations: List[dict],
) -> List[dict]:
    """
    Join the dependency graph with temporal correlation results to surface
    likely error cascade paths.

    A *cascade candidate* is a pair (origin, receiver) where:
    - ``receiver`` depends on ``origin`` (origin appears in receiver's edges), AND
    - Errors in both containers co-occur temporally (correlation_score > 0).

    Confidence assignment:
    - "high"   – direct dependency (high/medium confidence) AND correlation_score ≥ 0.5
    - "medium" – direct dependency (any confidence)         AND correlation_score > 0
    - "low"    – transitive or name_mention dependency       AND any correlation_score > 0

    Args:
        graph:        Output of :func:`build_graph`.
        correlations: Output of ``correlate()`` from ``correlator.py``.

    Returns:
        Sorted list of cascade candidate dicts:
        {
            "from":             str,   # error origin (the dependency)
            "to":               str,   # error receiver (the dependent service)
            "dependency_type":  str,   # how the dependency was inferred
            "correlation_score": float,
            "confidence":       str,
            "evidence":         str,   # human-readable summary
        }
        Sorted by confidence (high→low) then correlation_score (desc).
    """
    # Build (a, b) → score lookup (bidirectional)
    corr_lookup: Dict[Tuple[str, str], float] = {}
    for r in correlations:
        a, b, score = r["container_a"], r["container_b"], r["correlation_score"]
        corr_lookup[(a, b)] = score
        corr_lookup[(b, a)] = score

    candidates = []
    seen_pairs: Set[Tuple[str, str]] = set()

    for src, edges in graph.items():
        for edge in edges:
            target = edge["target"]
            score = corr_lookup.get((src, target), 0.0)
            if score == 0.0:
                continue

            # Canonical pair key to avoid duplicates
            pair = (min(src, target), max(src, target))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            dep_conf = edge["confidence"]
            inferred_from = edge["inferred_from"]

            if dep_conf in ("high", "medium") and score >= 0.5:
                cascade_conf = "high"
            elif dep_conf in ("high", "medium"):
                cascade_conf = "medium"
            else:
                cascade_conf = "low"

            candidates.append({
                "from": target,   # the dependency (likely error origin)
                "to": src,        # the dependent service (likely error receiver)
                "dependency_type": inferred_from,
                "correlation_score": round(score, 4),
                "confidence": cascade_conf,
                "evidence": (
                    f"dependency_graph({dep_conf}) + error_correlation({score:.2f})"
                ),
            })

    _ORDER = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda c: (_ORDER[c["confidence"]], -c["correlation_score"]))
    return candidates
