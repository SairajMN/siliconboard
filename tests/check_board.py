"""A run is replayed from board.json, so the board must survive a disk round trip."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from board import Board, BugReport, DesignSpec, DesignStatus, Port, RTLArtifact


def main() -> None:
    board = Board()
    assert board.status is DesignStatus.DRAFTING_SPEC
    assert not board.finished
    assert board.latest_rtl is None and board.latest_bug is None

    board.spec = DesignSpec(
        name="counter",
        description="8 bit up counter",
        io_ports=[Port(name="clk", direction="input", width=1)],
        behavior_notes="sync reset",
    )
    board.rtl_history.append(
        RTLArtifact(module_name="counter", filename="counter.v", source_code="module counter; endmodule", version=1)
    )
    board.bug_history.append(
        BugReport(
            summary="q stuck high",
            likely_cause="reset branch missing on line 12",
            failing_check="reset",
            suggested_fix="add the rst_n branch to the always block",
            evidence_quote="CHECK reset FAIL got=255 want=0",
        )
    )
    board.log("spec written")

    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        board.save(run_dir)
        reloaded = Board.load(run_dir)

    assert reloaded == board, "board did not survive the disk round trip"
    assert reloaded.latest_rtl.version == 1
    assert reloaded.latest_bug.evidence_quote == "CHECK reset FAIL got=255 want=0"
    assert reloaded.event_log == board.event_log

    board.status = DesignStatus.DONE
    assert board.finished
    board.status = DesignStatus.FAILED
    assert board.finished
    print("board: round trip and status helpers pass")


if __name__ == "__main__":
    main()