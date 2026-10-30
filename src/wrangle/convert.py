"""Convert: each source into the shared model, then one unified layer.

Applies the reviewed configuration — each source's record in wrangle.db — to the
publisher's data exactly as `wrangle fetch` stored it:

  fields:  which source field feeds each model field (null: none does)
  modes:   how each field's values land: as published, cleaned, or standardized
  values:  raw value -> model value, for standardized fields
  rules:   how to read a date field

Nothing lands silently. Every model field reports how many rows it filled and,
for each row it left null, why: blank at the source, no field fits, nobody
publishes it, a value nobody has decided yet, a date that could not be read or is
a placeholder. A source with undecided values still converts — its verdict says
"gaps", and the undecided values are named — because a demo you cannot run is
worse than one that tells you what is missing.

Output, per feature, under out/ (never committed: it is agency data):

  out/roads/R1.parquet        one GeoParquet per source, in the shared model
  out/roads/R1.json           what happened to every field
  out/roads/roads.parquet     every converted source, one layer
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pyarrow as pa

from .checks import as_code, is_blank
from .cleaning import clean_values, published
from .download import safe_name
from .mappings import mode_of
from .models import TargetField, TargetModel
from .values import EARLIEST, cell_kind, parse_date

OUT_DIR = Path(os.environ.get("WRANGLE_OUT_DIR") or Path(__file__).resolve().parents[2] / "out")

# Why a row is null. The order is the order a report lists them in.
REASONS = {
    "blank": "blank at the source",
    "no_field": "no field fits",
    "unpublished": "nobody publishes it",
    "unmatched": "field not matched yet",
    "decided_null": "decided: means nothing in the model",
    "undecided": "value not decided yet",
    "no_rule": "no date format chosen yet",
    "unreadable": "could not be read",
    "placeholder": "placeholder date",
    "placeholder_text": "placeholder (N/A, NULL, -)",
    "out_of_range": "outside the model's range",
    "refused": "refused by the model",
}
# Reasons that mean the review is not finished. Any of them makes the verdict "gaps".
OPEN = {"unmatched", "undecided", "no_rule"}


def out_path(feature: str, source_id: str) -> Path:
    safe_name(feature, source_id)
    return OUT_DIR / feature / f"{source_id}.parquet"


def report_path(feature: str, source_id: str) -> Path:
    safe_name(feature, source_id)
    return OUT_DIR / feature / f"{source_id}.json"


def unified_path(feature: str) -> Path:
    return OUT_DIR / feature / f"{feature}.parquet"


def read_report(feature: str, source_id: str) -> dict[str, Any] | None:
    p = report_path(feature, source_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# --- one field -------------------------------------------------------------------------


def convert_field(
    t: TargetField,
    gdf: gpd.GeoDataFrame,
    doc: dict[str, Any],
    domain: dict[str, str] | None = None,
) -> tuple[pd.Series, dict[str, Any]]:
    """One model field's column, and the account of every row it left null."""
    n = len(gdf)
    nulls: Counter[str] = Counter()
    undecided: Counter[str] = Counter()
    out = pd.Series([None] * n, index=gdf.index, dtype=object)
    field = doc["fields"].get(t.name)
    report: dict[str, Any] = {"field": t.name, "source_field": field, "how": None}

    if not t.sourced:
        nulls["unpublished"] = n
        report["how"] = "unpublished"
    elif t.name not in doc["fields"]:
        nulls["unmatched"] = n
        report["how"] = "unmatched"
    elif field is None:
        nulls["no_field"] = n
        report["how"] = "none"
    else:
        src = gdf[field]
        mode = mode_of(doc, t.name) if t.type != "date" else None
        report["mode"] = mode
        if mode in ("published", "cleaned"):
            out, nulls, steps = _as_published_or_cleaned(t, src, mode, domain)
            report["how"] = mode
            if steps:
                report["cleaned"] = steps
            if t.type == "enum":
                # Not standardized: the publisher's own words land as text, beside the
                # model's values from sources that were. Nothing to cast them onto.
                report["filled"] = int(out.notna().sum())
                report["nulls"] = {k: nulls[k] for k in REASONS if nulls.get(k)}
                report["undecided"] = []
                return _typed(t, out), report
        else:
            blank = is_blank(src)
            nulls["blank"] = int(blank.sum())
            codes = src[~blank].map(as_code)
            kind = cell_kind(t, int(codes.nunique()))
            report["how"] = kind
            if kind == "table":
                table = doc["values"].get(t.name, {})
                known = codes.isin(table.keys())
                for raw, rows in codes[~known].value_counts().items():
                    undecided[str(raw)] = int(rows)
                nulls["undecided"] = int((~known).sum())
                mapped = codes[known].map(lambda c: table[c])
                nulls["decided_null"] = int(mapped.isna().sum())
                out.loc[mapped.index] = mapped
            elif kind == "date":
                rule = doc["rules"].get(t.name)
                report["rule"] = rule
                if rule is None:
                    nulls["no_rule"] = len(codes)
                else:
                    read = {c: parse_date(c, rule) for c in codes.unique()}
                    dates = codes.map(read)
                    bad = dates.isna()
                    today = dt.date.today()
                    fake = ~bad & dates.map(lambda d: d is not None and not EARLIEST <= d <= today)
                    nulls["unreadable"] = int(bad.sum())
                    nulls["placeholder"] = int(fake.sum())
                    good = dates[~bad & ~fake]
                    out.loc[good.index] = good
            else:  # text, or a number field with too many values for a table
                vals = src[~blank].astype(str).str.strip()
                out.loc[vals.index] = vals if t.type == "string" else vals.map(_number)

    # Last gate, whatever the mode: the strict caster. A value it refuses is reported,
    # never kept.
    filled = out.dropna()
    cast = {}
    for v in filled.unique():
        try:
            cast[v] = t.coerce(v)
        except (ValueError, TypeError):
            cast[v] = None
    refused = filled.map(lambda v: cast[v] is None)
    nulls["refused"] = int(refused.sum())
    out.loc[filled.index] = filled.map(cast)

    report["filled"] = int(out.notna().sum())
    report["nulls"] = {k: nulls[k] for k in REASONS if nulls.get(k)}
    report["undecided"] = [{"raw": r, "rows": c} for r, c in undecided.most_common()]
    return _typed(t, out), report


