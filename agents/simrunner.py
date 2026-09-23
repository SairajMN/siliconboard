"""Ground truth: runs the real simulator; the log decides, no model involved."""

from __future__ import annotations

from agent import AgentResult, BaseAgent
from board import Board, SimResult
from tools import check_counts, rtl_module_name, simulate, stage, verdict


class SimRunnerAgent(BaseAgent):
    name = "sim_runner_agent"

    def run(self, board: Board) -> AgentResult:
        rtl, tb = board.latest_rtl, board.testbench
        if rtl is None or tb is None:
            return AgentResult(ok=False, err="need both RTL and a testbench on the board")
        if self.run_dir is None:
            return AgentResult(ok=False, err="sim runner has no run_dir")

        # the testbench is the simulation top; the DUT must never be
        tb_top = rtl_module_name(tb.source_code) or f"tb_{rtl.module_name}"
        work = stage(self.run_dir, rtl, tb)
        rc, log = simulate(work, rtl.filename, tb.filename, tb_top)

        if rc != 0:
            board.sim_history.append(
                SimResult(passed=None, raw_log=log, ambiguity="compile_failed", rtl_version=rtl.version)
            )
            board.log("sim: compile failed")
            return AgentResult(ok=False, err="compile_failed", log=log)

        passed = verdict(log)
        total, failed = check_counts(log)
        result = SimResult(passed=passed, total_checks=total, failed_checks=failed, raw_log=log, rtl_version=rtl.version)
        if passed is None:
            result.ambiguity = "log has no single terminal PASS/FAIL line"
        board.sim_history.append(result)
        board.log(f"sim: checks={total} failed={failed} verdict={passed}")

        if passed is None:
            board.ambiguity = result.ambiguity
            return AgentResult(ok=False, err="ambiguous sim verdict, refusing to guess", log=log)
        if not passed:
            return AgentResult(ok=False, err=f"simulator reported FAIL: {failed} of {total} checks failed", log=log)
        return AgentResult(ok=True, note=f"checks={total} failed={failed}")
