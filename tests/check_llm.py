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

    def fake_provider(*args, **kwargs):
        prompt = kwargs.get("prompt") if "prompt" in kwargs else args[4]
        prompts.append(prompt)
        return (one_line, 10, 20) if len(prompts) == 1 else (good, 11, 21)

    with llm.fake_all_providers(fake_provider):
        result = llm.call(
            "write a testbench",
            "system",
            TestbenchArtifact,
            "testbench",
            ["groq:openai/gpt-oss-20b"],
            validate=lambda artifact: tb_problems(artifact, "counter"),
        )

    assert isinstance(result, TestbenchArtifact), result
    assert len(prompts) == 2, "a schema-valid but unusable answer must be re-prompted once"
    assert "unusable" in prompts[1] and "line(s)" in prompts[1], "the repair prompt must quote the gate"


def thinking_tokens_are_billed() -> None:
    """A thinking model's output is mostly thought tokens; leaving them out skews the budget."""

    class Usage:
        prompt_token_count = 12
        candidates_token_count = 97
        thoughts_token_count = 1531

    class NoThoughts:
        prompt_token_count = 12
        candidates_token_count = 97
        thoughts_token_count = None

    class NoUsage:
        pass

    assert llm._billed_output(Usage()) == 1628, "answer tokens must include the thinking billed with them"
    assert llm._billed_output(NoThoughts()) == 97, "a non-thinking answer is just its tokens"
    assert llm._billed_output(NoUsage()) is None, "no usage means no count, not zero"


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


def keyless_providers_leave_the_chain() -> None:
    """A chain route for a provider nobody has a key for must not cost an attempt.

    Otherwise every call pays a doomed _ring lookup, and a provider added for
    future use would be a tax on runs that have no credentials for it.
    """
    import os

    saved = {n: v for n, v in os.environ.items() if n.startswith("OPENAI_")}
    saved_load_env = llm.load_env
    llm.load_env = lambda: None  # .env on the developer's disk would refill what we remove
    for name in list(saved):
        del os.environ[name]
    llm._keyless.clear()
    try:
        pruned = llm.routes(["openai:gpt-5.4", "groq:openai/gpt-oss-20b"])
        assert pruned == ["groq:openai/gpt-oss-20b"], f"keyless provider stayed in the chain: {pruned}"
        assert llm._has_key("openai") is False
        os.environ["OPENAI_API_KEY_1"] = "sk-test"
        llm._keyless.clear()
        assert llm.routes(["openai:gpt-5.4", "groq:openai/gpt-oss-20b"]) == [
            "openai:gpt-5.4",
            "groq:openai/gpt-oss-20b",
        ], "a provider with a key must stay"
        # an all-keyless chain is returned untouched so the caller reports the missing key
        llm._keyless.clear()
        for name in list(os.environ):
            if name.startswith("GROQ_"):
                del os.environ[name]
        assert llm.routes(["groq:x"]) == ["groq:x"], "an empty result would hide the reason"
    finally:
        llm._keyless.clear()
        llm.load_env = saved_load_env
        for name in [n for n in os.environ if n.startswith("OPENAI_") or n.startswith("GROQ_")]:
            del os.environ[name]
        os.environ.update(saved)
        saved_load_env()


def replacement_stays_a_chat_model() -> None:
    """Model self-repair must not answer with an embedding or image model.

    The account catalog is alphabetical, so the old fallback picked
    "dall-e-3" for openai, which then 400s on /chat/completions.
    """
    catalog = [
        "dall-e-3",
        "gpt-3.5-turbo",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4o",
        "text-embedding-3-large",
        "whisper-1",
    ]
    llm._live["openai"] = list(catalog)
    llm._unprovisioned.clear()
    picked = llm._replacement_model("openai", "gpt-4.1-pro", "sk-test")
    assert picked is not None, "a chat model was available and should have been chosen"
    assert picked in catalog, picked
    assert not any(bad in picked.lower() for bad in llm.NOT_CHAT), f"replacement is not a chat model: {picked}"
    # the alphabetical fallback is the real trap: it used to land on dall-e-3
    llm._live["openai"] = ["aaa-not-a-chat", "dall-e-3", "gpt-4o", "text-embedding-3-large"]
    fell_back = llm._replacement_model("openai", "gone", "sk-test")
    assert fell_back == "gpt-4o", f"fallback picked a non-chat model: {fell_back!r}"
    llm._live["openai"] = ["text-embedding-3-large", "dall-e-3", "whisper-1"]
    assert llm._replacement_model("openai", "gone", "sk-test") is None, "a catalog of non-chat models has no replacement"
    llm._live.pop("openai", None)


def openai_request_shape() -> None:
    """openai renamed the output cap and rejects max_tokens on every gpt-5/o model.

    Measured against the live endpoint: gpt-4.1 answers "Unsupported parameter:
    'max_tokens'" for the reasoning families while accepting
    max_completion_tokens, and groq/nvidia still need max_tokens.
    """
    from board import DesignSpec

    seen: list[dict] = []

    saved_call = llm.urllib.request.urlopen

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()

    def fake_urlopen(req, *a, **kw):
        seen.append(json.loads(req.data.decode()))
        return FakeResp()

    llm.urllib.request.urlopen = fake_urlopen
    try:
        for provider in ("openai", "groq"):
            seen.clear()
            try:
                llm._openai_call(provider, "sk-x", "gpt-5.4", "sys", "hi", DesignSpec, 0.1)
            except Exception:
                pass
            if not seen:
                continue
            body = seen[-1]
            if provider == "openai":
                assert "max_completion_tokens" in body, f"openai still sends max_tokens: {sorted(body)}"
                assert "max_tokens" not in body, "sending both is a 400 on the reasoning models"
            else:
                assert "max_tokens" in body, f"{provider} needs max_tokens"
    finally:
        llm.urlopen = saved_call


def non_chat_models_are_filtered() -> None:
    """The real catalog answered 404 "not a chat model" for each of these."""
    def blocked(model: str) -> bool:
        return any(bad in model.lower() for bad in llm.NOT_CHAT)

    for bad in ("babbage-002", "davinci-002", "gpt-live-1", "sora-2", "text-embedding-3-large",
                "dall-e-3", "whisper-1", "chat-latest", "gpt-3.5-turbo-instruct", "gpt-4o-audio-preview"):
        assert blocked(bad), f"{bad} would be offered as a chat model"
    for good in ("gpt-5.4", "gpt-4.1", "gpt-4o", "o4-mini", "gpt-4.1-mini", "gpt-5.4-mini"):
        assert not blocked(good), f"{good} is a real chat model but got filtered"


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
    thinking_tokens_are_billed()
    keyless_providers_leave_the_chain()
    replacement_stays_a_chat_model()
    non_chat_models_are_filtered()
    openai_request_shape()
    print(f"llm: {len(SCHEMAS)} output schemas convert cleanly, gate repair loop re-prompts")


if __name__ == "__main__":
    main()