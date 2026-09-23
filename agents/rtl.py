"""Writes synthesizable Verilog-2005 from the spec, or a targeted fix when a bug report exists."""

from __future__ import annotations

import llm
from agent import NO_INVENTION, NO_SYSTEMVERILOG, AgentResult, BaseAgent, build_prompt
from board import Board, DesignSpec, RTLArtifact
from tools import source_shape_problems, spec_mismatch

SYSTEM = f"""You write synthesizable digital hardware in Verilog-2005.

{NO_SYSTEMVERILOG}

Rules:
- Output the COMPLETE module in `source_code`. Never a diff, never a snippet, never a comment explaining what you changed.
- `module_name` must match the spec's module name exactly.
- `filename` is `<module_name>.v`.
- Include `` `timescale 1ns/1ps `` at the top of the file.
- Keep the port list identical to the spec: same names, same directions, same widths.
- Parameterise widths where the spec allows it.
- If a bug report is provided: fix exactly the described bug with the smallest change. Do not rewrite unrelated logic. Keep the port list identical to the previous attempt.

{NO_INVENTION}"""


def rtl_problems(spec: DesignSpec, rtl: RTLArtifact) -> list[str]:
    return source_shape_problems(rtl.source_code, "rtl") + spec_mismatch(spec, rtl)


class RTLAgent(BaseAgent):
    name = "rtl_agent"
    system = SYSTEM

    def run(self, board: Board) -> AgentResult:
        if board.spec is None:
            return AgentResult(ok=False, err="no spec on the board")

        sections = [("specification", board.spec.model_dump_json(indent=2))]
        if board.latest_rtl is not None:
            sections.append(("previous attempt", board.latest_rtl.source_code))
        if board.latest_bug is not None:
            sections.append(("bug report", board.latest_bug.model_dump_json(indent=2)))
        if board.lint is not None and not board.lint.clean:
            sections.append(("lint errors", "\n".join(board.lint.issues)))
        sections.append(("task", "Write the complete Verilog-2005 module now, as JSON matching the schema."))
        prompt = build_prompt(*sections)

        try:
            rtl = llm.call(
                prompt,
                self.system,
                RTLArtifact,
                "rtl",
                llm.HEAVY,
                board,
                validate=lambda artifact: rtl_problems(board.spec, artifact),
            )
        except llm.LLMError as exc:
            return AgentResult(ok=False, err=str(exc))

        rtl = rtl.model_copy(
            update={
                "filename": f"{rtl.module_name}.v",
                "version": len(board.rtl_history) + 1,  # monotonic, decided here, not by the model
            }
        )
        board.rtl_history.append(rtl)
        board.log(f"rtl v{rtl.version}: {rtl.module_name} via {board.llm_calls[-1].model if board.llm_calls else '?'}")
        return AgentResult(ok=True, note=f"v{rtl.version}, {len(rtl.source_code.splitlines())} lines")
