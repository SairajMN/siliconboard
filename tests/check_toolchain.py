"""W1-02..W1-04: the raw toolchain end to end, no LLM and no agents involved."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import cells_from_stat, check_counts, lint, run_eda, simulate, verdict

SMOKE = Path(__file__).resolve().parent.parent / "smoke"


def main() -> None:
    clean, issues, log = lint(SMOKE, "counter.v", "counter")
    assert clean, f"lint reported errors:\n{log}"
    print(f"lint: clean, {len(issues)} warnings recorded")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "noeol.v").write_text((SMOKE / "counter.v").read_text().rstrip("\n"))
        clean, issues, log = lint(tmpdir, "noeol.v", "counter")
        assert clean, "a style warning must not fail lint, only errors should"
        assert any("EOFNEWLINE" in issue for issue in issues), issues
        print("lint: style warnings recorded but not fatal")

    rc, log = simulate(SMOKE, "counter.v", "tb_counter.v", "tb_counter")
    assert rc == 0, f"sim exited {rc}:\n{log}"
    assert verdict(log) is True, f"sim did not pass cleanly:\n{log}"
    assert check_counts(log) == (4, 0), f"unexpected check counts: {check_counts(log)}"
    print("sim: PASS, 4 checks, 0 failed")

    rc, log = run_eda(["yosys", "-p", "read_verilog counter.v; synth -top counter; stat"], SMOKE)
    assert rc == 0, f"synth exit {rc}:\n{log}"
    cells = cells_from_stat(log)
    assert cells and cells > 0, f"no cell count in stat output:\n{log}"
    print(f"synth: {cells} cells")


if __name__ == "__main__":
    main()