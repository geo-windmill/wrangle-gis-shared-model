"""The configuration record: one row per source in SQLite, plus a history of every decision."""

from __future__ import annotations

import pytest

from wrangle import mappings


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(mappings, "DB_PATH", tmp_path / "wrangle.db")
    return tmp_path


def test_record_carries_what_was_reviewed(db):
    mappings.save_decision("roads", "R1", "Sacramento County", "name", "FULLSTREET", "automap")
    rec = mappings.load_record("roads", "R1")
    assert rec["publisher"] == "Sacramento County"
    assert rec["url"].endswith("/FeatureServer/0")  # from the locked source list
    assert rec["model"].startswith("roads v")
    assert rec["config"]["fields"] == {"name": "FULLSTREET"}
    assert rec["config"]["decided_by"] == {"name": "automap"}
    assert rec["created_at"] == rec["updated_at"]
    assert rec["converted_at"] is None


def test_history_keeps_old_and_new(db):
    pub = "Sacramento County"
    mappings.save_decision("roads", "R1", pub, "road_class", "CARTOCLASS")
    mappings.save_decision("roads", "R1", pub, "road_class", "CLASS")
    mappings.save_values("roads", "R1", pub, "road_class", {"TO": None}, "person")
    mappings.clear_value("roads", "R1", pub, "road_class", "TO")
    h = mappings.load_history("roads", "R1")
    assert [(e["kind"], e["old"], e["new"]) for e in h] == [
        ("field", None, "CARTOCLASS"),
        ("field", "CARTOCLASS", "CLASS"),
        ("value", None, None),  # decided: TO means nothing the model holds (stored as JSON null)
        ("value", None, None),  # cleared: back to undecided
    ]
    # the difference between "decided null" and "not decided" survives in the database
    con = mappings._connect()
    raw = con.execute("SELECT old, new FROM history WHERE kind = 'value' ORDER BY id").fetchall()
    assert [tuple(r) for r in raw] == [(None, "null"), ("null", None)]


def test_updated_at_moves_and_conversion_is_stamped(db):
    pub = "Yolo County"
    mappings.save_decision("roads", "R2", pub, "one_way", "OneWay")
    first = mappings.updated_at("roads", "R2")
    mappings.save_values("roads", "R2", pub, "one_way", {"FT": "forward"})
    assert mappings.updated_at("roads", "R2") > first
    mappings.mark_converted("roads", "R2", "gaps")
    rec = mappings.load_record("roads", "R2")
    assert rec["verdict"] == "gaps" and rec["converted_at"]


def test_clearing_a_rule_that_was_never_set_leaves_no_history(db):
    mappings.save_decision("roads", "R1", "S", "source_date", "AC_DATE")
    mappings.save_rule("roads", "R1", "S", "source_date", None)
    assert [h["kind"] for h in mappings.load_history("roads", "R1")] == ["field"]


def test_starting_edit_values_over_keeps_the_field_matches(db):
    pub = "Sacramento County"
    mappings.save_decision("roads", "R1", pub, "road_class", "CLASS")
    mappings.save_values("roads", "R1", pub, "road_class", {"A": "principal_arterial"})
    mappings.save_rule("roads", "R1", pub, "source_date", "yyyymmdd")
    mappings.save_mode("roads", "R1", pub, "name", "cleaned")
    mappings.clear_all("roads", "R1", pub, fields=False)
    cfg = mappings.load_record("roads", "R1")["config"]
    assert cfg["fields"] == {"road_class": "CLASS"}
    assert not cfg["values"] and not cfg["rules"] and not cfg["modes"]
    kinds = [h["kind"] for h in mappings.load_history("roads", "R1")[-4:]]
    assert sorted(kinds) == ["mode", "mode", "rule", "value"]  # road_class's mode went too


def test_starting_match_fields_over_forgets_everything(db):
    pub = "Sacramento County"
    mappings.save_decision("roads", "R1", pub, "road_class", "CLASS")
    mappings.save_values("roads", "R1", pub, "road_class", {"A": "principal_arterial"})
    mappings.clear_all("roads", "R1", pub, fields=True)
    assert mappings.load_record("roads", "R1") is None  # nothing decided: no record
    assert mappings.load_history("roads", "R1")[-1]["new"] is None


def test_a_person_can_add_a_value_to_the_vocabulary(db):
    from wrangle.models import load_model

    pub = "Yolo County"
    mappings.add_value("roads", "one_way", "two_way_bus", "two-way, buses only")
    added = mappings.load_added("roads")
    assert added == {"one_way": [("two_way_bus", "two-way, buses only")]}
    shipped = load_model("roads")
    model = shipped.extended(added)
    one_way = model.field("one_way")
    assert one_way.values[-1] == "two_way_bus" and one_way.meanings["two_way_bus"]
    assert one_way.coerce("two_way_bus") == "two_way_bus"  # the strict caster accepts it
    assert "two_way_bus" not in shipped.field("one_way").values  # the AI's model is untouched
    mappings.save_values("roads", "R2", pub, "one_way", {"2": "two_way_bus"})
    assert mappings.value_in_use("roads", "one_way", "two_way_bus") == ["R2"]
    mappings.save_values("roads", "R2", pub, "one_way", {"2": None})
    assert mappings.remove_value("roads", "one_way", "two_way_bus")
    assert mappings.load_added("roads") == {}


def test_every_value_meaning_explains_a_declared_value():
    from wrangle.models import load_model

    one_way = load_model("roads").field("one_way")
    assert one_way.meanings["no"].startswith("two-way")
