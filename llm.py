"""LLM router: Gemini primary, Groq then NVIDIA as OpenAI-compatible fallbacks."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from board import Board, LLMCall

HEAVY = [
    "gemini:gemini-3.8-flash",
    "gemini:gemini-3.7-flash",
    "groq:openai/gpt-oss-120b",
    "nvidia:qwen/qwen2.5-coder-32b-instruct",
]
LIGHT = [
    "gemini:gemini-3.5-flash-lite",
    "gemini:gemini-3.1-flash-lite",
    "groq:openai/gpt-oss-20b",
    "nvidia:meta/llama-3.3-70b-instruct",
]

OPENAI_COMPAT_BASE = {"groq": "https://api.groq.com/openai/v1", "nvidia": "https://integrate.api.nvidia.com/v1"}

MAX_OUTPUT_TOKENS = 8192
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


class LLMError(RuntimeError):
    pass


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
    time.sleep(max(1.0, _cooling.get((provider, slot), 0.0) - now))
    return slot, key


def _client_for(key: str) -> genai.Client:
    global _client, _client_key
    if _client is None or _client_key != key:
        _client = genai.Client(api_key=key)
        _client_key = key
    return _client


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
        "max_tokens": MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{OPENAI_COMPAT_BASE[provider]}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
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
) -> T:
    cached = _cache_file(role, system, prompt, models[0])
    if cached is not None and cached.exists():
        result = schema.model_validate_json(cached.read_text())
        if board is not None:
            board.llm_calls.append(LLMCall(role=role, model=models[0], key_slot=-1, cached=True))
        return result

    last: Exception | None = None
    model_idx = 0
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
        except ValidationError as exc:
            last = exc
            prompt += (
                "\n\nYour previous response did not match the schema. "
                f"Errors: {exc.error_count()}. Return ONLY the JSON object, no fences, no prose."
            )
            continue
        except json.JSONDecodeError as exc:
            last = exc
            prompt += f"\n\nYour previous response was not valid JSON ({exc.msg}). Return ONLY the JSON object."
            continue
        except LLMError as exc:
            last = exc
            message = str(exc)
            low = message.lower()
            if "429" in message or "rate limit" in low or "too many requests" in low:
                _cooling[(provider, slot)] = time.time() + COOLDOWN_S
                continue
            if model_idx < len(models) - 1:
                model_idx += 1
                continue
            raise LLMError(f"{role}: {message[:300]}") from exc
        except Exception as exc:
            last = exc
            message = str(exc)
            low = message.lower()
            if any(code in low for code in ("401", "403", "invalid_argument", "permission_denied", "unauthenticated", "api key not valid")):
                raise LLMError(f"{role}: request rejected, retrying cannot help: {message[:300]}")
            if "429" in message or "resource_exhausted" in low or "rate limit" in low:
                _cooling[(provider, slot)] = time.time() + COOLDOWN_S
                continue
            if ("404" in message or "not_found" in low or "no longer available" in low or "depleted" in low) and model_idx < len(models) - 1:
                model_idx += 1
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

    raise LLMError(f"{role}: {MAX_ATTEMPTS} attempts failed, last error: {last}")