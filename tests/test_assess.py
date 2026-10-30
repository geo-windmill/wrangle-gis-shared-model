"""Layer assessment: the Catalog card, and the two things it must not get wrong —
throughput measured from disk, and population measured with the wrong null test."""

from __future__ import annotations

import httpx
import pytest
import respx

from wrangle.arcgis import ArcGISClient, assess_layer, profile_fields
from wrangle.cache import Mode, ResponseCache

from .test_pagination import LAYER, QUERY, feats, layer_json


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path


def _handler(count=1000, features=200, fields=None):
    def handler(request):
        p = request.url.params
        if "returnCountOnly" in p:
            return httpx.Response(200, json={"count": count})
        if "returnDistinctValues" in p:
            field = p["outFields"]
            vals = (fields or {}).get(field, ["A", "B"])
            return httpx.Response(
                200, json={"features": [{"attributes": {field: v}} for v in vals]}
            )
        return httpx.Response(200, json=feats(range(1, features + 1)))

    return handler


# --- throughput must not be measured from cache -----------------------------


@respx.mock
async def test_throughput_is_recorded_live_and_reused_in_replay(cache_dir):
    """A replayed page measures disk speed. A Catalog card that promises a
    1-second load for 72,559 features is a lie told on stage."""
    respx.get(LAYER).respond(200, json=layer_json())
    respx.get(QUERY).mock(side_effect=_handler(count=72559))

    live = ResponseCache(root=cache_dir, mode=Mode.LIVE)
    async with ArcGISClient(cache=live) as c:
        first = await assess_layer(c, LAYER, source_id="R1")
    assert first.measured_features_per_s is not None
    recorded = live.get_metric(LAYER, "features_per_s")
    assert recorded == first.measured_features_per_s

    replay = ResponseCache(root=cache_dir, mode=Mode.REPLAY)
    async with ArcGISClient(cache=replay) as c:
        second = await assess_layer(c, LAYER, source_id="R1")
    assert second.measured_features_per_s == recorded
    assert second.estimated_load_s == first.estimated_load_s


@respx.mock
async def test_replay_without_a_recorded_rate_says_so_instead_of_guessing(cache_dir):
    respx.get(LAYER).respond(200, json=layer_json())
    respx.get(QUERY).mock(side_effect=_handler(count=500))

    # Record bodies but strip the metrics file, as a hand-assembled cache might.
    live = ResponseCache(root=cache_dir, mode=Mode.LIVE)
    async with ArcGISClient(cache=live) as c:
        await assess_layer(c, LAYER)
    (cache_dir / "metrics.json").unlink()

    replay = ResponseCache(root=cache_dir, mode=Mode.REPLAY)
    async with ArcGISClient(cache=replay) as c:
        a = await assess_layer(c, LAYER)
    assert a.measured_features_per_s is None
    assert a.estimated_load_s is None
    assert any("no recorded throughput" in n for n in a.notes)


# --- the card itself --------------------------------------------------------


@respx.mock
async def test_card_notes_a_missing_object_id_field(cache_dir):
    """Elk Grove's MapServer publishes no objectIdField."""
    respx.get(LAYER).respond(200, json=layer_json(objectIdField=None))
    respx.get(QUERY).mock(side_effect=_handler())
    async with ArcGISClient(cache=ResponseCache(root=cache_dir, mode=Mode.LIVE)) as c:
        a = await assess_layer(c, LAYER, source_id="R4")
    assert a.oid_field == "OBJECTID"
    assert any("no objectIdField" in n for n in a.notes)


@respx.mock
async def test_card_notes_a_crs_that_disagrees_with_its_coordinates(cache_dir):
    respx.get(LAYER).respond(
        200,
        json=layer_json(
            extent={"xmin": -121.5, "ymin": 38.4, "xmax": -121.3, "ymax": 38.7,
                    "spatialReference": {"wkid": 2226}}
        ),
    )
    respx.get(QUERY).mock(side_effect=_handler())
    async with ArcGISClient(cache=ResponseCache(root=cache_dir, mode=Mode.LIVE)) as c:
        a = await assess_layer(c, LAYER)
    assert not a.crs_plausible
    assert any("does not match coordinate magnitudes" in n for n in a.notes)


@respx.mock
async def test_a_token_required_layer_is_a_greyed_card_not_a_crash(cache_dir):
    """Carmichael Water District is publicly listed and answers 499."""
    respx.get(LAYER).respond(200, json={"error": {"code": 499, "message": "Token Required"}})
    async with ArcGISClient(cache=ResponseCache(root=cache_dir, mode=Mode.LIVE)) as c:
        a = await assess_layer(c, LAYER, source_id="H4", publisher="Carmichael Water District")
    assert not a.ok
    assert a.reason == "token_required"
    assert "UNAVAILABLE" in a.card_line()


