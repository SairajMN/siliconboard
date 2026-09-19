"""Canary: every schema we hand to Gemini must survive the SDK's own conversion."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from google.genai import _transformers as transformers  # ponytail: private hook as canary, drop if the import breaks

from board import BugReport, DesignSpec, RTLArtifact, TestbenchArtifact, TimingReport, RunReport
import llm  # noqa: E402  the module itself must import, not just its schemas

SCHEMAS = [DesignSpec, RTLArtifact, TestbenchArtifact, BugReport, TimingReport, RunReport]


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
    print(f"llm: {len(SCHEMAS)} output schemas convert cleanly (no refs, no anyOf)")


if __name__ == "__main__":
    main()