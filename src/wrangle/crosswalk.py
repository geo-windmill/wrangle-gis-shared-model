"""A first-guess crosswalk: which source field might feed each shared-model field.

Deliberately simple, and labelled as a suggestion wherever it is shown. It uses
only what any analyst would glance at — the field's name and alias against the
model's generic `hints`, whether the types can meet, and how often the field is
actually populated. It knows nothing about any one publisher.

That is the point. It picks Sacramento County's `CLASS` over `CARTOCLASS`
because the name is the better match, and nothing here can tell that the two
disagree on thousands of roads. Field matching is where a person decides.
"""

from __future__ import annotations

import re
from typing import Any

import geopandas as gpd

from .checks import as_code, is_blank
from .models import TargetField, TargetModel

# Below this share of populated rows a field is never suggested, however good its
# name. Yolo County's RoadClass (1%) is a perfect name match and the reason.
MIN_POPULATED = 0.05
MIN_SCORE = 0.5

_NUMERIC = {"Integer", "SmallInteger", "Double", "Single", "BigInteger"}
_NEVER = {"OID", "Geometry", "GlobalID", "Blob", "Raster", "GUID"}


def tokens(text: str | None) -> list[str]:
    """`Speed_Limit` -> speed, limit · `RoadClass` -> road, class · `FULLSTREET` -> fullstreet."""
    if not text:
        return []
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def hint_matches(words: list[str], hints: list[str]) -> list[dict[str, Any]]:
    """Each hint that matches a word, and how: 1.0 for a whole word, 0.7 when the
    hint sits inside a word ("class" in "cartoclass"), 0.6 when the word is a
    shortened hint ("lim" for "limit")."""
    found = []
    for rank, h in enumerate(hints):
        best: dict[str, Any] | None = None
        for w in words:
            if w == h:
                hit = {"points": 1.0, "kind": "whole word"}
            elif len(h) >= 3 and h in w:
                hit = {"points": 0.7, "kind": "inside a word"}
            elif len(w) >= 3 and h.startswith(w):
                hit = {"points": 0.6, "kind": "shortened"}
            else:
                continue
            if best is None or hit["points"] > best["points"]:
                best = {"hint": h, "word": w, "rank": rank, **hit}
        if best:
            found.append(best)
    return found


def name_score(words: list[str], hints: list[str]) -> tuple[float, int]:
    """Score a field name against a model field's hints; also return the rank of
    the best-matching hint, for breaking ties (hints are listed most telling first).

    The best single match counts in full; each further hint that also matches adds
    0.1 (`last_edited_date` beats `created_date` for an edit date); a field whose
    whole name is a hint ("Owner") gets 0.05 more.
    """
    found = hint_matches(words, hints)
    if not found:
        return 0.0, len(hints)
    top = max(found, key=lambda m: (m["points"], -m["rank"]))
    exact = 0.05 if len(words) == 1 and words[0] in hints else 0.0
    return top["points"] + 0.1 * min(2, len(found) - 1) + exact, top["rank"]


def types_meet(target: TargetField, source_type: str, values: Any) -> bool:
    if source_type in _NEVER:
        return False
    if target.type == "date":
        return source_type == "Date"
    if target.type in ("integer", "number"):
        if source_type in _NUMERIC:
            return True
        # A text column of numbers ("4", "6", "8") can still feed a number.
        return source_type == "String" and len(values) > 0 and _mostly_numeric(values)
    if target.type == "string":
        # Text that is really numbers (a length stored as text) is not a name.
        return source_type == "String" and not (len(values) > 0 and _mostly_numeric(values))
    return source_type != "Date"  # enums take codes of any type


def _mostly_numeric(values: Any) -> bool:
    sample = values.astype(str).head(500)
    return sample.str.fullmatch(r"\s*-?\d+(\.\d+)?\s*").mean() >= 0.9


