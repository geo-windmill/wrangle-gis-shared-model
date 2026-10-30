"""Esri JSON -> GeoParquet conversion, and the registry pieces the UI relies on."""

import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, Point

from wrangle.arcgis import LayerInfo
from wrangle.convert import out_path
from wrangle.download import esri_to_shapely, features_to_frame, raw_path
from wrangle.registry import load_sources


def test_point():
    assert esri_to_shapely({"x": -121.5, "y": 38.5}) == Point(-121.5, 38.5)


def test_point_with_null_coordinates_is_no_geometry():
    assert esri_to_shapely({"x": None, "y": None}) is None


def test_single_path_is_a_linestring_and_z_is_dropped():
    g = esri_to_shapely({"paths": [[[0, 0, 5], [1, 1, 6]]]})
    assert isinstance(g, LineString)
    assert not g.has_z


def test_many_paths_is_a_multilinestring():
    g = esri_to_shapely({"paths": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]})
    assert isinstance(g, MultiLineString)


def test_degenerate_path_is_skipped():
    assert esri_to_shapely({"paths": [[[0, 0]]]}) is None


def test_islands_are_shells_not_holes():
    # Esri: clockwise = shell. Two clockwise squares are two islands, not a hole.
    cw_a = [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]
    cw_b = [[5, 5], [5, 6], [6, 6], [6, 5], [5, 5]]
    g = esri_to_shapely({"rings": [cw_a, cw_b]})
    assert g.geom_type == "MultiPolygon"
    assert g.area == 2


def test_counter_clockwise_ring_is_a_hole():
    shell = [[0, 0], [0, 4], [4, 4], [4, 0], [0, 0]]
    hole = [[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]]
    g = esri_to_shapely({"rings": [shell, hole]})
    assert g.geom_type == "Polygon"
    assert g.area == 15


def test_missing_geometry():
    assert esri_to_shapely(None) is None


def test_unknown_geometry_raises():
    with pytest.raises(ValueError):
        esri_to_shapely({"curvePaths": []})


def test_frame_keeps_every_field_and_decodes_esri_dates():
    info = LayerInfo.model_validate(
        {
            "geometryType": "esriGeometryPoint",
            "fields": [
                {"name": "OBJECTID", "type": "esriFieldTypeOID"},
                {"name": "Operationa", "type": "esriFieldTypeString"},
                {"name": "Last_Updat", "type": "esriFieldTypeDate"},
            ],
        }
    )
    feats = [
        {"attributes": {"OBJECTID": 1, "Operationa": " ", "Last_Updat": 1709251200000},
         "geometry": {"x": -120.8, "y": 38.7}},
    ]
    gdf = features_to_frame(feats, info)
    # The single-space sentinel survives untouched: the raw layer is the publisher's.
    assert gdf.loc[0, "Operationa"] == " "
    assert gdf.loc[0, "Last_Updat"] == pd.Timestamp("2024-03-01", tz="UTC")
    assert gdf.crs.to_epsg() == 4326


def test_catalog_shows_one_greyed_card_per_feature():
    roads = load_sources("roads")
    assert [s.id for s in roads.cards if not s.loadable] == ["R7"]
    hydrants = load_sources("hydrants")
    assert [s.id for s in hydrants.cards if not s.loadable] == ["H5"]
    # Hidden rejections stay in the record.
    assert roads.get("R6").status == "rejected"


def test_every_feature_has_an_area_of_interest():
    assert "067" in load_sources("roads").boundary.where   # Sacramento
    assert "113" in load_sources("roads").boundary.where   # Yolo
    assert load_sources("hydrants").boundary.name == "California"


@pytest.mark.parametrize("sid", ["../wrangle", r"..\x", "R1/../../x", "C:/x", ""])
def test_an_id_from_a_request_never_becomes_a_path(sid):
    with pytest.raises(ValueError):
        raw_path("roads", sid)
    with pytest.raises(ValueError):
        out_path("roads", sid)
    assert raw_path("roads", "R1").name == "R1.parquet"
