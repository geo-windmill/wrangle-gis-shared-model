"""AI field matching: one AI call per source, checked before anyone sees it.

What the model is given: the shared model's fields with their descriptions, and
for every source field its name, alias, type, fill rate, coded domain and most
common values. Values are the point — a name can lie, the values rarely do.

What it returns: strict JSON, one row per model field — a source field (or none),
a confidence, and one sentence of reasoning.

What happens before it is shown: every suggestion is checked against the real
model and source field lists, and anything that is not on them is dropped and
reported. That is the one hallucination guard that matters.

Every response is recorded under cache/ai/, so a demo replays without the
network — and says that it did.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from .ai import ask, model_key
from .cache import DEFAULT_CACHE_DIR, Mode
from .models import TargetModel

PROMPT_VALUES = 5  # most common values per field shown to the model
AI_CACHE = Path(DEFAULT_CACHE_DIR) / "ai"

SYSTEM = """\
You match fields from a public GIS layer to a small shared data model.

For each model field, pick the one source field whose values best carry that
meaning, or null when no field fits. Judge by the values first and the names
second: a field with a perfect name whose values do not fit is the wrong answer,
and so is a field that is nearly empty. Prefer a publisher's own complete field
over parts that would have to be combined. Use each source field at most once.

Confidence: high when the values clearly carry the meaning, medium when they
probably do, low when it is a guess a person must check. Reasoning is one short
sentence that cites the values you relied on."""


class Match(BaseModel):
    target_field: str
    source_field: str | None
    confidence: Literal["high", "medium", "low"]
    reasoning: str


class MatchResponse(BaseModel):
    matches: list[Match]


def build_prompt(model: TargetModel, publisher: str, profiles: list[dict[str, Any]]) -> str:
    lines = [model.prompt_view(), "", f"Source layer: {publisher}", "Fields:"]
    for p in profiles:
        head = f"- {p['name']} ({p['type']}"
        if p["alias"]:
            head += f', alias "{p["alias"]}"'
        head += f") {round(p['populated_share'] * 100)}% filled, {p['distinct']} distinct"
        lines.append(head)
        if p["top_values"]:
            # The five most common are enough for the model, and keeping the
            # prompt stable keeps the recorded answers valid.
            vals = ", ".join(
                f"{v['value']}{' = ' + v['label'] if v['label'] else ''} ({v['share'] * 100:.0f}%)"
                for v in p["top_values"][:PROMPT_VALUES]
            )
            lines.append(f"    top values: {vals}")
        if p["domain"]:
            lines.append(
                "    declared domain: "
                + ", ".join(f"{k}={v}" for k, v in list(p["domain"].items())[:12])
            )
    return "\n".join(lines)


def validate(
    model: TargetModel, profiles: list[dict[str, Any]], response: MatchResponse
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep only suggestions that name real fields on both sides; report the rest."""
    targets = set(model.mappable_field_names)
    sources = {p["name"] for p in profiles}
    kept, dropped, seen = [], [], set()
    for m in response.matches:
        why = None
        if m.target_field not in targets:
            why = f"model has no field {m.target_field!r}"
        elif m.source_field is not None and m.source_field not in sources:
            why = f"source has no field {m.source_field!r}"
        elif m.target_field in seen:
            why = f"second answer for {m.target_field}"
        if why:
            dropped.append({**m.model_dump(), "why": why})
            continue
        seen.add(m.target_field)
        kept.append(m.model_dump())
    return kept, dropped


def has_recording(model: TargetModel, publisher: str, profiles: list[dict[str, Any]]) -> bool:
    return _cache_path(build_prompt(model, publisher, profiles)).exists()


def _cache_path(prompt: str) -> Path:
    digest = hashlib.sha1(f"{model_key()}\n{SYSTEM}\n{prompt}".encode()).hexdigest()[:16]
    return AI_CACHE / f"{digest}.json"


async def match_source(
    model: TargetModel, source_id: str, publisher: str, profiles: list[dict[str, Any]]
) -> dict[str, Any]:
    """Suggestions for one source, from the recording if there is one."""
    prompt = build_prompt(model, publisher, profiles)
    path = _cache_path(prompt)
    mode = Mode(os.environ.get("WRANGLE_CACHE_MODE", Mode.PREFER_CACHE.value))

    if path.exists() and mode in (Mode.REPLAY, Mode.PREFER_CACHE):
        record = json.loads(path.read_text(encoding="utf-8"))
        cached = True
    elif mode == Mode.REPLAY:
        raise LookupError(f"no recorded AI response for {source_id}; run once with the network")
    else:
        record = await ask(SYSTEM, prompt, MatchResponse)
        record["source_id"] = source_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1), encoding="utf-8")
        cached = False

    kept, dropped = validate(model, profiles, MatchResponse.model_validate(record["response"]))
    return {
        "source_id": source_id,
        "model": record["model"],
        "cached": cached,
        "recorded_at": record["recorded_at"],
        "matches": kept,
        "dropped": dropped,
    }


