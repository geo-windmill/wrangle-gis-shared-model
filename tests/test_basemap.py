"""The basemap kept on disk: fetched once, replayed after, never a network call in replay."""

from __future__ import annotations

import httpx
import pytest
import respx

from wrangle import basemap
from wrangle.cache import Mode

TILE = "planet/20261025_080012_pt/14/2662/6286.pbf"


@pytest.fixture(autouse=True)
def tmp_root(tmp_path, monkeypatch):
    monkeypatch.setattr(basemap, "root", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "path",
    [
        "../wrangle.db", "fonts/../../x", "styles//dark", "elsewhere/x.png", r"fonts\..\x", "",
        "fonts/C:/Windows/x.pbf", "fonts/x.pbf:stream", "fonts/CON/0-255.pbf", "fonts/nul.pbf",
        "fonts/.../x", "planetX/1/2/3.pbf",
    ],
)
def test_a_request_path_never_leaves_the_folder(path):
    with pytest.raises(ValueError):
        basemap.local_path(path)


def test_the_style_and_the_tile_index_are_kept_as_json(tmp_root):
    assert basemap.local_path("styles/dark") == tmp_root / "styles/dark.json"
    assert basemap.local_path("planet") == tmp_root / "planet.json"
    assert basemap.local_path(TILE) == tmp_root / TILE


@respx.mock
async def test_fetched_once_then_read_from_disk(tmp_root):
    route = respx.get(basemap.UPSTREAM + TILE).respond(200, content=b"tile")
    async with basemap.client() as c:
        assert await basemap.get(TILE, c, Mode.PREFER_CACHE) == b"tile"
        assert await basemap.get(TILE, c, Mode.PREFER_CACHE) == b"tile"
    assert route.call_count == 1
    assert (tmp_root / TILE).read_bytes() == b"tile"


@respx.mock
async def test_replay_never_touches_the_network():
    route = respx.get(basemap.UPSTREAM + TILE).respond(200, content=b"tile")
    async with basemap.client() as c:
        assert await basemap.get(TILE, c, Mode.REPLAY) is None
    assert not route.called


@respx.mock
async def test_an_empty_tile_is_kept_so_it_is_never_asked_again(tmp_root):
    respx.get(basemap.UPSTREAM + TILE).respond(204)
    async with basemap.client() as c:
        assert await basemap.get(TILE, c, Mode.PREFER_CACHE) == b""
    assert (tmp_root / TILE).exists()


@respx.mock
async def test_no_network_is_a_gap_not_an_error(tmp_root):
    respx.get(basemap.UPSTREAM + TILE).mock(side_effect=httpx.ConnectError("offline"))
    async with basemap.client() as c:
        assert await basemap.get(TILE, c, Mode.PREFER_CACHE) is None
    assert not (tmp_root / TILE).exists()


def test_tower_bridge_is_one_tile_at_street_level():
    assert basemap.tiles_for((-121.5013, 38.5803, -121.5013, 38.5803), 14) == [(14, 2662, 6286)]


def test_a_big_area_stops_before_it_needs_too_many_tiles():
    california = (-124.48, 32.53, -114.13, 42.01)
    zooms = {z for z, _, _ in basemap.tile_set([california], 14, 4000)}
    assert max(zooms) == 10


def test_fonts_are_read_from_the_style_including_literal_expressions():
    style = {"layers": [
        {"layout": {"text-font": ["Noto Sans Regular"]}},
        {"layout": {"text-font": ["literal", ["Noto Sans Bold", "Noto Sans Regular"]]}},
        {"layout": {}},
    ]}
    assert basemap._fonts(style) == {"Noto Sans Regular", "Noto Sans Bold,Noto Sans Regular"}
