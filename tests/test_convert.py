"""Convert without the network: every null has a reason, nothing lands silently."""

import datetime as dt

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from wrangle import convert
from wrangle.models import load_model

ROADS = load_model("roads")
N = 6


@pytest.fixture(autouse=True)
def _names_pass_through(monkeypatch):
    # Real name fields have thousands of values; these six would otherwise be a
    # value table, decided one by one, as in Edit values.
    monkeypatch.setattr("wrangle.values.STRING_TABLE", 2)


def _layer(x0=0.0, y0=0.0, step=1.0):
    gdf = gpd.GeoDataFrame(
        {
            "OBJECTID": range(1, N + 1),
            "FULLSTREET": ["A ST", "B ST ", " ", "C ST", "D ST", None],
            "CARTOCLASS": ["RES", "RES", "ART", "HWY", "TO", None],
            "ONEWAY": ["B", "B", "FT", "B", "TF", "B"],
            "SPEED": [25, 0, 35, 25, 25, 25],
            "AC_DATE": ["20040323", "20080231", "18991230", None, "20210917084908", "20040323"],
        },
        geometry=[LineString([(x0 + i * step, y0), (x0 + i * step, y0 + step)]) for i in range(N)],
        crs="EPSG:2226",
    )
    sidecar = {"oid_field": "OBJECTID", "verdict": {"outcome": "complete", "expected": N,
                                                     "fetched": N}}
    return gdf, sidecar


def _doc(**over):
    doc = {
        "fields": {"name": "FULLSTREET", "road_class": "CARTOCLASS", "one_way": "ONEWAY",
                   "speed_limit": "SPEED", "owner": None, "source_date": "AC_DATE"},
        "values": {
            "road_class": {"RES": "local", "ART": "principal_arterial", "TO": None},
            "one_way": {"B": "no", "FT": "forward", "TF": "reverse"},
            "speed_limit": {"25": 25, "0": None, "35": 35},
        },
        "values_decided_by": {},
        "rules": {"source_date": "yyyymmdd"},
        "modes": {"name": "cleaned"},  # a field with no mode lands as published
    }
    doc.update(over)
    return doc


def _field(report, name):
    return next(f for f in report["fields"] if f["field"] == name)


def test_every_null_has_a_reason():
    gdf, sc = _layer()
    out, rep = convert.convert_source(ROADS, gdf, sc, _doc(), "R1", "Test County")

    assert out["road_class"].tolist()[:3] == ["local", "local", "principal_arterial"]
    rc = _field(rep, "road_class")
    assert rc["nulls"] == {"blank": 1, "decided_null": 1, "undecided": 1}
    assert rc["undecided"] == [{"raw": "HWY", "rows": 1}]

    assert _field(rep, "speed_limit")["nulls"] == {"decided_null": 1}
    assert _field(rep, "owner")["nulls"] == {"no_field": N}
    assert _field(rep, "lanes")["nulls"] == {"unpublished": N}
    assert out["name"].tolist()[1] == "B ST"  # trimmed
    assert _field(rep, "name")["nulls"] == {"blank": 2}

    for f in rep["fields"]:  # the account always adds up
        assert f["filled"] + sum(f["nulls"].values()) == N, f["field"]


def test_dates_skip_impossible_days_and_placeholders():
    gdf, sc = _layer()
    out, rep = convert.convert_source(ROADS, gdf, sc, _doc(), "R1", "Test County")
    assert out["source_date"].tolist()[0] == dt.date(2004, 3, 23)
    assert out["source_date"].tolist()[4] == dt.date(2021, 9, 17)
    assert _field(rep, "source_date")["nulls"] == {"blank": 1, "unreadable": 1, "placeholder": 1}