def _as_published_or_cleaned(
    t: TargetField, src: pd.Series, mode: str, domain: dict[str, str] | None
) -> tuple[pd.Series, Counter[str], dict[str, int]]:
    """A field that is not standardized: no value to decide, so nothing is ever open.
    An enum field lands as this text; any other field still passes the strict caster
    after this (a published speed of 0 is below the model's minimum)."""
    nulls: Counter[str] = Counter()
    steps: Counter[str] = Counter()
    out = pd.Series([None] * len(src), index=src.index, dtype=object)
    blank = is_blank(src)
    nulls["blank"] = int(blank.sum())
    codes = src[~blank].map(as_code)
    counts = codes.value_counts().to_dict()
    if mode == "published":
        table = {raw: published(t, raw, domain) for raw in counts}
        for raw, n in counts.items():
            if table[raw] is None:
                nulls["unreadable"] += n
            elif isinstance(table[raw], str) and table[raw] != raw:
                steps["decode"] += n  # a code, landed as its label
    else:
        cleaned = clean_values(t, counts, domain)
        table = {raw: c["value"] for raw, c in cleaned.items()}
        why = {"blank": "blank", "placeholder": "placeholder_text",
               "unreadable": "unreadable", "out_of_range": "out_of_range"}
        for raw, c in cleaned.items():
            n = counts[raw]
            for step in c["steps"]:
                steps[step] += n
            if c["value"] is None:
                nulls[why[c["steps"][-1]]] += n
    landed = codes.map(table)
    out.loc[landed.index] = landed
    return out, nulls, dict(steps)


