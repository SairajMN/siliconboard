"""Turn a finished/ongoing run into the per-agent timeline the UI renders.

The CLI prints this same information as flat lines. The UI needs the shape
instead: which agent ran, in what order, how long each took and what it produced.
The derivation lives here, away from the HTTP layer, so a test can pin it against
a real board.json without starting a server.

Durations are approximate for a past run: board.log() stamps the *finish* of an
agent, so a stage lasts from the previous stage's last stamp to its own. The
server timestamps transitions live, which is exact; that is why a stage still in
flight is carried as `running`.

Nothing here is invented: every number is read off the Board. Fields the tools
never filled stay None and are simply not shown.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # imported only for the type check, keeps this module pure
    from board import Board

# one agent per job, in the order the orchestrator walks them
PHASES: list[tuple[str, str, str]] = [
    ("spec", "Drafting spec", "spec_agent"),
    ("rtl", "Generating RTL", "rtl_agent"),
    ("lint", "Linting", "lint_agent"),
    ("tb", "Writing testbench", "testbench_agent"),
    ("sim", "Simulating", "sim_runner_agent"),
    ("debug", "Debugging", "debug_agent"),
    ("synth", "Synthesizing", "synthesis_agent"),
    ("timing", "Analyzing timing", "timing_agent"),
    ("report", "Writing report", "report_agent"),
]

LABELS = {key: label for key, label, _ in PHASES}
AGENT_OF = {key: agent for key, _, agent in PHASES}

STATUS_PHASE = {
    "drafting_spec": "spec",
    "generating_rtl": "rtl",
    "linting": "lint",
    "writing_testbench": "tb",
    "simulating": "sim",
    "debugging": "debug",
    "synthesizing": "synth",
    "analyzing_timing": "timing",
    "place_and_route": "pnr",
}

# llm roles are not the same words as statuses ("testbench" vs "writing_testbench")
ROLE_PHASE = {
    "spec": "spec",
    "rtl": "rtl",
    "testbench": "tb",
    "timing": "timing",
    "report": "report",
    "debug": "debug",
}

# a backward route is logged under a terminal status, so the agent is named in the text
AGENT_KEYWORDS = [(agent, key) for key, _, agent in PHASES]

EVENT = re.compile(r"^(\d\d):(\d\d):(\d\d) \[([a-z_]+)\]\s*(.*)$")


def _seconds(stamp: str) -> int | None:
    match = re.match(r"^(\d\d):(\d\d):(\d\d)$", stamp or "")
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3)) if match else None


def _phase_of(status: str, message: str) -> str | None:
    """Which agent a log line belongs to. The message wins over the status: the
    report agent and every backward route log under a status that is not theirs."""
    for agent, key in AGENT_KEYWORDS:
        if agent in message:
            return key
    if message.startswith("report:"):
        return "report"
    return STATUS_PHASE.get(status)
def _parse(events: list[str]) -> list[dict[str, Any]]:
    parsed = []
    for line in events:
        match = EVENT.match(line.strip())
        if not match:
            continue
        hh, mm, ss, status, message = match.groups()
        parsed.append({
            "time": f"{hh}:{mm}:{ss}",
            "at": _seconds(f"{hh}:{mm}:{ss}"),
            "status": status,
            "message": message.strip(),
            "phase": _phase_of(status, message),
        })
    return parsed


def _hms(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _detail(label: str, value: Any) -> list[str] | None:
    if value is None or value == "" or value == []:
        return None
    return [label, _fmt(value)]


def _clean(pairs: list[list[str] | None]) -> list[list[str]]:
    return [pair for pair in pairs if pair]
def stage_details(board: "Board") -> dict[str, list[list[str]]]:
    """What each agent actually did, read straight off the Board."""
    out: dict[str, list[list[str]]] = {}

    if board.spec is not None:
        spec = board.spec
        inputs = [p for p in spec.io_ports if p.direction == "input"]
        outputs = [p for p in spec.io_ports if p.direction == "output"]
        out["spec"] = _clean([
            _detail("module", spec.name),
            _detail("ports", f"{len(inputs)} in, {len(outputs)} out"),
            _detail("clock", spec.clock_domain),
            _detail("target", f"{spec.target_freq_mhz:g} MHz" if spec.target_freq_mhz else None),
            _detail("assumptions", len(spec.assumptions)),
        ])

    if board.rtl_history:
        rtl = board.latest_rtl
        out["rtl"] = _clean([
            _detail("version", f"v{rtl.version}"),
            _detail("module", rtl.module_name),
            _detail("file", rtl.filename),
            _detail("lines", len(rtl.source_code.splitlines())),
        ])

    if board.lint is not None:
        out["lint"] = _clean([
            _detail("clean", board.lint.clean),
            _detail("issues", len(board.lint.issues)),
        ])

    if board.testbench is not None:
        tb = board.testbench
        out["tb"] = _clean([
            _detail("file", tb.filename),
            _detail("lines", len(tb.source_code.splitlines())),
            _detail("covers", tb.test_vectors_description),
        ])

    if board.sim_history:
        sim = board.latest_sim
        out["sim"] = _clean([
            _detail("verdict", "PASS" if sim.passed else "FAIL"),
            _detail("checks", f"{sim.total_checks} run, {sim.failed_checks} failed"
                    if sim.total_checks is not None else None),
            _detail("cross-check", (
                "agreed (siliconboard + icarus)" if sim.cross_agreed
                else "disagreed" if sim.cross_agreed is False
                else "not run" if not sim.cross_checked else None
            )),
            _detail("waveform", sim.wave_file),
            _detail("ambiguity", sim.ambiguity),
        ])

    if board.bug_history:
        bug = board.latest_bug
        out["debug"] = _clean([
            _detail("failing check", bug.failing_check),
            _detail("blames", bug.blames),
            _detail("cause", bug.likely_cause),
            _detail("fix", bug.suggested_fix),
        ])

    if board.synthesis_report is not None:
        syn = board.synthesis_report
        out["synth"] = _clean([
            _detail("cells", syn.cell_count),
            _detail("area", f"{syn.area_um2:g} um2" if syn.area_um2 else None),
        ])

    if board.timing_report is not None:
        timing = board.timing_report
        out["timing"] = _clean([
            _detail("target met", timing.met_target),
            _detail("worst slack", f"{timing.worst_slack_ns:g} ns" if timing.worst_slack_ns is not None else None),
            _detail("verified", timing.verified),
        ])

    if board.run_report is not None:
        out["report"] = _clean([
            _detail("words", len(board.run_report.markdown_summary.split())),
        ])

    return {key: value for key, value in out.items() if value}


def _llm_by_phase(board: "Board") -> dict[str, list[dict[str, Any]]]:
    """Each call was made by the agent whose role it carries; retries stack up."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for call in board.llm_calls:
        key = ROLE_PHASE.get(call.role)
        if key is None:
            continue
        grouped.setdefault(key, []).append({
            "model": call.model,
            "slot": call.key_slot,
            "tokens_in": call.tokens_in,
            "tokens_out": call.tokens_out,
            "cached": call.cached,
        })
    return grouped
