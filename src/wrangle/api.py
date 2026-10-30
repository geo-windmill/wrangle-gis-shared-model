"""The HTTP API behind the UI. Thin on purpose: every route is a few lines over
the registry, the assessment, or the GeoParquet files `wrangle fetch` wrote.

    GET  /api/features                              roads, hydrants
    GET  /api/{feature}/catalog                     Loading dock: one card per source
    GET  /api/{feature}/boundary                    area-of-interest outline
    GET  /api/{feature}/sources                     what is on disk, per source
    GET  /api/{feature}/sources/{id}/geojson        geometry + OID, for the map
    GET  /api/{feature}/sources/{id}/rows           the table drawer, paged
    GET  /api/{feature}/sources/{id}/features/{oid} one feature's raw attributes
    GET  /api/{feature}/sources/{id}/checks         quality warnings before ingest
    GET  /api/{feature}/crosswalk?ids=              suggested model-field crosswalk
    GET  /api/{feature}/compare?lon=&lat=&ids=      every source's feature at a point
    GET  /api/{feature}/sources/{id}/profile        every field, judged by its values
    POST /api/{feature}/match?ids=                  AI suggestions, one call per source
    POST /api/{feature}/automap?ids=                confirm where AI (high) and name agree
    GET/PUT/DELETE /api/{feature}/mappings/...      the decisions, kept in the configuration record
    GET  /api/{feature}/record/{id}                one source's configuration record and its history
    GET  /api/{feature}/values?ids=                 Edit values: every raw value, every method
    POST /api/{feature}/values/ask?ids=             AI for the value tables, one call each
    POST /api/{feature}/values/automap?ids=         confirm where two methods agree
    PUT/DELETE /api/{feature}/values/{id}/{field}   value decisions (and PUT .../rules/...)
    DELETE /api/{feature}/mappings?ids=, /values?ids=  start a stage over for these sources
    GET  /api/{feature}/convert?ids=                Convert: last conversion per source
    POST /api/{feature}/convert/{id}                one source into the shared model
    POST /api/{feature}/unify?ids=                  every converted source, one GeoParquet
    POST /api/{feature}/query                       DuckDB over the converted files (any SQL)
    GET  /api/{feature}/converted/{id}/geojson      a converted source, for the map
    GET  /api/{feature}/converted/rows              a page of the unified layer, as data
    POST /api/{feature}/sources/{id}/fetch          (re)download one source

Run with `wrangle serve`. The built UI is served from web/dist at `/`.
"""

from __future__ import annotations

import asyncio
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import shapely
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pyproj import Transformer
from shapely.geometry import box, shape

from .arcgis import ArcGISClient, assess_layer
from .cache import ResponseCache
from .checks import as_code, domain_spelling, run_checks
from .cleaning import clean_values, published
from .convert import (
    MAP_FIELD,
    QUERIES,
    convert_source,
    out_path,
    read_report,
    read_rows,
    run_query,
    unified_path,
    write_source,
    write_unified,
)
from .crosswalk import suggest
from .download import boundary_path, download_source, raw_path, read_sidecar
from .mappings import (
    add_value,
    clear_all,
    clear_decision,
    clear_value,
    load_added,
    load_doc,
    load_history,
    load_record,
    mark_converted,
    mode_of,
    remove_value,
    save_decision,
    save_mode,
    save_rule,
    save_values,
    updated_at,
    value_in_use,
)
from .matcher import has_recording, match_source
from .models import TargetModel, load_model
from .profiles import field_profiles
from .registry import Source, available_features, load_sources
from .value_matcher import build_prompt as build_value_prompt
from .value_matcher import match_values
from .values import (
    DATE_FORMATS,
    MAX_VALUES,
    cell_kind,
    date_options,
    distinct_values,
    reconcile,
    reviewed_index,
    text_preview,
)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

app = FastAPI(title="wrangle", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=1024)

class Decision(BaseModel):
    source_field: str | None


# One card per source, computed once per process. `?refresh=true` recomputes.
_catalog: dict[str, list[dict[str, Any]]] = {}
# Downloads started from the UI, keyed by (feature, source id).
_jobs: dict[tuple[str, str], dict[str, Any]] = {}


def _sources(feature: str):  # noqa: ANN202
    if feature not in available_features():
        raise HTTPException(404, f"no feature {feature!r}; have {available_features()}")
    return load_sources(feature)