def candidates(
    target: TargetField, gdf: gpd.GeoDataFrame, sidecar: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every source field that could feed `target`, best first."""
    total = len(gdf)
    out = []
    for f in sidecar["fields"]:
        if f["name"] == sidecar["oid_field"] or f["name"] not in gdf:
            continue
        hints = [h.lower() for h in target.hints]
        by_name, rank = name_score(tokens(f["name"]), hints)
        by_alias, alias_rank = name_score(tokens(f.get("alias")), hints)
        score, via, words = by_name, "name", tokens(f["name"])
        if 0.8 * by_alias > by_name:
            score, rank, via, words = 0.8 * by_alias, alias_rank, "alias", tokens(f.get("alias"))
        if score < MIN_SCORE:
            continue
        col = gdf[f["name"]]
        filled = col[~is_blank(col)]
        if not types_meet(target, f["type"], filled):
            continue
        share = len(filled) / total if total else 0.0
        counts = filled.map(as_code).value_counts()
        out.append(
            {
                "source_field": f["name"],
                "alias": f.get("alias"),
                "type": f["type"],
                "score": round(score, 2),
                # The same score as a plain percentage: 100% is a whole-word match on a hint
                # word; part of a word is 70%, a shortened word 60%; an alias counts 80%.
                "match_pct": min(100, round(score * 100)),
                "hint_rank": rank,
                "populated_share": round(share, 3),
                "distinct": int(len(counts)),
                "samples": [str(v) for v in counts.head(3).index],
                # Up to 50 most common values, as a share of every row (blanks included).
                "top_values": [
                    {"value": str(v), "share": round(n / total, 4)}
                    for v, n in counts.head(50).items()
                ],
                # Why it scored what it did, for the crosswalk's explanation popover.
                "why": {"via": via, "words": words, "matches": hint_matches(words, hints)},
            }
        )
    # Best name first. On a tie: a mostly-filled field beats a sparse one, then the
    # field matching the more telling hint, then the one with more distinct values.
    out.sort(
        key=lambda c: (-c["score"], c["populated_share"] < 0.5, c["hint_rank"], -c["distinct"])
    )
    return out


def suggest(
    model: TargetModel, gdf: gpd.GeoDataFrame, sidecar: dict[str, Any]
) -> dict[str, Any]:
    """One suggestion per model field for one source, each source field used once.

    Fields too empty to use are reported as `skipped`, so the crosswalk can say
    why the obvious name was passed over.
    """
    ranked = {t.name: candidates(t, gdf, sidecar) for t in model.fields if t.sourced}
    used: dict[str, str] = {}  # source field -> the model field it went to
    out: dict[str, Any] = {}
    # Hand out the strongest matches first, so a field that fits two targets goes
    # to the one it fits best.
    def strongest(name: str) -> float:
        return max((c["score"] for c in ranked[name]), default=0)

    order = sorted(ranked, key=strongest, reverse=True)
    for name in order:
        skipped = []
        pick = None
        passed_over = []
        for c in ranked[name]:
            if c["source_field"] in used:
                passed_over.append(_brief(c, f"already feeds {used[c['source_field']]}"))
                continue
            if c["populated_share"] < MIN_POPULATED:
                skipped.append(_brief(c, "nearly empty"))
                continue
            pick = c
            break
        if pick:
            used[pick["source_field"]] = name
            later = [c for c in ranked[name] if c is not pick and c["source_field"] not in used]
            for c in later:
                tied = c["score"] == pick["score"]
                why = "same score, lost the tie-break" if tied else "lower score"
                passed_over.append(_brief(c, why))
        # Every other candidate is kept, so a person can see all of them.
        out[name] = {"pick": pick, "skipped": skipped, "runners_up": passed_over}
    return out


def _brief(c: dict[str, Any], reason: str) -> dict[str, Any]:
    """A candidate that was not picked, with enough to explain it on its card."""
    return {
        "source_field": c["source_field"],
        "score": c["score"],
        "match_pct": c["match_pct"],
        "populated_share": c["populated_share"],
        "reason": reason,
        "why": c["why"],
    }
