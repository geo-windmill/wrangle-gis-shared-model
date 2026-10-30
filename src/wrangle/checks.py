"""Quality checks on a downloaded layer — what the loading dock warns about before
a layer is sent on to field matching.

Every check reads the publisher's raw data as `wrangle fetch` stored it. None of
them fix anything; they say what an analyst should know before trusting a layer.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

# Below this share of populated rows a field is "nearly empty". Yolo County's
# RoadClass (1%) is the case this exists for.
SPARSE_BELOW = 0.05
# Below this share inside the area of interest, say so.
COVERAGE_BELOW = 0.90


def run_checks(
    gdf: gpd.GeoDataFrame, sidecar: dict[str, Any], area: BaseGeometry | None
) -> dict[str, Any]:
    total = len(gdf)
    oid = sidecar["oid_field"]
    verdict = sidecar["verdict"]
    warnings: list[dict[str, str]] = []

    if verdict["outcome"] != "complete":
        warnings.append(
            {
                "level": "critical",
                "text": f"Fetched {verdict['fetched']:,} of {verdict['expected']:,} features "
                f"({verdict['outcome']}).",
            }
        )

    no_geom = int((gdf.geometry.isna() | gdf.geometry.is_empty).sum())
    if no_geom:
        warnings.append({"level": "warning", "text": f"{no_geom:,} features have no geometry."})

    inside = None
    if area is not None and total:
        inside = float(shapely.intersects(gdf.geometry.values, area).mean())
        if inside < COVERAGE_BELOW:
            outside = 100 - inside * 100
            warnings.append(
                {"level": "warning", "text": f"{outside:.0f}% of features fall outside the area."}
            )

    empty, sparse, off_domain, case_only = [], [], [], []
    for f in sidecar["fields"]:
        name = f["name"]
        if name == oid or name not in gdf:
            continue
        col = gdf[name]
        blank = is_blank(col)
        populated = total - int(blank.sum())
        if total and populated == 0:
            empty.append(name)
        elif total and populated / total < SPARSE_BELOW:
            sparse.append({"field": name, "share": round(populated / total, 3)})
        if f.get("domain"):
            values = col[~blank].map(as_code)
            outside = values[~values.isin(f["domain"].keys())]
            # `ST` where the domain declares `St`: the same code in another case. Real, but
            # a note, not a warning — it would bury the values that truly fall outside.
            spelled = {k.casefold(): k for k in f["domain"]}
            other_case = outside.str.casefold().isin(spelled)
            if other_case.any():
                top = outside[other_case].value_counts().index[0]
                case_only.append(
                    {"field": name, "rows": int(other_case.sum()),
                     "example": top, "as": spelled[top.casefold()]}
                )
            outside = outside[~other_case]
            if len(outside):
                off_domain.append(
                    {
                        "field": name,
                        "rows": int(len(outside)),
                        "examples": outside.value_counts().head(3).index.tolist(),
                    }
                )

    for o in off_domain:
        warnings.append(
            {
                "level": "warning",
                "text": f"{o['field']}: {o['rows']:,} "
                f"{'value is' if o['rows'] == 1 else 'values are'} not in the layer's own "
                f"domain (e.g. {', '.join(o['examples'])}).",
            }
        )
    for c in case_only:
        warnings.append(
            {
                "level": "info",
                "text": f"{c['field']}: {c['rows']:,} values match the layer's domain except "
                f"for letter case ({c['example']} for {c['as']}).",
            }
        )
    if empty:
        warnings.append(
            {"level": "info", "text": f"Blank in every row: {', '.join(empty)}."}
        )
    for s in sparse:
        pct = s["share"] * 100
        shown = f"{pct:.1f}" if pct < 1 else f"{pct:.0f}"
        warnings.append(
            {"level": "info", "text": f"{s['field']} is populated on {shown}% of rows."}
        )

    return {
        "rows": total,
        "verdict": verdict["outcome"],
        "inside_share": round(inside, 3) if inside is not None else None,
        "no_geometry": no_geom,
        "empty_fields": empty,
        "sparse_fields": sparse,
        "off_domain": off_domain,
        "case_only": case_only,
        "warnings": warnings,
    }


def is_blank(col: pd.Series) -> pd.Series:
    """Null, or a string of nothing but whitespace — El Dorado's single-space null."""
    if col.dtype == object or pd.api.types.is_string_dtype(col):
        return col.isna() | col.astype(str).str.strip().eq("")
    return col.isna()


def domain_spelling(code: str, domain: dict[str, str]) -> str | None:
    """The domain's own spelling of a code it declares only in another letter case."""
    if code in domain:
        return None
    return {k.casefold(): k for k in domain}.get(code.casefold())


def as_code(v: Any) -> str:
    """Match a value against domain codes, which are stored as strings.

    An integer column with nulls comes back as float, so 2 arrives as 2.0.
    """
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
