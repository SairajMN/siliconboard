"""Canary: every schema we hand to Gemini must survive the SDK's own conversion."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from google.genai import _transformers as transformers  # ponytail: private hook as canary, drop if the import breaks

from board import BugReport, DesignSpec, RTLArtifact, TestbenchArtifact, TimingReport, RunReport
import llm  # noqa: E402  the module itself must import, not just its schemas

SCHEMAS = [DesignSpec, RTLArtifact, TestbenchArtifact, BugReport, TimingReport, RunReport]


def repair_loop_reruns_the_gate() -> None:
    from agents.testbench import tb_problems

    one_line = json.dumps(
        {"filename": "tb_counter.v", "source_code": "module tb_counter; endmodule", "test_vectors_description": "flat"}
    )
    good = json.dumps(
        {
            "filename": "tb_counter.v",
            "source_code": (ROOT / "smoke" / "tb_counter.v").read_text(),
            "test_vectors_description": "from the smoke fixture",
        }
    )
    prompts: list[str] = []

    def fake_provider(provider, key, model, system, prompt, schema, temperature):
        prompts.append(prompt)
        return (one_line, 10, 20) if len(prompts) == 1 else (good, 11, 21)

    original = llm._openai_call
    llm._openai_call = fake_provider
    try:
        result = llm.call(
            "write a testbench",
            "system",
            TestbenchArtifact,
            "testbench",
            ["groq:openai/gpt-oss-20b"],
            validate=lambda artifact: tb_problems(artifact, "counter"),
        )
    finally:
        llm._openai_call = original

    assert isinstance(result, TestbenchArtifact), result
    assert len(prompts) == 2, "a schema-valid but unusable answer must be re-prompted once"
    assert "unusable" in prompts[1] and "line(s)" in prompts[1], "the repair prompt must quote the gate"


def keyless_provider_raises_every_time() -> None:
    """A provider with no key must fail the same way on every visit.

    The chain visits several models per provider, so the second visit used to
    reach min() on a cached empty ring and die with ValueError instead of
    LLMError, which is not something the chain can route around.
    """
    import os

    saved = {n: os.environ.pop(n) for n in list(os.environ) if n.startswith("NVIDIA_")}
    saved_load_env = llm.load_env
    llm.load_env = lambda: None  # the local .env also holds keys; this case needs none
    llm._rings.clear()
    try:
        for _ in range(3):
            try:
                llm._ring("nvidia")
            except llm.LLMError:
                continue
            raise AssertionError("a provider with no key must raise, not hand back a ring")
        assert "nvidia" not in llm._rings, "an empty ring must not be cached"
        os.environ["NVIDIA_API_KEY"] = "n" + "0" * 60
        llm._rings.clear()
        assert llm._ring("nvidia") == [(0, os.environ["NVIDIA_API_KEY"])], "the single-key ring is wrong"
    finally:
        llm._rings.clear()
        llm.load_env = saved_load_env
        for name in [n for n in os.environ if n.startswith("NVIDIA_")]:
            del os.environ[name]
        os.environ.update(saved)


def main() -> None:
    assert llm._unfence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert llm._unfence('{"a": 1}') == '{"a": 1}'
    assert isinstance(llm.keys("gemini"), list)
    for route in llm.HEAVY + llm.LIGHT:
        provider, _, model = route.partition(":")
        assert provider in llm.OPENAI_COMPAT_BASE or provider == "gemini", f"bad route {route}"
        assert model, f"route {route} has no model"

    import os
    os.environ.update({f"GROQ_API_KEY_{i}": f"g{i}000000000000000000" for i in range(1, 5)})
    llm._rings.clear()
    ring = llm._ring("groq")
    assert [s for s, _ in ring] == [1, 2, 3, 4]
    first = llm._take_key("groq")
    second = llm._take_key("groq")
    assert first[0] != second[0], "round-robin did not advance"
    llm._cooling[("groq", first[0])] = time.time() + 60
    third = llm._take_key("groq")
    assert third[0] != first[0], "cooled slot was picked again"
    for schema in SCHEMAS:
        converted = transformers.t_schema(None, schema)
        assert converted is not None, f"{schema.__name__} did not convert to a Gemini Schema"
        blob = converted.model_dump_json(exclude_none=True)
        for unsupported in ("$ref", "$defs", "anyOf"):
            assert unsupported not in blob, f"{schema.__name__} still contains {unsupported}, Gemini rejects it"
    repair_loop_reruns_the_gate()
    keyless_provider_raises_every_time()
    print(f"llm: {len(SCHEMAS)} output schemas convert cleanly, gate repair loop re-prompts")


if __name__ == "__main__":
    main()