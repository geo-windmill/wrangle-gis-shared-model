"""Edit values without the network: the methods, their guards, agreement, storage."""

import datetime as dt

import pandas as pd

from wrangle import mappings
from wrangle.models import load_model
from wrangle.value_matcher import ValueMatch, ValueResponse, build_prompt
from wrangle.value_matcher import validate as validate_ai
from wrangle.values import (
    candidates_for,
    cell_kind,
    date_options,
    distinct_values,
    match_vocab,
    numbers_in,
    parse_date,
    reviewed_index,
    same_digits,
    status,
)

ROADS = load_model("roads")
CLASS = ROADS.field("road_class")
SPEED = ROADS.field("speed_limit")
SIZE = load_model("hydrants").field("size_in")
HYDRANT_TYPE = load_model("hydrants").field("hydrant_type")


def _value(raw, label=None, declared=None):
    return {"raw": raw, "rows": 1, "share": 0.1, "label": label, "declared": declared}


def _ai(value, confidence="high"):
    return {"value": value, "confidence": confidence, "reasoning": "r"}


# --- the text methods ------------------------------------------------------------------


def test_exact_and_whole_word_matches_prefer_the_most_specific_term():
    assert match_vocab("LOCAL", CLASS.values) == ("exact", ["local"])
    assert match_vocab("Out of Service", load_model("hydrants").field("status").values) == (
        "exact",
        ["out_of_service"],
    )
    # "other" is a term too, but principal_arterial names more of the text
    assert match_vocab("Other Principal Arterial Rural", CLASS.values) == (
        "words",
        ["principal_arterial"],
    )


def test_abbreviations_match_but_ambiguity_is_reported_not_resolved():
    assert match_vocab("RMP", CLASS.values) == ("abbrev", ["ramp"])
    assert match_vocab("PED", CLASS.values) == ("abbrev", ["pedestrian"])
    assert match_vocab("ART", CLASS.values) == ("abbrev", ["principal_arterial", "minor_arterial"])
    assert match_vocab("draft", HYDRANT_TYPE.values) == ("abbrev", ["drafting"])


def test_fuzzy_guards_short_values_and_digits():
    assert match_vocab("SS", ["sanitary_sewer", "storm_drain"]) == ("", [])
    assert match_vocab("TO", CLASS.values) == ("", [])
    assert match_vocab("4S", ["four_s"]) == ("", [])  # digits never fuzzy-match
    assert not same_digits("4S", "2 1/2 X 4D")
    assert same_digits("2 1/2 DX4S", "2 1/2 D X 4S")


def test_normal_is_not_guessed_into_a_hydrant_type():
    assert match_vocab("NORMAL", HYDRANT_TYPE.values) == ("", [])


def test_a_domain_label_is_read_without_its_own_code():
    cands = candidates_for(CLASS, _value("26", "26 - Bike Path", True), [], None)
    assert [(c["value"], c["methods"][0]["method"]) for c in cands] == [("bikeway", "domain")]
    # a label that only repeats the code says nothing
    assert candidates_for(ROADS.field("one_way"), _value("FT", "FT", True), [], None) == []


# --- numbers -------------------------------------------------------------------------------


def test_numbers_in_text_read_fractions_and_decimals():
    assert numbers_in("2 1/2 X 4D") == [2.5, 4]
    assert numbers_in("2.5X4DX2.5AV") == [2.5, 4, 2.5]
    assert numbers_in("4S") == [4]


def test_zero_speed_is_cast_to_null_by_the_strict_caster():
    [c] = candidates_for(SPEED, _value("0"), [], None)
    assert c["value"] is None and "below min" in c["methods"][0]["detail"]
    [c] = candidates_for(SPEED, _value("25"), [], None)
    assert c["value"] == 25


def test_a_compound_size_spec_is_a_question_not_a_guess():
    cands = candidates_for(SIZE, _value("2 1/2 X 4D"), [], None)
    assert sorted(c["value"] for c in cands) == [2.5, 4]
    assert status(cands) == "conflict"


# --- agreement -----------------------------------------------------------------------------


def test_two_methods_agreeing_is_agree_and_one_is_single():
    v = _value("19", "19 - Local Urban", True)
    assert status(candidates_for(CLASS, v, [], None)) == "single"
    assert status(candidates_for(CLASS, v, [], _ai("local"))) == "agree"
    assert status(candidates_for(CLASS, v, [], _ai("interstate"))) == "conflict"


def test_a_low_confidence_ai_answer_is_shown_but_does_not_vote():
    cands = candidates_for(CLASS, _value("ALY"), [], _ai("alley", "low"))
    assert len(cands[0]["methods"]) == 2
    assert status(cands) == "single"


def test_nothing_placed_is_none():
    assert status(candidates_for(CLASS, _value("HWY"), [], None)) == "none"


def test_only_a_persons_decisions_count_as_reviewed():
    docs = {
        "R1": {
            "values": {"one_way": {"B": "no", "FT": "forward"}},
            "values_decided_by": {"one_way": {"B": "person", "FT": "automap"}},
        },
        "R4": {"values": {}, "values_decided_by": {}},
    }
    idx = reviewed_index("one_way", docs, {"R1": "Sacramento County"}, exclude="R4")
    assert idx == {"b": [{"publisher": "Sacramento County", "value": "no"}]}
    assert reviewed_index("one_way", docs, {}, exclude="R1") == {}