def build(board: "Board", run_id: str, now: int | None = None) -> dict[str, Any]:
    """The whole timeline: ordered stages with state, timing, lines and artifacts.

    `now` is the current wall-clock second (passed in so this stays deterministic
    under test); it only closes the duration of a stage that is still running.
    """
    parsed = _parse(list(board.event_log))
    llm = _llm_by_phase(board)
    details = stage_details(board)

    created = board.created_at
    base = created.hour * 3600 + created.minute * 60 + created.second

    # group consecutive lines that belong to the same agent
    groups: list[dict[str, Any]] = []
    for event in parsed:
        if event["phase"] is None:
            continue
        if groups and groups[-1]["phase"] == event["phase"]:
            groups[-1]["lines"].append(event["message"])
            groups[-1]["at"] = event["at"]
            groups[-1]["time"] = event["time"]
        else:
            groups.append({
                "phase": event["phase"],
                "lines": [event["message"]],
                "at": event["at"],
                "time": event["time"],
            })

    status = board.status.value
    running_phase = None if board.finished else STATUS_PHASE.get(status)

    stages = []
    cursor = base
    for group in groups:
        end = group["at"]
        if end is not None and end < cursor:  # past midnight, or a coarse clock
            end += 86400
        seconds = None if end is None or cursor is None else max(0, end - cursor)
        stages.append({
            "key": group["phase"],
            "label": LABELS.get(group["phase"], group["phase"]),
            "agent": AGENT_OF.get(group["phase"], group["phase"]),
            "state": "running" if group["phase"] == running_phase else "done",
            "started": _hms(cursor),
            "ended": None if group["phase"] == running_phase else group["time"],
            "seconds": None if group["phase"] == running_phase else seconds,
            "lines": group["lines"],
            "details": details.get(group["phase"], []),
            "llm": llm.get(group["phase"], []),
        })
        if end is not None:
            cursor = end

    # an agent that is running but has not logged anything yet still needs a row
    if running_phase is not None and all(stage["key"] != running_phase for stage in stages):
        stages.append({
            "key": running_phase,
            "label": LABELS.get(running_phase, running_phase),
            "agent": AGENT_OF.get(running_phase, running_phase),
            "state": "running",
            "started": _hms(cursor),
            "ended": None,
            "seconds": None if now is None or cursor is None else max(0, now - cursor),
            "lines": [],
            "details": details.get(running_phase, []),
            "llm": [],
        })

    if status == "failed":
        for stage in reversed(stages):
            if stage["state"] != "running":
                stage["state"] = "failed"
                break
    elif status == "done_with_warning" and stages:
        stages[-1]["state"] = "warning"

    seen = {stage["key"] for stage in stages}
    queued = [
        {"key": key, "label": LABELS[key], "agent": AGENT_OF[key], "state": "queued",
         "started": None, "ended": None, "seconds": None, "lines": [],
         "details": [], "llm": []}
        for key, _, _ in PHASES if key not in seen and not board.finished
    ]

    # stages stay in the order they actually happened: a backward route is the
    # headline of this system, and sorting into a canonical order would hide it.
    ordered = stages + queued

    return {
        "run_id": run_id,
        "status": status,
        "finished": board.finished,
        "created_at": board.created_at.isoformat(),
        "updated_at": board.updated_at.isoformat(),
        "input": board.request,
        "stages": ordered,
        "summary": _summary(board),
        "retries": dict(board.stage_retries),
        "events": list(board.event_log),
    }


def _summary(board: "Board") -> dict[str, Any]:
    """The headline numbers, for the strip at the top of a run."""
    sim = board.latest_sim
    timing = board.timing_report
    synth = board.synthesis_report
    return {
        "module": board.spec.name if board.spec else None,
        "rtl_versions": len(board.rtl_history),
        "sim_passed": sim.passed if sim else None,
        "checks": sim.total_checks if sim else None,
        "failed_checks": sim.failed_checks if sim else None,
        "cross_agreed": sim.cross_agreed if sim else None,
        "cells": synth.cell_count if synth else None,
        "slack_ns": timing.worst_slack_ns if timing else None,
        "met_target": timing.met_target if timing else None,
        "bugs": len(board.bug_history),
        "llm_calls": len(board.llm_calls),
        "tokens": sum((call.tokens_out or 0) for call in board.llm_calls) or None,
    }