def test_an_unfinished_review_converts_with_gaps_and_says_so():
    gdf, sc = _layer()
    doc = _doc(rules={})
    doc["fields"].pop("one_way")
    _, rep = convert.convert_source(ROADS, gdf, sc, doc, "R1", "Test County")
    assert rep["verdict"] == "gaps"
    assert _field(rep, "one_way")["nulls"] == {"unmatched": N}
    assert _field(rep, "source_date")["nulls"] == {"blank": 1, "no_rule": N - 1}
    # undecided HWY (1) + one_way unmatched (6) + no date rule (5)
    assert rep["open_rows"] == 1 + N + (N - 1)


def test_a_finished_review_is_complete():
    gdf, sc = _layer()
    doc = _doc()
    doc["values"]["road_class"]["HWY"] = "freeway_expressway"
    _, rep = convert.convert_source(ROADS, gdf, sc, doc, "R1", "Test County")
    assert rep["verdict"] == "complete" and rep["open_rows"] == 0


def test_the_strict_caster_has_the_last_word():
    gdf, sc = _layer()
    doc = _doc()
    doc["values"]["road_class"]["RES"] = "residential"  # not in the vocabulary
    out, rep = convert.convert_source(ROADS, gdf, sc, doc, "R1", "Test County")
    assert out["road_class"].isna().tolist()[:2] == [True, True]
    assert _field(rep, "road_class")["nulls"]["refused"] == 2


def test_a_published_number_still_meets_the_strict_caster():
    gdf, sc = _layer()
    doc = _doc(modes={"name": "cleaned", "speed_limit": "published"})
    out, rep = convert.convert_source(ROADS, gdf, sc, doc, "R1", "Test County")
    assert out["speed_limit"].isna().tolist()[:3] == [False, True, False]  # 0 is below min
    assert _field(rep, "speed_limit")["nulls"] == {"refused": 1}


def test_a_space_is_blank_as_published_too():
    gdf, sc = _layer()
    out, rep = convert.convert_source(ROADS, gdf, sc, _doc(modes={}), "R1", "Test County")
    assert out["name"].isna().tolist()[2] and _field(rep, "name")["nulls"] == {"blank": 2}


def test_output_is_in_the_model_with_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(convert, "OUT_DIR", tmp_path)
    gdf, sc = _layer()
    out, rep = convert.convert_source(ROADS, gdf, sc, _doc(), "R1", "Test County")
    assert list(out.columns) == [*ROADS.field_names, "source_id", "source_oid", "geometry"]
    assert out.crs.to_epsg() == 4326
    assert out["source_oid"].tolist() == list(range(1, N + 1))

    convert.write_source("roads", out, rep)
    out2, rep2 = convert.convert_source(ROADS, gdf, sc, _doc(), "R2", "Other County")
    convert.write_source("roads", out2, rep2)
    u = convert.write_unified("roads", ["R1", "R2", "R9"])  # R9 never converted: skipped
    assert u["rows"] == 2 * N and u["sources"] == {"R1": N, "R2": N}
    back = gpd.read_parquet(convert.unified_path("roads"))
    assert str(back["source_date"].dtype).startswith("date32")
    assert convert.read_report("roads", "R1")["verdict"] == rep["verdict"]


