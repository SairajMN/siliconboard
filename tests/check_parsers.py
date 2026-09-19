"""Parsers decide facts. This checks them against hand-built inputs, no docker needed."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from board import DesignSpec, Port, RTLArtifact
from agent import trim_log
from tools import (
    cells_from_stat,
    check_counts,
    rtl_module_name,
    rtl_ports,
    slack_from_sta,
    spec_mismatch,
    spec_problems,
    verdict,
)


def main() -> None:
    assert verdict("CHECK a PASS\nSUMMARY checks=1 failed=0\nPASS") is True
    assert verdict("CHECK a FAIL\nFAIL") is False
    assert verdict("CHECK a PASS\nnothing else") is None
    assert verdict("PASS\nFAIL") is None, "two terminal verdicts must be ambiguous, not guessed"
    assert verdict("") is None

    assert check_counts("SUMMARY checks=7 failed=2") == (7, 2)
    assert check_counts("no summary here") == (None, None)

    assert cells_from_stat("Number of cells:    1234") == 1234
    assert cells_from_stat("nothing") is None

    assert slack_from_sta("worst slack max   -0.350") == -0.35
    assert slack_from_sta("worst slack 1.2") == 1.2
    assert slack_from_sta("no timing here") is None

    noisy = [f"line {i} ok" for i in range(200)]
    noisy[120] = "CHECK mid FAIL got=1 want=2"
    trimmed = trim_log("\n".join(noisy), head=10, tail=10, limit=800)
    assert "FAIL got=1 want=2" in trimmed, "trim must keep flagged lines"
    assert len(trimmed) <= 820, len(trimmed)
    assert "lines omitted" in trimmed

    source = (ROOT / "smoke" / "counter.v").read_text()
    assert rtl_module_name(source) == "counter"
    ports = rtl_ports(source)
    assert set(ports) == {"clk", "rst_n", "en", "q"}, ports
    assert ports["clk"] == ("input", 1)
    assert ports["q"][0] == "output" and ports["q"][1] is None, "parameterised width must stay unresolved"

    spec = DesignSpec(
        name="counter",
        description="8 bit up counter",
        io_ports=[
            Port(name="clk", direction="input", width=1),
            Port(name="rst_n", direction="input", width=1),
            Port(name="en", direction="input", width=1),
            Port(name="q", direction="output", width=8),
        ],
        behavior_notes="sync reset, counts when enabled",
    )
    rtl = RTLArtifact(module_name="counter", filename="counter.v", source_code=source, version=1)
    assert spec_mismatch(spec, rtl) == [], spec_mismatch(spec, rtl)

    wrong = spec.model_copy(deep=True)
    wrong.io_ports.append(Port(name="enable", direction="input", width=1))
    problems = spec_mismatch(wrong, rtl)
    assert any("enable" in p for p in problems), problems

    bad_name = rtl.model_copy(update={"module_name": "counter_top"})
    assert any("module name" in p for p in spec_mismatch(spec, bad_name))

    assert spec_problems(spec) == [], spec_problems(spec)

    vague = spec.model_copy(update={"io_ports": []})
    assert any("too vague" in p for p in spec_problems(vague))

    illegal = spec.model_copy(update={"name": "8bit counter"})
    assert any("not a Verilog identifier" in p for p in spec_problems(illegal))

    duplicated = spec.model_copy(update={"io_ports": [*spec.io_ports, Port(name="clk", direction="input", width=1)]})
    assert any("declared twice" in p for p in spec_problems(duplicated))
    print("parsers: all checks pass")


if __name__ == "__main__":
    main()