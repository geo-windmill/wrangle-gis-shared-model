"""The raw material: every feature of a source, on disk, as GeoParquet.

This is what the loading dock shows and Match fields reads — the publisher's own
fields and values, untouched, reprojected to WGS84 so every layer draws on one map. Nothing here
maps anything into a shared model; that is the Convert stage.

Layout, human-readable on purpose:

    data/
      boundaries/roads.geojson        area of interest (public domain, committed)
      raw/roads/R1.parquet            every feature, publisher's own fields
      raw/roads/R1.json               what was fetched, from where, and the verdict

The agency data is not committed. `wrangle fetch` rebuilds it from the public
services; the record-and-replay cache makes a second run offline.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import (
    LinearRing,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    mapping,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .arcgis import ArcGISClient, LayerInfo
from .registry import Boundary, Source

DATA_DIR = Path(
    os.environ.get("WRANGLE_DATA_DIR", Path(__file__).resolve().parents[2] / "data")
)


def safe_name(*names: str) -> None:
    """Feature and source ids become file names: never a path (`../x`), whoever sent them."""
    for n in names:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", n):
            raise ValueError(f"{n!r} is not a feature or source id")


def raw_path(feature: str, source_id: str) -> Path:
    safe_name(feature, source_id)
    return DATA_DIR / "raw" / feature / f"{source_id}.parquet"


def sidecar_path(feature: str, source_id: str) -> Path:
    safe_name(feature, source_id)
    return DATA_DIR / "raw" / feature / f"{source_id}.json"


def boundary_path(feature: str) -> Path:
    safe_name(feature)
    return DATA_DIR / "boundaries" / f"{feature}.geojson"


# --- Esri JSON -> shapely ----------------------------------------------------


def esri_to_shapely(geom: dict[str, Any] | None) -> BaseGeometry | None:
    """Esri JSON geometry to shapely. Points and polylines are all this demo meets.

    Paths can carry a Z (or M) value per vertex; we keep x and y only.
    """
    if not geom:
        return None
    if "x" in geom:
        if geom["x"] is None or geom["y"] is None:
            return None
        return Point(geom["x"], geom["y"])
    if "paths" in geom:
        lines = [LineString([p[:2] for p in path]) for path in geom["paths"] if len(path) >= 2]
        if not lines:
            return None
        return lines[0] if len(lines) == 1 else MultiLineString(lines)
    if "rings" in geom:
        return _rings_to_polygon(geom["rings"])
    raise ValueError(f"unsupported Esri geometry keys: {sorted(geom)}")


def _rings_to_polygon(rings: list[list[list[float]]]) -> BaseGeometry | None:
    """Esri rings are a flat list: clockwise rings are outer shells, counter-
    clockwise rings are holes. Treating every ring after the first as a hole
    turns California's Channel Islands into holes in the mainland.
    """
    shells: list[Polygon] = []
    holes: list[LinearRing] = []
    for r in rings:
        if len(r) < 4:
            continue
        ring = LinearRing([p[:2] for p in r])
        (holes if ring.is_ccw else shells).append(Polygon(ring))
    if not shells:
        return None
    out = []
    for shell in shells:
        inner = [h.exterior for h in holes if shell.contains(h.representative_point())]
        out.append(Polygon(shell.exterior, inner))
    return out[0] if len(out) == 1 else MultiPolygon(out)


def features_to_frame(features: list[dict[str, Any]], info: LayerInfo) -> gpd.GeoDataFrame:
    """Esri features to a GeoDataFrame, keeping every field the publisher sent.

    Esri dates arrive as epoch milliseconds; they become real timestamps so the
    table drawer shows "2024-03-01" rather than 1709251200000.
    """
    rows = [f.get("attributes") or {} for f in features]
    geoms = [esri_to_shapely(f.get("geometry")) for f in features]
    df = pd.DataFrame(rows)
    for f in info.fields:
        if f.short_type == "Date" and f.name in df:
            df[f.name] = pd.to_datetime(df[f.name], unit="ms", utc=True, errors="coerce")
    return gpd.GeoDataFrame(df, geometry=geoms, crs="EPSG:4326")


# --- sources -----------------------------------------------------------------


async def download_source(client: ArcGISClient, feature: str, source: Source) -> dict[str, Any]:
    """Fetch every feature of one locked source and write it to disk.

    Returns the sidecar dict. Raises on a source that cannot be read at all; a
    source that reads partially is written, and its verdict says so.
    """
    info = await client.layer_info(source.url)
    features, verdict = await client.fetch_all(source.url, info=info, where=source.where)
    gdf = features_to_frame(features, info)

    out = raw_path(feature, source.id)
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(out)

    sidecar = {
        "source_id": source.id,
        "publisher": source.publisher,
        "attribution": source.attribution,
        "url": source.url,
        "where": source.where,
        "oid_field": info.oid_field,
        "geometry": info.geometry,
        "native_crs": info.wkid,
        "fetched_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "rows": len(gdf),
        "verdict": verdict.model_dump(),
        "fields": [
            {
                "name": f.name,
                "type": f.short_type,
                "alias": f.alias,
                "domain": {str(k): v for k, v in f.coded_values.items()} or None,
            }
            for f in info.fields
            if not f.is_geometry
        ],
    }
    sidecar_path(feature, source.id).write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
    return sidecar


def read_sidecar(feature: str, source_id: str) -> dict[str, Any] | None:
    p = sidecar_path(feature, source_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# --- boundaries --------------------------------------------------------------


async def download_boundary(client: ArcGISClient, feature: str, boundary: Boundary) -> Path:
    """Fetch, merge and save the area of interest as one GeoJSON feature."""
    payload = await client.get_json(
        f"{boundary.url}/query",
        {
            "where": boundary.where,
            "outFields": "NAME",
            "returnGeometry": "true",
            "outSR": 4326,
            "maxAllowableOffset": boundary.max_allowable_offset,
        },
    )
    parts = [esri_to_shapely(f.get("geometry")) for f in payload.get("features") or []]
    parts = [p.buffer(0) for p in parts if p is not None]
    if not parts:
        raise RuntimeError(f"boundary query returned no features: {boundary.where}")
    merged = unary_union(parts)
    names = sorted((f.get("attributes") or {}).get("NAME", "") for f in payload["features"])

    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": boundary.name,
                    "parts": names,
                    "attribution": boundary.attribution,
                    "source": boundary.url,
                    "where": boundary.where,
                },
                "geometry": _rounded(mapping(merged)),
            }
        ],
    }
    out = boundary_path(feature)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(collection), encoding="utf-8")
    return out


def _rounded(obj: Any, places: int = 5) -> Any:
    """Round coordinates to ~1 m. Keeps the committed boundary file small and diffable."""
    if isinstance(obj, float):
        return round(obj, places)
    if isinstance(obj, (list, tuple)):
        return [_rounded(v, places) for v in obj]
    if isinstance(obj, dict):
        return {k: _rounded(v, places) for k, v in obj.items()}
    return obj