def _number(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def _typed(t: TargetField, s: pd.Series) -> pd.Series:
    """A column Parquet can type: nullable ints and floats, strings, dates."""
    if t.type == "integer":
        return s.astype("Int64")
    if t.type == "number":
        return s.astype("Float64")
    if t.type == "boolean":
        return s.astype("boolean")
    if t.type == "date":  # a date, not a timestamp — even when every row is null
        return s.astype(pd.ArrowDtype(pa.date32()))
    return s.astype("string")


# --- one source --------------------------------------------------------------------------


def convert_source(
    model: TargetModel,
    gdf: gpd.GeoDataFrame,
    sidecar: dict[str, Any],
    doc: dict[str, Any],
    source_id: str,
    publisher: str,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    cols: dict[str, Any] = {}
    fields = []
    domains = {f["name"]: f.get("domain") for f in sidecar.get("fields", [])}
    for t in model.fields:
        cols[t.name], rep = convert_field(t, gdf, doc, domains.get(doc["fields"].get(t.name)))
        fields.append(rep)
    cols["source_id"] = pd.Series([source_id] * len(gdf), index=gdf.index, dtype="string")
    cols["source_oid"] = gdf[sidecar["oid_field"]].astype("int64")
    out = gpd.GeoDataFrame(cols, geometry=gdf.geometry.values, crs=gdf.crs).to_crs(model.crs)

    gaps = sum(f["nulls"].get(r, 0) for f in fields for r in OPEN)
    fetched = sidecar["verdict"]["outcome"]
    verdict = "partial" if fetched != "complete" else "gaps" if gaps else "complete"
    report = {
        "source_id": source_id,
        "publisher": publisher,
        "rows": len(out),
        "fetched": sidecar["verdict"],
        "verdict": verdict,
        "open_rows": gaps,
        "no_geometry": int((out.geometry.isna() | out.geometry.is_empty).sum()),
        "fields": fields,
        "converted_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    return out, report


def write_source(feature: str, out: gpd.GeoDataFrame, report: dict[str, Any]) -> None:
    sid = report["source_id"]
    p = out_path(feature, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(p, index=False)
    report_path(feature, sid).write_text(json.dumps(report, indent=1), encoding="utf-8")


def write_unified(feature: str, source_ids: list[str]) -> dict[str, Any]:
    """Every converted source in one layer. Sources overlap where publishers do —
    Caltrans and the counties both carry a state highway; which to trust is a
    policy, and the provenance columns are what let you write one."""
    paths = [out_path(feature, s) for s in source_ids]
    parts = [gpd.read_parquet(p) for p in paths if p.exists()]
    if not parts:
        raise FileNotFoundError(f"nothing converted yet for {feature}")
    unified = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=parts[0].crs)
    unified.to_parquet(unified_path(feature), index=False)
    return {
        "path": f"out/{feature}/{unified_path(feature).name}",
        "rows": len(unified),
        "sources": unified["source_id"].value_counts().to_dict(),
    }


# --- the payoff: one query over the unified file -------------------------------------------

# The model field the unified map is coloured by.
MAP_FIELD = {"roads": "road_class", "hydrants": "hydrant_type"}

# Preloaded in the query editor, shown as written; anyone can edit them or write their
# own. `sources` is a two-column table (source_id, publisher) from the registry; a path
# under out/ is a file Convert wrote. The first query is the payoff, and the only one
# drawn as a pivot (model values down, publishers across).
QUERIES: dict[str, list[dict[str, Any]]] = {
    "roads": [
        {
            "title": "Road miles by class, by publisher",
            "pivot": True,
            "sql": """\
SELECT s.publisher,
       u.road_class,
       round(sum(ST_Length(ST_Transform(
           u.geometry, 'EPSG:4326', 'EPSG:3310',
           always_xy := true))) / 1609.344, 1) AS miles
FROM 'out/roads/roads.parquet' AS u
JOIN sources AS s USING (source_id)
GROUP BY ALL
ORDER BY s.publisher, miles DESC""",
        },
        {
            "title": "How full each field is, by publisher",
            "sql": """\
SELECT s.publisher,
       count(*) AS roads,
       round(100 * count(u.name) / count(*)) AS name_pct,
       round(100 * count(u.road_class) / count(*)) AS road_class_pct,
       round(100 * count(u.one_way) / count(*)) AS one_way_pct,
       round(100 * count(u.speed_limit) / count(*)) AS speed_limit_pct,
       round(100 * count(u.source_date) / count(*)) AS source_date_pct
FROM 'out/roads/roads.parquet' AS u
JOIN sources AS s USING (source_id)
GROUP BY ALL
ORDER BY s.publisher""",
        },
        {
            "title": "Road class as it landed, by source",
            "sql": """\
SELECT source_id,
       road_class,
       count(*) AS roads
FROM 'out/roads/roads.parquet'
GROUP BY ALL
ORDER BY source_id, roads DESC""",
        },
        {
            "title": "Speed limits, by publisher",
            "sql": """\
SELECT s.publisher,
       u.speed_limit,
       count(*) AS roads
FROM 'out/roads/roads.parquet' AS u
JOIN sources AS s USING (source_id)
GROUP BY ALL
ORDER BY s.publisher, u.speed_limit NULLS LAST""",
        },
        {
            "title": "Same road, every source: Tower Bridge",
            "sql": """\
SELECT source_id, source_oid, name,
       road_class, one_way, speed_limit
FROM 'out/roads/roads.parquet'
WHERE (source_id = 'R1' AND source_oid IN (61036, 61037))
   OR (source_id = 'R2' AND source_oid IN (6803, 7530))
ORDER BY source_id, source_oid""",
        },
        {
            "title": "Most common street names",
            "sql": """\
SELECT name,
       count(*) AS segments,
       count(DISTINCT source_id) AS publishers
FROM 'out/roads/roads.parquet'
WHERE name IS NOT NULL
GROUP BY name
ORDER BY segments DESC
LIMIT 25""",
        },
    ],
    "hydrants": [
        {
            "title": "Hydrants by type, by publisher",
            "pivot": True,
            "sql": """\
SELECT s.publisher,
       u.hydrant_type,
       count(*) AS hydrants
FROM 'out/hydrants/hydrants.parquet' AS u
JOIN sources AS s USING (source_id)
GROUP BY ALL
ORDER BY s.publisher, hydrants DESC""",
        },
        {
            "title": "How full each field is, by publisher",
            "sql": """\
SELECT s.publisher,
       count(*) AS hydrants,
       round(100 * count(u.status) / count(*)) AS status_pct,
       round(100 * count(u.hydrant_type) / count(*)) AS hydrant_type_pct,
       round(100 * count(u.size_in) / count(*)) AS size_in_pct,
       round(100 * count(u.owner) / count(*)) AS owner_pct,
       round(100 * count(u.source_date) / count(*)) AS source_date_pct
FROM 'out/hydrants/hydrants.parquet' AS u
JOIN sources AS s USING (source_id)
GROUP BY ALL
ORDER BY s.publisher""",
        },
        {
            "title": "Outlet sizes",
            "sql": """\
SELECT size_in, count(*) AS hydrants
FROM 'out/hydrants/hydrants.parquet'
GROUP BY ALL
ORDER BY size_in NULLS LAST""",
        },
    ],
}

MAX_ROWS = 1000  # an answer is for reading; past this it is cut off, and says so
TIMEOUT_S = 30


def _connect(publishers: dict[str, str]) -> Any:
    """An in-memory DuckDB that can read and write only under out/, so SQL typed in
    the editor can't reach anything else on the machine."""
    import duckdb

    con = duckdb.connect()
    # Needs the network once to install; after that it loads from the local cache.
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("CREATE TABLE sources (source_id VARCHAR, publisher VARCHAR)")
    if publishers:
        con.executemany("INSERT INTO sources VALUES (?, ?)", list(publishers.items()))
    con.execute(f"SET allowed_directories = ['{OUT_DIR.resolve().as_posix()}/']")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def _cell(v: Any) -> Any:
    """A value the browser can show: geometry as short WKT, dates as text."""
    if isinstance(v, (bytes, bytearray)):
        try:
            from shapely import wkb

            text = wkb.loads(bytes(v)).wkt
            return text if len(text) <= 80 else text[:77] + "..."
        except Exception:  # noqa: BLE001 - not a geometry: say what it is
            return f"<{len(v)} bytes>"
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def run_query(feature: str, publishers: dict[str, str], sql: str | None = None) -> dict[str, Any]:
    """Run SQL with DuckDB, straight off the GeoParquet files under out/. No SQL means
    the feature's first preloaded query. Raises ValueError with DuckDB's message."""
    import threading
    import time

    import duckdb

    if not unified_path(feature).exists():
        raise FileNotFoundError(f"no unified {feature} layer yet")
    text = sql if sql is not None else QUERIES[feature][0]["sql"]
    con = _connect(publishers)
    timer = threading.Timer(TIMEOUT_S, con.interrupt)
    t0 = time.perf_counter()
    timer.start()
    try:
        rel = con.execute(text.replace("'out/", "'" + OUT_DIR.resolve().as_posix() + "/"))
        rows = rel.fetchmany(MAX_ROWS + 1) if rel.description else []
    except duckdb.InterruptException:
        raise ValueError(f"stopped after {TIMEOUT_S} s") from None
    except duckdb.Error as exc:
        raise ValueError(str(exc)) from None
    finally:
        timer.cancel()
    return {
        "sql": text,
        "columns": [d[0] for d in rel.description or []],
        "rows": [[_cell(v) for v in r] for r in rows[:MAX_ROWS]],
        "truncated": len(rows) > MAX_ROWS,
        "ms": round((time.perf_counter() - t0) * 1000),
    }


def read_rows(feature: str, source_id: str | None, offset: int, limit: int) -> dict[str, Any]:
    """A page of the converted rows as they landed in the model: where each came from
    first, then every column but the geometry, in source order."""
    import duckdb

    path = unified_path(feature)
    if not path.exists():
        raise FileNotFoundError(f"no unified {feature} layer yet")
    con = duckdb.connect()
    src = f"read_parquet('{path.resolve().as_posix()}')"
    where, params = ("WHERE source_id = $sid", {"sid": source_id}) if source_id else ("", {})
    total = con.execute(f"SELECT count(*) FROM {src} {where}", params).fetchone()[0]
    rel = con.execute(
        "SELECT source_id, source_oid, * EXCLUDE (geometry, source_id, source_oid) "
        f"FROM {src} {where} "
        f"ORDER BY source_id, source_oid LIMIT {int(limit)} OFFSET {int(offset)}",
        params,
    )
    return {
        "columns": [d[0] for d in rel.description],
        "rows": [[_cell(v) for v in r] for r in rel.fetchall()],
        "total": total,
        "offset": offset,
    }
