"""The locked source registry matches what the latest vetting run found (2026-10-30)."""

from __future__ import annotations

import pytest

from wrangle import available_features, load_sources
from wrangle.registry import REJECTION_REASONS


def test_both_feature_sets_load():
    assert set(available_features()) == {"roads", "hydrants"}


def test_roads_lineup():
    ss = load_sources("roads")
    assert [s.id for s in ss.loadable] == ["R1", "R2", "R3", "R4", "R5"]
    assert [s.id for s in ss.rejected] == ["R6", "R7"]


def test_hydrant_lineup():
    ss = load_sources("hydrants")
    assert [s.id for s in ss.loadable] == ["H1", "H2", "H3"]
    assert [s.id for s in ss.rejected] == ["H4", "H5"]


@pytest.mark.parametrize("feature", ["roads", "hydrants"])
def test_every_rejection_has_a_known_reason_and_a_detail(feature):
    """A greyed-out card must say why, in words, on stage."""
    for s in load_sources(feature).rejected:
        assert s.reason in REJECTION_REASONS, f"{s.id} has an unexplained reason {s.reason!r}"
        assert s.reason_detail and s.reason_detail.strip(), f"{s.id} has no detail"


@pytest.mark.parametrize("feature", ["roads", "hydrants"])
def test_every_loadable_source_pins_a_count_and_a_crs(feature):
    for s in load_sources(feature).loadable:
        assert s.expected_count, f"{s.id} pins no count, so revet cannot detect drift"
        assert s.expect.crs, f"{s.id} pins no CRS"


def test_caltrans_expected_count_uses_the_filtered_number():
    """R5 is 780k statewide and 24,952 in Sacramento County. The filter is the point."""
    r5 = load_sources("roads").get("R5")
    assert r5.where == "County_label='Sacramento'"
    assert r5.expect.count == 779834
    assert r5.expected_count == 24952


def test_elk_grove_pins_a_null_oid_field():
    """Its MapServer publishes none; the client must detect it by field type."""
    assert load_sources("roads").get("R4").expect.oid_field is None


def test_el_dorado_declares_a_space_null_sentinel():
    assert load_sources("hydrants").get("H2").sentinels == ("", " ")


def test_default_sentinels_cover_blank_and_space():
    assert load_sources("roads").get("R1").sentinels == ("", " ")


def test_drift_detection_reports_a_changed_count():
    r1 = load_sources("roads").get("R1")
    drift = r1.drift({"count": 72640, "wkid": 2226, "max_record_count": 2000,
                      "oid_field": "OBJECTID"})
    assert len(drift) == 1
    assert "72,596 -> 72,640 (+44)" in drift[0]


def test_drift_detection_is_quiet_when_nothing_moved():
    r1 = load_sources("roads").get("R1")
    assert r1.drift({"count": 72596, "wkid": 2226, "max_record_count": 2000,
                     "oid_field": "OBJECTID", "domain_count": 0}) == []


def test_drift_detection_catches_a_crs_change():
    r5 = load_sources("roads").get("R5")
    drift = r5.drift({"count": 24952, "wkid": 4326})
    assert any("EPSG:3857 -> EPSG:4326" in d for d in drift)


def test_drift_detection_catches_yolos_domains_disappearing():
    """R2's 26 coded domains are the tier-1 demo. If they go, the segment dies."""
    r2 = load_sources("roads").get("R2")
    drift = r2.drift({"count": 11493, "wkid": 2226, "domain_count": 0})
    assert any("coded-domain fields 26 -> 0" in d for d in drift)


def test_lookup_is_case_insensitive_and_errors_helpfully():
    ss = load_sources("roads")
    assert ss.get("r1").id == "R1"
    with pytest.raises(KeyError, match="no source"):
        ss.get("R99")


def test_totals_are_the_locked_counts():
    assert load_sources("roads").total_expected_features == 125_434
    assert load_sources("hydrants").total_expected_features == 77_330
