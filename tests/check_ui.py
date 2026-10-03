"""The UI replays runs from board.json, so its timeline must agree with the board.

No server and no network: timeline.build is a pure function, and the handful of
server helpers worth pinning (run-id validation, artifact listing, request
validation) are pure too. The fixture is a synthetic board, not a real run
directory, because runs/ is gitignored and this test must pass on a fresh clone.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ui.timeline as timeline
from board import (
    Board,
    DesignSpec,
    DesignStatus,
    LLMCall,
    LintResult,
    Port,
    RTLArtifact,
    RunReport,
    SimResult,
    SynthesisReport,
    TimingReport,
)
from ui import server


def finished_board() -> Board:
    """A small counter run, stamped the way board.log() stamps the real thing."""
    board = Board(request="an 8-bit up counter with a synchronous reset")
    board.created_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    board.event_log = [
        "10:00:03 [drafting_spec] spec: counter, 3 ports, target 200.0 MHz",
        "10:00:15 [generating_rtl] rtl v1: counter via gemini:gemini-3.1-pro-preview",
        "10:00:19 [linting] lint: clean",
        "10:00:27 [writing_testbench] tb: 110 lines, 10 checks",
        "10:00:41 [simulating] sim: checks=10 failed=0 verdict=True",
        "10:00:46 [synthesizing] synth: 28 cells",
        "10:01:09 [analyzing_timing] timing: slack=3.34 ns target=200.0 MHz MET via OpenSTA",
        "10:01:14 [done] report: wrote report.md",
    ]
    board.spec = DesignSpec(
        name="counter",
        description="8 bit up counter",
        io_ports=[
            Port(name="clk", direction="input"),
            Port(name="rst_n", direction="input"),
            Port(name="en", direction="input"),
            Port(name="q", direction="output", width=8),
        ],
        target_freq_mhz=200.0,
        behavior_notes="saturates at 255",
    )
    board.rtl_history.append(
        RTLArtifact(module_name="counter", filename="counter.v",
                    source_code="module counter;\nendmodule", version=1)
    )
    board.lint = LintResult(clean=True)
    board.sim_history.append(
        SimResult(passed=True, total_checks=10, failed_checks=0,
                  cross_checked=True, cross_agreed=True)
    )
    board.synthesis_report = SynthesisReport(cell_count=28)
    board.timing_report = TimingReport(met_target=True, worst_slack_ns=3.34, verified=True)
    board.run_report = RunReport(markdown_summary="counter reaches done")
    board.llm_calls.append(
        LLMCall(role="rtl", model="gemini-3.1-pro-preview", key_slot=1,
                tokens_in=900, tokens_out=1400)
    )
    board.status = DesignStatus.DONE
    return board


def check_finished_timeline() -> None:
    payload = timeline.build(finished_board(), "unit", now=10 * 3600 + 3600)
    stages = payload["stages"]
    keys = [stage["key"] for stage in stages]
    assert keys == ["spec", "rtl", "lint", "tb", "sim", "synth", "timing", "report"], keys
    assert all(stage["state"] == "done" for stage in stages)
    assert payload["finished"] is True

    # durations run from the previous stamp to this one; spec starts at created_at
    assert stages[0]["seconds"] == 3, stages[0]
    assert stages[1]["seconds"] == 12, stages[1]
    assert stages[4]["seconds"] == 14, stages[4]   # tb 10:00:27 -> sim 10:00:41
    assert stages[-1]["seconds"] == 5, stages[-1]  # timing 10:01:09 -> report 10:01:14

    # every row names the agent that produced it
    assert stages[0]["agent"] == "spec_agent"
    assert stages[4]["agent"] == "sim_runner_agent"
    assert stages[6]["agent"] == "timing_agent"

    # details are read off the board, never invented
    assert ["module", "counter"] in stages[0]["details"]
    assert ["target", "200 MHz"] in stages[0]["details"]
    assert ["clean", "yes"] in stages[2]["details"]
    assert ["verdict", "PASS"] in stages[4]["details"]
    assert ["cells", "28"] in stages[5]["details"]
    assert ["target met", "yes"] in stages[6]["details"]
    assert ["worst slack", "3.34 ns"] in stages[6]["details"]

    # llm calls are attributed to the stage whose role they carry
    assert stages[1]["llm"], "rtl stage lost its llm call"
    assert stages[1]["llm"][0]["tokens_out"] == 1400
    assert stages[1]["llm"][0]["model"] == "gemini-3.1-pro-preview"

    summary = payload["summary"]
    assert summary["module"] == "counter"
    assert summary["sim_passed"] is True and summary["checks"] == 10
    assert summary["cells"] == 28 and summary["slack_ns"] == 3.34
    assert summary["rtl_versions"] == 1 and summary["bugs"] == 0
    assert payload["input"].startswith("an 8-bit up counter")
    assert len(payload["events"]) == 8


def check_running_and_queued() -> None:
    board = finished_board()
    board.status = DesignStatus.SIMULATING
    board.event_log = board.event_log[:4]  # tb done, sim not yet logged
    payload = timeline.build(board, "unit", now=10 * 3600 + 60)  # 10:01:00
    states = {stage["key"]: stage["state"] for stage in payload["stages"]}

    assert states["sim"] == "running"
    assert states["tb"] == "done"
    assert payload["finished"] is False
    running = next(stage for stage in payload["stages"] if stage["key"] == "sim")
    assert running["seconds"] == 33, running  # tb stamp 10:00:27 -> now 10:01:00

    queued = [stage for stage in payload["stages"] if stage["state"] == "queued"]
    assert [stage["key"] for stage in queued] == ["debug", "synth", "timing", "report"]
    assert all(stage["agent"].endswith("_agent") for stage in queued)


def check_failed_marks_last_stage() -> None:
    board = finished_board()
    board.status = DesignStatus.FAILED
    payload = timeline.build(board, "unit")
    states = [stage["state"] for stage in payload["stages"]]
    assert states[-1] == "failed" and "running" not in states, states


def check_run_id_validation() -> None:
    for good in ("ui-1", "live5", "a.b_c-2"):
        assert server.valid_run_id(good), good
    for bad in ("", "../etc/passwd", "a b", "x" * 41, "runs/../secrets", "semi;colon"):
        assert not server.valid_run_id(bad), bad


def check_artifacts_and_history() -> None:
    """History pairs input to output; artifacts list every file a run left."""
    original_runs = server.RUNS
    try:
        with tempfile.TemporaryDirectory() as tmp:
            server.RUNS = Path(tmp)
            run = server.RUNS / "h1"
            (run / "work").mkdir(parents=True)
            (run / "waves").mkdir()
            (run / "board.json").write_text("{}")
            (run / "report.md").write_text("# report\n")
            (run / "work" / "rtl.v").write_text("module rtl; endmodule\n")
            (run / "waves" / "dump.vcd").write_text("$dumpvars\n")

            artifacts = server.artifacts_of("h1")
            groups = {artifact["group"] for artifact in artifacts}
            assert {"report.md", "board.json", "waves/*.vcd", "work/*.v"} <= groups, artifacts
            for artifact in artifacts:
                assert ".." not in Path(artifact["path"]).parts

            index = server.runs_index()
            rows = [row for row in index if row["run_id"] == "h1"]
            assert len(rows) == 1, index
            assert "input" in rows[0] and "status" in rows[0] and "mtime" in rows[0]

            assert server.artifacts_of("../nope") == []
            assert server.load_board("../nope") is None
    finally:
        server.RUNS = original_runs


def check_start_run_validation() -> None:
    """Validation must reject bad requests before anything touches disk."""
    code, message = server.start_run("../evil", "text", False, False)
    assert code == 400 and "run id" in message, (code, message)
    code, message = server.start_run("valid-id", "   \n ", False, False)
    assert code == 400 and "empty" in message, (code, message)
    assert not (server.RUNS / "valid-id").exists(), "validation wrote a directory"


def check_examples_index() -> None:
    examples = server.examples_index()
    assert examples, "examples/ has no spec.txt to suggest"
    for example in examples:
        assert example["text"].strip(), example
        assert {"id", "title", "text"} <= set(example)


def main() -> None:
    check_finished_timeline()
    check_running_and_queued()
    check_failed_marks_last_stage()
    check_run_id_validation()
    check_artifacts_and_history()
    check_start_run_validation()
    check_examples_index()
    print("check_ui: ok")


if __name__ == "__main__":
    main()

