"""Turns a natural-language request into a DesignSpec. Extraction, nothing else."""

from __future__ import annotations

import llm
from agent import NO_INVENTION, AgentResult, BaseAgent
from board import Board, DesignSpec
from tools import spec_problems

SYSTEM = f"""You extract a precise digital design specification from a natural-language request.

Rules:
- module name: a valid Verilog identifier, lowercase, no spaces, no file extension.
- every I/O port needs name, direction (input or output) and width in bits.
- clock_domain: the clock port name, or "none" for purely combinational logic.
- target_freq_mhz: a number, or null when the request does not state one.
- behavior_notes: three to six sentences covering reset behaviour, normal operation and edge cases.
- assumptions: one line per assumption you had to make, empty list when there were none.
- If the request is too vague to name any port, return an empty io_ports list.

{NO_INVENTION}"""


class SpecAgent(BaseAgent):
    name = "spec_agent"
    system = SYSTEM

    def run(self, board: Board) -> AgentResult:
        if not board.request.strip():
            return AgentResult(ok=False, err="board.request is empty")

        try:
            spec = llm.call(board.request, self.system, DesignSpec, "spec", llm.LIGHT, board)
        except llm.LLMError as exc:
            return AgentResult(ok=False, err=str(exc))

        problems = spec_problems(spec)
        if problems:
            return AgentResult(ok=False, err="; ".join(problems))

        board.spec = spec
        board.log(f"spec: {spec.name}, {len(spec.io_ports)} ports, target {spec.target_freq_mhz} MHz")
        return AgentResult(ok=True, note=f"{len(spec.io_ports)} ports, {len(spec.assumptions)} assumptions")