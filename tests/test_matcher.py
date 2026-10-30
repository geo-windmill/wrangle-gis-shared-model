"""Field matching without the network: the prompt, the guard, the decisions."""

import os

from wrangle import mappings
from wrangle.ai import load_env
from wrangle.matcher import Match as M
from wrangle.matcher import MatchResponse, build_prompt, validate
from wrangle.models import load_model

PROFILES = [
    {
        "name": "CARTOCLASS",
        "alias": None,
        "type": "String",
        "populated_share": 1.0,
        "distinct": 9,
        "top_values": [{"value": "RES", "share": 0.77, "label": None}],
        "domain": None,
    },
    {
        "name": "OneWay",
        "alias": "One Way",
        "type": "String",
        "populated_share": 0.99,
        "distinct": 3,
        "top_values": [{"value": "B", "share": 0.95, "label": "Both directions"}],
        "domain": {"B": "Both directions", "FT": "From-To"},
    },
]


def test_prompt_shows_values_and_domains_not_just_names():
    prompt = build_prompt(load_model("roads"), "Test County", PROFILES)
    assert "RES (77%)" in prompt
    assert "B = Both directions" in prompt
    assert "declared domain: B=Both directions" in prompt
    assert "lanes" not in prompt  # unsourced model fields are not asked about


def test_guard_drops_fields_that_do_not_exist_on_either_side():
    response = MatchResponse(
        matches=[
            M(target_field="road_class", source_field="CARTOCLASS", confidence="high",
              reasoning=""),
            M(target_field="one_way", source_field="ONE_WAY_DIR", confidence="high", reasoning=""),
            M(target_field="pavement", source_field="CARTOCLASS", confidence="low", reasoning=""),
            M(target_field="owner", source_field=None, confidence="medium", reasoning=""),
        ]
    )
    kept, dropped = validate(load_model("roads"), PROFILES, response)
    assert [m["target_field"] for m in kept] == ["road_class", "owner"]
    assert {d["why"] for d in dropped} == {
        "source has no field 'ONE_WAY_DIR'",
        "model has no field 'pavement'",
    }


def test_guard_keeps_only_the_first_answer_per_field():
    response = MatchResponse(
        matches=[
            M(target_field="one_way", source_field="OneWay", confidence="high", reasoning="a"),
            M(target_field="one_way", source_field="CARTOCLASS", confidence="low", reasoning="b"),
        ]
    )
    kept, dropped = validate(load_model("roads"), PROFILES, response)
    assert len(kept) == 1 and len(dropped) == 1


def test_env_file_never_overrides_the_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nWRANGLE_TEST_A=from-file\nWRANGLE_TEST_B='quoted'\n")
    monkeypatch.setenv("WRANGLE_TEST_A", "already-set")
    monkeypatch.delenv("WRANGLE_TEST_B", raising=False)
    load_env(env)
    assert os.environ["WRANGLE_TEST_A"] == "already-set"
    assert os.environ["WRANGLE_TEST_B"] == "quoted"


def test_decisions_round_trip_including_no_field_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(mappings, "DB_PATH", tmp_path / "wrangle.db")
    mappings.save_decision("roads", "R1", "Sacramento County", "road_class", "CARTOCLASS")
    mappings.save_decision("roads", "R1", "Sacramento County", "owner", None)
    assert mappings.load_mapping("roads", "R1") == {"road_class": "CARTOCLASS", "owner": None}
    mappings.clear_decision("roads", "R1", "Sacramento County", "owner")
    assert mappings.load_mapping("roads", "R1") == {"road_class": "CARTOCLASS"}
