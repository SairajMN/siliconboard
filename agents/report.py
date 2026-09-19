"""Narration only. Reads the finished Board, writes a human report, and never gates the design."""

from __future__ import annotations

import json

import llm
from agent import NO_INVENTION, AgentResult, BaseAgent
from board import Board, RunReport
from tools import check_results, unbacked_numbers

SYSTEM = f"""You summarise one finished chip-design agent run for a technical reader.

Rules:
- One key: markdown_summary, a markdown string under 400 words.
- Walk the pipeline in order: spec, RTL, lint, testbench, simulation, debug retries, synthesis, timing.
- Quote simulation verdicts and failing check names exactly as they appear in the facts.
- For each retry, state the bug, then how the next RTL version changed.
- Use a small table for the synthesis and timing numbers.
- End with a one-line verdict and whether the design met its target frequency.
- Every number you write must appear in the facts below. If a stage did not run, say it did not run.

{NO_INVENTION}"""


class ReportAgent(BaseAgent):
    name = "report_agent"
    system = SYSTEM

    def facts(self, board: Board) -> str:
        data = {
            "verdict": board.status.value,
            "request": board.request,
            "spec": board.spec.model_dump(mode="json") if board.spec else None,
            "rtl_versions": [
                {"version": r.version, "module_name": r.module_name, "lines": len(r.source_code.splitlines())}
                for r in board.rtl_history
            ],
            "lint": {"clean": board.lint.clean, "issues": board.lint.issues} if board.lint else None,
            "testbench": (
                {
                    "filename": board.testbench.filename,
                    "lines": len(board.testbench.source_code.splitlines()),
                    "covers": board.testbench.test_vectors_description,
                }
                if board.testbench
                else None
            ),
            "simulations": [
                {
                    "passed": s.passed,
                    "total_checks": s.total_checks,
                    "failed_checks": s.failed_checks,
                    "failing": [name for name, ok in check_results(s.raw_log).items() if not ok],
                    "ambiguity": s.ambiguity,
                }
                for s in board.sim_history
            ],
            "bugs": [b.model_dump(mode="json") for b in board.bug_history],
            "synthesis": (
                {"cell_count": board.synthesis_report.cell_count} if board.synthesis_report else None
            ),
            "timing": board.timing_report.model_dump(mode="json") if board.timing_report else None,
            "events": board.event_log,
        }
        return json.dumps(data, indent=2)

    def run(self, board: Board) -> AgentResult:
        facts = self.facts(board)
        try:
            report = llm.call(
                facts,
                self.system,
                RunReport,
                "report",
                llm.LIGHT,
                board,
                temperature=0.4,
                validate=lambda candidate: unbacked_numbers(candidate.markdown_summary, facts),
            )
        except llm.LLMError as exc:
            return AgentResult(ok=False, err=str(exc))

        board.run_report = report
        if self.run_dir is not None:
            (self.run_dir / "report.md").write_text(report.markdown_summary.rstrip() + "\n")
        board.log(f"report: {len(report.markdown_summary.split())} words")
        return AgentResult(ok=True, note=f"{len(report.markdown_summary.split())} words")