def _source(feature: str, source_id: str) -> Source:
    try:
        return _sources(feature).get(source_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None


# --- features ----------------------------------------------------------------


@app.get("/api/features")
def features() -> list[dict[str, Any]]:
    out = []
    for feat in available_features():
        ss, m = load_sources(feat), load_model(feat)
        out.append(
            {
                "feature": feat,
                "title": m.title,
                "geometry": m.geometry,
                "region": ss.region,
                "boundary": ss.boundary.name if ss.boundary else None,
                "loadable": len(ss.loadable),
                "cards": len(ss.cards),
            }
        )
    return out


@app.get("/api/{feature}/boundary")
def boundary(feature: str) -> FileResponse:
    _sources(feature)
    p = boundary_path(feature)
    if not p.exists():
        raise HTTPException(404, "no boundary yet — run `wrangle fetch`")
    return FileResponse(p, media_type="application/geo+json")


@lru_cache(maxsize=4)
def _boundary_geom(feature: str, mtime: float):  # noqa: ANN202 — mtime busts the cache
    fc = json.loads(boundary_path(feature).read_text(encoding="utf-8"))
    return shape(fc["features"][0]["geometry"])


def _area(feature: str):  # noqa: ANN202
    p = boundary_path(feature)
    return _boundary_geom(feature, p.stat().st_mtime) if p.exists() else None


# --- Loading dock: the source cards -----------------------------------------------


@app.get("/api/{feature}/catalog")
async def catalog(feature: str, refresh: bool = False) -> list[dict[str, Any]]:
    """One card per source: the one-request assessment, plus where it sits
    relative to the area of interest, plus the vetting record."""
    ss = _sources(feature)
    if refresh or feature not in _catalog:
        async with ArcGISClient(cache=ResponseCache()) as client:
            results = await asyncio.gather(
                *(_card(client, feature, s) for s in ss.cards)
            )
        _catalog[feature] = list(results)
    return _catalog[feature]


async def _card(client: ArcGISClient, feature: str, s: Source) -> dict[str, Any]:
    card: dict[str, Any] = {
        "id": s.id,
        "publisher": s.publisher,
        "attribution": s.attribution,
        "color": s.color,
        "url": s.url,
        "where": s.where,
        "loadable": s.loadable,
        "reason": s.reason,
        "reason_text": s.reason_text,
        "reason_detail": " ".join((s.reason_detail or "").split()) or None,
        "interest": [" ".join(n.split()) for n in s.interest],
    }
    try:
        a = await assess_layer(
            client, s.url, source_id=s.id, publisher=s.publisher, where=s.where
        )
    except Exception as exc:  # noqa: BLE001 — a dead host is a card, not a crash
        card["assessment"] = {"ok": False, "reason": type(exc).__name__, "reason_detail": str(exc)}
        return card

    card["assessment"] = {
        "ok": a.ok,
        "reason": a.reason,
        "reason_detail": a.reason_detail,
        "name": a.name,
        "geometry": a.geometry,
        "count": a.count,
        "field_count": a.field_count,
        "domain_count": a.domain_count,
        "wkid": a.wkid,
        "last_edit": a.last_edit,
        "max_record_count": a.max_record_count,
        "oid_field": a.oid_field,
        "estimated_load_s": a.estimated_load_s,
        "notes": a.notes,
    }
    card["coverage"] = _coverage(feature, s, a.bbox, a.wkid)
    return card


def _coverage(
    feature: str, s: Source, bbox: tuple[float, ...] | None, wkid: int | None
) -> dict[str, Any] | None:
    """How much of this source falls inside the area of interest.

    With the data on disk: the share of features that touch the area. Without it,
    an estimate from the layer's declared extent — which, for a statewide layer
    narrowed by a `where` clause (Caltrans), badly undercounts. The card says
    which one it is.
    """
    area = _area(feature)
    if area is None:
        return None
    gdf = _frame_or_none(feature, s.id)
    if gdf is not None and len(gdf):
        inside = shapely.intersects(gdf.geometry.values, area)
        return {"share": round(float(inside.mean()), 3), "method": "features"}
    if not bbox or not wkid:
        return None
    try:
        t = Transformer.from_crs(wkid, 4326, always_xy=True)
        x0, y0 = t.transform(bbox[0], bbox[1])
        x1, y1 = t.transform(bbox[2], bbox[3])
    except Exception:  # noqa: BLE001
        return None
    ext = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    if ext.area == 0:
        return None
    return {"share": round(ext.intersection(area).area / ext.area, 3), "method": "extent"}


# --- Loading dock: the downloaded layers -------------------------------------------


@lru_cache(maxsize=16)
def _frame(feature: str, source_id: str, mtime: float) -> gpd.GeoDataFrame:  # noqa: ARG001
    return gpd.read_parquet(raw_path(feature, source_id))


def _frame_or_none(feature: str, source_id: str) -> gpd.GeoDataFrame | None:
    p = raw_path(feature, source_id)
    return _frame(feature, source_id, p.stat().st_mtime) if p.exists() else None


def _frame_or_404(feature: str, source_id: str) -> gpd.GeoDataFrame:
    _source(feature, source_id)
    gdf = _frame_or_none(feature, source_id)
    if gdf is None:
        raise HTTPException(404, f"{source_id} not downloaded — run `wrangle fetch {feature}`")
    return gdf


@app.get("/api/{feature}/sources")
def sources(feature: str) -> list[dict[str, Any]]:
    """Every loadable source and what state its data is in: the loading dock's status chips."""
    out = []
    for s in _sources(feature).loadable:
        sc = read_sidecar(feature, s.id)
        job = _jobs.get((feature, s.id))
        if job and job["state"] == "running":
            status = "fetching"
        elif job and job["state"] == "error":
            status = "error"
        elif sc is None:
            status = "missing"
        else:
            status = "ready" if sc["verdict"]["outcome"] == "complete" else "partial"
        gdf = _frame_or_none(feature, s.id) if sc else None
        bbox = (
            [round(float(v), 6) for v in gdf.geometry.total_bounds]
            if gdf is not None and len(gdf)
            else None
        )
        out.append(
            {
                "id": s.id,
                "publisher": s.publisher,
                "attribution": s.attribution,
                "color": s.color,
                "status": status,
                "error": job.get("error") if job else None,
                "rows": sc["rows"] if sc else None,
                "oid_field": sc["oid_field"] if sc else None,
                "geometry": sc["geometry"] if sc else None,
                "fetched_at": sc["fetched_at"] if sc else None,
                "verdict": sc["verdict"] if sc else None,
                "bbox": bbox,
                "fields": sc["fields"] if sc else [],
            }
        )
    return out


@lru_cache(maxsize=16)
def _checks(feature: str, source_id: str, mtime: float) -> dict[str, Any]:  # noqa: ARG001
    return run_checks(
        _frame_or_404(feature, source_id), read_sidecar(feature, source_id), _area(feature)
    )


@app.get("/api/{feature}/sources/{source_id}/checks")
def source_checks(feature: str, source_id: str) -> dict[str, Any]:
    _frame_or_404(feature, source_id)
    return _checks(feature, source_id, raw_path(feature, source_id).stat().st_mtime)


@lru_cache(maxsize=16)
def _geojson(feature: str, source_id: str, mtime: float) -> bytes:  # noqa: ARG001
    gdf = _frame_or_404(feature, source_id)
    oid = read_sidecar(feature, source_id)["oid_field"]
    # ~10 cm of precision is plenty for a projector and halves the payload.
    geoms = shapely.set_precision(gdf.geometry.values, 1e-6)
    slim = gpd.GeoDataFrame({"oid": gdf[oid].astype("int64").values}, geometry=geoms, crs=gdf.crs)
    slim.index = slim["oid"]
    return slim.to_json(drop_id=False).encode()


@app.get("/api/{feature}/sources/{source_id}/geojson")
def source_geojson(feature: str, source_id: str) -> Response:
    _frame_or_404(feature, source_id)
    mtime = raw_path(feature, source_id).stat().st_mtime
    return Response(_geojson(feature, source_id, mtime), media_type="application/geo+json")


def _records(df: Any) -> list[dict[str, Any]]:
    """Rows as plain JSON: NaN -> null, timestamps -> ISO dates."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


@app.get("/api/{feature}/sources/{source_id}/rows")
def source_rows(feature: str, source_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    gdf = _frame_or_404(feature, source_id)
    page = gdf.drop(columns=gdf.geometry.name).iloc[offset : offset + min(limit, 500)]
    return {
        "total": len(gdf),
        "offset": offset,
        "columns": [c for c in gdf.columns if c != gdf.geometry.name],
        "rows": _records(page),
    }


@app.get("/api/{feature}/sources/{source_id}/features/{oid}")
def source_feature(feature: str, source_id: str, oid: int) -> dict[str, Any]:
    """One feature exactly as the publisher sent it, with coded values decoded
    alongside (never instead of) the raw code."""
    gdf = _frame_or_404(feature, source_id)
    sc = read_sidecar(feature, source_id)
    hit = gdf[gdf[sc["oid_field"]] == oid]
    if hit.empty:
        raise HTTPException(404, f"no feature {oid} in {source_id}")
    bbox = [round(float(v), 6) for v in hit.geometry.total_bounds]
    return {"source_id": source_id, "oid": oid, "bbox": bbox, "attributes": _attributes(hit, sc)}


def _attributes(hit: gpd.GeoDataFrame, sc: dict[str, Any]) -> list[dict[str, Any]]:
    """One row's attributes for display: raw value, plus the decoded label and an
    in-domain flag wherever the layer declares a coded domain."""
    row = _records(hit.drop(columns=hit.geometry.name))[0]
    meta = {f["name"]: f for f in sc["fields"]}
    out = []
    for name, value in row.items():
        f = meta.get(name, {})
        domain = f.get("domain") or {}
        code = as_code(value) if value is not None else None
        spelled = domain_spelling(code, domain) if domain and code is not None else None
        out.append(
            {
                "name": name,
                "alias": f.get("alias"),
                "type": f.get("type"),
                "value": value,
                "decoded": domain.get(spelled or code) if domain and code is not None else None,
                # a code the domain declares in another case counts as in it, and says so
                "in_domain": (code in domain or spelled is not None)
                if domain and code is not None else None,
                "domain_spelling": spelled,
                "blank": isinstance(value, str) and not value.strip(),
            }
        )
    return out


# --- side by side: the merge, before it happens --------------------------------


@lru_cache(maxsize=16)
def _suggestion(feature: str, source_id: str, mtime: float) -> dict[str, Any]:  # noqa: ARG001
    gdf = _frame_or_404(feature, source_id)
    return suggest(_model(feature), gdf, read_sidecar(feature, source_id))


def _suggestions(feature: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    out = {}
    for sid in ids:
        if raw_path(feature, sid).exists():
            out[sid] = _suggestion(feature, sid, raw_path(feature, sid).stat().st_mtime)
    return out


@app.get("/api/{feature}/crosswalk")
def crosswalk(feature: str, ids: str) -> dict[str, Any]:
    """Shared-model fields down the side, sources across: which source field each
    cell would draw from. A first guess by name, type and population — Match fields
    is where it gets checked."""
    ss = _sources(feature)
    model, base = _model(feature), load_model(feature)
    wanted = [i for i in ids.split(",") if i]
    sugg = _suggestions(feature, wanted)
    return {
        "fields": [
            {
                "name": t.name,
                "type": t.type,
                "sourced": t.sourced,
                "description": " ".join(t.description.split()),
                "values": t.values,
                "meanings": t.meanings or None,
                "added": [v for v in t.values or [] if v not in (base.field(t.name).values or [])]
                or None,
                "unit": t.unit,
                "notes": " ".join(t.notes.split()) or None,
            }
            for t in model.fields
        ],
        "sources": [{"id": i, "publisher": ss.get(i).publisher} for i in wanted if i in sugg],
        "cells": {
            t.name: {sid: s.get(t.name) for sid, s in sugg.items()}
            for t in model.fields
            if t.sourced
        },
    }


def _model(feature: str) -> TargetModel:
    """The shared model as a person works with it: the shipped vocabularies plus any
    value a person added. Field and value prompts use `load_model` (the shipped one)."""
    return load_model(feature).extended(load_added(feature))


class NewValue(BaseModel):
    value: str
    meaning: str = ""


@app.post("/api/{feature}/vocabulary/{target}")
def add_vocabulary(feature: str, target: str, body: NewValue) -> dict[str, Any]:
    """Add a value to an enum field's vocabulary, for every source. Written as the
    vocabulary writes its values: lower case, words joined by `_` ("two-way" -> two_way)."""
    t = _target(feature, target)
    if t.type != "enum":
        raise HTTPException(422, f"{target} is not an enum: it has no vocabulary")
    value = re.sub(r"[^a-z0-9]+", "_", body.value.strip().lower()).strip("_")
    if not value or len(value) > 40:
        raise HTTPException(422, "a value is 1 to 40 letters, digits or _")
    if value in (t.values or []):
        raise HTTPException(409, f"{target} already has {value!r}")
    add_value(feature, target, value, " ".join(body.meaning.split()))
    return {"field": target, "value": value}


@app.delete("/api/{feature}/vocabulary/{target}/{value}")
def remove_vocabulary(feature: str, target: str, value: str) -> dict[str, Any]:
    """Take back a value a person added. Refused while a decision uses it."""
    used = value_in_use(feature, target, value)
    if used:
        where = ", ".join(used)
        raise HTTPException(409, f"{value!r} is a decision in {where}: change those first")
    if not remove_value(feature, target, value):
        raise HTTPException(404, f"{value!r} was not added to {target}")
    return {"field": target, "removed": value}


# --- Match fields -----------------------------------------------------------------


@lru_cache(maxsize=16)
def _profiles(feature: str, source_id: str, mtime: float) -> list[dict[str, Any]]:  # noqa: ARG001
    return field_profiles(_frame_or_404(feature, source_id), read_sidecar(feature, source_id))


def _profiles_for(feature: str, source_id: str) -> list[dict[str, Any]]:
    _frame_or_404(feature, source_id)
    return _profiles(feature, source_id, raw_path(feature, source_id).stat().st_mtime)


@app.get("/api/{feature}/sources/{source_id}/profile")
def source_profile(feature: str, source_id: str) -> list[dict[str, Any]]:
    """Every field of one source, judged by its values: fill, variety, top values."""
    return _profiles_for(feature, source_id)


@app.post("/api/{feature}/match")
async def match(feature: str, ids: str, cached_only: bool = False) -> dict[str, Any]:
    """AI suggestions, one call per source, run side by side. `cached_only`
    returns what is already recorded without spending anything."""
    ss = _sources(feature)
    model = load_model(feature)  # the shipped model: the prompt, and so the recordings

    async def one(sid: str) -> dict[str, Any]:
        s = ss.get(sid)
        profiles = _profiles_for(feature, sid)
        if cached_only and not has_recording(model, s.publisher, profiles):
            return {"source_id": sid, "pending": True}
        try:
            return await match_source(model, sid, s.publisher, profiles)
        except Exception as exc:  # noqa: BLE001 — one source failing never blocks the rest
            return {"source_id": sid, "error": f"{type(exc).__name__}: {exc}"}

    results = await asyncio.gather(*(one(i) for i in ids.split(",") if i))
    return {r["source_id"]: r for r in results}


@app.get("/api/{feature}/mappings")
def mappings(feature: str, ids: str) -> dict[str, dict[str, Any]]:
    """The decisions made so far, per source, and who made each one."""
    _sources(feature)
    return {sid: load_doc(feature, sid) for sid in ids.split(",") if sid}


@app.get("/api/{feature}/record/{source_id}")
def record(feature: str, source_id: str) -> dict[str, Any]:
    """One source's whole configuration record, and every decision that built it."""
    _source(feature, source_id)
    return {"record": load_record(feature, source_id), "history": load_history(feature, source_id)}


@app.post("/api/{feature}/automap")
async def automap(feature: str, ids: str) -> dict[str, Any]:
    """Confirm, in one go, every undecided cell where two independent opinions
    agree and the AI is sure: the AI's pick has high confidence and the name guess
    picked the same field. Anything less stays for a person. Uses recorded AI
    answers only — it never spends anything."""
    ss = _sources(feature)
    wanted = [i for i in ids.split(",") if i]
    ai = await match(feature, ids, cached_only=True)
    names = _suggestions(feature, wanted)
    confirmed = []
    for sid in wanted:
        done = load_doc(feature, sid)["fields"]
        for m in ai.get(sid, {}).get("matches") or []:
            target = m["target_field"]
            guess = ((names.get(sid) or {}).get(target) or {}).get("pick") or {}
            if target in done or m["confidence"] != "high":
                continue
            if m["source_field"] != guess.get("source_field"):
                continue  # the two opinions disagree: a person decides
            field = m["source_field"]
            save_decision(feature, sid, ss.get(sid).publisher, target, field, "automap")
            confirmed.append({"source_id": sid, "target": target, "source_field": field})
    return {"confirmed": confirmed}


@app.put("/api/{feature}/mappings/{source_id}/{target}")
def decide(feature: str, source_id: str, target: str, body: Decision) -> dict[str, Any]:
    """A person confirms which source field feeds `target` — or that none does."""
    s = _source(feature, source_id)
    if target not in _model(feature).field_names:
        raise HTTPException(404, f"model has no field {target!r}")
    if body.source_field is not None and body.source_field not in {
        p["name"] for p in _profiles_for(feature, source_id)
    }:
        raise HTTPException(422, f"{source_id} has no field {body.source_field!r}")
    return save_decision(feature, s.id, s.publisher, target, body.source_field)


@app.delete("/api/{feature}/mappings/{source_id}/{target}")
def undecide(feature: str, source_id: str, target: str) -> dict[str, Any]:
    s = _source(feature, source_id)
    return clear_decision(feature, s.id, s.publisher, target)


@app.delete("/api/{feature}/mappings")
def clear_fields(feature: str, ids: str) -> dict[str, Any]:
    """Start Match fields over for these sources: every field match goes, and the value
    decisions that hang off them. The history keeps what was cleared."""
    ss = _sources(feature)
    for sid in [i for i in ids.split(",") if i]:
        _source(feature, sid)
        clear_all(feature, sid, ss.get(sid).publisher, fields=True)
    return {"cleared": ids}


# --- Edit values ------------------------------------------------------------------


class ValueDecisions(BaseModel):
    values: dict[str, Any]  # raw value -> model value; null is a decision


class RuleDecision(BaseModel):
    rule: str | None


class ModeDecision(BaseModel):
    mode: str  # published | cleaned | standardized


@lru_cache(maxsize=64)
def _distinct(feature: str, source_id: str, field: str, mtime: float) -> dict[str, Any]:  # noqa: ARG001
    meta = _field_meta(feature, source_id, field)
    return distinct_values(_frame_or_404(feature, source_id)[field], meta.get("domain"))


@lru_cache(maxsize=64)
def _date_options(feature: str, source_id: str, field: str, mtime: float) -> list[dict[str, Any]]:  # noqa: ARG001
    col = _frame_or_404(feature, source_id)[field]
    return date_options(col, _field_meta(feature, source_id, field)["type"])


def _field_meta(feature: str, source_id: str, field: str) -> dict[str, Any]:
    for f in read_sidecar(feature, source_id)["fields"]:
        if f["name"] == field:
            return f
    raise HTTPException(404, f"{source_id} has no field {field!r}")


def _cell_plan(feature: str, source_id: str, target: str, field: str) -> dict[str, Any]:
    """What a cell is made of before anyone's opinion: its values, its kind, and —
    for a table the AI may see — the prompt."""
    s = _source(feature, source_id)
    t = _model(feature).field(target)
    base = load_model(feature).field(target)  # what the AI is shown: recordings stay valid
    mtime = raw_path(feature, source_id).stat().st_mtime
    d = _distinct(feature, source_id, field, mtime)
    kind = cell_kind(t, d["distinct"])
    meta = _field_meta(feature, source_id, field)
    plan = {"source": s, "target": t, "field": field, "distinct": d, "kind": kind,
            "mtime": mtime, "prompt": None, "base": base, "domain": meta.get("domain"),
            "mode": mode_of(load_doc(feature, source_id), target) if t.type != "date" else None}
    if kind == "table" and t.type in ("enum", "integer", "number"):
        plan["prompt"] = build_value_prompt(
            base, s.publisher, meta, d["values"], d["blank"] / d["total"] if d["total"] else 0
        )
    return plan


def _plans(feature: str, ids: list[str]) -> list[dict[str, Any]]:
    """Every model field a person has matched to a source field, in model order."""
    model = _model(feature)
    docs = {sid: load_doc(feature, sid) for sid in ids if raw_path(feature, sid).exists()}
    return [
        _cell_plan(feature, sid, t.name, docs[sid]["fields"][t.name])
        for t in model.fields
        for sid in ids
        if sid in docs and docs[sid]["fields"].get(t.name)
    ]


async def _ai_for(plan: dict[str, Any], cached_only: bool) -> dict[str, Any] | None:
    if plan["prompt"] is None:
        return None
    label = f"{plan['source'].id}.{plan['field']} -> {plan['target'].name}"
    try:
        return await match_values(
            plan["base"], plan["distinct"]["values"], plan["prompt"], label, cached_only
        )
    except Exception as exc:  # noqa: BLE001 — one cell failing never blocks the rest
        return {"error": f"{type(exc).__name__}: {exc}"}


async def _cells(feature: str, ids: list[str]) -> list[dict[str, Any]]:
    ss = _sources(feature)
    plans = _plans(feature, ids)
    docs = {sid: load_doc(feature, sid) for sid in ids}
    publishers = {sid: ss.get(sid).publisher for sid in ids}
    ais = await asyncio.gather(*(_ai_for(p, cached_only=True) for p in plans))
    out = []
    for p, ai in zip(plans, ais, strict=True):
        sid, t = p["source"].id, p["target"]
        doc = docs[sid]
        d = p["distinct"]
        cell: dict[str, Any] = {
            "field": t.name,
            "source": sid,
            "source_field": p["field"],
            "kind": p["kind"],
            "total": d["total"],
            "blank": d["blank"],
            "distinct": d["distinct"],
            "rule": doc["rules"].get(t.name),
            "mode": p["mode"],
            # Standardizing needs a short list of values to decide one by one.
            "modes": ["published", "cleaned", "standardized"] if p["kind"] == "table"
            else ["published", "cleaned"] if p["kind"] != "date" else [],
            # Does the layer publish labels for its codes? Sacramento's never does.
            "domain": bool(p["domain"]),
            "ai": None if ai is None else {k: v for k, v in ai.items() if k != "values"},
        }
        if p["kind"] != "date":
            cell["landing"] = _landing(t, d, p["domain"])
        if p["kind"] == "table":
            cell["rows"] = reconcile(
                t,
                d["values"][:MAX_VALUES],
                reviewed_index(t.name, docs, publishers, exclude=sid),
                (ai or {}).get("values") or {},
                doc["values"].get(t.name, {}),
                doc["values_decided_by"].get(t.name, {}),
            )
        elif p["kind"] == "date":
            cell["options"] = _date_options(feature, sid, p["field"], p["mtime"])
        else:
            cell["preview"] = text_preview(_frame_or_404(feature, sid)[p["field"]])
        out.append(cell)
    return out


def _landing(t: Any, d: dict[str, Any], domain: dict[str, str] | None) -> dict[str, Any]:
    """What each raw value becomes as published and cleaned — no decisions needed.
    For a table every value; for a long text field, only the values cleaning changes."""
    counts = {v["raw"]: v["rows"] for v in d["values"]}
    cleaned = clean_values(t, counts, domain)
    steps: dict[str, int] = {}
    for raw, c in cleaned.items():
        for step in c["steps"]:
            steps[step] = steps.get(step, 0) + counts[raw]
    rows = [
        {"raw": raw, "rows": counts[raw], "published": published(t, raw, domain),
         "cleaned": c["value"], "steps": c["steps"]}
        for raw, c in cleaned.items()
    ]
    if len(rows) > MAX_VALUES:
        rows = [r for r in rows if r["steps"]][:12]
    return {"steps": steps, "rows": rows}


@app.get("/api/{feature}/values")
async def values(feature: str, ids: str) -> dict[str, Any]:
    """Every matched field, per source, with each raw value and what every method
    says it means. Reads recorded AI answers only: free."""
    _sources(feature)
    return {"cells": await _cells(feature, [i for i in ids.split(",") if i])}


@app.post("/api/{feature}/values/ask")
async def values_ask(feature: str, ids: str) -> dict[str, Any]:
    """Ask the AI about every value table, side by side. Recorded answers replay."""
    plans = [
        p for p in _plans(feature, [i for i in ids.split(",") if i])
        if p["prompt"] and p["mode"] == "standardized"
    ]
    results = await asyncio.gather(*(_ai_for(p, cached_only=False) for p in plans))
    return {
        "asked": len(plans),
        "errors": [
            {"source": p["source"].id, "field": p["target"].name, "error": r["error"]}
            for p, r in zip(plans, results, strict=True)
            if r and "error" in r
        ],
    }


@app.post("/api/{feature}/values/automap")
async def values_automap(feature: str, ids: str) -> dict[str, Any]:
    """Confirm every undecided value where two or more methods answered and all of
    them agree. Anything less stays for a person. Never spends anything."""
    ss = _sources(feature)
    confirmed = []
    for cell in await _cells(feature, [i for i in ids.split(",") if i]):
        if cell["mode"] != "standardized":
            continue
        agreed = {
            r["raw"]: r["candidates"][0]["value"]
            for r in cell.get("rows") or []
            if not r["decided"] and r["status"] == "agree"
        }
        if agreed:
            sid = cell["source"]
            save_values(feature, sid, ss.get(sid).publisher, cell["field"], agreed, "automap")
            confirmed += [{"source": sid, "field": cell["field"], "raw": r} for r in agreed]
    return {"confirmed": confirmed}


@app.delete("/api/{feature}/values")
def clear_values(feature: str, ids: str) -> dict[str, Any]:
    """Start Edit values over for these sources: every value decision, date rule and
    mode goes; the field matches stay."""
    ss = _sources(feature)
    for sid in [i for i in ids.split(",") if i]:
        _source(feature, sid)
        clear_all(feature, sid, ss.get(sid).publisher, fields=False)
    return {"cleared": ids}


@app.put("/api/{feature}/values/{source_id}/{target}")
def decide_values(
    feature: str, source_id: str, target: str, body: ValueDecisions
) -> dict[str, Any]:
    """A person decides what raw values mean in the model. Every value goes through
    the model's strict caster first: a value the model cannot hold is refused."""
    s = _source(feature, source_id)
    t = _target(feature, target)
    field = load_doc(feature, source_id)["fields"].get(target)
    if not field:
        raise HTTPException(409, f"{target} has no source field in {source_id} yet")
    mtime = raw_path(feature, source_id).stat().st_mtime
    raws = {v["raw"] for v in _distinct(feature, source_id, field, mtime)["values"]}
    clean = {}
    for raw, value in body.values.items():
        if raw not in raws:
            raise HTTPException(422, f"{source_id}.{field} has no value {raw!r}")
        try:
            clean[raw] = t.coerce(value.strip() if isinstance(value, str) else value)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
    return save_values(feature, s.id, s.publisher, target, clean)


@app.delete("/api/{feature}/values/{source_id}/{target}")
def undecide_value(feature: str, source_id: str, target: str, raw: str) -> dict[str, Any]:
    s = _source(feature, source_id)
    return clear_value(feature, s.id, s.publisher, target, raw)


@app.put("/api/{feature}/rules/{source_id}/{target}")
def decide_rule(feature: str, source_id: str, target: str, body: RuleDecision) -> dict[str, Any]:
    """One rule for a field with too many values for a table: a date format, or
    `as_is` for text that passes through."""
    s = _source(feature, source_id)
    t = _target(feature, target)
    allowed = set(DATE_FORMATS) if t.type == "date" else {"as_is"}
    if body.rule is not None and body.rule not in allowed:
        raise HTTPException(422, f"{body.rule!r} is not one of {sorted(allowed)}")
    return save_rule(feature, s.id, s.publisher, target, body.rule)


@app.put("/api/{feature}/modes/{source_id}/{target}")
def decide_mode(feature: str, source_id: str, target: str, body: ModeDecision) -> dict[str, Any]:
    """How one field's values land: as published, cleaned, or standardized."""
    s = _source(feature, source_id)
    t = _target(feature, target)
    if t.type == "date":
        raise HTTPException(422, "a date field is read with a format, not a mode")
    try:
        return save_mode(feature, s.id, s.publisher, target, body.mode)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


def _target(feature: str, target: str):  # noqa: ANN202
    try:
        return _model(feature).field(target)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None


# --- Convert ----------------------------------------------------------------------


def _stale(feature: str, source_id: str, report: dict[str, Any] | None) -> bool:
    """Converted before the latest decision, or before the latest download."""
    if report is None:
        return False
    built = out_path(feature, source_id).stat().st_mtime
    decided = updated_at(feature, source_id)
    if decided is not None and decided.timestamp() > built:
        return True
    raw = raw_path(feature, source_id)
    return raw.exists() and raw.stat().st_mtime > built


@app.get("/api/{feature}/convert")
def convert_status(feature: str, ids: str) -> dict[str, Any]:
    """Each source's last conversion and whether a decision has moved on since;
    the unified layer; the query that reads it."""
    ss = _sources(feature)
    wanted = [i for i in ids.split(",") if i]
    out = []
    for sid in wanted:
        report = read_report(feature, sid) if out_path(feature, sid).exists() else None
        out.append(
            {
                "id": sid,
                "publisher": ss.get(sid).publisher,
                "report": report,
                "stale": _stale(feature, sid, report),
            }
        )
    u = unified_path(feature)
    return {
        "sources": out,
        "unified": {"path": f"out/{feature}/{u.name}", "built": u.stat().st_mtime}
        if u.exists()
        else None,
        "map_field": MAP_FIELD.get(feature),
        "queries": QUERIES.get(feature, []),
    }


@app.post("/api/{feature}/convert/{source_id}")
def convert_one(feature: str, source_id: str) -> dict[str, Any]:
    """One source into the shared model, by the reviewed decisions. Returns what
    happened to every field."""
    s = _source(feature, source_id)
    gdf = _frame_or_404(feature, source_id)
    out, report = convert_source(
        _model(feature), gdf, read_sidecar(feature, source_id), load_doc(feature, source_id),
        s.id, s.publisher,
    )
    write_source(feature, out, report)
    mark_converted(feature, source_id, report["verdict"])
    return report


@app.post("/api/{feature}/unify")
def unify(feature: str, ids: str) -> dict[str, Any]:
    """Every converted source in one GeoParquet."""
    _sources(feature)
    try:
        return write_unified(feature, [i for i in ids.split(",") if i])
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc)) from None


