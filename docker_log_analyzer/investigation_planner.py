"""
investigation_planner.py – Deterministic rule-based DevOps investigation planner.

Given symptom descriptions, classifies signals and produces a structured
investigation plan mapped to available MCP tools. Saves the plan to a
Markdown file under .cache/plans/ and returns the file path.

No LLM. No network calls. Fully offline and reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Signal keyword patterns → category
# --------------------------------------------------------------------------- #

_SIGNAL_PATTERNS: dict[str, list[str]] = {
    "crash": [
        r"\b(crash|exception|traceback|panic|fatal|oom|killed|segfault|core\s*dump)\b",
        r"\b(500|503|502|error|err)\b",
    ],
    "spike": [
        r"\b(spike|burst|flood|surge|latency|slow|timeout|hang|high\s*load|cpu|memory)\b",
        r"\b(4xx|5xx|rate|throughput|performance)\b",
    ],
    "cascade": [
        r"\b(cascade|propagat|downstream|upstream|dependency|circuit.?breaker|retry)\b",
        r"\b(connection\s*refused|unavailable|unreachable|no\s*such\s*host)\b",
    ],
    "security": [
        r"\b(secret|credential|key|token|password|leak|exposure|pii|sensitive)\b",
        r"\b(unauthorized|forbidden|403|401)\b",
    ],
    "pattern": [
        r"\b(pattern|format|language|timestamp|log\s*level|health.?check)\b",
    ],
}

# Maps focus → which signal categories it restricts to (empty = no restriction)
_FOCUS_SIGNALS: dict[str, set[str]] = {
    "root_cause":  {"crash", "cascade", "spike"},
    "security":    {"security"},
    "performance": {"spike"},
    "general":     set(),
}

VALID_FOCUSES = set(_FOCUS_SIGNALS)

# --------------------------------------------------------------------------- #
# Plan step
# --------------------------------------------------------------------------- #

@dataclass
class PlanStep:
    step: int
    action: str
    target: str | None
    reason: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"step": self.step, "action": self.action, "reason": self.reason}
        if self.target:
            d["target"] = self.target
        if self.parameters:
            d["parameters"] = self.parameters
        return d

    def to_md_row(self) -> str:
        target_str = self.target or "all containers"
        params_str = ", ".join(f"`{k}={v}`" for k, v in self.parameters.items()) if self.parameters else "—"
        return f"| {self.step} | `{self.action}` | {target_str} | {self.reason} | {params_str} |"


# --------------------------------------------------------------------------- #
# Symptom classification
# --------------------------------------------------------------------------- #

def classify_symptoms(symptoms: list[str]) -> set[str]:
    """Return set of signal categories detected in symptom descriptions."""
    text = " ".join(symptoms).lower()
    signals: set[str] = set()
    for category, patterns in _SIGNAL_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                signals.add(category)
                break
    return signals


# --------------------------------------------------------------------------- #
# Plan generation
# --------------------------------------------------------------------------- #

def generate_plan(
    symptoms: list[str],
    containers: list[str] | None = None,
    focus: str | None = None,
    plans_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Generate a structured investigation plan and save it to a Markdown file.

    Parameters
    ----------
    symptoms    : Human-readable observed problem descriptions.
    containers  : Specific containers to scope (None = all running containers).
    focus       : 'root_cause', 'security', 'performance', or 'general'.
    plans_dir   : Directory to write the plan file (defaults to .cache/plans/).

    Returns
    -------
    dict with keys:
        status, signals_detected, focus, containers_in_scope,
        step_count, plan (list of dicts), plan_file (path to saved .md)
    """
    signals = classify_symptoms(symptoms)
    if not signals:
        signals = {"crash"}  # sensible default

    focus = (focus or "general").lower()
    if focus not in VALID_FOCUSES:
        focus = "general"

    focus_set = _FOCUS_SIGNALS[focus]
    if focus_set:
        # Restrict to signals within the focus; if none match, honour the focus
        # definition anyway so unrelated tools are never activated.
        active_signals = (signals & focus_set) or focus_set
    else:
        active_signals = signals  # general: use all detected signals

    targets: list[str] = containers if containers else []
    step_num = 1
    steps: list[PlanStep] = []

    # -- 1. Discover running containers (skip when scope is already explicit) --
    if not containers:
        steps.append(PlanStep(
            step=step_num,
            action="list_containers",
            target=None,
            reason="Discover which containers are running to scope the investigation",
        ))
        step_num += 1

    # -- 2. Pattern analysis --------------------------------------------------
    if "pattern" in active_signals or "crash" in active_signals or focus in ("general", "root_cause"):
        if targets:
            for c in targets:
                steps.append(PlanStep(
                    step=step_num,
                    action="analyze_patterns",
                    target=c,
                    reason=f"Detect log format, language, error level distribution in {c}",
                    parameters={"container_name": c},
                ))
                step_num += 1
        else:
            steps.append(PlanStep(
                step=step_num,
                action="analyze_patterns",
                target=None,
                reason="Detect log format, language, and error level distribution across all containers",
            ))
            step_num += 1

    # -- 3. Get last errors ---------------------------------------------------
    # Only emitted when containers are scoped: get_last_errors requires a
    # container_name argument and cannot run against "all containers".
    # In the broad (no-scope) flow, analyze_patterns + analyze_error_spikes
    # cover all containers; the caller identifies the culprit from those
    # results and then runs get_last_errors manually on that container.
    if "crash" in active_signals or "cascade" in active_signals or focus in ("general", "root_cause"):
        if targets:
            for c in targets:
                steps.append(PlanStep(
                    step=step_num,
                    action="get_last_errors",
                    target=c,
                    reason=f"Extract recent error and fatal log lines from {c} to characterize the failure mode",
                    parameters={"container_name": c, "limit": 20},
                ))
                step_num += 1

    # -- 4. Detect error spikes -----------------------------------------------
    if "spike" in active_signals or "crash" in active_signals or focus in ("general", "root_cause", "performance"):
        if targets:
            for c in targets:
                steps.append(PlanStep(
                    step=step_num,
                    action="analyze_error_spikes",
                    target=c,
                    reason=f"Identify time windows with abnormal error rates in {c} to pinpoint when the failure started",
                    parameters={"container_name": c},
                ))
                step_num += 1
        else:
            steps.append(PlanStep(
                step=step_num,
                action="analyze_error_spikes",
                target=None,
                reason="Identify time windows with abnormal error rates across all containers",
            ))
            step_num += 1

    # -- 5. Cross-container correlation (requires ≥2 containers) --------------
    _multi = not containers or len(containers) != 1  # True when scope is broad or explicit ≥2
    if _multi and ("cascade" in active_signals or "spike" in active_signals or focus in ("root_cause", "general")):
        steps.append(PlanStep(
            step=step_num,
            action="analyze_correlations",
            target=None,
            reason="Measure temporal error correlation across containers to detect cascade failures",
            parameters={"container_names": containers or None, "time_window_seconds": 30},
        ))
        step_num += 1

    # -- 6. Map service dependencies (requires ≥2 containers) -----------------
    if _multi and ("cascade" in active_signals or focus in ("root_cause", "general")):
        steps.append(PlanStep(
            step=step_num,
            action="map_service_dependencies",
            target=None,
            reason="Infer the service call graph from log patterns to understand dependency topology",
            parameters={"containers": containers or None},
        ))
        step_num += 1

    # -- 7. Security scan -----------------------------------------------------
    if "security" in active_signals or focus == "security":
        steps.append(PlanStep(
            step=step_num,
            action="detect_data_leaks",
            target=None,
            reason="Scan for exposed secrets, credentials, API keys, and PII in log output",
            parameters={"container_names": containers or None, "severity_filter": "all"},
        ))
        step_num += 1

    # -- 8. Rank root causes (final) ------------------------------------------
    if focus in ("root_cause", "general") or "cascade" in active_signals:
        steps.append(PlanStep(
            step=step_num,
            action="analyze_root_causes",
            target=None,
            reason="Score containers by root-cause likelihood using dependency fan-in, cascade paths, and spike timing",
            parameters={"containers": containers or None},
        ))
        step_num += 1

    # -- 9. Code context deep-dive (always last when containers are scoped) ---
    # Added when specific containers are known so the plan points directly at code.
    if containers and ("crash" in active_signals or "cascade" in active_signals or focus in ("root_cause", "general")):
        for c in containers:
            steps.append(PlanStep(
                step=step_num,
                action="analyze_code_context",
                target=c,
                reason=(
                    f"Parse stack traces from {c} error logs and surface the source code "
                    f"around each error line — requires REPO_PATHS or CONTAINER_REPO_MAP "
                    f"to be configured in .env"
                ),
                parameters={"container_name": c},
            ))
            step_num += 1

    # -- Save to Markdown file ------------------------------------------------
    plan_file = _save_plan_md(
        steps=steps,
        symptoms=symptoms,
        signals=active_signals,
        focus=focus,
        containers=containers,
        plans_dir=plans_dir,
    )

    return {
        "status": "success",
        "signals_detected": sorted(active_signals),
        "focus": focus,
        "containers_in_scope": containers or ["all"],
        "step_count": len(steps),
        "plan": [s.to_dict() for s in steps],
        "plan_file": str(plan_file),
    }


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #

