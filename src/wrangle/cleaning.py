"""How a field's values land, short of standardizing them: as published, or cleaned.

Edit values offers three modes per field and source:

  published     what the publisher's own map shows: the text as written, except that a
                code the layer's domain declares lands as its label. A number field
                reads the text as a number, because a number column cannot hold
                anything else, and conversion still refuses one outside the model's
                range. An enum field left like this holds the publisher's text.
  cleaned       generic tidying that needs no knowledge of the model's vocabulary:
                  trim        leading, trailing and repeated spaces
                  blank       nothing left after trimming -> null
                  placeholder N/A, NULL, <Null>, None, "-", "?" ... -> null
                  decode      a code the layer's own domain declares -> its label
                  case        spellings that differ only in case -> the commonest one
                  number      a number field reads its text as a number; one outside
                              the model's range (speed 0) -> null
  standardized  every raw value onto the model's own values, decided one by one
                (values.py). The only mode with anything to decide.

Every step is counted, so the conversion report can say what cleaning did.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .models import TargetField

# Text that stands for "no value". Compared case-insensitively, after trimming.
PLACEHOLDERS = {"n/a", "na", "#n/a", "null", "<null>", "(null)", "none", "nil", "nan"}
_ONLY_PUNCT = re.compile(r"[\W_]+")


def tidy(raw: str) -> str:
    """Trim, and collapse runs of whitespace to one space."""
    return " ".join(str(raw).split())


def is_placeholder(text: str) -> bool:
    return text.casefold() in PLACEHOLDERS or bool(_ONLY_PUNCT.fullmatch(text))


def number(text: str) -> float | int | None:
    try:
        x = float(text)
    except ValueError:
        return None
    return int(x) if x.is_integer() else x


def decode(text: str, domain: dict[str, str]) -> str | None:
    """The domain label for a code, tolerating "01" for 1 and "2.0" for 2."""
    if text in domain:
        return domain[text]
    n = number(text)
    if n is None:
        return None
    for code, label in domain.items():
        if number(code) == n:
            return label
    return None


def published(target: TargetField, raw: str, domain: dict[str, str] | None = None) -> Any:
    """A raw value as published: what the publisher's own map shows. A code the layer's
    coded-value domain declares lands as its label (Yolo's `19` is "19 - Local Urban"),
    because the label is what the publisher means and a bare code means nothing once it
    leaves the layer. A number field still needs a number."""
    if target.type in ("integer", "number"):
        n = number(tidy(raw))
        if n is not None and target.type == "integer" and not isinstance(n, int):
            return None  # 2.5 cannot be an integer; reported as unreadable
        return n
    if domain:
        label = decode(tidy(raw), domain)
        if label is not None:
            return label
    return raw


def clean_values(
    target: TargetField, counts: dict[str, int], domain: dict[str, str] | None
) -> dict[str, dict[str, Any]]:
    """Every distinct raw value (with its row count) -> {"value", "steps"}.

    `steps` names what cleaning did to it, in order; a null value's last step says
    why it is null (blank, placeholder, unreadable, out_of_range)."""
    numeric = target.type in ("integer", "number")
    out: dict[str, dict[str, Any]] = {}
    for raw in counts:
        text, steps = tidy(raw), []
        if text != raw:
            steps.append("trim")
        if not text:
            out[raw] = {"value": None, "steps": [*steps, "blank"]}
            continue
        if is_placeholder(text):
            out[raw] = {"value": None, "steps": [*steps, "placeholder"]}
            continue
        if domain:
            label = decode(text, domain)
            if label is not None and tidy(label) != text:
                text, steps = tidy(label), [*steps, "decode"]
        if numeric:
            n = published(target, text)
            if n is None:
                out[raw] = {"value": None, "steps": [*steps, "unreadable"]}
                continue
            try:
                kept = target.coerce(n)
            except (ValueError, TypeError):
                kept = None
            if kept is None:
                steps = [*steps, "out_of_range"]
            out[raw] = {"value": kept, "steps": steps}
            continue
        out[raw] = {"value": text, "steps": steps}

    # One spelling per word: the commonest, by rows.
    if not numeric:
        rows: Counter[str] = Counter()
        for raw, n in counts.items():
            v = out[raw]["value"]
            if v is not None:
                rows[v] += n
        best: dict[str, str] = {}
        for v, _ in rows.most_common():
            best.setdefault(v.casefold(), v)
        for o in out.values():
            v = o["value"]
            if v is not None and best[v.casefold()] != v:
                o["value"] = best[v.casefold()]
                o["steps"] = [*o["steps"], "case"]
    return out
