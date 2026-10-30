"""The pagination ladder, against synthetic failures.

docs/SOURCES.md records that none of the eight locked layers reproduces the WAF
block, the offset refusal, or the duplicate-page bug, though other ArcGIS servers
do. The ladder still has to handle them, so the failures are simulated here —
this is the only place they are exercised, and the reason the code exists.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from wrangle.arcgis import ArcGISClient, ArcGISError
from wrangle.arcgis.types import LayerInfo
from wrangle.cache import Mode, ResponseCache

LAYER = "https://svc.test/arcgis/rest/services/Roads/FeatureServer/0"
QUERY = f"{LAYER}/query"


def layer_json(**over):
    base = {
        "id": 0,
        "name": "Centerlines",
        "type": "Feature Layer",
        "geometryType": "esriGeometryPolyline",
        "objectIdField": "OBJECTID",
        "maxRecordCount": 1000,
        "capabilities": "Query",
        "advancedQueryCapabilities": {"supportsPagination": True, "supportsOrderBy": True},
        "extent": {
            "xmin": 6623168.0, "ymin": 1770991.0, "xmax": 6841341.0, "ymax": 2035993.0,
            "spatialReference": {"wkid": 2226, "latestWkid": 2226},
        },
        "fields": [
            {"name": "OBJECTID", "type": "esriFieldTypeOID"},
            {"name": "FULLSTREET", "type": "esriFieldTypeString"},
            {"name": "CARTOCLASS", "type": "esriFieldTypeString"},
        ],
    }
    base.update(over)
    return base


def feats(oids):
    return {
        "features": [
            {"attributes": {"OBJECTID": o, "FULLSTREET": f"ST {o}"}, "geometry": {"paths": []}}
            for o in oids
        ]
    }


@pytest.fixture
def cache(tmp_path):
    return ResponseCache(root=tmp_path, mode=Mode.LIVE)


# --- error surfacing --------------------------------------------------------


@respx.mock
async def test_error_object_in_a_200_body_is_an_error(cache):
    respx.get(LAYER).respond(
        200, json={"error": {"code": 499, "message": "Token Required", "details": []}}
    )
    async with ArcGISClient(cache=cache) as c:
        with pytest.raises(ArcGISError) as ei:
            await c.layer_info(LAYER)
    assert ei.value.code == 499
    assert ei.value.needs_token


@respx.mock
async def test_a_403_with_a_json_body_is_still_parsed(cache):
    """Hamilton County: the directory 403s the browser, the API answers."""
    respx.get(LAYER).respond(403, json=layer_json())
    async with ArcGISClient(cache=cache) as c:
        info = await c.layer_info(LAYER)
    assert info.name == "Centerlines"


# --- identity ---------------------------------------------------------------


def test_oid_field_falls_back_to_field_type_when_mapserver_omits_it():
    """Elk Grove's MapServer publishes no objectIdField."""
    info = LayerInfo.model_validate(layer_json(objectIdField=None))
    assert info.oid_field == "OBJECTID"


def test_oid_field_finds_a_non_standard_name():
    info = LayerInfo.model_validate(
        layer_json(
            objectIdField=None,
            fields=[{"name": "FID", "type": "esriFieldTypeOID"},
                    {"name": "NAME", "type": "esriFieldTypeString"}],
        )
    )
    assert info.oid_field == "FID"


def test_crs_plausibility_catches_degrees_declared_as_state_plane():
    good = LayerInfo.model_validate(layer_json())
    assert good.extent.plausible_for_wkid()
    bad = LayerInfo.model_validate(
        layer_json(extent={"xmin": -121.5, "ymin": 38.4, "xmax": -121.3, "ymax": 38.7,
                           "spatialReference": {"wkid": 2226}})
    )
    assert not bad.extent.plausible_for_wkid()


# --- the WAF story ----------------------------------------------------------


@respx.mock
async def test_count_falls_back_when_1_equals_1_is_waf_blocked(cache):
    """A firewall that rejects any where clause containing `1=1`."""
    respx.get(LAYER).respond(200, json=layer_json())

    def handler(request):
        where = request.url.params.get("where", "")
        if "1=1" in where:
            return httpx.Response(200, json={"error": {"code": 400, "message": "Blocked"}})
        return httpx.Response(200, json={"count": 2791})

    respx.get(QUERY).mock(side_effect=handler)
    async with ArcGISClient(cache=cache) as c:
        assert await c.count(LAYER) == 2791