@respx.mock
async def test_a_layer_with_only_system_fields_is_rejected(cache_dir):
    respx.get(LAYER).respond(
        200,
        json=layer_json(
            fields=[
                {"name": "OBJECTID", "type": "esriFieldTypeOID"},
                {"name": "GlobalID", "type": "esriFieldTypeGlobalID"},
                {"name": "Shape__Length", "type": "esriFieldTypeDouble"},
            ]
        ),
    )
    respx.get(QUERY).mock(side_effect=_handler())
    async with ArcGISClient(cache=ResponseCache(root=cache_dir, mode=Mode.LIVE)) as c:
        a = await assess_layer(c, LAYER)
    assert not a.ok
    assert a.reason == "no_mappable_attributes"


# --- population, and the space-as-null trap ---------------------------------


@respx.mock
async def test_population_rate_counts_a_space_as_absent(cache_dir):
    """El Dorado County has zero SQL nulls; `IS NULL` reports 0 for a 4% field."""
    respx.get(LAYER).respond(
        200,
        json=layer_json(
            fields=[
                {"name": "OBJECTID", "type": "esriFieldTypeOID"},
                {"name": "Operationa", "type": "esriFieldTypeString"},
            ]
        ),
    )
    seen_clauses = []

    def handler(request):
        p = request.url.params
        if "returnCountOnly" in p:
            where = p["where"]
            seen_clauses.append(where)
            if "IS NOT NULL" in where:
                return httpx.Response(200, json={"count": 588})
            return httpx.Response(200, json={"count": 13950})
        if "returnDistinctValues" in p:
            return httpx.Response(
                200,
                json={
                    "features": [
                        {"attributes": {"Operationa": v}} for v in (" ", "OPERATIONAL")
                    ]
                },
            )
        return httpx.Response(200, json=feats([1]))

    respx.get(QUERY).mock(side_effect=handler)
    async with ArcGISClient(cache=ResponseCache(root=cache_dir, mode=Mode.LIVE)) as c:
        profiles = await profile_fields(c, LAYER, sentinels=("", " "))

    prof = next(p for p in profiles if p.name == "Operationa")
    assert prof.populated == 588
    assert prof.total == 13950
    assert prof.is_effectively_empty
    assert round(prof.population_rate, 3) == 0.042
    # the guard that makes it work
    clause = next(c for c in seen_clauses if "IS NOT NULL" in c)
    assert "Operationa <> ' '" in clause
    assert "Operationa <> ''" in clause
    # the sentinel itself must never be offered as a sample value
    assert " " not in prof.sample_values


@respx.mock
async def test_sentinel_clauses_are_not_applied_to_numeric_fields(cache_dir):
    """`SPEED <> ''` is a type error on a numeric column."""
    respx.get(LAYER).respond(
        200,
        json=layer_json(
            fields=[
                {"name": "OBJECTID", "type": "esriFieldTypeOID"},
                {"name": "SPEED", "type": "esriFieldTypeSmallInteger"},
            ]
        ),
    )
    seen = []

    def handler(request):
        p = request.url.params
        if "returnCountOnly" in p:
            seen.append(p["where"])
            return httpx.Response(200, json={"count": 100})
        if "returnDistinctValues" in p:
            return httpx.Response(200, json={"features": [{"attributes": {"SPEED": 35}}]})
        return httpx.Response(200, json=feats([1]))

    respx.get(QUERY).mock(side_effect=handler)
    async with ArcGISClient(cache=ResponseCache(root=cache_dir, mode=Mode.LIVE)) as c:
        await profile_fields(c, LAYER, sentinels=("", " "))

    clause = next(s for s in seen if "IS NOT NULL" in s)
    assert "<>" not in clause, f"string sentinel leaked onto a numeric field: {clause}"


@respx.mock
async def test_profile_carries_coded_domains_through_to_the_matcher(cache_dir):
    """The matcher gets names, types, domains AND sample values. That is the point."""
    respx.get(LAYER).respond(
        200,
        json=layer_json(
            fields=[
                {"name": "OBJECTID", "type": "esriFieldTypeOID"},
                {
                    "name": "YCFC",
                    "type": "esriFieldTypeString",
                    "domain": {
                        "type": "codedValue",
                        "name": "YCFC",
                        "codedValues": [
                            {"code": "02", "name": "02 - Other Principal Arterial Rural"},
                            {"code": "11", "name": "11 - Principal Arterial Interstate Urban"},
                        ],
                    },
                },
            ]
        ),
    )
    respx.get(QUERY).mock(side_effect=_handler(count=11493, fields={"YCFC": ["02", "11"]}))
    async with ArcGISClient(cache=ResponseCache(root=cache_dir, mode=Mode.LIVE)) as c:
        profiles = await profile_fields(c, LAYER)

    prof = next(p for p in profiles if p.name == "YCFC")
    assert prof.coded_values["02"] == "02 - Other Principal Arterial Rural"
    assert prof.sample_values == ["02", "11"]
