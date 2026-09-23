"""Deterministic controller: reads board.status, dispatches one agent, persists after every step.

The loop, the routing and the retry policy live here and nowhere else. A failed stage routes
backward to the agent that can fix it, up to a per-stage cap; exhausted or unroutable is FAILED.
"""

from __future__ import annotations

from pathlib import Path

from agents.debug import DebugAgent
from agents.lint import LintAgent
from agents.rtl import RTLAgent
from agents.simrunner import SimRunnerAgent
from agents.spec import SpecAgent
from agents.synth import SynthesisAgent
from agents.timing import TimingAgent
from agents.testbench import TestbenchAgent
from agent import AgentResult, BaseAgent
from board import Board, DesignStatus
from tools import sabotage_reset

AGENTS: dict[DesignStatus, BaseAgent] = {
    DesignStatus.DRAFTING_SPEC: SpecAgent(),
    DesignStatus.GENERATING_RTL: RTLAgent(),
    DesignStatus.LINTING: LintAgent(),
    DesignStatus.WRITING_TESTBENCH: TestbenchAgent(),
    DesignStatus.SIMULATING: SimRunnerAgent(),
    DesignStatus.DEBUGGING: DebugAgent(),
    DesignStatus.SYNTHESIZING: SynthesisAgent(),
    DesignStatus.ANALYZING_TIMING: TimingAgent(),
}

NEXT = {
    "spec_agent": DesignStatus.GENERATING_RTL,
    "rtl_agent": DesignStatus.LINTING,
    "lint_agent": DesignStatus.WRITING_TESTBENCH,
    "testbench_agent": DesignStatus.SIMULATING,
    "sim_runner_agent": DesignStatus.SYNTHESIZING,
    "debug_agent": DesignStatus.GENERATING_RTL,
    "synthesis_agent": DesignStatus.ANALYZING_TIMING,
    "timing_agent": DesignStatus.DONE,
}

STOP = {
    "spec": "spec_agent",
    "rtl": "rtl_agent",
    "lint": "lint_agent",
    "tb": "testbench_agent",
    "sim": "sim_runner_agent",
    "debug": "debug_agent",
    "synth": "synthesis_agent",
    "timing": "timing_agent",
}

# None = no earlier stage can fix it (the model already re-prompted itself internally)
BACKWARD: dict[str, DesignStatus | None] = {
    "spec_agent": None,
    "rtl_agent": None,
    "lint_agent": DesignStatus.GENERATING_RTL,
    # a TB failure is fixed by another testbench attempt (fresh chain pass), not by older state
    "testbench_agent": DesignStatus.WRITING_TESTBENCH,
    "sim_runner_agent": DesignStatus.DEBUGGING,
    "debug_agent": None,
    "synthesis_agent": DesignStatus.GENERATING_RTL,
    "timing_agent": DesignStatus.GENERATING_RTL,
}

MAX_BACKWARD = {"lint_agent": 3, "testbench_agent": 2, "sim_runner_agent": 3, "synthesis_agent": 2, "timing_agent": 2}


class Orchestrator:
    def __init__(self, board: Board, run_dir: Path, inject: str | None = None):
        self.board = board
        self.run_dir = run_dir
        self.inject = inject

    def step(self) -> tuple[BaseAgent, AgentResult]:
        agent = AGENTS[self.board.status]
        agent.run_dir = self.run_dir
        result = agent.run(self.board)
        if result.ok:
            if self.inject and agent.name == "rtl_agent" and self.board.latest_rtl is not None:
                self._inject()
            if agent.name == "debug_agent" and self.board.latest_bug is not None:
                blamed = self.board.latest_bug.blames
                if blamed == "testbench":
                    self.board.status = DesignStatus.WRITING_TESTBENCH
                    self.board.log("debug blames the testbench, back to writing_testbench")
                    self.board.save(self.run_dir)
                    return agent, result
            self.board.status = NEXT[agent.name]
        else:
            self._route(agent, result)
        self.board.save(self.run_dir)
        return agent, result

    def _route(self, agent: BaseAgent, result: AgentResult) -> None:
        attempts = self.board.stage_retries.get(agent.name, 0) + 1
        self.board.stage_retries[agent.name] = attempts
        target = BACKWARD.get(agent.name)
        cap = MAX_BACKWARD.get(agent.name, 1)
        if target is None or attempts > cap:
            # a timing miss ships flagged instead of failing: functionally correct beats on-time
            if agent.name == "timing_agent":
                self.board.status = DesignStatus.DONE_WITH_WARNING
                self.board.log(f"{agent.name}: failed {attempts}x, shipping with warning: {result.err}")
            else:
                self.board.status = DesignStatus.FAILED
                self.board.log(f"{agent.name}: failed {attempts}x, giving up: {result.err}")
            return
        self.board.status = target
        self.board.log(f"{agent.name}: failed ({attempts}/{cap}), back to {target.value}: {result.err}")

    def _inject(self) -> None:
        rtl = self.board.latest_rtl
        try:
            rtl.source_code = sabotage_reset(rtl.source_code)
        except ValueError as exc:
            self.board.log(f"inject aborted: {exc}")
        else:
            self.board.log(f"injected bug ({self.inject}) into {rtl.filename}")
        self.inject = None

    def run(self, stop_after: str | None = None) -> AgentResult | None:
        while not self.board.finished:
            agent, result = self.step()
            if stop_after is not None and agent.name == STOP[stop_after]:
                return result
            if not result.ok and self.board.status == DesignStatus.FAILED:
                return result
        return None