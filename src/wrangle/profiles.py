"""What you need to judge a field by its values, not its name.

One profile per source field: type, alias, how full it is, how varied, its most
common values, and its coded domain if the layer declares one. The Match fields
screen shows these to a person; the field matcher shows the same thing to the model.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd

from .checks import as_code, is_blank

# Up to 50 most common values per field. Measured on the locked sources: every
# code list fits (the largest, West Sacramento's RecDwg, has 49); fields with more
# are names, ids, ZIPs and dates, where a longer list helps nobody judge.
TOP_VALUES = 50
DOMAIN_CODES = 20  # enough to read a domain; Yolo's largest has dozens


def field_profiles(gdf: gpd.GeoDataFrame, sidecar: dict[str, Any]) -> list[dict[str, Any]]:
    total = len(gdf)
    out = []
    for f in sidecar["fields"]:
        name = f["name"]
        if name == sidecar["oid_field"] or name not in gdf:
            continue
        col = gdf[name]
        filled = col[~is_blank(col)]
        counts = filled.map(as_code).value_counts()
        domain = f.get("domain") or {}
        out.append(
            {
                "name": name,
                "alias": f.get("alias") if f.get("alias") != name else None,
                "type": f["type"],
                "populated_share": round(len(filled) / total, 3) if total else 0.0,
                "distinct": int(len(counts)),
                "top_values": [
                    {
                        "value": str(v),
                        "share": round(n / total, 4),
                        "label": domain.get(str(v)),
                    }
                    for v, n in counts.head(TOP_VALUES).items()
                ],
                "domain": dict(list(domain.items())[:DOMAIN_CODES]) or None,
            }
        )
    return out
