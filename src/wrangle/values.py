"""Edit values: every raw value of a decided field, onto the model's vocabulary or type.

Each method answers on its own, cheapest first, so that agreement means something:

  domain    the layer's own coded-value domain: the code's label, read like text.
            Free and exact — and silent on a value the domain does not declare.
  text      the raw value against the vocabulary: exact, case-fold, whole words.
  abbrev    guarded fuzzy: RMP -> ramp, PED -> pedestrian. Three letters at least,
            so SS and SD can never collide, and never on a value with digits in it.
  cast      a number through the model's strict caster: 0 below a min becomes null.
  parse     numbers inside text: "2 1/2 X 4D" holds 2.5 and 4. More than one is a
            question for a person, not a guess.
  keep      text kept as published (string fields).
  reviewed  what a person already decided for the same raw value in another source.
  ai        the AI model, shown the field's whole distinct-value list (value_matcher.py).

A value is confirmed automatically only when at least two methods answered and
every one of them agrees. Anything else waits for a person.

Fields with too many distinct values for a table — names, dates — get one rule
instead: text passes through; dates are parsed in a format a person confirms.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pandas as pd

from .checks import as_code, is_blank
from .models import TargetField

MAX_VALUES = 100  # more distinct values than this and a number field is a rule, not a table
STRING_TABLE = 30  # a text field with this many or fewer values can be edited value by value
MIN_ABBREV = 3  # SS and SD must never match anything

# Methods in the order they rank, cheapest and surest first.
TIER = {"reviewed": 0, "domain": 1, "text": 2, "cast": 2, "abbrev": 3, "parse": 3, "keep": 3,
        "ai": 4}

# --- reading values ---------------------------------------------------------------------


def distinct_values(col: pd.Series, domain: dict[str, str] | None) -> dict[str, Any]:
    """A column's distinct values, most common first, with how many rows each
    covers, its domain label, and whether the domain declares it at all."""
    total = len(col)
    blank = is_blank(col)
    counts = col[~blank].map(as_code).value_counts()
    rows = [
        {
            "raw": str(v),
            "rows": int(n),
            "share": round(n / total, 4) if total else 0.0,
            "label": domain.get(str(v)) if domain else None,
            "declared": (str(v) in domain) if domain else None,
        }
        for v, n in counts.items()
    ]
    return {"total": total, "blank": int(blank.sum()), "distinct": len(rows), "values": rows}


def cell_kind(target: TargetField, distinct: int) -> str:
    """table: decide value by value. text, date, number: one rule for the field."""
    if target.type == "date":
        return "date"
    if target.type == "string" and distinct > STRING_TABLE:
        return "text"
    if target.type in ("integer", "number") and distinct > MAX_VALUES:
        return "number"
    return "table"


# --- the text methods ------------------------------------------------------------------


def words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).split()


def digit_runs(s: str) -> list[str]:
    """The digit signature: "2 1/2 X 4D" -> ["2", "1", "2", "4"]. Two strings whose
    signatures differ are different things, however alike they look."""
    return re.findall(r"\d+", s)


def same_digits(a: str, b: str) -> bool:
    return digit_runs(a) == digit_runs(b)


def _is_abbrev(short: str, word: str) -> bool:
    """`short` abbreviates `word`: a prefix (PED, pedestrian), or its letters in
    order starting with the same one (RMP, ramp; ALY, alley)."""
    if len(short) < MIN_ABBREV or not short.isalpha() or short[0] != word[0]:
        return False
    if word.startswith(short):
        return True
    it = iter(word)
    return all(ch in it for ch in short)


def match_vocab(text: str, vocab: list[str]) -> tuple[str, list[str]]:
    """How `text` names a vocabulary term: ("exact" | "words" | "abbrev", terms),
    trying the surest first. More than one term back means it is ambiguous."""
    w = words(text)
    if not w:
        return "", []
    terms = {t: t.split("_") for t in vocab}

    exact = [t for t, tw in terms.items() if tw == w]
    if exact:
        return "exact", exact

    # Every word of the term appears in the text; the most specific term wins:
    # "Other Principal Arterial" is principal_arterial, not other.
    whole = [t for t, tw in terms.items() if set(tw) <= set(w)]
    if whole:
        most = max(len(terms[t]) for t in whole)
        return "words", [t for t in whole if len(terms[t]) == most]

    # Values with digits never fuzzy-match: 4S is not 2 1/2 X 4D.
    if digit_runs(text):
        return "", []
    abbrev = [t for t, tw in terms.items() if any(_is_abbrev(a, b) for a in w for b in tw)]
    return ("abbrev", abbrev) if abbrev else ("", [])


# --- numbers -----------------------------------------------------------------------------

_NUMBER = re.compile(r"(\d+)\s+(\d+)/(\d+)|(\d+)/(\d+)|(\d+(?:\.\d+)?)")


def numbers_in(text: str) -> list[float]:
    """Every number written in `text`, fractions included: "2 1/2 X 4D" -> [2.5, 4]."""
    out = []
    for m in _NUMBER.finditer(text):
        whole, num, den, fnum, fden, plain = m.groups()
        if whole:
            out.append(int(whole) + int(num) / int(den))
        elif fnum:
            out.append(int(fnum) / int(fden))
        else:
            out.append(float(plain))
    return out


def _cast(target: TargetField, raw: str) -> tuple[bool, Any, str]:
    """(ok, value, detail). A value the strict caster refuses becomes null, and
    the detail says why."""
    try:
        number = float(raw)
    except ValueError:
        return False, None, ""
    try:
        return True, target.coerce(number), f"{_fmt(number)} {target.unit or ''}".strip()
    except ValueError as exc:
        return True, None, str(exc).replace(f" for {target.name}", "")


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


# --- putting it together ------------------------------------------------------------------


def candidates_for(
    target: TargetField,
    value: dict[str, Any],
    reviewed: list[dict[str, Any]],
    ai: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Every method's answer for one raw value, grouped by the model value it gives.

    `reviewed` is [{"publisher", "value"}] — a person's decision for the same raw
    value in another source. `ai` is that value's AI answer, if there is one.
    """
    raw, label = value["raw"], value["label"]
    found: list[tuple[Any, dict[str, Any]]] = []

    def say(to: Any, method: str, detail: str, **extra: Any) -> None:
        found.append((to, {"method": method, "detail": detail, **extra}))

    for r in reviewed:
        say(r["value"], "reviewed", r["publisher"])

    if target.type == "enum":
        vocab = list(target.values or [])
        # A label that repeats its own code ("26 - Bike Path", "Ramp - Urban (21)")
        # is read without it: the code is not content, and its digits would trip
        # the fuzzy guard.
        text = re.sub(rf"(?<![0-9A-Za-z]){re.escape(raw)}(?![0-9A-Za-z])", " ", label or "")
        if words(text) and words(label) != words(raw):
            how, terms = match_vocab(text, vocab)
            for t in terms:
                say(t, "domain", f"{raw} = {label}", how=how)
        how, terms = match_vocab(raw, vocab)
        for t in terms:
            say(t, "abbrev" if how == "abbrev" else "text", how)

    elif target.type in ("integer", "number"):
        ok, out, detail = _cast(target, raw)
        if ok:
            say(out, "cast", detail)
        else:
            for n in dict.fromkeys(numbers_in(raw)):
                try:
                    say(target.coerce(n), "parse", f"{_fmt(n)} in “{raw}”")
                except ValueError as exc:
                    say(None, "parse", str(exc).replace(f" for {target.name}", ""))

    elif target.type == "string":
        say(raw.strip(), "keep", "as published")

    if ai:
        say(ai["value"], "ai", ai["reasoning"], confidence=ai["confidence"])

    groups: dict[Any, list[dict[str, Any]]] = {}
    for to, m in found:
        groups.setdefault(_key(to), []).append(m)
    out = [{"value": _unkey(k), "methods": ms} for k, ms in groups.items()]
    out.sort(key=lambda c: (-_weight(c["methods"]), min(TIER[m["method"]] for m in c["methods"])))
    return out


