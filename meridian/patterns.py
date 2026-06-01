"""
Pattern detection engine — Phase 1.

Four detectors, each returning a list of Finding objects:
  1. retry_spiral        — same Bash command repeated 3+ times
  2. agent_spawn_cost    — Agent tool calls over duration threshold
  3. context_bloat       — cache_read_tokens growing > 2× in one session
  4. tool_inefficiency   — WebFetch chains, ToolSearch overhead

All thresholds are read from ~/.meridian/config.json (see meridian/config.py).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from . import config as _cfg


# --------------------------------------------------------------------------- #
# Finding
# --------------------------------------------------------------------------- #

@dataclass
class Finding:
    pattern:        str          # machine-readable key
    severity:       str          # "high" | "medium" | "low"
    trace_id:       str
    title:          str
    detail:         str
    recommendation: str
    waste_ms:       float = 0.0
    wasted_tokens:  int   = 0
    evidence:       list[str] = field(default_factory=list)


_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _normalize_cmd(cmd: str) -> str:
    """Strip env-var prefixes and shell boilerplate to get the core invocation."""
    if not cmd:
        return ""
    c = cmd.strip()
    # Remove leading VAR=value assignments
    c = re.sub(r'^(\s*[A-Z_][A-Z0-9_]*=\S+\s*)+', '', c)
    # Remove "source .venv/bin/activate &&" prefix
    c = re.sub(r'source\s+\S+\s*&&\s*', '', c)
    # Remove "bash -c" wrapper
    c = re.sub(r'^bash\s+-c\s+["\']?', '', c)
    return re.sub(r'\s+', ' ', c).strip()


def _cmd_key(cmd: str) -> str:
    return _normalize_cmd(cmd)[:80]


# --------------------------------------------------------------------------- #
# 1. Retry spiral
# --------------------------------------------------------------------------- #

def detect_retry_spirals(
    sessions: dict[str, list[dict]],
    min_repeats: int | None = None,
) -> list[Finding]:
    if min_repeats is None:
        min_repeats = _cfg.get("retry_spiral.min_repeats")
    """
    Detects when the same normalised Bash command runs ≥ min_repeats times
    in one session — the classic "probe environment, fail, retry" loop.
    """
    findings: list[Finding] = []

    for trace_id, spans in sessions.items():
        bash_spans = [
            s for s in spans
            if s.get("span_type") == "tool"
            and s.get("tool_name") == "Bash"
            and s.get("full_command")
        ]
        if len(bash_spans) < min_repeats:
            continue

        buckets: dict[str, list[dict]] = defaultdict(list)
        for s in bash_spans:
            key = _cmd_key(s["full_command"])
            if key:
                buckets[key].append(s)

        for key, group in buckets.items():
            if len(group) < min_repeats:
                continue

            total_ms   = sum(s.get("duration_ms") or 0 for s in group)
            # All but the last run are waste (the last presumably worked)
            waste_ms   = total_ms - (group[-1].get("duration_ms") or 0)
            severity   = "high" if len(group) >= 5 else "medium"

            findings.append(Finding(
                pattern="retry_spiral",
                severity=severity,
                trace_id=trace_id,
                title=f"Retry spiral: same command ran {len(group)}×",
                detail=(
                    f"`{key[:70]}{'…' if len(key) > 70 else ''}` "
                    f"executed {len(group)} times. "
                    f"Total: {total_ms/1000:.1f}s · Waste: {waste_ms/1000:.1f}s."
                ),
                recommendation=_retry_rec(key, group),
                waste_ms=waste_ms,
                evidence=[s.get("full_command", "")[:120] for s in group],
            ))

    return findings


def _retry_rec(key: str, group: list[dict]) -> str:
    cmds = [s.get("full_command", "") for s in group]
    has_venv    = any("venv" in c or "activate" in c for c in cmds)
    has_pypath  = any("PYTHONPATH" in c for c in cmds)
    is_python   = "python" in key.lower()
    is_node     = "npm" in key or "node" in key

    if is_python and (has_venv or has_pypath):
        return (
            "Add to CLAUDE.md: always activate the virtualenv before running Python. "
            "E.g. `source .venv/bin/activate`. "
            "This eliminates PYTHONPATH probe loops entirely."
        )
    if is_node:
        return (
            "Add to CLAUDE.md: run `npm install` once at session start and "
            "specify the project directory explicitly."
        )
    return (
        "Add to CLAUDE.md: document the correct invocation and environment "
        "for this command so Claude doesn't need to probe it at runtime."
    )


# --------------------------------------------------------------------------- #
# 2. Agent spawn cost
# --------------------------------------------------------------------------- #

def detect_agent_spawns(
    sessions: dict[str, list[dict]],
    threshold_ms: float | None = None,
) -> list[Finding]:
    if threshold_ms is None:
        threshold_ms = _cfg.get("agent_spawn.threshold_ms")
    """
    Flags Agent tool calls that ran longer than threshold_ms.
    Long agents carry full model startup cost and inherit the parent context.
    """
    findings: list[Finding] = []

    for trace_id, spans in sessions.items():
        for s in spans:
            if s.get("span_type") != "tool" or s.get("tool_name") != "Agent":
                continue
            dur = s.get("duration_ms") or 0
            if dur < threshold_ms:
                continue

            severity = "high" if dur > 90_000 else "medium"
            findings.append(Finding(
                pattern="agent_spawn_cost",
                severity=severity,
                trace_id=trace_id,
                title=f"Expensive Agent spawn: {dur/1000:.0f}s",
                detail=(
                    f"An Agent subagent ran for {dur/1000:.1f}s. "
                    "Agent spawns inherit the full parent context window and "
                    "pay model startup cost on every call."
                ),
                recommendation=(
                    "Add to CLAUDE.md: prefer targeted grep/find/Read calls over "
                    "open-ended Agent spawns for exploration. "
                    "When you must spawn, use `subagent_type=Explore` — "
                    "it is read-only and significantly faster."
                ),
                waste_ms=dur,
                evidence=[f"Agent duration: {dur/1000:.1f}s"],
            ))

    return findings


# --------------------------------------------------------------------------- #
# 3. Context bloat
# --------------------------------------------------------------------------- #

def detect_context_bloat(
    sessions: dict[str, list[dict]],
    growth_threshold: float | None = None,
    absolute_threshold: int | None = None,
    min_growth: int | None = None,
) -> list[Finding]:
    if growth_threshold is None:
        growth_threshold = _cfg.get("context_bloat.growth_threshold")
    if absolute_threshold is None:
        absolute_threshold = _cfg.get("context_bloat.absolute_threshold")
    if min_growth is None:
        min_growth = _cfg.get("context_bloat.min_growth")
    """
    Detects sessions where cache_read_tokens grows > 2× or exceeds 80k.
    Every LLM turn pays the full accumulated cache cost.
    """
    findings: list[Finding] = []

    for trace_id, spans in sessions.items():
        llm = [
            s for s in spans
            if s.get("span_type") == "llm_request"
            and s.get("cache_read_tokens") is not None
        ]
        if len(llm) < 2:
            continue

        series      = [s["cache_read_tokens"] for s in llm]
        start       = series[0]
        peak        = max(series)

        growth = peak - start
        if start == 0:
            continue
        ratio = peak / start
        exceeds_absolute = peak >= absolute_threshold and growth >= min_growth
        exceeds_ratio = ratio >= growth_threshold
        if not (exceeds_absolute or exceeds_ratio):
            continue

        ratio = peak / start  # recompute for display (already computed above)
        inflection = next(
            (i for i, t in enumerate(series) if t >= absolute_threshold),
            len(series) - 1,
        )
        severity = "high" if peak > 120_000 else "medium"

        findings.append(Finding(
            pattern="context_bloat",
            severity=severity,
            trace_id=trace_id,
            title=f"Context bloat: {start:,} → {peak:,} cache tokens ({ratio:.1f}×)",
            detail=(
                f"Cache tokens grew {ratio:.1f}× over {len(llm)} LLM turns "
                f"({start:,} → {peak:,}). "
                f"Crossed {absolute_threshold:,} at turn {inflection + 1}."
            ),
            recommendation=(
                "Add to CLAUDE.md: run `/compact` when cache tokens exceed 80k, "
                "or open a fresh session for unrelated tasks. "
                "Long sessions compound token costs on every subsequent turn."
            ),
            wasted_tokens=peak - start,
            evidence=[f"Turn {i+1}: {t:,} tokens" for i, t in enumerate(series)],
        ))

    return findings


# --------------------------------------------------------------------------- #
# 4. Tool inefficiency
# --------------------------------------------------------------------------- #

def detect_tool_inefficiency(
    sessions: dict[str, list[dict]],
    fetch_chain_threshold: int | None = None,
    tool_search_threshold: int | None = None,
) -> list[Finding]:
    if fetch_chain_threshold is None:
        fetch_chain_threshold = _cfg.get("webfetch_chain.min_length")
    if tool_search_threshold is None:
        tool_search_threshold = _cfg.get("tool_search.min_calls")
    """
    Detects:
      - WebFetch chains: 3+ sequential fetches with no other tools between them
      - ToolSearch overhead: repeated deferred schema lookups
    """
    findings: list[Finding] = []

    for trace_id, spans in sessions.items():
        tool_spans = [s for s in spans if s.get("span_type") == "tool"]

        # WebFetch chains
        current_run: list[dict] = []
        for s in tool_spans:
            if s.get("tool_name") == "WebFetch":
                current_run.append(s)
            else:
                if len(current_run) >= fetch_chain_threshold:
                    _add_fetch_chain(findings, trace_id, current_run)
                current_run = []
        if len(current_run) >= fetch_chain_threshold:
            _add_fetch_chain(findings, trace_id, current_run)

        # ToolSearch overhead
        ts_count = sum(1 for s in tool_spans if s.get("tool_name") == "ToolSearch")
        if ts_count > tool_search_threshold:
            findings.append(Finding(
                pattern="tool_search_overhead",
                severity="low",
                trace_id=trace_id,
                title=f"ToolSearch: {ts_count} schema lookups in one session",
                detail=(
                    f"{ts_count} ToolSearch calls were made. "
                    "Each lookup loads a deferred tool's schema into the context window."
                ),
                recommendation=(
                    "Review which MCP servers are configured but rarely used. "
                    "Remove unused MCP servers — their schemas are loaded on every session."
                ),
                evidence=[f"{ts_count} ToolSearch calls"],
            ))

    return findings


def _add_fetch_chain(
    findings: list[Finding], trace_id: str, run: list[dict]
) -> None:
    total_ms = sum(s.get("duration_ms") or 0 for s in run)
    findings.append(Finding(
        pattern="webfetch_chain",
        severity="medium",
        trace_id=trace_id,
        title=f"WebFetch chain: {len(run)} sequential fetches ({total_ms/1000:.1f}s)",
        detail=(
            f"{len(run)} WebFetch calls ran back-to-back. "
            "Each fetch triggers a separate LLM turn to process the result."
        ),
        recommendation=(
            "Add to CLAUDE.md: list all URLs you need upfront so they can be "
            "fetched together, rather than one-at-a-time."
        ),
        waste_ms=total_ms,
        evidence=[s.get("full_command") or "WebFetch" for s in run],
    ))


# --------------------------------------------------------------------------- #
# 5. Probe spiral (same target, different queries)
# --------------------------------------------------------------------------- #

def _extract_target(cmd: str) -> str | None:
    """Extract the file/directory being targeted from a tool command."""
    import shlex
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()

    # For grep/find/cat/head/tail: look for path-like arguments
    for part in parts:
        if part.startswith("/") or part.startswith("./") or part.startswith("~/"):
            # Strip to the base directory for grouping
            p = part.rstrip("/")
            return p
    return None


def detect_probe_spirals(
    sessions: dict[str, list[dict]],
    min_probes: int = 4,
) -> list[Finding]:
    """
    Detects when many DIFFERENT tool calls target the SAME file or directory.
    Classic pattern: Claude searches the same file 5+ times with different grep
    patterns because it doesn't know exactly what it's looking for.
    """
    findings: list[Finding] = []

    for trace_id, spans in sessions.items():
        bash_spans = [
            s for s in spans
            if s.get("span_type") == "tool"
            and s.get("tool_name") in ("Bash", "Read", "Edit")
            and s.get("full_command")
        ]
        if len(bash_spans) < min_probes:
            continue

        # Group by target path
        target_map: dict[str, list[dict]] = defaultdict(list)
        for s in bash_spans:
            target = _extract_target(s["full_command"] or "")
            if target:
                target_map[target].append(s)

        for target, group in target_map.items():
            # Only flag if the commands are meaningfully different (not exact repeats —
            # those are caught by detect_retry_spirals)
            unique_cmds = {_cmd_key(s["full_command"]) for s in group}
            if len(group) < min_probes or len(unique_cmds) < 3:
                continue

            total_ms = sum(s.get("duration_ms") or 0 for s in group)
            short_target = target.split("/")[-1] or target

            findings.append(Finding(
                pattern="probe_spiral",
                severity="medium",
                trace_id=trace_id,
                title=f"Probe spiral: {len(group)} different searches on '{short_target}'",
                detail=(
                    f"{len(group)} tool calls targeted '{short_target}' with "
                    f"{len(unique_cmds)} different queries ({total_ms/1000:.1f}s total). "
                    f"Claude was searching for something it didn't know the exact location of."
                ),
                recommendation=(
                    f"Add to CLAUDE.md: document what lives in '{short_target}' "
                    f"so Claude knows where to look without probing. "
                    f"Check the last successful command in this sequence for the answer."
                ),
                waste_ms=total_ms,
                evidence=[s.get("full_command", "")[:100] for s in group[:4]],
            ))

    return findings


# --------------------------------------------------------------------------- #
# 6. High baseline context
# --------------------------------------------------------------------------- #

def detect_high_baseline(
    sessions: dict[str, list[dict]],
    baseline_threshold: int | None = None,
    min_sessions: int = 3,
) -> list[Finding]:
    """
    Detects when the FIRST LLM turn's cache_read_tokens is already high across
    multiple sessions. This means the system prompt + CLAUDE.md + MCP schemas
    are large before any work begins — a structural cost paid on every session.
    """
    if baseline_threshold is None:
        baseline_threshold = _cfg.get("context_bloat.absolute_threshold")

    high_baseline_sessions = []
    for trace_id, spans in sessions.items():
        llm_spans = [
            s for s in spans
            if s.get("span_type") == "llm_request"
            and s.get("cache_read_tokens") is not None
        ]
        if not llm_spans:
            continue
        first_cache = llm_spans[0]["cache_read_tokens"]
        if first_cache >= baseline_threshold:
            high_baseline_sessions.append((trace_id, first_cache))

    if len(high_baseline_sessions) < min(min_sessions, len(sessions)):
        return []

    avg_baseline = sum(t for _, t in high_baseline_sessions) // len(high_baseline_sessions)
    severity = "high" if avg_baseline > 100_000 else "medium"

    return [Finding(
        pattern="high_baseline_context",
        severity=severity,
        trace_id=high_baseline_sessions[0][0],
        title=(
            f"Heavy system prompt: {len(high_baseline_sessions)} sessions "
            f"start above {baseline_threshold:,} cache tokens"
        ),
        detail=(
            f"{len(high_baseline_sessions)} sessions opened with an average of "
            f"{avg_baseline:,} cache tokens before any work — "
            f"this is your CLAUDE.md + MCP schemas + system prompt baseline. "
            f"Every new session pays this cost upfront."
        ),
        recommendation=(
            "Run `!meridian analyse --session <id>` on a fresh 1-turn session "
            "to measure the true baseline. Then audit: "
            "(1) remove unused MCP servers, "
            "(2) trim your global CLAUDE.md, "
            "(3) move project-specific rules to per-project CLAUDE.md files."
        ),
        wasted_tokens=avg_baseline * len(high_baseline_sessions),
        evidence=[
            f"Session {tid[:12]}: starts at {tok:,} cache tokens"
            for tid, tok in high_baseline_sessions[:5]
        ],
    )]


# --------------------------------------------------------------------------- #
# Run all detectors
# --------------------------------------------------------------------------- #

def run_all(sessions: dict[str, list[dict]]) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(detect_retry_spirals(sessions))
    findings.extend(detect_probe_spirals(sessions))
    findings.extend(detect_agent_spawns(sessions))
    findings.extend(detect_context_bloat(sessions))
    findings.extend(detect_tool_inefficiency(sessions))
    findings.extend(detect_high_baseline(sessions))
    return sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 9))