# --- reading a column ----------------------------------------------------------------------


def test_distinct_values_flag_values_outside_the_declared_domain():
    col = pd.Series(["B", "B", "FT", "2", " ", None])
    d = distinct_values(col, {"B": "B", "FT": "FT", "TF": "TF"})
    assert d["blank"] == 2
    assert {v["raw"]: v["declared"] for v in d["values"]} == {"B": True, "FT": True, "2": False}


def test_cell_kinds():
    assert cell_kind(CLASS, 500) == "table"
    assert cell_kind(ROADS.field("name"), 5000) == "text"
    assert cell_kind(ROADS.field("owner"), 5) == "table"
    assert cell_kind(ROADS.field("source_date"), 3) == "date"


# --- dates ---------------------------------------------------------------------------------


def test_dates_refuse_impossible_days_and_iso_needs_its_dashes():
    assert parse_date("20040323", "yyyymmdd") == dt.date(2004, 3, 23)
    assert parse_date("20210917084908", "yyyymmdd") == dt.date(2021, 9, 17)
    assert parse_date("20080231", "yyyymmdd") is None  # February 31st, really published
    assert parse_date("20040323", "iso") is None
    assert parse_date("2004-03-23", "iso") == dt.date(2004, 3, 23)


def test_date_options_report_failures_and_placeholders():
    col = pd.Series(["20040323", "20040323", "20080231", "18991230", None])
    [opt] = date_options(col, "String")
    assert opt["format"] == "yyyymmdd"
    assert opt["failed"]["examples"] == ["20080231"]
    assert opt["placeholder"]["examples"] == ["18991230"]
    assert opt["range"] == ["2004-03-23", "2004-03-23"]


# --- the AI's guard ------------------------------------------------------------------------


def test_ai_prompt_shows_every_value_and_flags_undeclared_ones():
    field = {"name": "OneWay", "alias": "One-Way Travel", "type": "String",
             "domain": {"B": "B", "FT": "FT", "TF": "TF"}}
    d = distinct_values(pd.Series(["B"] * 95 + ["FT"] * 4 + ["2"]), field["domain"])
    prompt = build_prompt(ROADS.field("one_way"), "Test County", field, d["values"], 0)
    assert "one of: no, forward, reverse" in prompt
    assert "- 2 (1%) (not in the declared domain)" in prompt
    assert "notes" not in prompt.lower()  # the model notes would hand it the answers


def test_ai_guard_drops_invented_values_and_out_of_range_numbers():
    values = [_value("B"), _value("0"), _value("25")]
    resp = ValueResponse(values=[
        ValueMatch(raw="B", value="no", confidence="high", reasoning=""),
        ValueMatch(raw="X", value="no", confidence="high", reasoning=""),
        ValueMatch(raw="B", value="forward", confidence="low", reasoning=""),
    ])
    kept, dropped = validate_ai(ROADS.field("one_way"), values, resp)
    assert kept == {"B": {"value": "no", "confidence": "high", "reasoning": ""}}
    assert len(dropped) == 2

    resp = ValueResponse(values=[
        ValueMatch(raw="0", value="0", confidence="high", reasoning=""),
        ValueMatch(raw="25", value="25", confidence="high", reasoning=""),
    ])
    kept, dropped = validate_ai(SPEED, values, resp)
    assert kept["25"]["value"] == 25 and "0" not in kept
    assert "below min" in dropped[0]["why"]


# --- storage -------------------------------------------------------------------------------


def test_value_decisions_survive_field_saves_and_go_when_the_field_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(mappings, "DB_PATH", tmp_path / "wrangle.db")
    pub = "Sacramento County"
    mappings.save_decision("roads", "R1", pub, "one_way", "ONEWAY")
    mappings.save_values("roads", "R1", pub, "one_way", {"B": "no", "0": None})
    mappings.save_decision("roads", "R1", pub, "name", "FULLSTREET")  # another field
    mappings.save_decision("roads", "R1", pub, "one_way", "ONEWAY")  # same choice again
    doc = mappings.load_doc("roads", "R1")
    assert doc["values"]["one_way"] == {"B": "no", "0": None}
    assert doc["values_decided_by"]["one_way"] == {"B": "person", "0": "person"}

    mappings.clear_value("roads", "R1", pub, "one_way", "0")
    assert mappings.load_doc("roads", "R1")["values"]["one_way"] == {"B": "no"}

    mappings.save_decision("roads", "R1", pub, "one_way", "DIRECTION")  # a different field
    assert "one_way" not in mappings.load_doc("roads", "R1")["values"]


def test_rules_round_trip_and_an_empty_file_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(mappings, "DB_PATH", tmp_path / "wrangle.db")
    mappings.save_decision("roads", "R1", "S", "source_date", "AC_DATE")
    mappings.save_rule("roads", "R1", "S", "source_date", "yyyymmdd")
    assert mappings.load_doc("roads", "R1")["rules"] == {"source_date": "yyyymmdd"}
    mappings.clear_decision("roads", "R1", "S", "source_date")
    assert mappings.load_record("roads", "R1") is None  # nothing decided: no record
    assert [h["kind"] for h in mappings.load_history("roads", "R1")][-2:] == ["rule", "field"]