def _key(v: Any) -> Any:
    # 25 and 25.0 are one answer; None is an answer too.
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def _unkey(k: Any) -> Any:
    return int(k) if isinstance(k, float) and k.is_integer() else k


def _counts(m: dict[str, Any]) -> bool:
    """A low-confidence AI answer is shown, but it is not a vote."""
    return not (m["method"] == "ai" and m.get("confidence") == "low")


def _weight(methods: list[dict[str, Any]]) -> float:
    return sum(1 if _counts(m) else 0.5 for m in methods)


def status(candidates: list[dict[str, Any]]) -> str:
    """agree: two or more methods, one answer. single: one method. conflict: the
    methods give different answers. none: nothing could place it."""
    if not candidates:
        return "none"
    if len(candidates) > 1:
        return "conflict"
    return "agree" if sum(_counts(m) for m in candidates[0]["methods"]) >= 2 else "single"


def reconcile(
    target: TargetField,
    values: list[dict[str, Any]],
    reviewed: dict[str, list[dict[str, Any]]],
    ai: dict[str, dict[str, Any]],
    decided: dict[str, Any],
    decided_by: dict[str, str],
) -> list[dict[str, Any]]:
    """One row per raw value: what every method says, the status, and the decision."""
    rows = []
    for v in values:
        raw = v["raw"]
        cands = candidates_for(target, v, reviewed.get(_norm(raw), []), ai.get(raw))
        rows.append(
            {
                **v,
                "candidates": cands,
                "status": status(cands),
                "decided": raw in decided,
                "decision": decided.get(raw),
                "decided_by": decided_by.get(raw),
            }
        )
    return rows


