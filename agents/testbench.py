"""Writes a self-checking testbench from the SPEC's behaviour, not the RTL's implementation."""

from __future__ import annotations

import re

import llm
from agent import NO_INVENTION, AgentResult, BaseAgent, build_prompt
from board import Board, RTLArtifact, TestbenchArtifact
from tools import lint, rtl_module_name, source_shape_problems, stage

SYSTEM = f"""You write a self-checking Verilog-2005 testbench for the given module.

Rules:
- Test the SPEC's intended behaviour. Do not assume the RTL under test is correct, and do not read logic values back to decide what should happen: the expected values come from the spec.
- `` `timescale 1ns/1ps `` at the top.
- Instantiate the DUT by port name.
- Generate a clock (10 ns period) and drive the reset as the spec describes.
- Give every reg you drive an explicit value at time 0, before the first clock edge. An input left at X makes every check on it meaningless.
- Release the reset before the first check that expects a non-zero result, not later in the file. A design still in reset reads its reset value and every such check fails even when the RTL is correct.
- Declare any string passed to a task wide enough for the longest name you pass: for a 24-character longest name that is `input [8*24-1:0] name;` (8 bits per character). The harness rejects any name longer than the declared width.
- Keep every check name at most 32 characters: `sub_wrap`, never `subtraction_wraps_from_0x80_to_0x7f`. If a name would be longer, shorten the name — do not widen the port instead.
- Never change stimulus on a clock edge. Change inputs on the negedge of the clock (or `#1` after a posedge), otherwise the testbench races the DUT and failures are fake.
- Sample the DUT output only after the clock edge that should have produced it: wait for the next negedge, then compare. A check that runs inside `always @(posedge clk)` reads the value from before the edge and will report a false failure.
- Keep all checking in one `initial` block. Declare `integer checks = 0; integer failed = 0;` and update them with blocking assignments only (`failed = failed + 1;`). A `<=` update on a scoreboard counter races that block and reports the wrong summary.
- Derive every expected value from the spec. Do not assert an absolute cycle number (a `cycle == 258` style expectation) unless the spec itself states that count.
- Include: one reset check, at least one nominal-case check, and at least one edge-case check (boundary values, back-to-back operations, full/empty or overflow conditions).
- Every check prints exactly one line: `CHECK <name> PASS` or `CHECK <name> FAIL got=<observed> want=<expected>`. A FAIL line without both values is rejected.
- Print a final `SUMMARY checks=<n> failed=<m>` line.
- Then print exactly one terminal verdict on its own line: `PASS` if failed==0, otherwise `FAIL`. The simulation runner greps for this — a missing or duplicated verdict line fails the run.
- End with `$finish`.
- `filename` is `tb_<module_name>.v`.
- Output the COMPLETE file in `source_code`. Never a diff or snippet.

{NO_INVENTION}"""


_RESET_INPUT = re.compile(r"(^|_)(rst|reset)(_n|n|_b|b)?$", re.I)


def tb_problems(tb: TestbenchArtifact, dut: str, spec: DesignSpec | None = None) -> list[str]:
    problems = source_shape_problems(tb.source_code, "testbench")
    if rtl_module_name(tb.source_code) is None:
        problems.append("no module declaration in the testbench")
    if not re.search(rf"\b{re.escape(dut)}\s+\w+\s*\(", tb.source_code):
        problems.append(f"testbench never instantiates {dut}")
    if '$display("PASS")' not in tb.source_code or '$display("FAIL")' not in tb.source_code:
        problems.append('testbench lacks the terminal $display("PASS")/$display("FAIL") verdict')
    if "$finish" not in tb.source_code:
        problems.append("testbench never calls $finish")
    fail_lines = " ".join(ln for ln in tb.source_code.splitlines() if "FAIL" in ln and "$display" in ln)
    if not fail_lines or "got" not in fail_lines or "want" not in fail_lines:
        problems.append("the FAIL line must print got and want values, the debug agent has no other evidence")
    # both rules below come from a real testbench the simulator caught in runs/w2-repair
    if re.search(r"\b(checks|failed|passes|failures)\s*<=", tb.source_code, re.I):
        problems.append("scoreboard counters updated with a non-blocking assignment, which races the initial block")
    if re.search(r"always\s*@\s*\(\s*posedge[^)]*\)[\s\S]*?\$display\(\s*\"CHECK", tb.source_code):
        problems.append("runs checks inside an always @(posedge clk) block, so it samples before the edge")
    problems.extend(_name_width_problems(tb))
    if spec is not None:
        problems.extend(_reset_order_problems(tb, spec))
    return problems


