"""Routing: forward, backward, and per-stage caps. Stubbed agents, no docker, no llm."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import orchestrator as orch
import llm
import tools
from agent import AgentResult, BaseAgent
from board import Board, DesignStatus, RunReport


class Stub(BaseAgent):
    def __init__(self, name: str, fail_times: int):
        self.name = name
        self.fail_times = fail_times
        self.calls = 0

    def run(self, board: Board) -> AgentResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            return AgentResult(ok=False, err=f"{self.name} stub failure")
        return AgentResult(ok=True)


def stubs(**fails: int) -> dict[DesignStatus, BaseAgent]:
    return {status: Stub(agent.name, fails.get(agent.name, 0)) for status, agent in orch.AGENTS.items()}


def run_with(agent_table, inject=None) -> tuple[Board, Path, dict]:
    tmp = Path(tempfile.mkdtemp(prefix="sbrouting-"))
    original = orch.AGENTS
    orch.AGENTS = agent_table
    try:
        board = Board(request="stub")
        orch.Orchestrator(board, tmp, inject=inject).run()
    finally:
        orch.AGENTS = original
    return board, tmp, agent_table


def calls(table, name: str) -> int:
    return next(a for a in table.values() if a.name == name).calls


def forward_pass_reaches_done() -> None:
    board, tmp, table = run_with(stubs())
    assert board.status == DesignStatus.DONE, board.status
    assert board.stage_retries == {}, "a clean run records no retries"
    counts = {a.name: a.calls for a in table.values()}
    assert all(n == (0 if name == "debug_agent" else 1) for name, n in counts.items()), counts
    shutil.rmtree(tmp)


def a_sim_failure_routes_through_debug_and_back() -> None:
    board, tmp, table = run_with(stubs(sim_runner_agent=1))
    assert board.status == DesignStatus.DONE, board.status
    assert board.stage_retries == {"sim_runner_agent": 1}
    assert "back to debugging" in "\n".join(board.event_log)
    assert calls(table, "debug_agent") == 1, "the debug agent triaged the one failure"
    assert calls(table, "rtl_agent") == 2, "rtl regenerated once, after the debug report"
    shutil.rmtree(tmp)


def exhausted_retries_fail_the_run() -> None:
    board, tmp, table = run_with(stubs(sim_runner_agent=99))
    assert board.status == DesignStatus.FAILED, board.status
    assert board.stage_retries["sim_runner_agent"] == 4, "3 retries allowed, the 4th must give up"
    assert calls(table, "debug_agent") == 3, "one triage per allowed retry"
    assert any("giving up" in line for line in board.event_log)
    shutil.rmtree(tmp)


def lint_failures_cap_at_three() -> None:
    board, tmp, table = run_with(stubs(lint_agent=99))
    assert board.status == DesignStatus.FAILED, board.status
    assert board.stage_retries["lint_agent"] == 4
    assert calls(table, "rtl_agent") == 4, "initial write plus three targeted rewrites"
    shutil.rmtree(tmp)


def testbench_failures_retry_the_stage_before_giving_up() -> None:
    board, tmp, table = run_with(stubs(testbench_agent=99))
    assert board.status == DesignStatus.FAILED, board.status
    assert board.stage_retries["testbench_agent"] == 3, "2 stage retries allowed, the 3rd must give up"
    assert calls(table, "rtl_agent") == 1, "an unusable testbench must not burn RTL rewrites"
    assert "back to writing_testbench" in "\n".join(board.event_log)
    shutil.rmtree(tmp)


def testbench_recovers_on_second_stage_pass() -> None:
    board, tmp, table = run_with(stubs(testbench_agent=1))
    assert board.status == DesignStatus.DONE, board.status
    assert board.stage_retries == {"testbench_agent": 1}
    assert calls(table, "rtl_agent") == 1, "rtl is untouched by a testbench retry"
    shutil.rmtree(tmp)


def a_timing_miss_reworks_then_ships_with_warning() -> None:
    board, tmp, table = run_with(stubs(timing_agent=99))
    assert board.status == DesignStatus.DONE_WITH_WARNING, board.status
    assert board.stage_retries["timing_agent"] == 3, "2 reworks allowed, the 3rd miss ships flagged"
    assert calls(table, "rtl_agent") == 3, "initial write plus two timing-driven reworks"
    assert any("shipping with warning" in line for line in board.event_log)
    shutil.rmtree(tmp)


def timing_recovers_after_one_rework() -> None:
    board, tmp, table = run_with(stubs(timing_agent=1))
    assert board.status == DesignStatus.DONE, board.status
    assert board.stage_retries == {"timing_agent": 1}
    assert calls(table, "rtl_agent") == 2, "one rework, then the timing stage passed"
    shutil.rmtree(tmp)


def a_testbench_blame_reroutes_to_writing_the_testbench() -> None:
    from board import BugReport

    class BlameTB(Stub):
        def run(self, board):
            self.calls += 1
            board.bug_history.append(
                BugReport(
                    summary="hold expectation is off by one",
                    likely_cause="stimulus changes enable a negedge late",
                    failing_check="hold_1",
                    suggested_fix="sample after the edge that consumes the new enable value",
                    evidence_quote="CHECK hold_1 FAIL got=4 want=3",
                    blames="testbench",
                )
            )
            return AgentResult(ok=True)

    table = stubs(sim_runner_agent=1)
    table[DesignStatus.DEBUGGING] = BlameTB("debug_agent", 0)
    board, tmp, _ = run_with(table)
    assert board.status == DesignStatus.DONE, board.status
    assert "back to writing_testbench" in "\n".join(board.event_log)
    assert calls(table, "testbench_agent") == 2, "the testbench must be rewritten after a testbench blame"
    assert calls(table, "rtl_agent") == 1, "a testbench blame must not regenerate the RTL"
    shutil.rmtree(tmp)


def replay_rebuilds_the_report_without_calling_anything() -> None:
    import main as cli

    tmp = Path(tempfile.mkdtemp())
    (tmp / "runs" / "r").mkdir(parents=True)
    board = Board(request="counter")
    board.status = DesignStatus.DONE
    board.run_report = RunReport(markdown_summary="# done\n\n24 cells.\n")
    board.save(tmp / "runs" / "r")

    # any outbound call is a bug, so make them all explode
    class Boom(Exception):
        pass

    real_call, real_ship = llm.call, tools.run_eda
    llm.call = tools.run_eda = lambda *a, **k: (_ for _ in ()).throw(Boom("replay made a call"))
    cli.ROOT = tmp
    try:
        assert cli.run_replay("r") == 0
        assert (tmp / "runs" / "r" / "report.md").read_text() == "# done\n\n24 cells.\n"
        assert cli.run_replay("missing") == 1

        board.run_report = None
        board.save(tmp / "runs" / "r")
        assert cli.run_replay("r") == 1, "a run with no stored report must refuse, not invent one"
    finally:
        llm.call, tools.run_eda = real_call, real_ship
        cli.ROOT = Path(__file__).resolve().parent.parent
        shutil.rmtree(tmp)


def main() -> None:
    forward_pass_reaches_done()
    a_sim_failure_routes_through_debug_and_back()
    exhausted_retries_fail_the_run()
    lint_failures_cap_at_three()
    testbench_failures_retry_the_stage_before_giving_up()
    testbench_recovers_on_second_stage_pass()
    a_timing_miss_reworks_then_ships_with_warning()
    timing_recovers_after_one_rework()
    a_testbench_blame_reroutes_to_writing_the_testbench()
    replay_rebuilds_the_report_without_calling_anything()
    print("routing: forward, backward via debug, stage caps, tb retry, tb-blame reroute, timing rework, offline replay verified")


if __name__ == "__main__":
    main()