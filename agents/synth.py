"""Real Yosys run; the cell count comes from parsing, never from a model."""

from __future__ import annotations

from agent import AgentResult, BaseAgent
from board import Board, SynthesisReport
from tools import cells_from_stat, run_eda, stage


class SynthesisAgent(BaseAgent):
    name = "synthesis_agent"

    def run(self, board: Board) -> AgentResult:
        rtl = board.latest_rtl
        if rtl is None:
            return AgentResult(ok=False, err="no RTL on the board")
        if self.run_dir is None:
            return AgentResult(ok=False, err="synthesis agent has no run_dir")

        work = stage(self.run_dir, rtl)
        script = f"read_verilog {rtl.filename}; synth -top {rtl.module_name}; stat"
        rc, log = run_eda(["yosys", "-p", script], work)
        if rc != 0:
            return AgentResult(ok=False, err="synthesis_failed", log=log)

        cells = cells_from_stat(log)
        board.synthesis_report = SynthesisReport(cell_count=cells, raw_log=log)
        if cells is None:
            return AgentResult(ok=False, err="yosys stat printed no cell count", log=log)
        board.log(f"synth: {cells} cells")
        return AgentResult(ok=True, log=log, note=f"{cells} cells")