def _save_plan_md(
    steps: list[PlanStep],
    symptoms: list[str],
    signals: set[str],
    focus: str,
    containers: list[str] | None,
    plans_dir: Path | None,
) -> Path:
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")

    if plans_dir is None:
        # Resolve relative to repo root (.cache/plans/)
        plans_dir = Path(__file__).parent.parent / ".cache" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    scope_slug = "_".join(containers) if containers else "all"
    # Truncate slug to keep filenames reasonable
    if len(scope_slug) > 40:
        scope_slug = scope_slug[:40]
    filename = f"{timestamp}_{focus}_{scope_slug}.md"
    path = plans_dir / filename

    scope_display = ", ".join(f"`{c}`" for c in containers) if containers else "_all running containers_"
    symptoms_md = "\n".join(f"- {s}" for s in symptoms)
    signals_md = ", ".join(f"`{s}`" for s in sorted(signals))

    lines: list[str] = [
        f"# Investigation Plan",
        f"",
        f"**Generated:** {now.strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"**Focus:** `{focus}`  ",
        f"**Signals detected:** {signals_md}  ",
        f"**Scope:** {scope_display}  ",
        f"",
        f"## Observed Symptoms",
        f"",
        symptoms_md,
        f"",
        f"## Steps",
        f"",
        f"| # | Action | Target | Reason | Parameters |",
        f"|---|--------|--------|--------|------------|",
    ]
    for s in steps:
        lines.append(s.to_md_row())

    lines += [
        f"",
        f"## Tool Reference",
        f"",
        f"| Tool | Purpose |",
        f"|------|---------|",
        f"| `list_containers` | Discover all running Docker containers |",
        f"| `analyze_patterns` | Detect log format, language, error level distribution |",
        f"| `get_last_errors` | Return the last N error/fatal lines from a container |",
        f"| `analyze_error_spikes` | Rolling-window spike detection across time buckets |",
        f"| `analyze_correlations` | Pairwise temporal error correlation across containers |",
        f"| `map_service_dependencies` | Infer service dependency graph from log patterns |",
        f"| `detect_data_leaks` | Scan for secrets, credentials, API keys, PII |",
        f"| `analyze_root_causes` | Score containers by root-cause likelihood |",
        f"",
        f"---",
        f"_Generated by `plan_investigation` MCP tool — docker-log-analyzer_",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
