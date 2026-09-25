"""Ground truth: runs the real simulator; the log decides, no model involved."""

from __future__ import annotations

import shutil

from agent import AgentResult, BaseAgent
from board import Board, SimResult
from tools import (
    check_counts,
    rtl_module_name,
    simulate,
    simulate_iverilog,
    stage,
    verdict,
    with_vcd,
)


class SimRunnerAgent(BaseAgent):
    name = "sim_runner_agent"
    # set by the orchestrator from the CLI flags; both stay off for normal runs
    cross_sim: bool = False
    waves: bool = False

    def run(self, board: Board) -> AgentResult:
        rtl, tb = board.latest_rtl, board.testbench
        if rtl is None or tb is None:
            return AgentResult(ok=False, err="need both RTL and a testbench on the board")
        if self.run_dir is None:
            return AgentResult(ok=False, err="sim runner has no run_dir")

        # the testbench is the simulation top; the DUT must never be
        tb_top = rtl_module_name(tb.source_code) or f"tb_{rtl.module_name}"
        work = stage(self.run_dir, rtl, self._tb_with_waves(tb) if self.waves else tb)
        rc, log = simulate(work, rtl.filename, tb.filename, tb_top, trace=self.waves)

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
        result.wave_file = self._collect_wave(work) if self.waves else None
        board.sim_history.append(result)
        board.log(f"sim: checks={total} failed={failed} verdict={passed}")

        if passed is None:
            board.ambiguity = result.ambiguity
            return AgentResult(ok=False, err="ambiguous sim verdict, refusing to guess", log=log)
        if not passed:
            return AgentResult(ok=False, err=f"simulator reported FAIL: {failed} of {total} checks failed", log=log)

        if not self.cross_sim:
            return AgentResult(ok=True, note=f"checks={total} failed={failed}")

        return self._cross_check(board, work, rtl, tb, tb_top, result, total, failed)

    def _tb_with_waves(self, tb):
        return tb.model_copy(update={"source_code": with_vcd(tb.source_code, "waves/dump.vcd")})

    def _collect_wave(self, work) -> str | None:
        produced = [p for p in work.glob("obj_dir/*.vcd")]
        if not produced:
            return None
        target = self.run_dir / "waves"
        target.mkdir(parents=True, exist_ok=True)
        moved = target / "dump.vcd"
        shutil.move(str(produced[0]), moved)
        return str(moved.relative_to(self.run_dir.parent.parent))

    def _cross_check(self, board, work, rtl, tb, tb_top, result, total, failed) -> AgentResult:
        rc2, log2 = simulate_iverilog(work, rtl.filename, tb.filename, tb_top)
        second = verdict(log2) if rc2 == 0 else None
        result.cross_checked = True
        result.cross_agreed = second is not None and second == result.passed
        result.cross_log = log2
        board.log(f"cross-check: icarus says {second} (verilator said {result.passed})")

        if second is None:
            result.ambiguity = "the second simulator produced no usable verdict"
            return AgentResult(ok=False, err=result.ambiguity, log=log2)
        if not result.cross_agreed:
            return AgentResult(
                ok=False,
                err=f"simulators disagree: verilator {result.passed}, icarus {second}",
                log=log2,
            )
        return AgentResult(ok=True, note=f"checks={total} failed={failed} agreed={second}")