class Query(BaseModel):
    sql: str | None = None  # none: the feature's first preloaded query


@app.post("/api/{feature}/query")
def query(feature: str, body: Query | None = None) -> dict[str, Any]:
    """SQL run by DuckDB over the converted files: a preloaded query, or one typed in
    the editor. DuckDB can read only under out/."""
    ss = _sources(feature)
    if feature not in QUERIES and not (body and body.sql):
        raise HTTPException(404, f"no query for {feature}")
    try:
        pubs = {s.id: s.publisher for s in ss.loadable}
        return run_query(feature, pubs, body.sql if body else None)
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get("/api/{feature}/converted/rows")
def converted_rows(
    feature: str, source: str | None = None, offset: int = 0, limit: int = 100
) -> dict[str, Any]:
    """A page of the unified layer, as it landed in the model (no geometry)."""
    _sources(feature)
    if source:
        _source(feature, source)
    try:
        return read_rows(feature, source, max(0, offset), min(max(1, limit), 500))
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc)) from None


NO_VALUE = "<null>"  # groups the rows a model field left null


@lru_cache(maxsize=16)
def _converted_geojson(feature: str, source_id: str, mtime: float) -> bytes:  # noqa: ARG001
    """One feature per value of the map field, its parts gathered (not unioned) into
    a multi-geometry. The map only colours by class, and a few dozen features draw
    in a moment where 72,000 separate ones stall the browser. The GeoParquet on disk
    keeps every feature."""
    gdf = gpd.read_parquet(out_path(feature, source_id))
    field = MAP_FIELD.get(feature)
    keys = gdf[field].astype(object).where(gdf[field].notna(), None) if field else None
    parts, owner = shapely.get_parts(gdf.geometry.values, return_index=True)
    parts = shapely.set_precision(parts, 1e-5)  # ~1 m: plenty for a screen
    by = pd.Series(keys.values[owner] if keys is not None else [None] * len(parts))
    lines = gdf.geom_type.str.contains("Line").any()
    rows = []
    for value, idx in by.groupby(by.fillna(NO_VALUE), sort=False).groups.items():
        geom = (shapely.multilinestrings if lines else shapely.multipoints)(parts[idx])
        rows.append({"v": None if value == NO_VALUE else value, "geometry": geom})
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=gdf.crs)
    return out.to_json(na="null").encode()


