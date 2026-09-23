"""Turns a failing simulation log into a bug report the RTL agent can act on — evidence first."""

from __future__ import annotations

import llm
from agent import NO_INVENTION, AgentResult, BaseAgent, build_prompt, trim_log
from board import Board, BugReport
from tools import check_results, fail_lines


def _history(board: Board) -> str:
    lines = []
    for s in board.sim_history:
        tag = f"rtl v{s.rtl_version}" if s.rtl_version is not None else "rtl v?"
        fails = [f"CHECK {n} FAIL got={g} want={w}" for n, g, w in fail_lines(s.raw_log)]
        lines.append(f"{tag}: " + ("; ".join(fails) if fails else "(no failing CHECK lines parsed)"))
    return "\n".join(lines)


def testbench_blame_problems(board: Board) -> list[str]:
    """A testbench blame must be earned: the same failing evidence under two different RTL versions."""
    sims = board.sim_history
    cur_v = sims[-1].rtl_version if sims else None
    current = set(fail_lines(sims[-1].raw_log)) if sims else set()
    if len(sims) < 2:
        return ["blames=testbench needs at least two simulation attempts to compare across rtl versions"]
    if not current:
        return ["blames=testbench needs failing CHECK lines that print got= and want= values"]
    for prior in sims[:-1]:
        if prior.rtl_version is None or cur_v is None or prior.rtl_version == cur_v:
            continue
        if set(fail_lines(prior.raw_log)) & current:
            return []
    return [
        "blames=testbench requires the same CHECK ... FAIL got=... want=... line under two different "
        "rtl_versions in the failure history; no earlier attempt failed the same way after the RTL changed"
    ]

SYSTEM = f"""You are debugging a failing hardware simulation.

Given the specification, the RTL, the testbench, and the raw simulation log, produce one bug report:
- failing_check: the exact CHECK name from the log that failed, copied character for character. If the log failed to compile instead, use compile_failed.
- evidence_quote: one verbatim line from the simulation log that shows the failure — the `CHECK <name> FAIL` line or the first %Error line. Copy it exactly; the harness checks it against the log and rejects the report if it does not appear there.
- likely_cause: what the blamed artifact most likely does wrong. For the RTL, name the exact signal and, when the log gives it, the source line; for the testbench, name the expectation and the stimulus timing that produced it.
- suggested_fix: the smallest change to the blamed artifact that fixes this failure. Never suggest rewriting the module or the whole testbench.
- blames: `rtl`, or `testbench`. Choose `testbench` ONLY when the failure history shows the same check failing with the same got and want values under two different RTL versions — the design changed and the failure did not, so the expectation itself is wrong (typically stimulus changed a negedge too late or too early). The harness compares the history itself and rejects a testbench blame it cannot verify there.
- summary: one sentence.

{NO_INVENTION}"""


def bug_problems(bug: BugReport, raw_log: str) -> list[str]:
    problems: list[str] = []
    quote = bug.evidence_quote.strip()
    first_line = next((ln for ln in quote.splitlines() if ln.strip()), "")
    if not quote or quote.upper() == "UNKNOWN":
        problems.append("evidence_quote is empty or UNKNOWN")
    elif first_line not in raw_log:
        problems.append(
            "evidence_quote is not a verbatim line of the simulation log: "
            f"{first_line[:120]!r} — copy the real FAIL or %Error line"
        )
    failed = {name for name, ok in check_results(raw_log).items() if not ok}
    if failed and bug.failing_check not in failed and bug.failing_check != "compile_failed":
        problems.append(
            f"failing_check {bug.failing_check!r} is not one of the failed checks {sorted(failed)}"
        )
    if not bug.suggested_fix.strip() or bug.suggested_fix.strip().upper() == "UNKNOWN":
        problems.append("suggested_fix must name a concrete change")
    return problems


class DebugAgent(BaseAgent):
    name = "debug_agent"
    system = SYSTEM

    def run(self, board: Board) -> AgentResult:
        if board.spec is None:
            return AgentResult(ok=False, err="no spec on the board")
        rtl = board.latest_rtl
        if rtl is None:
            return AgentResult(ok=False, err="no RTL on the board")
        sim = board.latest_sim
        if sim is None:
            return AgentResult(ok=False, err="no simulation result on the board")
        if sim.passed:
            return AgentResult(ok=False, err="the last simulation passed, there is nothing to debug")
        if not sim.raw_log.strip():
            return AgentResult(ok=False, err="the simulation log is empty, nothing to triage")

        prompt = build_prompt(
            ("specification", board.spec.model_dump_json(indent=2)),
            ("rtl", rtl.source_code),
            ("testbench", board.testbench.source_code if board.testbench else "(none)"),
            ("simulation log", trim_log(sim.raw_log)),
            ("failure history across rtl versions", _history(board)),
            ("task", "Write the bug report now, as JSON matching the schema."),
        )

        def gate(bug: BugReport) -> list[str]:
            problems = bug_problems(bug, sim.raw_log)
            if bug.blames == "testbench":
                problems += testbench_blame_problems(board)
            return problems

        try:
            bug = llm.call(prompt, self.system, BugReport, "debug", llm.HEAVY, board, validate=gate)
        except llm.LLMError as exc:
            return AgentResult(ok=False, err=str(exc))

        board.bug_history.append(bug)
        board.log(f"bug: {bug.failing_check} — {bug.summary}")
        return AgentResult(ok=True, note=bug.suggested_fix[:120])