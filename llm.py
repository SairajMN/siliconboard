"""LLM router: Gemini primary, Groq then NVIDIA as OpenAI-compatible fallbacks."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from board import Board, LLMCall

try:
    import certifi  # already a transitive dependency of google-genai
    _CAFILE: str | None = certifi.where()
except ImportError:
    _CAFILE = "/etc/ssl/cert.pem" if Path("/etc/ssl/cert.pem").exists() else None

_SSL_CONTEXT = ssl.create_default_context(cafile=_CAFILE)

HEAVY = [
    "gemini:gemini-3.8-flash",
    "gemini:gemini-3.7-flash",
    "groq:openai/gpt-oss-120b",
    "groq:qwen/qwen3.8-27b",
    "nvidia:nvidia/nemotron-3-super-120b-a12b",
]
LIGHT = [
    "gemini:gemini-3.5-flash-lite",
    "gemini:gemini-3.1-flash-lite",
    "groq:openai/gpt-oss-20b",
    "groq:qwen/qwen3.8-27b",
    "nvidia:nvidia/nemotron-3.5-lightning-30b-a3b",
]

OPENAI_COMPAT_BASE = {"groq": "https://api.groq.com/openai/v1", "nvidia": "https://integrate.api.nvidia.com/v1"}

MAX_OUTPUT_TOKENS = 8192
# free tiers meter output tokens per minute, and the limits differ per provider and
# model: groq rejected an 8192 ask with "Limit 1000, Requested 1116", so start low
# and let the measured limit lower it further
PROVIDER_MAX_TOKENS = {"groq": 2048, "nvidia": 4096}
_cap: dict[str, int] = {}
MAX_ATTEMPTS = 6
COOLDOWN_S = 60.0

# the SDK warns on every generate_content call; our logs are the audit trail
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

T = TypeVar("T", bound=BaseModel)

_cache_dir: Path | None = None
_client: genai.Client | None = None
_client_key: str | None = None
_rings: dict[str, list[tuple[int, str]]] = {}
_next: dict[str, int] = {}
_cooling: dict[tuple[str, int], float] = {}
_dead: set[str] = set()  # provider failed hard (no credits, bad key): attempts are for real work


class LLMError(RuntimeError):
    pass


class _Repair(Exception):
    """Schema-valid output that the harness's own gate refused. Re-prompt, do not fail."""


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def keys(provider: str = "gemini") -> list[tuple[int, str]]:
    load_env()
    prefix = provider.upper()
    found = [(i, os.environ[f"{prefix}_API_KEY_{i}"]) for i in range(1, 5) if os.environ.get(f"{prefix}_API_KEY_{i}")]
    if not found and os.environ.get(f"{prefix}_API_KEY"):
        found = [(0, os.environ[f"{prefix}_API_KEY"])]
    return found


def _ring(provider: str) -> list[tuple[int, str]]:
    if provider not in _rings:
        _rings[provider] = keys(provider)
        if not _rings[provider]:
            raise LLMError(f"no {provider.upper()}_API_KEY_1..4 in .env and no {provider.upper()}_API_KEY set")
    return _rings[provider]


def _take_key(provider: str) -> tuple[int, str]:
    ring = _ring(provider)
    now = time.time()
    start = _next.get(provider, 0)
    for offset in range(len(ring)):
        slot, key = ring[(start + offset) % len(ring)]
        if _cooling.get((provider, slot), 0.0) <= now:
            _next[provider] = (start + offset + 1) % len(ring)
            return slot, key
    slot, key = min(ring, key=lambda kv: _cooling.get((provider, kv[0]), 0.0))
    # long waits belong to the orchestrator, not the key picker: cap it and let the caller move on
    time.sleep(min(max(1.0, _cooling.get((provider, slot), 0.0) - now), 5.0))
    return slot, key


def _client_for(key: str) -> genai.Client:
    global _client, _client_key
    if _client is None or _client_key != key:
        _client = genai.Client(api_key=key)
        _client_key = key
    return _client


_live: dict[str, list[str]] = {}
# a model the catalog lists but the account cannot call: remembering it stops the
# chain from picking the same dead end twice in one run
_unprovisioned: set[tuple[str, str]] = set()
PREFER = ("120b", "coder", "70b", "gpt-oss")