@app.get("/api/{feature}/converted/{source_id}/geojson")
def converted_geojson(feature: str, source_id: str) -> Response:
    """A converted source for the map: geometry, OID, and the model field it is coloured by."""
    _source(feature, source_id)
    p = out_path(feature, source_id)
    if not p.exists():
        raise HTTPException(404, f"{source_id} not converted yet")
    return Response(
        _converted_geojson(feature, source_id, p.stat().st_mtime),
        media_type="application/geo+json",
    )


def _plain(v: Any) -> Any:
    """A cell as JSON: null for any missing value, numbers as numbers, dates as text."""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if hasattr(v, "item"):  # numpy and pandas scalars
        v = v.item()
    return v if isinstance(v, (int, float, str, bool)) else str(v)


@lru_cache(maxsize=8)
def _converted_frame(feature: str, source_id: str, mtime: float) -> gpd.GeoDataFrame:  # noqa: ARG001
    """A converted source, and its geometry in metres for the click tolerance."""
    gdf = gpd.read_parquet(out_path(feature, source_id))
    return gdf.assign(_m=gdf.geometry.to_crs(3310))


@app.get("/api/{feature}/converted/at")
def converted_at(
    feature: str, lon: float, lat: float, ids: str, tolerance_m: float = 25
) -> dict[str, Any]:
    """The converted row under a click: each converted source's nearest feature, as it
    landed in the shared model, with its line so the map can outline it."""
    ss = _sources(feature)
    point = gpd.GeoSeries.from_xy([lon], [lat], crs=4326).to_crs(3310)
    hits = []
    for sid in [i for i in ids.split(",") if i]:
        p = out_path(feature, sid)
        if sid not in {s.id for s in ss.loadable} or not p.exists():
            continue
        gdf = _converted_frame(feature, sid, p.stat().st_mtime)
        idx, _ = gpd.GeoSeries(gdf["_m"], crs=3310).sindex.nearest(
            point, max_distance=tolerance_m, return_distance=True
        )
        if not len(idx[1]):
            continue
        row = gdf.iloc[int(idx[1][0])]
        hits.append({
            "id": sid,
            "publisher": ss.get(sid).publisher,
            "row": {k: _plain(v) for k, v in row.drop(["geometry", "_m"]).items()},
            "geometry": shapely.geometry.mapping(row.geometry),
        })
    return {"hits": hits}


