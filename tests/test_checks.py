"""Loading-dock quality checks on small hand-built layers."""

import geopandas as gpd
from shapely.geometry import Point, box

from wrangle.checks import run_checks

AREA = box(0, 0, 10, 10)


def _layer(rows, fields, outcome="complete"):
    gdf = gpd.GeoDataFrame(
        [r[0] for r in rows], geometry=[r[1] for r in rows], crs="EPSG:4326"
    )
    sidecar = {
        "oid_field": "OBJECTID",
        "verdict": {"outcome": outcome, "fetched": len(rows), "expected": 100},
        "fields": [{"name": "OBJECTID", "domain": None}, *fields],
    }
    return gdf, sidecar


def test_single_space_counts_as_blank():
    rows = [({"OBJECTID": i, "Operationa": " "}, Point(1, 1)) for i in range(30)]
    gdf, sc = _layer(rows, [{"name": "Operationa", "domain": None}])
    out = run_checks(gdf, sc, AREA)
    assert out["empty_fields"] == ["Operationa"]


def test_integer_codes_with_nulls_still_match_their_domain():
    # An int column with a null reads back as float: 1 -> 1.0. That is not off-domain.
    rows = [({"OBJECTID": 1, "OneWay": 1}, Point(1, 1)),
            ({"OBJECTID": 2, "OneWay": None}, Point(1, 1)),
            ({"OBJECTID": 3, "OneWay": 2}, Point(1, 1))]
    gdf, sc = _layer(rows, [{"name": "OneWay", "domain": {"0": "B", "1": "FT"}}])
    out = run_checks(gdf, sc, AREA)
    assert out["off_domain"] == [{"field": "OneWay", "rows": 1, "examples": ["2"]}]


def test_a_code_in_another_case_is_a_note_not_a_warning():
    rows = [({"OBJECTID": i, "STREETTYPE": v}, Point(1, 1))
            for i, v in enumerate(["ST", "ST", "Ave", "ALLE"])]
    gdf, sc = _layer(rows, [{"name": "STREETTYPE", "domain": {"St": "St", "Ave": "Ave"}}])
    out = run_checks(gdf, sc, AREA)
    assert out["off_domain"] == [{"field": "STREETTYPE", "rows": 1, "examples": ["ALLE"]}]
    assert out["case_only"] == [{"field": "STREETTYPE", "rows": 2, "example": "ST", "as": "St"}]
    note = next(w for w in out["warnings"] if "letter case" in w["text"])
    assert note["level"] == "info" and "(ST for St)" in note["text"]


def test_features_outside_the_area_are_reported():
    rows = [({"OBJECTID": 1}, Point(1, 1)), ({"OBJECTID": 2}, Point(50, 50))]
    gdf, sc = _layer(rows, [])
    out = run_checks(gdf, sc, AREA)
    assert out["inside_share"] == 0.5
    assert any("outside the area" in w["text"] for w in out["warnings"])


def test_a_truncated_download_is_critical():
    rows = [({"OBJECTID": 1}, Point(1, 1))]
    gdf, sc = _layer(rows, [], outcome="truncated")
    out = run_checks(gdf, sc, AREA)
    assert out["warnings"][0]["level"] == "critical"
