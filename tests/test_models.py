"""The shared models load, validate, and coerce the way the talk claims."""

from __future__ import annotations

import datetime as dt

import pytest

from wrangle import available_models, load_model
from wrangle.models import TargetField


def test_both_models_load():
    assert set(available_models()) == {"roads", "hydrants"}


@pytest.mark.parametrize("name", ["roads", "hydrants"])
def test_model_is_small_enough_to_read_on_one_screen(name):
    """The audience must be able to read the whole model on one screen."""
    m = load_model(name)
    assert len(m.fields) <= 10, f"{name} has grown past one screen"
    assert len(m.provenance) == 2


def test_roads_shape():
    m = load_model("roads")
    assert m.geometry == "linestring"
    assert m.crs == "EPSG:4326"
    assert m.field_names == [
        "name", "road_class", "one_way", "speed_limit",
        "owner", "lanes", "surface", "source_date",
    ]


def test_one_way_is_three_state_not_boolean():
    """A boolean would collapse forward and reverse. That is the whole point."""
    f = load_model("roads").field("one_way")
    assert f.type == "enum"
    assert set(f.values) == {"no", "forward", "reverse"}


def test_unsourced_fields_are_declared_and_excluded_from_matching():
    roads = load_model("roads")
    assert set(roads.unsourced_field_names) == {"lanes", "surface"}
    assert "lanes" not in roads.mappable_field_names
    # but still a real target field, so provenance and output carry the column
    assert "lanes" in roads.field_names

    hydrants = load_model("hydrants")
    assert set(hydrants.unsourced_field_names) == {"flow_class", "install_year"}


def test_prompt_view_omits_unsourced_and_includes_vocabularies():
    view = load_model("roads").prompt_view()
    assert "road_class" in view
    assert "minor_arterial" in view
    assert "surface" not in view, "do not ask the matcher for a field nothing publishes"


def test_speed_limit_rejects_zero_via_min():
    """Sources use 0 for unknown. It must not survive as a speed limit."""
    f = load_model("roads").field("speed_limit")
    with pytest.raises(ValueError, match="below min"):
        f.coerce(0)
    assert f.coerce(35) == 35


def test_an_integer_field_refuses_a_fraction_instead_of_truncating_it():
    f = load_model("roads").field("lanes")
    assert f.coerce(3.0) == 3 and f.coerce("3") == 3
    with pytest.raises(ValueError, match="whole number"):
        f.coerce(2.5)


def test_enum_coercion_rejects_a_value_outside_the_vocabulary():
    f = load_model("roads").field("road_class")
    assert f.coerce("minor_arterial") == "minor_arterial"
    with pytest.raises(ValueError, match="not in the road_class vocabulary"):
        f.coerce("Collector")


def test_blank_and_space_coerce_to_none_for_nullable_fields():
    """El Dorado County's null sentinel is a single space."""
    f = load_model("hydrants").field("owner")
    assert f.coerce(" ") is None
    assert f.coerce("") is None
    assert f.coerce(None) is None


def test_date_field_accepts_arcgis_epoch_milliseconds():
    f = load_model("roads").field("source_date")
    assert f.coerce(1_788_859_984_485) == dt.date(2026, 9, 8)
    assert f.coerce("2026-07-20") == dt.date(2026, 7, 20)


def test_coercion_raises_instead_of_silently_nulling():
    """Text cast to a date must fail loudly: a silent null reports success and loses the value."""
    f = load_model("hydrants").field("source_date")
    with pytest.raises(ValueError):
        f.coerce("not a date")


def test_enum_field_without_values_is_rejected():
    with pytest.raises(ValueError, match="declares no values"):
        TargetField(name="x", type="enum")


def test_non_enum_field_with_values_is_rejected():
    with pytest.raises(ValueError, match="declares values"):
        TargetField(name="x", type="string", values=["a"])


def test_size_in_bounds_reject_a_parsed_garbage_value():
    """"2.5X4DX2.5AV" must not become 254.25 inches."""
    f = load_model("hydrants").field("size_in")
    assert f.coerce(4) == 4.0
    with pytest.raises(ValueError, match="above max"):
        f.coerce(254.25)