def _norm(raw: str) -> str:
    return " ".join(str(raw).split()).casefold()


def reviewed_index(
    target: str, docs: dict[str, dict[str, Any]], publishers: dict[str, str], exclude: str
) -> dict[str, list[dict[str, Any]]]:
    """Every value decision a person made for `target` in the other sources, by
    raw value — so the second publisher of B/FT/TF costs nothing to review."""
    out: dict[str, list[dict[str, Any]]] = {}
    for sid, doc in docs.items():
        if sid == exclude:
            continue
        by = doc["values_decided_by"].get(target, {})
        for raw, value in doc["values"].get(target, {}).items():
            if by.get(raw) == "person":
                out.setdefault(_norm(raw), []).append(
                    {"publisher": publishers.get(sid, sid), "value": value}
                )
    return out


# --- dates -------------------------------------------------------------------------------

DATE_FORMATS = {
    "date": "a date field, taken as is",
    "yyyymmdd": "digits, 20040323 — a time after it is dropped",
    "iso": "2004-03-23",
    "m/d/yyyy": "3/23/2004",
}
# Before this, or after today, a date is a placeholder (1899-12-30 is the zero date
# of a spreadsheet; 9999-12-31 means "never"). It lands as null.
EARLIEST = dt.date(1900, 1, 1)


def parse_date(raw: str, fmt: str) -> dt.date | None:
    s = str(raw).strip()
    try:
        if fmt == "date":
            return pd.Timestamp(s).date()
        if fmt == "yyyymmdd":
            if not (s.isdigit() and len(s) in (8, 12, 14)):
                return None
            return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        if fmt == "iso":  # with its dashes: Python would also read 20040323
            return dt.date.fromisoformat(s[:10]) if s[4:5] == "-" else None
        if fmt == "m/d/yyyy":
            return dt.datetime.strptime(s.split()[0], "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return None
    raise ValueError(f"no date format {fmt!r}")


def date_options(col: pd.Series, source_type: str) -> list[dict[str, Any]]:
    """Every date format that reads at least some of the column, best first, each
    with the same evidence: how much it reads, what it cannot, the range it gives."""
    total = len(col)
    blank = is_blank(col)
    counts = col[~blank].map(as_code).value_counts()
    filled = int(counts.sum())
    formats = ["date"] if source_type == "Date" else [f for f in DATE_FORMATS if f != "date"]
    out = []
    for fmt in formats:
        parsed, failed, placeholder = {}, [], []
        for raw, n in counts.items():
            d = parse_date(raw, fmt)
            if d is None:
                failed.append((raw, n))
            elif d < EARLIEST or d > dt.date.today():
                placeholder.append((raw, n))
            else:
                parsed[raw] = (d, n)
        read = sum(n for _, n in parsed.values())
        if not read:
            continue
        dates = [d for d, _ in parsed.values()]
        top = sorted(parsed.items(), key=lambda kv: -kv[1][1])[:5]
        out.append(
            {
                "format": fmt,
                "about": DATE_FORMATS[fmt],
                "read_share": round(read / filled, 4) if filled else 0.0,
                "filled_share": round(filled / total, 4) if total else 0.0,
                "failed": {"rows": sum(n for _, n in failed),
                           "examples": [str(r) for r, _ in failed[:3]]},
                "placeholder": {"rows": sum(n for _, n in placeholder),
                                "examples": [str(r) for r, _ in placeholder[:3]]},
                "range": [min(dates).isoformat(), max(dates).isoformat()],
                "examples": [{"raw": str(r), "date": d.isoformat()} for r, (d, _) in top],
            }
        )
    out.sort(key=lambda o: -o["read_share"])
    return out


def text_preview(col: pd.Series) -> dict[str, Any]:
    """A text field passes through as published, trimmed; blank becomes null."""
    blank = is_blank(col)
    counts = col[~blank].astype(str).str.strip().value_counts()
    return {
        "filled_share": round(1 - blank.mean(), 4) if len(col) else 0.0,
        "distinct": int(len(counts)),
        "examples": [str(v) for v in counts.head(5).index],
    }