# --- the ratchet ------------------------------------------------------------


@respx.mock
async def test_page_size_ratchets_down_and_never_grows_back(cache):
    respx.get(LAYER).respond(200, json=layer_json())
    seen_sizes = []

    def handler(request):
        p = request.url.params
        if "returnCountOnly" in p:
            return httpx.Response(200, json={"count": 1500})
        size = int(p["resultRecordCount"])
        offset = int(p["resultOffset"])
        seen_sizes.append(size)
        if size > 250:
            return httpx.Response(200, json={"error": {"code": 500, "message": "too big"}})
        oids = range(offset + 1, min(offset + size, 1500) + 1)
        return httpx.Response(200, json=feats(oids))

    respx.get(QUERY).mock(side_effect=handler)
    async with ArcGISClient(cache=cache) as c:
        rows, verdict = await c.fetch_all(LAYER, out_fields=["FULLSTREET"])

    assert len(rows) == 1500
    assert verdict.outcome == "partial"  # it recorded the failures, honestly
    assert verdict.final_page_size == 250
    # monotonically non-increasing: 1000, 500, 250, 250, ...
    assert seen_sizes == sorted(seen_sizes, reverse=True)


# --- the duplicate story ----------------------------------------------------


@respx.mock
async def test_seen_oid_dedupe_survives_a_service_repeating_rows(cache):
    """A service can return the right NUMBER of the wrong rows."""
    respx.get(LAYER).respond(200, json=layer_json(maxRecordCount=10))

    # 30 features, pages of 10, and the second page overlaps the first by 5.
    pages = {
        0: (range(1, 11), True),
        10: (range(6, 16), True),   # 6..10 are repeats
        20: (range(16, 26), True),
        30: (range(26, 31), False),  # short page ends the scan
    }

    def handler(request):
        p = request.url.params
        if "returnCountOnly" in p:
            return httpx.Response(200, json={"count": 30})
        oids, exceeded = pages.get(int(p["resultOffset"]), ([], False))
        return httpx.Response(200, json={**feats(oids), "exceededTransferLimit": exceeded})

    respx.get(QUERY).mock(side_effect=handler)
    async with ArcGISClient(cache=cache) as c:
        rows, verdict = await c.fetch_all(LAYER, out_fields=["FULLSTREET"])

    oids = [r["attributes"]["OBJECTID"] for r in rows]
    assert len(oids) == len(set(oids)) == 30
    assert verdict.duplicates_dropped == 5
    assert verdict.outcome == "complete"


# --- the OID windowing fallback ---------------------------------------------


@respx.mock
async def test_falls_back_to_oid_windows_when_offset_paging_refuses(cache):
    respx.get(LAYER).respond(200, json=layer_json())
    used_windows = []

    def handler(request):
        p = request.url.params
        where = p.get("where", "")
        if "returnCountOnly" in p:
            return httpx.Response(200, json={"count": 2000})
        if "outStatistics" in p:
            # OIDs do NOT start at 1 — West Sacramento starts at 4,383,991.
            return httpx.Response(
                200,
                json={"features": [{"attributes": {"oid_min": 4383991, "oid_max": 4385990}}]},
            )
        if "resultOffset" in p:
            return httpx.Response(
                200, json={"error": {"code": 400, "message": "Pagination not supported"}}
            )
        used_windows.append(where)
        lo = int(where.split(">=")[1].split("AND")[0])
        hi = int(where.rsplit("<=", 1)[1])
        return httpx.Response(200, json=feats(range(max(lo, 4383991), min(hi, 4385990) + 1)))

    respx.get(QUERY).mock(side_effect=handler)
    async with ArcGISClient(cache=cache) as c:
        rows, verdict = await c.fetch_all(LAYER, out_fields=["FULLSTREET"])

    assert verdict.strategy == "oid-window"
    assert len(rows) == 2000
    assert used_windows, "should have issued OBJECTID window queries"
    assert all(o["attributes"]["OBJECTID"] >= 4383991 for o in rows)


