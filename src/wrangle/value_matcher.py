"""AI value matching: one AI call per model field and source, checked before use.

What the model is given: the model field as the shared model defines it (type,
description, vocabulary or unit and range — not the notes, which are written for
people and would hand it the answers), the source field's name and alias, its
declared domain, and every distinct value with its share of rows. The whole list,
not just the hard values: abbreviations and codes are read from their neighbours.

What it returns: strict JSON, one row per raw value — the model value it means
(or null), a confidence, and one sentence of reasoning.

What happens before it is shown: every answer must name a raw value that exists
and a model value the field accepts — the vocabulary for an enum, the strict
caster for a number. Anything else is dropped and reported.

Recorded under cache/ai/ and replayed, like the field matcher.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from .ai import ask, model_key
from .cache import Mode
from .matcher import AI_CACHE
from .models import TargetField

SYSTEM = """\
You map the raw values of one field in a public GIS layer onto one field of a
small shared data model.

For every raw value listed, give the model value it means, or null when it means
unknown, not applicable, or something the model field cannot hold. Read the whole
list: values that abbreviate or respell each other belong together, and a code's
meaning can often be read from its label, its neighbours and its share of rows. A
value outside the layer's own declared domain is suspect. Do not stretch: when a
value could mean two model values, pick the likelier and say so with low
confidence. For a number field, give the number in the model's unit as text, or
null for a placeholder that is not a measurement.

Confidence: high when the meaning is clear, medium when it is probably right, low
when a person must check. Reasoning is one short sentence."""

PROMPT_VALUES = 100  # every value of every table cell; the largest real one has 19


class ValueMatch(BaseModel):
    raw: str
    value: str | None
    confidence: Literal["high", "medium", "low"]
    reasoning: str


class ValueResponse(BaseModel):
    values: list[ValueMatch]


def build_prompt(
    target: TargetField, publisher: str, field: dict[str, Any], values: list[dict[str, Any]],
    blank_share: float,
) -> str:
    """`field` is the source field's sidecar entry: name, alias, type, domain."""
    head = f"Model field: {target.name} ({target.type}"
    if target.unit:
        head += f", {target.unit}"
    if target.min is not None or target.max is not None:
        head += f", {_n(target.min)} to {_n(target.max)}"
    lines = [head + f"): {' '.join(target.description.split())}"]
    if target.values:
        lines.append(f"  one of: {', '.join(target.values)}")
    src = f"Source: {publisher}, field {field['name']} ({field['type']}"
    if field.get("alias") and field["alias"] != field["name"]:
        src += f', alias "{field["alias"]}"'
    lines += ["", src + ")"]
    if field.get("domain"):
        lines.append(
            "Declared domain: "
            + ", ".join(f"{k}={v}" for k, v in list(field["domain"].items())[:PROMPT_VALUES])
        )
    lines.append("Values (share of rows):")
    for v in values[:PROMPT_VALUES]:
        label = f" = {v['label']}" if v["label"] and v["label"] != v["raw"] else ""
        off = " (not in the declared domain)" if v["declared"] is False else ""
        lines.append(f"- {v['raw']}{label} ({_share(v['share'])}){off}")
    if blank_share:
        lines.append(f"Blank: {_share(blank_share)}")
    return "\n".join(lines)


def _n(x: float | None) -> str:
    return "?" if x is None else (str(int(x)) if float(x).is_integer() else str(x))


def _share(s: float) -> str:
    p = s * 100
    return f"{p:.1f}%" if 0 < p < 0.95 else f"{p:.0f}%"


def validate(
    target: TargetField, values: list[dict[str, Any]], response: ValueResponse
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Keep answers that name a real raw value and a model value the field accepts."""
    raws = {v["raw"] for v in values}
    kept: dict[str, dict[str, Any]] = {}
    dropped = []
    for m in response.values:
        why, value = None, None
        if m.raw not in raws:
            why = f"no raw value {m.raw!r}"
        elif m.raw in kept:
            why = f"second answer for {m.raw!r}"
        else:
            try:
                value = _accept(target, m.value)
            except ValueError as exc:
                why = str(exc)
        if why:
            dropped.append({**m.model_dump(), "why": why})
            continue
        kept[m.raw] = {"value": value, "confidence": m.confidence, "reasoning": m.reasoning}
    return kept, dropped


def _accept(target: TargetField, value: str | None) -> Any:
    if value is None or not str(value).strip() or str(value).strip().lower() == "null":
        return None
    if target.type in ("integer", "number"):
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"{value!r} is not a number") from None
        return target.coerce(number)
    return target.coerce(value)


def _cache_path(prompt: str) -> Path:
    digest = hashlib.sha1(f"{model_key()}\n{SYSTEM}\n{prompt}".encode()).hexdigest()[:16]
    return AI_CACHE / f"values-{digest}.json"


def has_recording(prompt: str) -> bool:
    return _cache_path(prompt).exists()


async def match_values(
    target: TargetField, values: list[dict[str, Any]], prompt: str, label: str,
    cached_only: bool = False,
) -> dict[str, Any]:
    """The AI's answer for one cell, from the recording if there is one. `label`
    names the cell in the recording and in errors."""
    path = _cache_path(prompt)
    mode = Mode(os.environ.get("WRANGLE_CACHE_MODE", Mode.PREFER_CACHE.value))

    if path.exists() and mode in (Mode.REPLAY, Mode.PREFER_CACHE):
        record = json.loads(path.read_text(encoding="utf-8"))
        cached = True
    elif cached_only:
        return {"pending": True}
    elif mode == Mode.REPLAY:
        raise LookupError(f"no recorded AI response for {label}; run once with the network")
    else:
        record = await ask(SYSTEM, prompt, ValueResponse)
        record["cell"] = label
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1), encoding="utf-8")
        cached = False

    kept, dropped = validate(target, values, ValueResponse.model_validate(record["response"]))
    return {
        "model": record["model"],
        "cached": cached,
        "recorded_at": record["recorded_at"],
        "values": kept,
        "dropped": dropped,
    }
