"""Deterministic controller: reads board.status, dispatches one agent, persists after every step.

The loop, the routing and the retry policy live here and nowhere else. Week 2 scope is the
forward pass only: any failure stops the run. Backward routing with the Debug agent is Week 3.
"""

from __future__ import annotations

from pathlib import Path

from agents.lint import LintAgent
from agents.rtl import RTLAgent
from agents.simrunner import SimRunnerAgent
from agents.spec import SpecAgent
from agents.synth import SynthesisAgent
from agents.testbench import TestbenchAgent
from agent import AgentResult, BaseAgent
from board import Board, DesignStatus

AGENTS: dict[DesignStatus, BaseAgent] = {
    DesignStatus.DRAFTING_SPEC: SpecAgent(),
    DesignStatus.GENERATING_RTL: RTLAgent(),
    DesignStatus.LINTING: LintAgent(),
    DesignStatus.WRITING_TESTBENCH: TestbenchAgent(),
    DesignStatus.SIMULATING: SimRunnerAgent(),
    DesignStatus.SYNTHESIZING: SynthesisAgent(),
}

NEXT = {
    "spec_agent": DesignStatus.GENERATING_RTL,
    "rtl_agent": DesignStatus.LINTING,
    "lint_agent": DesignStatus.WRITING_TESTBENCH,
    "testbench_agent": DesignStatus.SIMULATING,
    "sim_runner_agent": DesignStatus.SYNTHESIZING,
    "synthesis_agent": DesignStatus.DONE,
}


class Orchestrator:
    def __init__(self, board: Board, run_dir: Path):
        self.board = board
        self.run_dir = run_dir

    def step(self) -> tuple[BaseAgent, AgentResult]:
        agent = AGENTS[self.board.status]
        agent.run_dir = self.run_dir
        result = agent.run(self.board)
        if result.ok:
            self.board.retry_count = 0
            self.board.status = NEXT[agent.name]
        else:
            # Week 2 is the forward pass only; Debug + backward routing arrive in Week 3
            self.board.status = DesignStatus.FAILED
        self.board.save(self.run_dir)
        return agent, result

    def run(self, stop_after: str | None = None) -> AgentResult | None:
        while not self.board.finished:
            agent, result = self.step()
            if stop_after is not None and agent.name == f"{stop_after}_agent":
                return result
            if not result.ok:
                return result
        return None