"""Lint gate: Verilator's exit code is the verdict, no model gets a vote."""

from __future__ import annotations

from agent import AgentResult, BaseAgent
from board import Board, LintResult
from tools import lint, stage


class LintAgent(BaseAgent):
    name = "lint_agent"

    def run(self, board: Board) -> AgentResult:
        rtl = board.latest_rtl
        if rtl is None:
            return AgentResult(ok=False, err="no RTL on the board")
        if self.run_dir is None:
            return AgentResult(ok=False, err="lint agent has no run_dir")

        work = stage(self.run_dir, rtl)
        clean, issues, log = lint(work, rtl.filename, rtl.module_name)
        board.lint = LintResult(clean=clean, issues=issues, raw_log=log)
        if not clean:
            board.log(f"lint: {len(issues)} errors")
            if not issues:
                # the exit code came from docker or a crash, not verilator: say what actually happened
                first = next((ln.strip() for ln in log.splitlines() if ln.strip()), "no output at all")
                return AgentResult(ok=False, err=f"lint tool failed before any error line: {first}", log=log)
            return AgentResult(ok=False, err=f"{len(issues)} lint errors", log=log)
        board.log(f"lint: clean, {len(issues)} warnings")
        return AgentResult(ok=True, log=log, note=f"{len(issues)} warnings")
