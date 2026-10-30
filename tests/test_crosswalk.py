"""The suggested crosswalk: generic cues only, and honest about what it skips."""

import geopandas as gpd
from shapely.geometry import Point

from wrangle.crosswalk import name_score, suggest, tokens
from wrangle.models import load_model


def test_tokens_split_camel_case_and_underscores():
    assert tokens("Speed_Limit") == ["speed", "limit"]
    assert tokens("RoadClass") == ["road", "class"]
    assert tokens("FULLSTREET") == ["fullstreet"]


def test_whole_word_beats_a_hint_inside_a_word():
    whole, _ = name_score(["class"], ["class"])
    inside, _ = name_score(["cartoclass"], ["class"])
    assert whole > inside


def _layer(columns, types):
    n = 40
    gdf = gpd.GeoDataFrame(columns, geometry=[Point(0, 0)] * n, crs="EPSG:4326")
    sidecar = {
        "oid_field": "OBJECTID",
        "fields": [{"name": k, "type": t, "alias": None} for k, t in types.items()],
    }
    return gdf, sidecar


def test_a_perfect_name_that_is_nearly_empty_is_skipped_not_suggested():
    n = 40
    gdf, sc = _layer(
        {
            "OBJECTID": range(n),
            # the better name, 2.5% populated
            "Functional_Class": ["Local"] + [None] * (n - 1),
            "CATEGORY": ["Local", "Arterial"] * (n // 2),
        },
        {"OBJECTID": "OID", "Functional_Class": "String", "CATEGORY": "String"},
    )
    cell = suggest(load_model("roads"), gdf, sc)["road_class"]
    assert cell["pick"]["source_field"] == "CATEGORY"
    assert cell["skipped"][0]["source_field"] == "Functional_Class"


def test_a_name_field_never_draws_from_numbers():
    n = 40
    gdf, sc = _layer(
        {
            "OBJECTID": range(n),
            "Street_Length": [str(100.5 + i) for i in range(n)],
            "STREET": [f"ROAD {i}" for i in range(n)],
        },
        {"OBJECTID": "OID", "Street_Length": "String", "STREET": "String"},
    )
    assert suggest(load_model("roads"), gdf, sc)["name"]["pick"]["source_field"] == "STREET"


def test_unsourced_model_fields_get_no_suggestion():
    gdf, sc = _layer(
        {"OBJECTID": range(40), "LANES": [2] * 40}, {"OBJECTID": "OID", "LANES": "Integer"}
    )
    assert "lanes" not in suggest(load_model("roads"), gdf, sc)
