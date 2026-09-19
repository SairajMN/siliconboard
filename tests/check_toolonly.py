"""Tool-only agents against real docker tools: good pair passes, sabotaged pair fails,
ambiguous log refuses to guess, and neither agent ever mentions the LLM."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import AgentResult  # noqa: E402
from agents.lint import LintAgent  # noqa: E402
from agents.simrunner import SimRunnerAgent  # noqa: E402
from agents.synth import SynthesisAgent  # noqa: E402
from agents.testbench import tb_lint_problems  # noqa: E402
from board import Board, RTLArtifact, TestbenchArtifact  # noqa: E402

RTL_SRC = (ROOT / "smoke" / "counter.v").read_text()
TB_SRC = (ROOT / "smoke" / "tb_counter.v").read_text()

GOOD_RTL = RTLArtifact(module_name="counter", filename="counter.v", source_code=RTL_SRC, version=1)
GOOD_TB = TestbenchArtifact(
    filename="tb_counter.v", source_code=TB_SRC, test_vectors_description="smoke tb"
)
SABOTAGED_RTL = RTLArtifact(
    module_name="counter", filename="counter.v",
    source_code=RTL_SRC.replace("q <= q + 1'b1;", "q <= q + 8'd2;"), version=2,
)
COMPILE_BROKEN_RTL = RTLArtifact(
    module_name="counter", filename="counter.v",
    # an undeclared LHS would auto-create an implicit wire and compile;
    # an undeclared RHS identifier is a hard error
    source_code=RTL_SRC.replace("q <= q + 1'b1;", "q <= q + missing_wire;"), version=3,
)
AMBIGUOUS_TB = TestbenchArtifact(
    filename="tb_counter.v", source_code=TB_SRC.replace('check("reset"', '$display("PASS");\n        check("reset"'),
    test_vectors_description="two terminal verdict lines",
)
NO_VERDICT_TB = TestbenchArtifact(
    filename="tb_counter.v", source_code=TB_SRC.replace('$display("PASS");', "").replace('$display("FAIL");', ""),
    test_vectors_description="no terminal verdict",
)


def fresh(board: Board) -> tuple[Board, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="sbtoolonly-"))
    for cls in (LintAgent, SimRunnerAgent, SynthesisAgent):
        cls.run_dir = tmp
    return board, tmp


def lint_ok() -> None:
    board, tmp = fresh(Board(status="linting", rtl_history=[GOOD_RTL]))
    r = LintAgent().run(board)
    assert r.ok, r.err
    assert board.lint is not None and board.lint.clean
    shutil.rmtree(tmp)


def lint_catches_broken() -> None:
    board, tmp = fresh(Board(status="linting", rtl_history=[COMPILE_BROKEN_RTL]))
    r = LintAgent().run(board)
    assert not r.ok and board.lint is not None and not board.lint.clean
    assert board.lint.issues
    shutil.rmtree(tmp)


def sim_pass() -> None:
    board, tmp = fresh(Board(status="simulating", rtl_history=[GOOD_RTL], testbench=GOOD_TB))
    r = SimRunnerAgent().run(board)
    assert r.ok, r.err
    assert board.latest_sim.passed is True
    assert board.latest_sim.total_checks == 4 and board.latest_sim.failed_checks == 0
    shutil.rmtree(tmp)


def sim_catches_bug() -> None:
    board, tmp = fresh(Board(status="simulating", rtl_history=[SABOTAGED_RTL], testbench=GOOD_TB))
    r = SimRunnerAgent().run(board)
    assert not r.ok and board.latest_sim.passed is False
    assert board.latest_sim.failed_checks and board.latest_sim.failed_checks > 0
    shutil.rmtree(tmp)


def ambiguous_refused() -> None:
    board, tmp = fresh(Board(status="simulating", rtl_history=[GOOD_RTL], testbench=AMBIGUOUS_TB))
    r = SimRunnerAgent().run(board)
    assert not r.ok and board.latest_sim.passed is None
    assert board.ambiguity and "no single terminal" in board.ambiguity
    board, tmp = fresh(Board(status="simulating", rtl_history=[GOOD_RTL], testbench=NO_VERDICT_TB))
    r = SimRunnerAgent().run(board)
    assert not r.ok and board.latest_sim.passed is None
    shutil.rmtree(tmp)


def compile_fail() -> None:
    board, tmp = fresh(Board(status="simulating", rtl_history=[COMPILE_BROKEN_RTL], testbench=GOOD_TB))
    r = SimRunnerAgent().run(board)
    assert not r.ok and r.err == "compile_failed"
    assert board.latest_sim.ambiguity == "compile_failed"
    shutil.rmtree(tmp)


def synth_cells() -> None:
    board, tmp = fresh(Board(status="synthesizing", rtl_history=[GOOD_RTL]))
    r = SynthesisAgent().run(board)
    assert r.ok, r.err
    assert board.synthesis_report.cell_count and board.synthesis_report.cell_count > 0
    shutil.rmtree(tmp)


def no_llm_anywhere() -> None:
    for path in ("agents/simrunner.py", "agents/synth.py", "agents/lint.py"):
        assert "llm" not in (ROOT / path).read_text().lower(), f"{path} references the LLM"


def missing_inputs_refused() -> None:
    for cls in (LintAgent, SimRunnerAgent, SynthesisAgent):
        cls.run_dir = None
    r = SimRunnerAgent().run(Board(status="simulating"))
    assert not r.ok and "testbench" in r.err
    r = LintAgent().run(Board(status="linting"))
    assert not r.ok and "no RTL" in r.err
    r = SynthesisAgent().run(Board(status="synthesizing", rtl_history=[GOOD_RTL]))
    assert not r.ok and "no run_dir" in r.err


def tb_lint_gate() -> None:
    # the real testbench gate: structural problems are free, a compiling one costs one verilator run
    good = TestbenchArtifact(filename="tb_counter.v", source_code=TB_SRC, test_vectors_description="fixture")
    broken = good.model_copy(update={"source_code": good.source_code.replace("counter dut", "missing_module dut")})
    tmp = Path(tempfile.mkdtemp())
    assert tb_lint_problems(good, GOOD_RTL, tmp) == []
    problems = tb_lint_problems(broken, GOOD_RTL, tmp)
    assert problems and "does not compile" in problems[0], problems
    shutil.rmtree(tmp)


def main() -> None:
    lint_ok()
    lint_catches_broken()
    tb_lint_gate()
    sim_pass()
    sim_catches_bug()
    ambiguous_refused()
    compile_fail()
    synth_cells()
    no_llm_anywhere()
    missing_inputs_refused()
    print("tool-only agents: lint/sim/synth verified on real docker tools, ambiguity refused, no LLM refs")


if __name__ == "__main__":
    main()