@respx.mock
async def test_sparse_oids_still_complete(cache):
    """Yolo County: 12,775 OIDs cover 11,493 features — density 0.90."""
    respx.get(LAYER).respond(200, json=layer_json())
    present = list(range(1, 2001, 2))  # every other OID exists

    def handler(request):
        p = request.url.params
        if "returnCountOnly" in p:
            return httpx.Response(200, json={"count": len(present)})
        if "outStatistics" in p:
            return httpx.Response(
                200, json={"features": [{"attributes": {"oid_min": 1, "oid_max": 1999}}]}
            )
        if "resultOffset" in p:
            return httpx.Response(200, json={"error": {"code": 400, "message": "nope"}})
        where = p["where"]
        lo = int(where.split(">=")[1].split("AND")[0])
        hi = int(where.rsplit("<=", 1)[1])
        return httpx.Response(200, json=feats([o for o in present if lo <= o <= hi]))

    respx.get(QUERY).mock(side_effect=handler)
    async with ArcGISClient(cache=cache) as c:
        rows, verdict = await c.fetch_all(LAYER, out_fields=["FULLSTREET"])

    assert len(rows) == len(present)
    assert verdict.outcome in ("complete", "partial")


# --- verdicts ---------------------------------------------------------------


@respx.mock
async def test_a_short_fetch_is_reported_as_truncated_not_success(cache):
    """A job nobody is watching is not a job that finished."""
    respx.get(LAYER).respond(200, json=layer_json())

    def handler(request):
        p = request.url.params
        if "returnCountOnly" in p:
            return httpx.Response(200, json={"count": 5000})
        if int(p["resultOffset"]) == 0:
            return httpx.Response(200, json=feats(range(1, 1001)))
        return httpx.Response(200, json=feats([]))

    respx.get(QUERY).mock(side_effect=handler)
    async with ArcGISClient(cache=cache) as c:
        rows, verdict = await c.fetch_all(LAYER, out_fields=["FULLSTREET"])

    assert len(rows) == 1000
    assert verdict.outcome == "truncated"
    assert not verdict.ok
    assert "1000/5000" in verdict.summary()


# --- outFields subsetting ---------------------------------------------------


@respx.mock
async def test_out_fields_always_includes_the_oid_and_drops_unknown_fields(cache):
    """Ask only for what was mapped, plus the OID."""
    respx.get(LAYER).respond(200, json=layer_json())
    captured = {}

    def handler(request):
        p = request.url.params
        if "returnCountOnly" in p:
            return httpx.Response(200, json={"count": 1})
        captured["outFields"] = p["outFields"]
        return httpx.Response(200, json=feats([1]))

    respx.get(QUERY).mock(side_effect=handler)
    async with ArcGISClient(cache=cache) as c:
        await c.fetch_all(LAYER, out_fields=["FULLSTREET", "NOT_A_REAL_FIELD"])

    sent = captured["outFields"].split(",")
    assert "OBJECTID" in sent
    assert "FULLSTREET" in sent
    assert "NOT_A_REAL_FIELD" not in sent, "never forward a field the layer does not have"


# --- domain decoding --------------------------------------------------------


def test_coded_domain_decode_misses_a_value_absent_from_the_domain():
    """Yolo County's OneWay domain declares B/FT/TF; the data contains a 2."""
    info = LayerInfo.model_validate(
        layer_json(
            fields=[
                {"name": "OBJECTID", "type": "esriFieldTypeOID"},
                {
                    "name": "OneWay",
                    "type": "esriFieldTypeString",
                    "domain": {
                        "type": "codedValue",
                        "name": "Centerline One Way",
                        "codedValues": [
                            {"code": "B", "name": "B"},
                            {"code": "FT", "name": "FT"},
                            {"code": "TF", "name": "TF"},
                        ],
                    },
                },
            ]
        )
    )
    dom = info.domains["OneWay"]
    assert dom.decode("FT") == "FT"
    assert dom.decode("2") is None, "tier 1 must report a miss, not invent a decode"


def test_domain_decode_tolerates_int_vs_str_codes():
    info = LayerInfo.model_validate(
        layer_json(
            fields=[
                {"name": "OBJECTID", "type": "esriFieldTypeOID"},
                {
                    "name": "F_System",
                    "type": "esriFieldTypeSmallInteger",
                    "domain": {
                        "type": "codedValue",
                        "name": "fc",
                        "codedValues": [{"code": 1, "name": "Interstate"}],
                    },
                },
            ]
        )
    )
    assert info.domains["F_System"].decode("1") == "Interstate"
    assert info.domains["F_System"].decode(1) == "Interstate"