def live_models(provider: str, key: str) -> list[str]:
    if provider in OPENAI_COMPAT_BASE and provider not in _live:
        req = urllib.request.Request(
            f"{OPENAI_COMPAT_BASE[provider]}/models",
            headers={"Authorization": f"Bearer {key}", "User-Agent": "siliconboard/0.1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
                data = json.load(resp)
            _live[provider] = sorted(m["id"] for m in data.get("data", []))
        except Exception:
            _live[provider] = []
    return _live.get(provider, [])


def _replacement_model(provider: str, dead: str, key: str) -> str | None:
    ids = live_models(provider, key)
    if dead in ids or not ids:
        return None
    usable = [i for i in ids if (provider, i) not in _unprovisioned]
    for pref in PREFER:
        for i in usable:
            if pref in i.lower():
                return i
    return usable[0] if usable else None


def _skip_or_replace(provider: str, model: str, key: str, models: list[str], model_idx: int) -> int:
    """A dead model is replaced in place so the chain repairs itself for the rest of the run."""
    if model_idx >= len(models) - 1:
        return model_idx
    replacement = _replacement_model(provider, model, key)
    if replacement and replacement != model:
        models[model_idx] = f"{provider}:{replacement}"
        return model_idx
    return model_idx + 1


def _next_provider(models: list[str], model_idx: int) -> int:
    """A broken key or an empty account is not per-model: leave the whole provider behind."""
    provider = models[model_idx].partition(":")[0]
    i = model_idx + 1
    while i < len(models) and models[i].partition(":")[0] == provider:
        i += 1
    return i


def _lower_cap(provider: str, message: str) -> bool:
    """A 'request too large' 429 names the real ceiling: adopt it and retry the same route."""
    m = re.search(r"[Ll]imit (\d+)", message)
    if m is None:
        return False
    limit = int(m.group(1))
    current = _cap.get(provider) or PROVIDER_MAX_TOKENS.get(provider) or MAX_OUTPUT_TOKENS
    if limit - 64 >= current:
        return False
    _cap[provider] = max(512, limit - 64)
    return True


def _next_after_repair(models: list[str], model_idx: int, repairs: int) -> tuple[int, int]:
    """Two unusable answers in a row from the same model: stop repairing and let the chain move on."""
    if repairs >= 2 and model_idx < len(models) - 1:
        return model_idx + 1, 0
    return model_idx, repairs


def routes(models: list[str]) -> list[str]:
    usable = [route for route in models if route.partition(":")[0] not in _dead]
    return usable or models


def dead_providers() -> list[str]:
    return sorted(_dead)


def _openai_call(provider: str, key: str, model: str, system: str, prompt: str,
                 schema: type[BaseModel], temperature: float) -> tuple[str, int | None, int | None]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                f"{system}\n\nReturn ONLY a single JSON object matching this JSON Schema. "
                f"No markdown fences, no prose:\n{json.dumps(schema.model_json_schema())}"
            )},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": _cap.get(provider) or PROVIDER_MAX_TOKENS.get(provider) or MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{OPENAI_COMPAT_BASE[provider]}/chat/completions",
        data=json.dumps(body).encode(),
        # Groq sits behind Cloudflare: the default Python-urllib signature is banned (error 1010)
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "siliconboard/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180, context=_SSL_CONTEXT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise LLMError(f"{provider} http {exc.code}: {detail}") from exc
    usage = data.get("usage") or {}
    return data["choices"][0]["message"]["content"], usage.get("prompt_tokens"), usage.get("completion_tokens")


def set_cache_dir(path: Path | None) -> None:
    global _cache_dir
    _cache_dir = path
    if path is not None:
        path.mkdir(parents=True, exist_ok=True)


def _cache_file(name: str, system: str, prompt: str, model: str) -> Path | None:
    if _cache_dir is None:
        return None
    digest = hashlib.sha1(f"{model}|{system}|{prompt}".encode()).hexdigest()[:12]
    return _cache_dir / f"{name}-{digest}.json"


# every failed attempt is recorded: a chain that dies must say where and why,
# and a listed model is not a provisioned one (nvidia 404s on models the catalog shows)
TRACE: list[str] = []


def _trace(note: str) -> None:
    TRACE.append(note)
    del TRACE[:-40]
    if os.environ.get("SB_LLM_TRACE"):
        print(f"llm! {note}", file=sys.stderr, flush=True)


def _unfence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        body = text.split("```")[1]
        text = body[4:] if body.startswith("json") else body
    return text.strip()


