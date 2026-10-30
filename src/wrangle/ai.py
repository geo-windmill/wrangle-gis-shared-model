"""Which AI answers, and how: an open model on your own machine, or Claude.

Two providers, one interface. Both are asked for the same structured output (a
pydantic model), and both answers go through the same guards and the same
recordings, so nothing downstream knows or cares which one answered.

    WRANGLE_AI=ollama      the default: an open-weight model, run locally by Ollama (https://ollama.com).
                           No key, no cost, nothing leaves the machine.
                           WRANGLE_AI_MODEL picks the model (default qwen3:14b; also e.g.
                           gpt-oss:20b, qwen3:8b); OLLAMA_HOST points at another Ollama server.
    WRANGLE_AI=anthropic   Claude, through Anthropic's API. Needs ANTHROPIC_API_KEY and the
                           `ai` extra (pip install -e ".[ai]").

Every answer is recorded under cache/ai/, keyed on the model and the exact prompt,
so each model keeps its own recordings and a demo replays offline.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

CLAUDE = "claude-opus-5-5"
DEFAULT_PROVIDER = "ollama"  # open by default; WRANGLE_AI=anthropic for Claude
DEFAULT_OLLAMA_MODEL = "qwen3:14b"
OLLAMA_CONTEXT = 16384


def provider() -> str:
    load_env()
    p = os.environ.get("WRANGLE_AI", DEFAULT_PROVIDER).strip().lower()
    if p not in ("ollama", "anthropic"):
        raise ValueError(f"WRANGLE_AI must be 'ollama' or 'anthropic', not {p!r}")
    return p


def model_key() -> str:
    """The model's name in recordings. Claude keeps its original key, so recordings
    made before open models were added still replay."""
    if provider() == "anthropic":
        return CLAUDE
    return f"ollama:{os.environ.get('WRANGLE_AI_MODEL', DEFAULT_OLLAMA_MODEL)}"


async def ask(system: str, prompt: str, output_format: type[BaseModel]) -> dict[str, Any]:
    """One structured-output call to whichever provider is configured."""
    if provider() == "anthropic":
        return await _ask_claude(system, prompt, output_format)
    return await _ask_ollama(system, prompt, output_format)


# --- open models, via Ollama -----------------------------------------------------------


async def _ask_ollama(system: str, prompt: str, output_format: type[BaseModel]) -> dict[str, Any]:
    model = model_key().removeprefix("ollama:")
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith("http"):
        host = f"http://{host}"
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "format": output_format.model_json_schema(),  # Ollama constrains the output to this
        "stream": False,
        # Ollama's default context is 4,096 tokens and it cuts longer prompts silently; one
        # layer's field list runs to ~7,000. Room for the prompt and the answer.
        "options": {"temperature": 0, "num_ctx": OLLAMA_CONTEXT},
    }
    if model.startswith("qwen3"):
        body["think"] = False  # straight to the answer; the schema already asks for reasons
    elif model.startswith("gpt-oss"):
        body["think"] = "low"
    async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0)) as client:
        try:
            r = await client.post(f"{host}/api/chat", json=body)
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Ollama is not running at {host}: start it, or set WRANGLE_AI"
            ) from exc
        if r.status_code == 404:
            raise RuntimeError(f"model {model!r} not found: run `ollama pull {model}`")
        r.raise_for_status()
        data = r.json()
    parsed = output_format.model_validate_json(data["message"]["content"])
    return {
        "model": f"ollama:{data.get('model', model)}",
        "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "response": parsed.model_dump(),
        "usage": {
            "input_tokens": data.get("prompt_eval_count"),
            "output_tokens": data.get("eval_count"),
            "seconds": round((data.get("total_duration") or 0) / 1e9, 1),
        },
    }


# --- Claude, via Anthropic's API (optional) ---------------------------------------------


async def _ask_claude(system: str, prompt: str, output_format: type[BaseModel]) -> dict[str, Any]:
    from anthropic import AsyncAnthropic  # optional dependency: the `ai` extra

    client = AsyncAnthropic()
    # If a safety classifier declines, the API re-runs the request on a fallback
    # model inside the same call. Field names are not risky, but a demo on stage
    # should not stop on a false positive.
    response = await client.beta.messages.parse(
        model=CLAUDE,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_format=output_format,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined this request")
    if response.parsed_output is None:
        raise RuntimeError(f"no structured output (stop reason {response.stop_reason})")
    return {
        "model": response.model,
        "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "response": response.parsed_output.model_dump(),
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    }


def load_env(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from .env (beside pyproject.toml) into the environment, without
    overriding anything already set. No dependency, no logging of values."""
    path = path or Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
