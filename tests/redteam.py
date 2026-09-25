"""Adversarial checks: feed the harness fabricated claims and prove it refuses them.

Every case here is offline. A fake provider stands in for the real API so the gate is
tested, not the model. Grows one case per LLM-backed agent as they land.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm
from agents.debug import DebugAgent, bug_problems
from agents.report import ReportAgent
from board import (
    Board,
    BugReport,
    DesignSpec,
    LintResult,
    Port,
    RTLArtifact,
    SimResult,
    SynthesisReport,
    TestbenchArtifact,
)
from tools import check_results, fail_lines, unbacked_numbers

RTL_SRC = (ROOT / "smoke" / "counter.v").read_text()
TB_SRC = (ROOT / "smoke" / "tb_counter.v").read_text()
SIM_LOG = "CHECK reset PASS\nCHECK count_three FAIL\nSUMMARY checks=2 failed=1\nFAIL\n"

FACT_BOARD = Board(
    request="an 8-bit counter",
    spec=DesignSpec(
        name="counter",
        description="8 bit up counter",
        io_ports=[Port(name="clk", direction="input", width=1), Port(name="q", direction="output", width=8)],
        target_freq_mhz=200.0,
        behavior_notes="sync reset",
    ),
    rtl_history=[RTLArtifact(module_name="counter", filename="counter.v", source_code=RTL_SRC, version=1)],
    lint=LintResult(clean=True),
    testbench=TestbenchArtifact(filename="tb_counter.v", source_code=TB_SRC, test_vectors_description="fixture"),
    sim_history=[SimResult(passed=False, total_checks=2, failed_checks=1, raw_log=SIM_LOG)],
    synthesis_report=SynthesisReport(cell_count=24),
)


def the_number_gate_only_knows_board_numbers() -> None:
    facts = "cells 24, target 200.0, version 1"
    assert unbacked_numbers("Synthesis reported 24 cells at 200.0 MHz.", facts) == []
    assert unbacked_numbers("Synthesis reported 4211 cells.", facts) == ["4211"]

    # a timestamp in the facts is not a fact: it must not excuse an invented time
    stamped = '{"created_at": "2026-09-19T07:17:29.123456+00:00", "cell_count": 24}'
    assert unbacked_numbers("Synthesis finished at 07:17:29 with 24 cells.", stamped) == ["07", "17", "29"]

    # markdown ordered-list markers and thousands separators are formatting, not claims
    assert unbacked_numbers("1. cells 1024\n2. done", "cells 1024") == []


def check_names_are_parsed_not_guessed() -> None:
    assert check_results(SIM_LOG) == {"reset": True, "count_three": False}
    # FAIL lines in real testbenches end with got=/want= — the parser must not drop them
    verbose = "CHECK hold_1 FAIL got=4 want=3\nCHECK inc_1 PASS\nSUMMARY checks=2 failed=1\nFAIL\n"
    assert check_results(verbose) == {"hold_1": False, "inc_1": True}, check_results(verbose)
    assert fail_lines(verbose) == [("hold_1", "4", "3")], fail_lines(verbose)


class _FakeReport:
    def __init__(self, markdown: str):
        self.markdown = markdown
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return json.dumps({"markdown_summary": self.markdown}), 10, 20


def run_report_agent(markdown: str, run_dir: Path):
    run_dir.mkdir(parents=True, exist_ok=True)
    llm._rings.clear()
    llm._cooling.clear()
    llm._dead.clear()
    fake = _FakeReport(markdown)
    board = FACT_BOARD.model_copy(deep=True)
    original_cache = llm._cache_dir
    llm.set_cache_dir(run_dir / "llm")
    try:
        agent = ReportAgent()
        agent.run_dir = run_dir
        with llm.fake_all_providers(fake):
            result = agent.run(board)
    finally:
        llm._cache_dir = original_cache
    return result, board, fake


def report_agent_refuses_an_invented_number(root: Path) -> None:
    run_dir = root / "reject"
    result, board, fake = run_report_agent("Synthesis used 4211 cells over 3 retries.", run_dir)
    assert not result.ok, "an invented number must never be narrated as fact"
    assert "4211" in (result.err or ""), result.err
    assert fake.calls > 1, "the gate must re-prompt instead of accepting the first answer"
    assert board.run_report is None, "a rejected report must not land on the Board"
    assert not (run_dir / "report.md").exists(), "no report file after a rejected report"


def report_agent_accepts_backed_numbers(root: Path) -> None:
    markdown = "Synthesis reported 24 cells. Simulation ran 2 checks, 1 failed (count_three)."
    result, board, _ = run_report_agent(markdown, root / "accept")
    assert result.ok, result.err
    assert board.run_report is not None
    assert (root / "accept" / "report.md").read_text().strip() == markdown


class _FakeBug:
    def __init__(self, bug: BugReport):
        self.bug = bug
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.bug.model_dump_json(), 10, 20


def run_debug_agent(bug: BugReport, run_dir: Path):
    run_dir.mkdir(parents=True, exist_ok=True)
    llm._rings.clear()
    llm._cooling.clear()
    fake = _FakeBug(bug)
    board = FACT_BOARD.model_copy(deep=True)
    original_cache = llm._cache_dir
    llm.set_cache_dir(run_dir / "llm")
    try:
        with llm.fake_all_providers(fake):
            result = DebugAgent().run(board)
    finally:
        llm._cache_dir = original_cache
    return result, board, fake


def the_debug_gate_checks_both_the_quote_and_the_check_name() -> None:
    # quote is verbatim in the log, but reset PASSED: the bug blames the wrong check
    wrong_check = BugReport(
        summary="reset broken",
        likely_cause="reset logic line 9",
        failing_check="reset",
        suggested_fix="fix the reset polarity on line 9",
        evidence_quote="CHECK reset PASS",
    )
    problems = bug_problems(wrong_check, SIM_LOG)
    assert any("not one of the failed checks" in p for p in problems), problems


def debug_agent_refuses_a_fabricated_quote(root: Path) -> None:
    fabricated = BugReport(
        summary="count never increments",
        likely_cause="count_reg not updated on line 12",
        failing_check="count_three",
        suggested_fix="change line 12 to count_reg <= count_reg + 1",
        evidence_quote="CHECK count_three FAIL got=99 want=3",
    )
    result, board, fake = run_debug_agent(fabricated, root / "debug-reject")
    assert not result.ok, "a quote that is not in the log must never be accepted"
    assert "evidence_quote" in (result.err or ""), result.err
    assert fake.calls > 1, "the gate must re-prompt instead of accepting the first answer"
    assert board.bug_history == [], "a fabricated bug report must never land on the Board"


def debug_agent_accepts_a_real_quote(root: Path) -> None:
    honest = BugReport(
        summary="count_three fails against correct RTL",
        likely_cause="off-by-one in the increment condition",
        failing_check="count_three",
        suggested_fix="increment one cycle earlier: move the enable check before the reset branch",
        evidence_quote="CHECK count_three FAIL",
    )
    result, board, _ = run_debug_agent(honest, root / "debug-accept")
    assert result.ok, result.err
    assert len(board.bug_history) == 1, "a cited, verifiable bug report lands on the Board"


def debug_gate_only_earns_a_testbench_blame_from_cross_version_evidence(root: Path) -> None:
    # one attempt only: blaming the testbench is not yet earned
    lone = BugReport(
        summary="expectations look wrong",
        likely_cause="stimulus changes a negedge late",
        failing_check="count_three",
        suggested_fix="sample one edge later in the testbench",
        evidence_quote="CHECK count_three FAIL",
        blames="testbench",
    )
    result, board, fake = run_debug_agent(lone, root / "blame-lone")
    assert not result.ok, "a testbench blame without cross-version evidence must be rejected"
    assert "two simulation attempts" in (result.err or ""), result.err
    assert fake.calls > 1, "the gate must re-prompt instead of accepting the first answer"
    assert board.bug_history == []

    # the same check failed identically under two rtl versions: the blame is earned
    evidenced = "CHECK reset PASS\nCHECK count_three FAIL got=99 want=3\nSUMMARY checks=2 failed=1\nFAIL\n"
    board2 = FACT_BOARD.model_copy(deep=True)
    board2.sim_history = [
        SimResult(passed=False, raw_log=evidenced, rtl_version=1),
        SimResult(passed=False, raw_log=evidenced, rtl_version=2),
    ]
    import llm as _llm
    from agents.debug import DebugAgent as _Debug

    _llm._rings.clear()
    _llm._cooling.clear()
    fake2 = _FakeBug(lone)
    with _llm.fake_all_providers(fake2):
        result2 = _Debug().run(board2)
    assert result2.ok, result2.err
    assert board2.bug_history[-1].blames == "testbench"


def main() -> None:
    os.environ.setdefault("GROQ_API_KEY_1", "g1000000000000000000")
    the_number_gate_only_knows_board_numbers()
    check_names_are_parsed_not_guessed()
    the_debug_gate_checks_both_the_quote_and_the_check_name()
    root = Path(tempfile.mkdtemp(prefix="sb-redteam-"))
    try:
        report_agent_refuses_an_invented_number(root)
        report_agent_accepts_backed_numbers(root)
        debug_agent_refuses_a_fabricated_quote(root)
        debug_agent_accepts_a_real_quote(root)
        debug_gate_only_earns_a_testbench_blame_from_cross_version_evidence(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("redteam: invented numbers, fabricated quotes, and unearned testbench blames rejected")


if __name__ == "__main__":
    main()