def call(
    prompt: str,
    system: str,
    schema: type[T],
    role: str,
    models: list[str],
    board: Board | None = None,
    temperature: float = 0.2,
    validate: Callable[[T], list[str]] | None = None,
) -> T:
    cached = _cache_file(role, system, prompt, models[0])  # keyed on the chain head, stable across the run
    if cached is not None and cached.exists():
        result = schema.model_validate_json(cached.read_text())
        if validate is None or not validate(result):
            if board is not None:
                board.llm_calls.append(LLMCall(role=role, model=models[0], key_slot=-1, cached=True))
            return result
    models = routes(models)
    TRACE.clear()

    last: Exception | None = None
    model_idx = 0
    repairs = 0
    attempt = 0
    while attempt < MAX_ATTEMPTS:
        attempt += 1
        route = models[min(model_idx, len(models) - 1)]
        provider, _, model = route.partition(":")
        try:
            slot, key = _take_key(provider)
        except LLMError as exc:
            if model_idx < len(models) - 1:
                model_idx += 1
                last = exc
                continue
            raise
        try:
            if provider == "gemini":
                response = _client_for(key).models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=temperature,
                        max_output_tokens=MAX_OUTPUT_TOKENS,
                    ),
                )
                text = response.text or ""
                usage = getattr(response, "usage_metadata", None)
                tokens_in = getattr(usage, "prompt_token_count", None)
                tokens_out = getattr(usage, "candidates_token_count", None)
            else:
                text, tokens_in, tokens_out = _openai_call(provider, key, model, system, prompt, schema, temperature)
            result = schema.model_validate_json(_unfence(text))
            if validate is not None and (problems := validate(result)):
                raise _Repair("; ".join(problems))
        except _Repair as exc:
            last = exc
            repairs += 1
            _trace(f"{route} unusable output: {str(exc)[:120]}")
            model_idx, repairs = _next_after_repair(models, model_idx, repairs)
            prompt += (
                f"\n\nYour previous response was valid JSON but unusable: {exc}. "
                "Return the corrected COMPLETE artifact, as JSON matching the schema."
            )
            continue
        except ValidationError as exc:
            last = exc
            repairs += 1
            _trace(f"{route} schema mismatch: {exc.error_count()} error(s)")
            model_idx, repairs = _next_after_repair(models, model_idx, repairs)
            prompt += (
                "\n\nYour previous response did not match the schema. "
                f"Errors: {exc.error_count()}. Return ONLY the JSON object, no fences, no prose."
            )
            continue
        except json.JSONDecodeError as exc:
            last = exc
            repairs += 1
            _trace(f"{route} invalid json: {exc.msg}")
            model_idx, repairs = _next_after_repair(models, model_idx, repairs)
            prompt += f"\n\nYour previous response was not valid JSON ({exc.msg}). Return ONLY the JSON object."
            continue
        except LLMError as exc:
            last = exc
            message = str(exc)
            low = message.lower()
            _trace(f"{route} failed: {message[:140]}")
            if "failed to validate json" in low or "failed_generation" in low:
                # the server could not parse the model's own answer: ask again, it is not model-specific
                repairs += 1
                model_idx, repairs = _next_after_repair(models, model_idx, repairs)
                prompt += "\n\nYour previous response was not valid JSON. Return ONLY the JSON object, no fences, no prose."
                continue
            if "request too large" in low or ("reduce max_tokens" in low):
                if _lower_cap(provider, message):
                    _trace(f"{provider} output cap lowered to {_cap[provider]}, retrying {model}")
                    continue
            if "429" in message or "rate limit" in low or "too many requests" in low:
                _cooling[(provider, slot)] = time.time() + COOLDOWN_S
                now = time.time()
                if model_idx < len(models) - 1 and all(
                    _cooling.get((provider, s), 0.0) > now for s, _ in _ring(provider)
                ):
                    model_idx += 1  # whole provider is rate-limited: the chain moves on
                continue
            if any(tag in low for tag in ("404", "410", "not_found", "end of life", "no longer available", "gone")):
                if "not found for account" in low or "function" in low and "not found" in low:
                    _unprovisioned.add((provider, model))
                _trace(f"{provider}:{model} unavailable, replacing")
                model_idx = _skip_or_replace(provider, model, key, models, model_idx)
                continue
            if any(code in message for code in ("401", "403")) or "unauthorized" in low or "invalid api key" in low:
                _dead.add(provider)
                model_idx = _next_provider(models, model_idx)
                if model_idx >= len(models):
                    raise LLMError(f"{role}: request rejected, retrying cannot help: {message[:300]}") from exc
                continue
            if model_idx < len(models) - 1:
                model_idx += 1
                continue
            raise LLMError(f"{role}: {message[:300]}") from exc
        except Exception as exc:
            last = exc
            message = str(exc)
            low = message.lower()
            _trace(f"{route} failed: {message[:140]}")
            if any(code in low for code in ("401", "403", "permission_denied", "unauthenticated", "api key not valid")):
                _dead.add(provider)
                model_idx = _next_provider(models, model_idx)
                if model_idx >= len(models):
                    raise LLMError(f"{role}: request rejected, retrying cannot help: {message[:300]}") from exc
                continue
            if "depleted" in low or ("invalid_argument" in low and "quota" in low):
                _dead.add(provider)
                model_idx = _next_provider(models, model_idx)
                if model_idx >= len(models):
                    raise LLMError(f"{role}: {message[:300]}") from exc
                continue
            if "429" in message or "resource_exhausted" in low or "rate limit" in low:
                _cooling[(provider, slot)] = time.time() + COOLDOWN_S
                now = time.time()
                if model_idx < len(models) - 1 and all(
                    _cooling.get((provider, s), 0.0) > now for s, _ in _ring(provider)
                ):
                    model_idx += 1  # whole provider is rate-limited: the chain moves on
                continue
            if ("404" in message or "not_found" in low or "no longer available" in low or "410" in message) and model_idx < len(models) - 1:
                model_idx = _skip_or_replace(provider, model, key, models, model_idx)
                continue
            time.sleep(min(2.0 * attempt, 10.0))
            continue

        if board is not None:
            board.llm_calls.append(
                LLMCall(role=role, model=route, key_slot=slot, tokens_in=tokens_in, tokens_out=tokens_out)
            )
        if cached is not None:
            cached.write_text(result.model_dump_json(indent=2))
        return result

    raise LLMError(f"{role}: {MAX_ATTEMPTS} attempts failed, last error: {last}\n  attempts: " + " | ".join(TRACE[-6:]))