"""Turns a failing simulation log into a bug report the RTL agent can act on — evidence first."""

from __future__ import annotations

import llm
from agent import NO_INVENTION, AgentResult, BaseAgent, build_prompt, trim_log
from board import Board, BugReport
from tools import check_results

SYSTEM = f"""You are debugging a failing hardware simulation.

Given the specification, the RTL, the testbench, and the raw simulation log, produce one bug report:
- failing_check: the exact CHECK name from the log that failed, copied character for character. If the log failed to compile instead, use compile_failed.
- evidence_quote: one verbatim line from the simulation log that shows the failure — the `CHECK <name> FAIL` line or the first %Error line. Copy it exactly; the harness checks it against the log and rejects the report if it does not appear there.
- likely_cause: what the RTL most likely does wrong. Name the exact signal and, when the log gives it, the source line.
- suggested_fix: the smallest change to the RTL that fixes this failure. Never suggest rewriting the module.
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
            ("task", "Write the bug report now, as JSON matching the schema."),
        )

        def gate(bug: BugReport) -> list[str]:
            return bug_problems(bug, sim.raw_log)

        try:
            bug = llm.call(prompt, self.system, BugReport, "debug", llm.HEAVY, board, validate=gate)
        except llm.LLMError as exc:
            return AgentResult(ok=False, err=str(exc))

        board.bug_history.append(bug)
        board.log(f"bug: {bug.failing_check} — {bug.summary}")
        return AgentResult(ok=True, note=bug.suggested_fix[:120])