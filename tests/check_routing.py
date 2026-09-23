"""Routing: forward, backward, and per-stage caps. Stubbed agents, no docker, no llm."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import orchestrator as orch
from agent import AgentResult, BaseAgent
from board import Board, DesignStatus


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


def main() -> None:
    forward_pass_reaches_done()
    a_sim_failure_routes_through_debug_and_back()
    exhausted_retries_fail_the_run()
    lint_failures_cap_at_three()
    print("routing: forward, backward via debug, per-stage caps verified (stubbed agents)")


if __name__ == "__main__":
    main()