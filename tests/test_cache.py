"""The record-and-replay cache. On stage this is what stands between the demo and
conference wifi, so its behaviour is pinned."""

from __future__ import annotations

import pytest

from wrangle.cache import CacheKey, CacheMiss, Mode, ResponseCache


def test_key_ignores_param_order_and_the_f_param():
    a = CacheKey.build("https://x.test/0/query", {"where": "1=1", "outFields": "*", "f": "json"})
    b = CacheKey.build("https://x.test/0/query", {"outFields": "*", "f": "pjson", "where": "1=1"})
    assert a.digest == b.digest


def test_key_ignores_a_token_so_a_recording_still_replays():
    a = CacheKey.build("https://x.test/0/query", {"where": "1=1"})
    b = CacheKey.build("https://x.test/0/query", {"where": "1=1", "token": "secret"})
    assert a.digest == b.digest


def test_key_folds_query_string_into_params():
    a = CacheKey.build("https://x.test/0/query?where=1%3D1")
    b = CacheKey.build("https://x.test/0/query", {"where": "1=1"})
    assert a.digest == b.digest


def test_key_separates_different_queries():
    a = CacheKey.build("https://x.test/0/query", {"resultOffset": 0})
    b = CacheKey.build("https://x.test/0/query", {"resultOffset": 2000})
    assert a.digest != b.digest


def test_key_trailing_slash_is_not_significant():
    assert CacheKey.build("https://x.test/0/").digest == CacheKey.build("https://x.test/0").digest


def test_roundtrip_writes_body_meta_and_index(tmp_path):
    c = ResponseCache(root=tmp_path, mode=Mode.LIVE)
    key = CacheKey.build("https://services1.arcgis.com/a/0/query", {"where": "1=1"})
    c.put(key, {"count": 72559}, elapsed_ms=410)

    assert c.get(key) == {"count": 72559}
    assert (tmp_path / "services1.arcgis.com" / f"{key.digest}.json").exists()
    meta = tmp_path / "services1.arcgis.com" / f"{key.digest}.meta.json"
    assert meta.exists() and "recorded_at" in meta.read_text()
    assert key.digest in c.entries()
    assert c.stats.writes == 1 and c.stats.hits == 1


def test_replay_mode_never_writes(tmp_path):
    c = ResponseCache(root=tmp_path, mode=Mode.REPLAY)
    key = CacheKey.build("https://x.test/0", {})
    c.put(key, {"a": 1})
    assert c.get(key) is None
    assert not c.may_fetch and c.may_read


def test_mode_policy_matrix(tmp_path):
    def modes(m):
        c = ResponseCache(root=tmp_path, mode=m)
        return (c.may_read, c.may_fetch, c.may_write, c.prefers_network)

    assert modes(Mode.REPLAY) == (True, False, False, False)
    assert modes(Mode.PREFER_CACHE) == (True, True, True, False)
    assert modes(Mode.LIVE) == (False, True, True, True)
    assert modes(Mode.REFRESH) == (False, True, True, True)


def test_cache_miss_message_tells_you_how_to_fix_it():
    key = CacheKey.build("https://x.test/0/query", {"where": "1=1"})
    with pytest.raises(CacheMiss, match="--live"):
        raise CacheMiss(key)


def test_mode_reads_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("WRANGLE_CACHE_MODE", "replay")
    assert ResponseCache(root=tmp_path).mode is Mode.REPLAY


def test_describe_summarises_what_is_recorded(tmp_path):
    c = ResponseCache(root=tmp_path, mode=Mode.LIVE)
    c.put(CacheKey.build("https://a.test/0", {}), {"x": 1})
    c.put(CacheKey.build("https://b.test/0", {}), {"y": 2})
    d = c.describe()
    assert "2 recorded responses" in d
    assert "a.test" in d and "b.test" in d