def _name_width_problems(tb: TestbenchArtifact) -> list[str]:
    """A check name wider than the task's port is truncated silently, and the log loses
    which check failed: 'enable_increment_1' printed as 'rement_1' in runs/w2-live3."""
    width = None
    for m in re.finditer(r"input\s*\[\s*8\s*\*\s*(\d+)\s*-\s*1\s*:\s*0\s*\]", tb.source_code):
        width = int(m.group(1)) if width is None else max(width, int(m.group(1)))
    if width is None:
        return []
    longest = max((len(s) for s in re.findall(r'"([^"]*)"', tb.source_code)), default=0)
    if longest > width:
        return [
            f"the check-name port holds {width} characters but a {longest}-character name is passed, "
            "so printed names are truncated"
        ]
    return []


def _reset_order_problems(tb: TestbenchArtifact, spec: DesignSpec) -> list[str]:
    """A reset still asserted when a non-zero expectation is checked makes the design read its
    reset value: runs/w2-live3 and w2-live4 failed against correct RTL for exactly this reason.

    The expectation may be any argument of the check call, so every literal on the line counts:
    check("count_1", count, 8'd1) and check_value(8'd1, "count_1") are both read correctly.
    """
    bases = {"d": 10, "h": 16, "b": 2, "o": 8}
    for port in spec.io_ports:
        if port.direction != "input" or not _RESET_INPUT.search(port.name):
            continue
        active_low = bool(re.search(r"(_n|n|_b|b)$", port.name, re.I))
        deassert = r"1'b1|1" if active_low else r"1'b0|0"
        lines = tb.source_code.splitlines()
        released = next(
            (
                i
                for i, ln in enumerate(lines)
                if re.search(rf"\b{port.name}\s*[<]?=\s*({deassert})\s*;", ln) and "<=" not in ln
            ),
            None,
        )
        for i, ln in enumerate(lines):
            if i >= (released if released is not None else len(lines)):
                continue
            if not re.search(r"\bcheck\w*\s*\(", ln):
                continue
            values = [
                int(digits, bases[base])
                for base, digits in re.findall(r"\d+\s*'\s*([dhbo])\s*([0-9a-fA-F]+)", ln)
            ]
            values += [int(v) for v in re.findall(r",\s*(\d+)\s*[,)]", ln)]
            if any(v != 0 for v in values):
                where = "never released" if released is None else f"released later, on line {released + 1}"
                return [
                    f"{port.name} is {where}, but the check on line {i + 1} expects a non-zero result, "
                    "so the design reads its reset value and the check fails against correct RTL"
                ]
    return []


def tb_lint_problems(tb: TestbenchArtifact, rtl: RTLArtifact, run_dir: Path) -> list[str]:
    top = rtl_module_name(tb.source_code)
    if top is None:
        return ["no module declaration in the testbench"]
    work = stage(run_dir, rtl, tb)
    clean, issues, _ = lint(work, [rtl.filename, tb.filename], top, timing=True)
    return [] if clean else [f"the testbench does not compile: {issue}" for issue in issues[:5]]


class TestbenchAgent(BaseAgent):
    name = "testbench_agent"
    system = SYSTEM

    def run(self, board: Board) -> AgentResult:
        if board.spec is None:
            return AgentResult(ok=False, err="no spec on the board")
        rtl = board.latest_rtl
        if rtl is None:
            return AgentResult(ok=False, err="no RTL on the board")
        dut = rtl.module_name

        # the testbench sees the spec and the port map, never the RTL body
        sections = [("specification", board.spec.model_dump_json(indent=2))]
        if board.latest_bug is not None and board.latest_bug.blames == "testbench":
            sections.append(
                (
                    "the previous testbench was rejected",
                    board.latest_bug.model_dump_json(indent=2)
                    + "\nWrite a different testbench: recheck the expected value and the edge you apply each "
                    "stimulus change on. Changing an input on a negedge means the next posedge still sees the "
                    "OLD value — derive want= from the value the DUT holds at the sampling edge.",
                )
            )
        sections.append(("dut port map", str({p.name: (p.direction, p.width) for p in board.spec.io_ports})))
        sections.append(("dut module name", dut))
        sections.append(("task", "Write the complete self-checking testbench now, as JSON matching the schema."))
        prompt = build_prompt(*sections)

        def gate(artifact: TestbenchArtifact) -> list[str]:
            problems = tb_problems(artifact, dut, board.spec)
            if problems or self.run_dir is None:
                return problems
            return tb_lint_problems(artifact, rtl, self.run_dir)

        try:
            tb = llm.call(prompt, self.system, TestbenchArtifact, "testbench", llm.LIGHT, board, validate=gate)
        except llm.LLMError as exc:
            return AgentResult(ok=False, err=str(exc))

        tb = tb.model_copy(update={"filename": f"tb_{dut}.v"})
        board.testbench = tb
        board.log(f"testbench: {rtl_module_name(tb.source_code)}, {len(tb.source_code.splitlines())} lines")
        return AgentResult(ok=True, note=tb.test_vectors_description[:120])