def test_the_road_miles_query_runs_over_the_unified_file(tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    try:  # the spatial extension needs the network once
        duckdb.connect().execute("INSTALL spatial; LOAD spatial;")
    except duckdb.Error:
        pytest.skip("DuckDB spatial extension not available offline")
    monkeypatch.setattr(convert, "OUT_DIR", tmp_path)
    gdf, sc = _layer(-121.5, 38.5, 0.1)  # Sacramento, in degrees
    gdf = gdf.set_crs(4326, allow_override=True)
    out, rep = convert.convert_source(ROADS, gdf, sc, _doc(), "R1", "Test County")
    convert.write_source("roads", out, rep)
    convert.write_unified("roads", ["R1"])
    q = convert.run_query("roads", {"R1": "Test County"})
    assert q["columns"] == ["publisher", "road_class", "miles"]
    local = next(r for r in q["rows"] if r[1] == "local")
    assert local[0] == "Test County" and 13 < local[2] < 14.5  # two 0.1° lines ≈ 6.9 mi each


def _unified(tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    try:  # the spatial extension needs the network once
        duckdb.connect().execute("INSTALL spatial; LOAD spatial;")
    except duckdb.Error:
        pytest.skip("DuckDB spatial extension not available offline")
    out_dir = tmp_path / "out"
    monkeypatch.setattr(convert, "OUT_DIR", out_dir)
    gdf, sc = _layer(-121.5, 38.5, 0.1)
    gdf = gdf.set_crs(4326, allow_override=True)
    out, rep = convert.convert_source(ROADS, gdf, sc, _doc(), "R1", "Test County")
    convert.write_source("roads", out, rep)
    convert.write_unified("roads", ["R1"])
    return out_dir


def test_every_preloaded_query_runs(tmp_path, monkeypatch):
    _unified(tmp_path, monkeypatch)
    for q in convert.QUERIES["roads"]:
        assert convert.run_query("roads", {"R1": "Test County"}, q["sql"])["columns"], q["title"]


def test_typed_sql_runs_and_shows_geometry_as_text(tmp_path, monkeypatch):
    _unified(tmp_path, monkeypatch)
    sql = "SELECT name, geometry FROM 'out/roads/roads.parquet' LIMIT 1"
    q = convert.run_query("roads", {}, sql)
    assert q["columns"] == ["name", "geometry"] and q["rows"][0][1].startswith("LINESTRING")


def test_typed_sql_cannot_reach_outside_out(tmp_path, monkeypatch):
    _unified(tmp_path, monkeypatch)
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    with pytest.raises(ValueError, match="Permission"):
        convert.run_query("roads", {}, f"SELECT * FROM '{secret.as_posix()}'")
    with pytest.raises(ValueError):
        convert.run_query("roads", {}, "SET enable_external_access = true")


def test_a_long_answer_is_cut_off_and_says_so(tmp_path, monkeypatch):
    _unified(tmp_path, monkeypatch)
    monkeypatch.setattr(convert, "MAX_ROWS", 3)
    q = convert.run_query("roads", {}, "SELECT * FROM range(10)")
    assert len(q["rows"]) == 3 and q["truncated"]


def test_bad_sql_says_why(tmp_path, monkeypatch):
    _unified(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="nope"):
        convert.run_query("roads", {}, "SELECT nope FROM 'out/roads/roads.parquet'")


def test_the_data_reads_a_page_at_a_time(tmp_path, monkeypatch):
    _unified(tmp_path, monkeypatch)
    page = convert.read_rows("roads", "R1", 2, 3)
    assert page["total"] == N and len(page["rows"]) == 3
    assert "geometry" not in page["columns"] and page["columns"][:2] == ["source_id", "source_oid"]
    oid = page["columns"].index("source_oid")
    assert [r[oid] for r in page["rows"]] == [3, 4, 5]
    assert convert.read_rows("roads", "R9", 0, 10)["total"] == 0


def test_a_click_finds_the_converted_row_under_it(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from wrangle import api

    monkeypatch.setattr(convert, "OUT_DIR", tmp_path / "out")
    gdf, sc = _layer(-121.5, 38.5, 0.001)  # ~100 m apart, in degrees
    gdf = gdf.set_crs(4326, allow_override=True)
    out, rep = convert.convert_source(ROADS, gdf, sc, _doc(), "R1", "Test County")
    convert.write_source("roads", out, rep)
    c = TestClient(api.app)
    near = c.get("/api/roads/converted/at?lon=-121.4990&lat=38.5005&ids=R1&tolerance_m=30").json()
    (hit,) = near["hits"]
    assert hit["row"]["source_oid"] == 2 and hit["row"]["speed_limit"] is None  # 0 mph → null
    assert hit["row"]["road_class"] == "local" and hit["geometry"]["type"] == "LineString"
    far = c.get("/api/roads/converted/at?lon=-121.40&lat=38.40&ids=R1&tolerance_m=30").json()
    assert far["hits"] == []
