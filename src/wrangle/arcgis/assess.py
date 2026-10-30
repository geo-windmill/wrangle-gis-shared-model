"""Layer assessment — what becomes a source card on the loading dock.

"What you can learn from one HTTP request." The cheap assessment
is metadata only. `profile_fields` is the extra spend that makes the field matcher
work, because names and types alone are not enough (Yolo County's RoadClass).
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from .client import ArcGISClient, ArcGISError
from .types import DEFAULT_NULL_SENTINELS, FieldProfile, LayerInfo


class Assessment(BaseModel):
    """One source card."""

    source_id: str = ""
    publisher: str = ""
    url: str
    ok: bool = True
    reason: str | None = None
    reason_detail: str | None = None

    name: str | None = None
    geometry: str | None = None
    wkid: int | None = None
    crs_plausible: bool = True
    count: int | None = None
    field_count: int = 0
    domain_count: int = 0
    oid_field: str | None = None
    max_record_count: int | None = None
    supports_pagination: bool = False
    supports_order_by: bool = False
    last_edit: str | None = None
    bbox: tuple[float, float, float, float] | None = None

    metadata_ms: int = 0
    count_ms: int = 0
    measured_features_per_s: float | None = None

    fields: list[dict[str, Any]] = Field(default_factory=list)
    profiles: list[FieldProfile] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def estimated_load_s(self) -> float | None:
        if not self.count or not self.measured_features_per_s:
            return None
        return round(self.count / self.measured_features_per_s, 1)

    def card_line(self) -> str:
        if not self.ok:
            return f"{self.source_id or '—'} {self.publisher}: UNAVAILABLE ({self.reason})"
        est = f"~{self.estimated_load_s}s" if self.estimated_load_s else "?"
        return (
            f"{self.source_id or '—'} {self.publisher}: {self.count:,} {self.geometry}, "
            f"{self.field_count} fields, {self.domain_count} domains, "
            f"EPSG:{self.wkid}, edited {self.last_edit or '?'}, load {est}"
        )


async def assess_layer(
    client: ArcGISClient,
    url: str,
    *,
    source_id: str = "",
    publisher: str = "",
    where: str = "1=1",
    measure_throughput: bool = True,
) -> Assessment:
    """The one-fetch assessment, plus a count and an optional timing probe."""
    t0 = time.perf_counter()
    try:
        info: LayerInfo = await client.layer_info(url)
    except ArcGISError as exc:
        return Assessment(
            source_id=source_id,
            publisher=publisher,
            url=url,
            ok=False,
            reason="token_required" if exc.needs_token else "metadata_failed",
            reason_detail=str(exc),
        )
    meta_ms = int((time.perf_counter() - t0) * 1000)

    a = Assessment(
        source_id=source_id,
        publisher=publisher,
        url=url,
        name=info.name,
        geometry=info.geometry,
        wkid=info.wkid,
        crs_plausible=info.extent.plausible_for_wkid() if info.extent else True,
        field_count=len(info.data_fields),
        domain_count=len(info.domains),
        oid_field=info.oid_field,
        max_record_count=info.max_record_count,
        supports_pagination=info.supports_pagination,
        supports_order_by=info.supports_order_by,
        last_edit=info.last_edit.isoformat() if info.last_edit else None,
        bbox=info.extent.bbox if info.extent else None,
        metadata_ms=meta_ms,
        fields=[
            {
                "name": f.name,
                "type": f.short_type,
                "alias": f.alias,
                "coded_values": len(f.coded_values),
            }
            for f in info.data_fields
        ],
    )

    if not info.object_id_field:
        a.notes.append(
            f"layer JSON declares no objectIdField; using {info.oid_field!r} by field type"
        )
    if not a.crs_plausible:
        a.notes.append(
            f"declared EPSG:{a.wkid} does not match coordinate magnitudes in the extent"
        )
    if not info.supports_pagination:
        a.notes.append("no supportsPagination — will need OBJECTID windowing")

    t1 = time.perf_counter()
    try:
        a.count = await client.count(url, where)
    except ArcGISError as exc:
        a.ok = False
        a.reason = "count_failed"
        a.reason_detail = str(exc)
        return a
    a.count_ms = int((time.perf_counter() - t1) * 1000)

    if measure_throughput and a.count:
        a.measured_features_per_s = await _throughput(client, url, info, where, a)

    if a.field_count == 0:
        a.ok = False
        a.reason = "no_mappable_attributes"
        a.reason_detail = "every field is a system field"

    return a


async def _throughput(
    client: ArcGISClient,
    url: str,
    info: LayerInfo,
    where: str,
    a: Assessment,
) -> float | None:
    """Features per second from the publisher's server, not from our disk.

    A cached probe measures disk speed. Timing a replayed page would put a
    1-second load estimate for 72,559 features on the source card, which is a
    lie told on stage — so the rate is measured once, recorded, and read back.
    """
    recorded = client.cache.get_metric(url, "features_per_s")
    probe = min(info.page_size, 1000)
    params = {
        "where": where,
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": 4326,
        "resultOffset": 0,
        "resultRecordCount": probe,
    }

    before = client.cache.stats.hits
    t0 = time.perf_counter()
    try:
        payload = await client.get_json(f"{url}/query", params)
    except ArcGISError as exc:
        a.notes.append(f"throughput probe failed: {exc}")
        return recorded
    secs = time.perf_counter() - t0
    got = len(payload.get("features") or [])

    if client.cache.stats.hits > before:
        # Served from disk. Trust the recorded rate.
        if recorded is None:
            a.notes.append(
                "no recorded throughput for this layer; load estimate unavailable "
                "until `wrangle cache warm` runs on the network"
            )
        return recorded

    if got and secs > 0:
        rate = round(got / secs, 1)
        client.cache.record_metric(url, "features_per_s", rate)
        return rate
    return recorded


async def profile_fields(
    client: ArcGISClient,
    url: str,
    *,
    info: LayerInfo | None = None,
    where: str = "1=1",
    total: int | None = None,
    sentinels: tuple[str, ...] = DEFAULT_NULL_SENTINELS,
    sample_limit: int = 30,
    only: list[str] | None = None,
) -> list[FieldProfile]:
    """Distinct values, coded domains and population rate per field.

    This is the extra input a name-only matcher never sees, and it is the reason
    the demo catches Yolo County's 1%-populated RoadClass and El Dorado County's
    space-as-null.
    """
    info = info or await client.layer_info(url)
    total = total if total is not None else await client.count(url, where)
    wanted = [f for f in info.data_fields if not only or f.name in only]

    out: list[FieldProfile] = []
    for f in wanted:
        prof = FieldProfile(
            name=f.name,
            type=f.short_type,
            alias=f.alias,
            coded_values={str(k): v for k, v in f.coded_values.items()},
            total=total,
        )
        try:
            vals = await client.distinct_values(url, f.name, where=where, limit=sample_limit)
            prof.sample_values = [v for v in vals if v is not None and str(v).strip()]
            prof.distinct_count = len(vals)
        except ArcGISError:
            pass
        try:
            prof.populated = await client.populated_count(
                url, f, where=where, sentinels=sentinels
            )
        except ArcGISError:
            pass
        out.append(prof)
    return out
