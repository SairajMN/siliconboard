"""Timing: OpenSTA computes the slack on a sky130-mapped netlist; the model only explains it."""

from __future__ import annotations

import shutil
from pathlib import Path

import llm
from agent import AgentResult, BaseAgent, build_prompt, trim_log
from board import Board, TimingReport
from tools import run_eda, slack_from_sta, stage, unbacked_numbers

STA_IMAGE = "siliconboard/sta:1.0"
LIBERTY_NAME = "sky130hd_tt_025C_1v80.lib"
LIBERTY_SRC = Path(__file__).resolve().parent.parent / "pdk" / LIBERTY_NAME

SYSTEM = """You explain one static-timing result to a hardware engineer.

Rules:
- critical_path: name the startpoint, endpoint and the dominant logic of the worst path, using only
  signals and cells that appear in the STA report you were given.
- explanation: what the slack means against the clock target, and which direction of restructuring
  would recover time if the target was missed.
- Quote every number exactly as it appears in the facts. met_target and worst_slack_ns come from the
  tool; copy them from the verdict line rather than re-deriving them.
Return ONLY the JSON object: no markdown fences, no prose before or after."""


class TimingAgent(BaseAgent):
    name = "timing_agent"
    system = SYSTEM

    def run(self, board: Board) -> AgentResult:
        if board.spec is None:
            return AgentResult(ok=False, err="no spec on the board")
        rtl = board.latest_rtl
        if rtl is None:
            return AgentResult(ok=False, err="no RTL on the board")
        if self.run_dir is None:
            return AgentResult(ok=False, err="timing agent has no run_dir")
        if not LIBERTY_SRC.exists():
            return AgentResult(ok=False, err=f"liberty missing: run pdk/fetch_liberty.py ({LIBERTY_NAME})")

        work = stage(self.run_dir, rtl)
        lib = work / LIBERTY_NAME
        if not lib.exists():
            shutil.copy(LIBERTY_SRC, lib)

        period = 1000.0 / board.spec.target_freq_mhz if board.spec.target_freq_mhz else 10.0
        mapping = (
            f"read_verilog {rtl.filename}; synth -top {rtl.module_name}; "
            f"dfflibmap -liberty {LIBERTY_NAME}; abc -liberty {LIBERTY_NAME}; "
            "opt_clean; write_verilog -noattr netlist.v"
        )
        rc, log = run_eda(["yosys", "-p", mapping], work)
        if rc != 0 or not (work / "netlist.v").exists():
            return AgentResult(ok=False, err="mapping the RTL to sky130 cells failed", log=log)

        clock, inputs, outputs = self._ports(board)
        if clock is None:
            return AgentResult(ok=False, err="the spec has no clock port to constrain")
        (work / "sta.tcl").write_text(self._tcl(clock, rtl.module_name, period, inputs, outputs))
        rc, sta_log = run_eda(["sta", "-no_init", "-exit", "sta.tcl"], work, image=STA_IMAGE, timeout=120)
        slack = slack_from_sta(sta_log)
        if rc != 0 or slack is None:
            return AgentResult(ok=False, err=f"STA produced no slack (rc={rc})", log=sta_log)

        # met_target and worst_slack_ns are written from the parse; any model guess is overwritten
        met = slack >= 0.0
        report = TimingReport(met_target=met, worst_slack_ns=round(slack, 3), verified=True)
        facts = build_prompt(
            ("module", rtl.module_name),
            ("clock", f"port {clock}, period {period:g} ns"),
            ("target", f"{board.spec.target_freq_mhz:g} MHz" if board.spec.target_freq_mhz else "none in spec, 100 MHz assumed"),
            ("verdict from OpenSTA", f"worst slack {slack:g} ns, {'MET' if met else 'VIOLATED'}"),
            ("cells", str(board.synthesis_report.cell_count) if board.synthesis_report else "unknown"),
            ("sta report", trim_log(sta_log, head=60, tail=40)),
            ("task", "Explain the critical path now, as JSON matching the schema."),
        )
        try:
            told = llm.call(
                facts,
                self.system,
                TimingReport,
                "timing",
                llm.LIGHT,
                board,
                validate=lambda r: unbacked_numbers(f"{r.critical_path}\n{r.explanation}", facts),
            )
        except llm.LLMError as exc:
            board.log(f"timing: explanation skipped: {exc}")
        else:
            report.critical_path = told.critical_path
            report.explanation = told.explanation

        board.timing_report = report
        target = board.spec.target_freq_mhz or 100
        board.log(f"timing: slack={report.worst_slack_ns} ns target={target} MHz {'MET' if met else 'MISSED'} via OpenSTA")
        if not met:
            return AgentResult(ok=False, err=f"target missed: worst slack {slack:g} ns at {target} MHz", log=sta_log)
        return AgentResult(ok=True, note=f"slack {report.worst_slack_ns} ns", log=sta_log)

    @staticmethod
    def _ports(board: Board):
        spec = board.spec
        names = {p.name for p in spec.io_ports}
        clock = spec.clock_domain if spec.clock_domain in names else None
        if clock is None:
            clock = next((p.name for p in spec.io_ports if p.direction == "input"), None)
        inputs = [p.name for p in spec.io_ports if p.direction == "input" and p.name != clock]
        outputs = [p.name for p in spec.io_ports if p.direction == "output"]
        return clock, inputs, outputs

    @staticmethod
    def _tcl(clock: str, module: str, period: float, inputs: list[str], outputs: list[str]) -> str:
        delay = period * 0.2
        lines = [
            f"read_liberty {LIBERTY_NAME}",
            "read_verilog netlist.v",
            f"link_design {module}",
            f"create_clock -period {period:.3f} -name clk [get_ports {clock}]",
        ]
        if inputs:
            lines.append(f"set_input_delay -clock clk {delay:.3f} [get_ports {{{' '.join(inputs)}}}]")
        if outputs:
            lines.append(f"set_output_delay -clock clk {delay:.3f} [get_ports {{{' '.join(outputs)}}}]")
        lines.append("report_checks -path_delay max -group_path_count 1")
        return "\n".join(lines) + "\n"