"""Agent behaviour that needs no API key and no docker. LLM calls are faked here."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm
from agents.rtl import RTLAgent, rtl_problems
from agents.spec import SpecAgent
from agents.testbench import TestbenchAgent, tb_problems
from board import Board, DesignSpec, Port, RTLArtifact, TestbenchArtifact

ROOT = Path(__file__).resolve().parent.parent
RTL_SRC = (ROOT / "smoke" / "counter.v").read_text()
TB_SRC = (ROOT / "smoke" / "tb_counter.v").read_text()
ONE_LINE_TB = (ROOT / "tests" / "fixtures" / "tb_one_line.v").read_text()
LATE_RESET_TB = (ROOT / "tests" / "fixtures" / "tb_reset_late_narrow_names.v").read_text()

COUNTER_SPEC = DesignSpec(
    name="counter",
    description="8 bit up counter",
    io_ports=[
        Port(name="clk", direction="input", width=1),
        Port(name="rst_n", direction="input", width=1),
        Port(name="en", direction="input", width=1),
        Port(name="q", direction="output", width=8),
    ],
    behavior_notes="sync active-low reset, counts when enabled",
)

# the spec that produced runs/w2-live3, where the DUT is called up_counter
LATE_RESET_SPEC = COUNTER_SPEC.model_copy(update={"name": "up_counter"})


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.roles: list[str] = []
        self.prompts: list[str] = []
        self.gate = None

    def __call__(self, prompt, system, schema, role, models, board=None, temperature=0.2, validate=None):
        self.roles.append(role)
        self.prompts.append(prompt)
        self.gate = validate
        return self.payload


def patched(fake, fn):
    original = llm.call
    llm.call = fake
    try:
        fn()
    finally:
        llm.call = original


def spec_agent_refuses_empty() -> None:
    agent = SpecAgent()
    assert agent.name == "spec_agent"
    assert agent.system.strip(), "every agent needs a system prompt"

    board = Board()
    result = agent.run(board)
    assert result.ok is False, result
    assert "request is empty" in (result.err or ""), result
    assert board.spec is None, "a failed extraction must not write a spec onto the Board"


def rtl_gate_flags_wrong_module_and_flat_files() -> None:
    good = RTLArtifact(module_name="counter", filename="counter.v", source_code=RTL_SRC, version=1)
    assert rtl_problems(COUNTER_SPEC, good) == []

    renamed = RTL_SRC.replace("module counter", "module counter_top")
    wrong = good.model_copy(update={"module_name": "counter_top", "source_code": renamed})
    assert "module name" in "; ".join(rtl_problems(COUNTER_SPEC, wrong))

    flat = good.model_copy(update={"source_code": RTL_SRC.replace("\n", " ")})
    assert any("line(s)" in p for p in rtl_problems(COUNTER_SPEC, flat)), "a one-line module must be rejected"


def rtl_agent_owns_version_and_hands_its_gate_to_llm() -> None:
    def check() -> None:
        board = Board(request="counter please", spec=COUNTER_SPEC)
        r = RTLAgent().run(board)
        assert r.ok, r.err
        rtl = board.latest_rtl
        assert rtl.version == 1 and rtl.filename == "counter.v", "version and filename are decided by the agent"
        assert fake.roles == ["rtl"] and "specification" in fake.prompts[0]
        assert fake.gate is not None, "the agent must hand its gate to llm.call, not re-check afterwards"
        assert fake.gate(fake.payload) == []
        assert fake.gate(fake.payload.model_copy(update={"source_code": RTL_SRC.replace("\n", " ")}))
        assert fake.gate(fake.payload.model_copy(update={"module_name": "up_counter"}))

        board2 = Board(request="again", spec=COUNTER_SPEC, rtl_history=[board.latest_rtl])
        assert RTLAgent().run(board2).ok
        assert board2.latest_rtl.version == 2, "version must be monotonic across rewrites"

    fake = FakeLLM(RTLArtifact(module_name="counter", filename="whatever.v", source_code=RTL_SRC, version=99))
    patched(fake, check)


def a_failed_llm_call_leaves_the_board_clean() -> None:
    class Boom(FakeLLM):
        def __call__(self, *args, **kwargs):
            raise llm.LLMError("rtl: 6 attempts failed, last error: refuse")

    def check() -> None:
        board = Board(request="counter please", spec=COUNTER_SPEC)
        r = RTLAgent().run(board)
        assert not r.ok and "attempts failed" in (r.err or ""), r
        assert board.rtl_history == [], "a rejected artifact must never land on the Board"

    patched(Boom(None), check)


def tb_gate_catches_every_bad_shape() -> None:
    good = TestbenchArtifact(filename="tb_counter.v", source_code=TB_SRC, test_vectors_description="fixture")
    assert tb_problems(good, "counter", COUNTER_SPEC) == []

    # the testbench that really failed verilator in runs/w2-resume: one line, escaped quotes
    real = TestbenchArtifact(filename="tb_up_counter.v", source_code=ONE_LINE_TB, test_vectors_description="fixture")
    problems = tb_problems(real, "up_counter")
    assert any("line(s)" in p for p in problems), problems
    assert any("escapes" in p for p in problems), problems

    # the testbench that passed every static gate in runs/w2-live3 and then failed 6 of 12
    # checks against correct RTL: the reset was released after the non-zero checks
    late = TestbenchArtifact(
        filename="tb_up_counter.v", source_code=LATE_RESET_TB, test_vectors_description="fixture"
    )
    late_problems = tb_problems(late, "up_counter", LATE_RESET_SPEC)
    assert any("released later" in p or "never released" in p for p in late_problems), late_problems
    assert any("truncated" in p for p in late_problems), late_problems

    detached = good.model_copy(update={"source_code": TB_SRC.replace("counter dut", "other dut")})
    assert "never instantiates" in "; ".join(tb_problems(detached, "counter"))

    no_verdict = TB_SRC.replace('$display("PASS");', "").replace('$display("FAIL");', "")
    assert "verdict" in "; ".join(tb_problems(good.model_copy(update={"source_code": no_verdict}), "counter"))

    no_finish = TB_SRC.replace("$finish;", "")
    assert "$finish" in "; ".join(tb_problems(good.model_copy(update={"source_code": no_finish}), "counter"))

    prose = good.model_copy(update={"source_code": "Here is the testbench:\n" + TB_SRC})
    assert "prose" in "; ".join(tb_problems(prose, "counter"))

    # the testbench that really reported "CHECK reset FAIL" with no values in runs/w2-repair
    silent = TB_SRC.replace('got=%0d want=%0d", name, got, want', '", name')
    assert "evidence" in "; ".join(tb_problems(good.model_copy(update={"source_code": silent}), "counter"))


def tb_agent_names_its_own_file_and_gates_it() -> None:
    def check() -> None:
        board = Board(request="counter please", spec=COUNTER_SPEC, rtl_history=[GOOD_RTL])
        r = TestbenchAgent().run(board)
        assert r.ok, r.err
        assert board.testbench.filename == "tb_counter.v", "the agent owns the filename"
        assert fake.gate is not None and fake.gate(fake.payload) == []

    GOOD_RTL = RTLArtifact(module_name="counter", filename="counter.v", source_code=RTL_SRC, version=1)
    fake = FakeLLM(
        TestbenchArtifact(filename="tb_counter.v", source_code=TB_SRC, test_vectors_description="fixture")
    )
    patched(fake, check)


def tb_gate_catches_a_racy_testbench() -> None:
    racy = TestbenchArtifact(
        filename="tb_up_counter.v",
        source_code=(ROOT / "tests" / "fixtures" / "tb_racy.v").read_text(),
        test_vectors_description="the testbench that failed verilator in runs/w2-repair",
    )
    problems = tb_problems(racy, "up_counter")
    assert any("non-blocking assignment" in p for p in problems), problems
    assert any("always @(posedge clk)" in p for p in problems), problems


def main() -> None:
    spec_agent_refuses_empty()
    rtl_gate_flags_wrong_module_and_flat_files()
    rtl_agent_owns_version_and_hands_its_gate_to_llm()
    a_failed_llm_call_leaves_the_board_clean()
    tb_gate_catches_every_bad_shape()
    tb_gate_catches_a_racy_testbench()
    tb_agent_names_its_own_file_and_gates_it()
    print("agents: spec/rtl/testbench gates verified offline (llm faked, no network)")


if __name__ == "__main__":
    main()