@lru_cache(maxsize=16)
def _projected(feature: str, source_id: str, mtime: float) -> gpd.GeoSeries:  # noqa: ARG001
    # California Albers: metres, equal-area, good statewide — so a click tolerance
    # means the same distance in Sacramento and Los Angeles.
    return _frame_or_404(feature, source_id).geometry.to_crs(3310)


@app.get("/api/{feature}/compare")
def compare(
    feature: str, lon: float, lat: float, ids: str, tolerance_m: float = 25
) -> dict[str, Any]:
    """Every source's nearest feature to one point, lined up on the shared model —
    the same road, as each publisher describes it."""
    ss = _sources(feature)
    model = _model(feature)
    wanted = [i for i in ids.split(",") if i]
    sugg = _suggestions(feature, wanted)
    point = gpd.GeoSeries.from_xy([lon], [lat], crs=4326).to_crs(3310)
    out = []
    for sid in wanted:
        if sid not in sugg:
            continue
        geoms = _projected(feature, sid, raw_path(feature, sid).stat().st_mtime)
        idx, dist = geoms.sindex.nearest(point, max_distance=tolerance_m, return_distance=True)
        entry: dict[str, Any] = {"id": sid, "publisher": ss.get(sid).publisher, "hit": None}
        if len(idx[1]):
            gdf = _frame_or_404(feature, sid)
            sc = read_sidecar(feature, sid)
            hit = gdf.iloc[[int(idx[1][0])]]
            attrs = _attributes(hit, sc)
            by_name = {a["name"]: a for a in attrs}
            # Each model field shows the attribute the suggested crosswalk points at.
            lined_up = {}
            for t in model.fields:
                if not t.sourced:
                    continue
                pick = (sugg[sid].get(t.name) or {}).get("pick")
                lined_up[t.name] = by_name.get(pick["source_field"]) if pick else None
            entry["hit"] = {
                "oid": int(hit[sc["oid_field"]].iloc[0]),
                "distance_m": round(float(dist[0]), 1),
                "bbox": [round(float(v), 6) for v in hit.geometry.total_bounds],
                "model": lined_up,
                "attributes": attrs,
            }
        out.append(entry)
    return {
        "point": [lon, lat],
        "tolerance_m": tolerance_m,
        "fields": [{"name": t.name, "sourced": t.sourced} for t in model.fields],
        "sources": out,
    }


@app.post("/api/{feature}/sources/{source_id}/fetch")
async def source_fetch(feature: str, source_id: str) -> dict[str, Any]:
    """Download one source in the background. A failing layer never blocks the others."""
    s = _source(feature, source_id)
    key = (feature, s.id)
    if _jobs.get(key, {}).get("state") == "running":
        return _jobs[key]
    _jobs[key] = {"state": "running"}

    async def run() -> None:
        try:
            async with ArcGISClient(cache=ResponseCache()) as client:
                await download_source(client, feature, s)
            _jobs[key] = {"state": "done"}
            _catalog.pop(feature, None)  # coverage changes once data is local
        except Exception as exc:  # noqa: BLE001
            _jobs[key] = {"state": "error", "error": f"{type(exc).__name__}: {exc}"}

    asyncio.get_running_loop().create_task(run())
    return _jobs[key]


# --- the UI -------------------------------------------------------------------

if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
else:

    @app.get("/")
    def no_ui() -> dict[str, str]:
        return {"ui": "not built — `cd web && npm install && npm run build`", "api": "/api/docs"}
