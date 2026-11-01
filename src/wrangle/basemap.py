"""The basemap, kept on disk so the map looks the same with the wifi off.

The map draws OpenFreeMap's dark style (OpenStreetMap data). The browser asks
`wrangle serve` for it instead of tiles.openfreemap.org, and every file served — the
style, the tile index, vector tiles, fonts, sprites — is kept under cache/basemap/ the
first time and read from there after. `wrangle cache basemap` fills it ahead of time
for each feature's area of interest.

The cache mode applies, as it does to the agency layers: in `replay` a file that is not
on disk is a 404, never a network call, so an offline map shows a gap instead of hanging.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx

from .arcgis.client import USER_AGENT
from .cache import DEFAULT_CACHE_DIR, Mode

UPSTREAM = "https://tiles.openfreemap.org/"
STYLE = "styles/dark"
# Everything the style points at lives under one of these.
ALLOWED = ("styles/", "planet", "natural_earth/", "fonts/", "sprites/")
TYPES = {".pbf": "application/x-protobuf", ".png": "image/png", ".json": "application/json"}
# Unicode blocks the labels here need: Latin, Latin-1 and Latin Extended, punctuation.
GLYPH_RANGES = ("0-255", "256-511", "8192-8447")


def root() -> Path:
    return Path(DEFAULT_CACHE_DIR) / "basemap"


def mode() -> Mode:
    return Mode(os.environ.get("WRANGLE_CACHE_MODE", Mode.PREFER_CACHE.value))


def local_path(path: str) -> Path:
    """Where one upstream file is kept. A path from a request never leaves the folder."""
    parts = path.split("/")
    if not path.startswith(ALLOWED) or "\\" in path or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"not a basemap file: {path!r}")
    # The style and the tile index have no extension; the index ("planet") shares its
    # name with the tile folder, so both are kept as .json.
    return root() / (path if Path(path).suffix in TYPES else path + ".json")


def media_type(p: Path) -> str:
    return TYPES.get(p.suffix, "application/octet-stream")


async def get(path: str, client: httpx.AsyncClient, m: Mode | None = None) -> bytes | None:
    """One basemap file: from disk if it is there, else from upstream (kept for next
    time). None when it can't be had: not on disk in `replay`, upstream says 404, or
    there is no network."""
    p, m = local_path(path), m or mode()
    if m in (Mode.REPLAY, Mode.PREFER_CACHE) and p.exists():
        return p.read_bytes()
    if m is Mode.REPLAY:
        return None
    try:
        r = await client.get(UPSTREAM + path)
    except httpx.HTTPError:
        return None  # offline: the map draws without this piece
    if r.status_code == 204:
        body = b""  # an empty tile (open water): keep it, so it is never asked again
    elif r.status_code == 200:
        body = r.content
    else:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(p)
    return body


def client(concurrency: int = 8) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=5),
        headers={"User-Agent": USER_AGENT},
        limits=httpx.Limits(max_connections=concurrency),
        follow_redirects=True,
    )


# --- filling it ahead of time ------------------------------------------------------


def tiles_for(bbox: tuple[float, float, float, float], z: int) -> list[tuple[int, int, int]]:
    """Every Web Mercator tile at zoom `z` that touches a lon/lat box."""
    n = 2**z

    def tx(lon: float) -> int:
        return min(n - 1, max(0, int((lon + 180) / 360 * n)))

    def ty(lat: float) -> int:
        lat = max(-85.0511, min(85.0511, lat))
        y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
        return min(n - 1, max(0, int(y)))

    x0, y0, x1, y1 = bbox
    return [(z, x, y) for x in range(tx(x0), tx(x1) + 1) for y in range(ty(y1), ty(y0) + 1)]


def tile_set(
    bboxes: Iterable[tuple[float, float, float, float]], max_zoom: int, max_tiles: int
) -> set[tuple[int, int, int]]:
    """The tiles covering each box, zoom by zoom, until a box needs more than
    `max_tiles` at one zoom: a county goes to street level, a state stops early."""
    out: set[tuple[int, int, int]] = set()
    for bbox in bboxes:
        for z in range(max_zoom + 1):
            ts = tiles_for(bbox, z)
            if len(ts) > max_tiles:
                break
            out.update(ts)
    return out


def _fonts(style: dict) -> set[str]:
    stacks: set[str] = set()

    def walk(v: object) -> None:
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            if not any(x in ("literal", "get", "case", "match", "coalesce") for x in v):
                stacks.add(",".join(v))
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for layer in style.get("layers", []):
        walk((layer.get("layout") or {}).get("text-font"))
    return stacks


def _rel(url: str) -> str:
    if not url.startswith(UPSTREAM):
        raise ValueError(f"style points outside {UPSTREAM}: {url}")
    return url[len(UPSTREAM):]


async def prefetch(
    bboxes: list[tuple[float, float, float, float]],
    max_zoom: int = 14,
    max_tiles: int = 4000,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Download the style and everything it needs to draw these boxes. Files already
    on disk are skipped (in `refresh`, fetched again)."""
    m = mode()
    if m is Mode.REPLAY:
        raise RuntimeError("WRANGLE_CACHE_MODE is replay: nothing can be downloaded")
    async with client() as c:
        style_body = await get(STYLE, c, m)
        if style_body is None:
            raise RuntimeError(f"could not get the basemap style from {UPSTREAM}{STYLE}")
        style = json.loads(style_body)
        paths: list[str] = []
        tiles = {}
        for src in style.get("sources", {}).values():
            if "url" in src:  # a tile index (TileJSON) that names the tiles
                body = await get(_rel(src["url"]), c, m)
                if body is None:
                    raise RuntimeError(f"could not get the tile index {src['url']}")
                src = {**src, **json.loads(body)}
            for template in src.get("tiles", []):
                cap = min(max_zoom, int(src.get("maxzoom", max_zoom)))
                tiles[_rel(template)] = tile_set(bboxes, cap, max_tiles)
        for template, ts in tiles.items():
            paths += [
                template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
                for z, x, y in sorted(ts)
            ]
        if glyphs := style.get("glyphs"):
            for stack in sorted(_fonts(style)):
                paths += [
                    _rel(glyphs).replace("{fontstack}", stack).replace("{range}", r)
                    for r in GLYPH_RANGES
                ]
        sprites = style.get("sprite") or []
        for s in [sprites] if isinstance(sprites, str) else [x["url"] for x in sprites]:
            paths += [_rel(s) + ext for ext in (".json", ".png", "@2x.json", "@2x.png")]

        counts = {"files": len(paths), "missing": 0}
        done = 0
        sem = asyncio.Semaphore(8)

        async def one(path: str) -> None:
            nonlocal done
            async with sem:
                if await get(path, c, m) is None:
                    counts["missing"] += 1
            done += 1
            if progress:
                progress(done, len(paths))

        await asyncio.gather(*(one(p) for p in paths))
    counts["bytes"] = sum(f.stat().st_size for f in root().rglob("*") if f.is_file())
    return counts
