"""Value modes: as published, cleaned, standardized — and what Convert does with each."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from wrangle import mappings
from wrangle.cleaning import clean_values, decode, published, tidy
from wrangle.convert import convert_field
from wrangle.models import load_model

ROADS = load_model("roads")
CLASS = ROADS.field("road_class")
SPEED = ROADS.field("speed_limit")
NAME = ROADS.field("name")


def test_tidy_trims_and_collapses():
    assert tidy("  MAIN   ST ") == "MAIN ST"


def test_codes_decode_tolerantly():
    domain = {"1": "Interstate", "14": "14 - Other Principal Arterial Urban"}
    assert decode("01", domain) == "Interstate"
    assert decode("14", domain) == "14 - Other Principal Arterial Urban"
    assert decode("99", domain) is None


def test_published_keeps_text_and_reads_numbers():
    assert published(CLASS, " A ") == " A "
    assert published(SPEED, "0") == 0  # as published: 0 stays 0
    assert published(SPEED, "fast") is None


def test_cleaning_steps():
    counts = {" Main  St": 3, "MAIN ST": 1, "N/A": 2, "-": 1, "   ": 1}
    out = clean_values(NAME, counts, None)
    assert out[" Main  St"] == {"value": "Main St", "steps": ["trim"]}
    assert out["MAIN ST"] == {"value": "Main St", "steps": ["case"]}  # the commonest spelling wins
    assert out["N/A"]["value"] is None and out["N/A"]["steps"] == ["placeholder"]
    assert out["-"]["steps"] == ["placeholder"]
    assert out["   "]["steps"] == ["trim", "blank"]


def test_cleaning_decodes_but_never_invents():
    out = clean_values(CLASS, {"14": 5, "A": 2}, {"14": "Other Principal Arterial"})
    assert out["14"] == {"value": "Other Principal Arterial", "steps": ["decode"]}
    assert out["A"] == {"value": "A", "steps": []}  # no label published: stays a code


def test_cleaning_a_number_respects_the_models_range():
    out = clean_values(SPEED, {"25": 9, "0": 3, "25.0": 1}, None)
    assert out["25"]["value"] == 25 and out["25.0"]["value"] == 25
    assert out["0"] == {"value": None, "steps": ["out_of_range"]}


def _gdf(values):
    return gpd.GeoDataFrame({"CLS": values}, geometry=[Point(0, 0)] * len(values), crs=4326)


def _list(col):
    return [None if pd.isna(v) else v for v in col]


def _doc(**sections):
    doc = {k: {} for k in mappings.SECTIONS}
    doc["fields"] = {"road_class": "CLS"}
    doc.update(sections)
    return doc


def test_a_field_with_no_mode_lands_as_published():
    gdf = _gdf(["A", "LOCAL", None])
    col, rep = convert_field(CLASS, gdf, _doc())
    assert rep["mode"] == "published" and rep["how"] == "published"
    # Not standardized, an enum field holds the publisher's text: nothing to decide, nothing open.
    assert _list(col) == ["A", "LOCAL", None]
    assert rep["nulls"] == {"blank": 1} and not rep["undecided"] and rep["filled"] == 2


def test_as_published_a_coded_value_lands_as_its_label():
    domain = {"19": "19 - Local Urban", "9": "09 - Local Rural"}
    assert published(CLASS, "19", domain) == "19 - Local Urban"
    assert published(CLASS, "09", domain) == "09 - Local Rural"  # "09" is code 9
    assert published(CLASS, "XX", domain) == "XX"  # no label published: stays as written
    assert published(SPEED, "25", {"25": "Twenty-five"}) == 25  # a number stays a number
    gdf = _gdf(["19", "19", None])
    col, _ = convert_field(CLASS, gdf, _doc(), domain)
    assert _list(col) == ["19 - Local Urban"] * 2 + [None]
    _, rep = convert_field(NAME, gdf, _doc(fields={"name": "CLS"}), domain)
    assert rep["cleaned"] == {"decode": 2}


def test_a_text_field_as_published_lands_in_its_own_column():
    doc = _doc(fields={"name": "CLS"})
    col, rep = convert_field(NAME, _gdf([" Main St", None]), doc)
    assert _list(col) == [" Main St", None]


def test_cleaned_counts_every_step():
    gdf = _gdf(["14", " n/a ", "14"])
    doc = _doc(modes={"road_class": "cleaned"})
    col, rep = convert_field(CLASS, gdf, doc, {"14": "Collector"})
    assert _list(col) == ["Collector", None, "Collector"]
    assert rep["cleaned"] == {"decode": 2, "trim": 1, "placeholder": 1}
    assert rep["nulls"] == {"placeholder_text": 1}


def test_standardized_is_the_value_by_value_walk():
    doc = _doc(values={"road_class": {"A": "minor_arterial"}})  # values decided: standardized
    col, rep = convert_field(CLASS, _gdf(["A", "LOCAL"]), doc)
    assert rep["mode"] == "standardized"
    assert _list(col) == ["minor_arterial", None]
    assert rep["undecided"] == [{"raw": "LOCAL", "rows": 1}]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(mappings, "DB_PATH", tmp_path / "wrangle.db")


def test_modes_are_recorded_and_survive_leaving_standardized(db):
    mappings.save_decision("roads", "R9", "Somewhere", "road_class", "CLS")
    mappings.save_values("roads", "R9", "Somewhere", "road_class", {"A": "minor_arterial"})
    doc = mappings.load_doc("roads", "R9")
    assert mappings.mode_of(doc, "road_class") == "standardized"
    mappings.save_mode("roads", "R9", "Somewhere", "road_class", "published")
    doc = mappings.load_doc("roads", "R9")
    assert mappings.mode_of(doc, "road_class") == "published"
    assert doc["values"]["road_class"] == {"A": "minor_arterial"}  # kept for coming back
    assert mappings.load_history("roads", "R9")[-1]["kind"] == "mode"
    with pytest.raises(ValueError):
        mappings.save_mode("roads", "R9", "Somewhere", "road_class", "tidy")  # type: ignore[arg-type]
