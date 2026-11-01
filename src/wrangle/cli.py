"""`wrangle` — the command line the demo is driven from.

Inspect the shared models, list the locked sources, assess and re-vet them live,
profile fields, manage the cache, download the public data (`fetch`), and run the
app (`serve`).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .arcgis import ArcGISClient, assess_layer, profile_fields
from .cache import Mode, ResponseCache
from .models import available_models, load_model
from .registry import available_features, load_sources

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Wrangle public geospatial data into a shared model.",
)
model_app = typer.Typer(no_args_is_help=True, help="Inspect the shared models.")
source_app = typer.Typer(no_args_is_help=True, help="Inspect and re-vet locked sources.")
cache_app = typer.Typer(no_args_is_help=True, help="Manage the record-and-replay cache.")
app.add_typer(model_app, name="model")
app.add_typer(source_app, name="source")
app.add_typer(cache_app, name="cache")

console = Console()


def _cache(live: bool, refresh: bool = False) -> ResponseCache:
    """--live or a refresh choose the mode; otherwise WRANGLE_CACHE_MODE does (default: prefer)."""
    mode = Mode.REFRESH if refresh else (Mode.LIVE if live else None)
    return ResponseCache(mode=mode)


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"wrangle {__version__}")


# --- models -----------------------------------------------------------------


@model_app.command("list")
def model_list() -> None:
    """List the shared models."""
    for name in available_models():
        m = load_model(name)
        unsourced = m.unsourced_field_names
        tail = f" ({len(unsourced)} unsourced: {', '.join(unsourced)})" if unsourced else ""
        console.print(
            f"[cyan]{name}[/] v{m.version} — {m.title}, {m.geometry}, "
            f"{len(m.fields)} fields + {len(m.provenance)} provenance{tail}"
        )


@model_app.command("show")
def model_show(
    name: Annotated[str, typer.Argument(help="roads or hydrants")],
) -> None:
    """Show a shared model as the audience reads it."""
    m = load_model(name)
    console.print(f"[bold]{m.title}[/] — model [cyan]{m.model_name}[/] v{m.version}")
    console.print(f"geometry {m.geometry}, {m.crs}\n")

    t = Table(show_lines=False, header_style="bold")
    t.add_column("field")
    t.add_column("type")
    t.add_column("null")
    t.add_column("vocabulary / unit")
    t.add_column("sourced")
    for f in m.fields:
        vocab = ", ".join(f.values) if f.values else (f.unit or "")
        t.add_row(
            f.name,
            f.type,
            "yes" if f.nullable else "NO",
            vocab,
            "[green]yes[/]" if f.sourced else "[yellow]no[/]",
        )
    console.print(t)

    p = Table(show_lines=False, header_style="bold", title="provenance")
    p.add_column("field")
    p.add_column("type")
    for pf in m.provenance:
        p.add_row(pf.name, pf.type)
    console.print(p)

    unsourced = m.unsourced_field_names
    if unsourced:
        console.print(
            f"\n[yellow]No locked source publishes:[/] {', '.join(unsourced)} "
            "— the columns ship empty on purpose (see docs/SOURCES.md)."
        )


@model_app.command("prompt")
def model_prompt(name: Annotated[str, typer.Argument(help="roads or hydrants")]) -> None:
    """Print the target schema exactly as the field matcher will see it."""
    console.print(load_model(name).prompt_view())


# --- sources ----------------------------------------------------------------


@source_app.command("list")
def source_list(
    feature: Annotated[str | None, typer.Argument(help="roads or hydrants")] = None,
) -> None:
    """List locked sources, including the rejected ones and why."""
    for feat in [feature] if feature else available_features():
        ss = load_sources(feat)
        console.print(
            f"\n[bold]{ss.feature}[/] — {ss.region} "
            f"(vetted {ss.vetted}, {len(ss.loadable)} loadable, {len(ss.rejected)} rejected)"
        )
        t = Table(header_style="bold")
        t.add_column("id")
        t.add_column("publisher")
        t.add_column("features", justify="right")
        t.add_column("CRS")
        t.add_column("status")
        for s in ss.sources:
            count = s.expected_count
            if s.loadable:
                status = "[green]loadable[/]"
            else:
                status = f"[yellow]{s.reason}[/]"
            t.add_row(
                s.id,
                s.publisher,
                f"{count:,}" if count else "—",
                f"EPSG:{s.expect.crs}" if s.expect.crs else "—",
                status,
            )
        console.print(t)
        console.print(
            f"total locked features: [bold]{ss.total_expected_features:,}[/]"
        )


@source_app.command("show")
def source_show(
    feature: Annotated[str, typer.Argument(help="roads or hydrants")],
    source_id: Annotated[str, typer.Argument(help="e.g. R2")],
) -> None:
    """Show one locked source and what it proves."""
    s = load_sources(feature).get(source_id)
    console.print(f"[bold]{s.id}[/] {s.publisher}  [{s.status}]")
    console.print(f"{s.url}")
    if s.where != "1=1":
        console.print(f"where: [cyan]{s.where}[/]")
    if not s.loadable:
        console.print(f"\n[yellow]rejected — {s.reason}[/]: {s.reason_text}")
        if s.reason_detail:
            console.print(f"  {' '.join(s.reason_detail.split())}")
    if s.interest:
        console.print("\n[bold]why it earns its card[/]")
        for note in s.interest:
            console.print(f"  • {' '.join(note.split())}")


@source_app.command("assess")
def source_assess(
    feature: Annotated[str, typer.Argument(help="roads or hydrants")],
    live: Annotated[bool, typer.Option("--live", help="Go to the network.")] = False,
    include_rejected: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    """Assess every locked source — the loading dock's source cards, on the command line."""
    ss = load_sources(feature)
    picked = ss.sources if include_rejected else ss.loadable

    async def run() -> None:
        async with ArcGISClient(cache=_cache(live)) as client:
            results = await asyncio.gather(
                *(
                    assess_layer(
                        client,
                        s.url,
                        source_id=s.id,
                        publisher=s.publisher,
                        where=s.where,
                    )
                    for s in picked
                ),
                return_exceptions=True,
            )
            t = Table(header_style="bold", title=f"{feature} — sources")
            for col, just in [
                ("id", "left"), ("publisher", "left"), ("geom", "left"),
                ("features", "right"), ("fields", "right"), ("domains", "right"),
                ("CRS", "left"), ("edited", "left"), ("load", "right"), ("status", "left"),
            ]:
                t.add_column(col, justify=just)
            for s, r in zip(picked, results, strict=True):
                if isinstance(r, Exception):
                    t.add_row(s.id, s.publisher, "—", "—", "—", "—", "—", "—", "—",
                              f"[red]{type(r).__name__}[/]")
                    continue
                # A source the vetting run rejected stays rejected on the card even
                # if the service answers today — R6 responds fine and still has
                # nothing to map.
                if not s.loadable:
                    status = f"[yellow]{s.reason}[/]"
                elif r.ok:
                    status = "[green]ready[/]"
                else:
                    status = f"[red]{r.reason}[/]"
                t.add_row(
                    r.source_id, r.publisher, r.geometry or "—",
                    f"{r.count:,}" if r.count else "—",
                    str(r.field_count), str(r.domain_count),
                    f"EPSG:{r.wkid}" if r.wkid else "—",
                    r.last_edit or "—",
                    f"{r.estimated_load_s}s" if r.estimated_load_s else "—",
                    status,
                )
            console.print(t)
            for r in results:
                if isinstance(r, Exception):
                    continue
                for note in r.notes:
                    console.print(f"  [yellow]{r.source_id}[/]: {note}")
            console.print(client.cache.stats.summary())

    asyncio.run(run())


@source_app.command("revet")
def source_revet(
    feature: Annotated[str | None, typer.Argument(help="roads or hydrants")] = None,
) -> None:
    """Re-vet locked sources against the network and report drift.

    Run this the week before any live demo. Always goes live.
    """
    feats = [feature] if feature else available_features()
    exit_code = 0

    async def run() -> int:
        nonlocal exit_code
        async with ArcGISClient(cache=_cache(live=True, refresh=True)) as client:
            for feat in feats:
                ss = load_sources(feat)
                console.print(f"\n[bold]{feat}[/] — locked {ss.vetted}")
                for s in ss.sources:
                    if not s.loadable:
                        console.print(f"  [dim]{s.id} {s.publisher}: skipped ({s.reason})[/]")
                        continue
                    try:
                        a = await assess_layer(
                            client, s.url, source_id=s.id, publisher=s.publisher,
                            where=s.where, measure_throughput=False,
                        )
                    except Exception as exc:  # noqa: BLE001 — report, do not abort the sweep
                        console.print(
                            f"  [red]{s.id} {s.publisher}: {type(exc).__name__}: {exc}[/]"
                        )
                        exit_code = 1
                        continue
                    if not a.ok:
                        console.print(
                            f"  [red]{s.id} {s.publisher}: {a.reason} — {a.reason_detail}[/]"
                        )
                        exit_code = 1
                        continue
                    drift = s.drift(a.model_dump())
                    if drift:
                        console.print(f"  [yellow]{s.id} {s.publisher}: DRIFT[/]")
                        for d in drift:
                            console.print(f"      {d}")
                        exit_code = 1
                    else:
                        console.print(f"  [green]{s.id} {s.publisher}: unchanged[/] ({a.count:,})")
            console.print(f"\n{client.cache.stats.summary()}")
        return exit_code

    raise typer.Exit(asyncio.run(run()))


@source_app.command("profile")
def source_profile(
    feature: Annotated[str, typer.Argument(help="roads or hydrants")],
    source_id: Annotated[str, typer.Argument(help="e.g. R2")],
    live: Annotated[bool, typer.Option("--live")] = False,
    field: Annotated[list[str] | None, typer.Option("--field", "-f")] = None,
) -> None:
    """Profile a source's fields: domains, sample values, population rate.

    The population column is the point. A field can be a perfect name match and
    be 1% populated.
    """
    s = load_sources(feature).get(source_id)

    async def run() -> None:
        async with ArcGISClient(cache=_cache(live)) as client:
            info = await client.layer_info(s.url)
            profiles = await profile_fields(
                client, s.url, info=info, where=s.where,
                sentinels=s.sentinels, only=list(field) if field else None,
            )
            t = Table(header_style="bold", title=f"{s.id} {s.publisher} — field profile")
            t.add_column("field")
            t.add_column("type")
            t.add_column("domain", justify="right")
            t.add_column("populated", justify="right")
            t.add_column("sample values")
            for p in profiles:
                rate = p.population_rate
                pct = f"{100 * rate:.0f}%" if rate is not None else "—"
                if p.is_effectively_empty:
                    pct = f"[red]{pct}[/]"
                samples = ", ".join(str(v) for v in p.sample_values[:8])
                t.add_row(
                    p.name, p.type,
                    str(len(p.coded_values)) if p.coded_values else "—",
                    pct, samples[:70],
                )
            console.print(t)
            console.print(
                f"null sentinels treated as absent: {s.sentinels!r}\n"
                f"{client.cache.stats.summary()}"
            )

    asyncio.run(run())


# --- data -------------------------------------------------------------------


@app.command()
def fetch(
    feature: Annotated[str | None, typer.Argument(help="roads or hydrants (default: both)")] = None,
    source_ids: Annotated[list[str] | None, typer.Argument(help="e.g. R1 R3")] = None,
    live: Annotated[bool, typer.Option("--live", help="Ignore the cache.")] = False,
) -> None:
    """Download the public data: every feature of every loadable source, plus the
    area-of-interest boundary. Writes GeoParquet to data/raw/.

    First run needs the network (a few minutes). After that it replays from cache.
    """
    from .download import boundary_path, download_boundary, download_source, raw_path

    feats = [feature] if feature else available_features()

    async def run() -> int:
        failed = 0
        async with ArcGISClient(cache=_cache(live)) as client:
            for feat in feats:
                ss = load_sources(feat)
                if ss.boundary:
                    path = await download_boundary(client, feat, ss.boundary)
                    console.print(f"[dim]{feat} boundary: {ss.boundary.name} -> {path.name}[/]")
                picked = [ss.get(i) for i in source_ids] if source_ids else ss.loadable
                # One failing source never blocks the rest.
                results = await asyncio.gather(
                    *(download_source(client, feat, s) for s in picked),
                    return_exceptions=True,
                )
                for s, sc in zip(picked, results, strict=True):
                    if isinstance(sc, BaseException):
                        failed += 1
                        console.print(f"  [red]{s.id} {s.publisher}: {type(sc).__name__}: {sc}[/]")
                        continue
                    v = sc["verdict"]
                    colour = "green" if v["outcome"] == "complete" else "yellow"
                    console.print(
                        f"  [{colour}]{s.id} {s.publisher}: {v['outcome']}[/] "
                        f"{v['fetched']:,}/{v['expected']:,} in {v['pages']} pages "
                        f"-> {raw_path(feat, s.id).name}"
                    )
                if ss.boundary and not boundary_path(feat).exists():
                    failed += 1
            console.print(f"\n{client.cache.stats.summary()}")
        return 1 if failed else 0

    raise typer.Exit(asyncio.run(run()))


@app.command()
def serve(
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
    reload: Annotated[bool, typer.Option(help="Reload on code change (development).")] = False,
) -> None:
    """Run the app: API plus the built UI at http://localhost:8000."""
    import uvicorn

    uvicorn.run("wrangle.api:app", host="127.0.0.1", port=port, reload=reload)


# --- cache ------------------------------------------------------------------


@cache_app.command("status")
def cache_status() -> None:
    """What is recorded."""
    c = ResponseCache()
    console.print(f"cache dir: {c.root}")
    console.print(f"mode:      {c.mode.value}")
    console.print(c.describe())


@cache_app.command("warm")
def cache_warm(
    feature: Annotated[str | None, typer.Argument(help="roads or hydrants")] = None,
) -> None:
    """Record the metadata, counts and field profiles the demo replays on stage.

    Run this on good wifi. After it, the loading dock and Match fields work offline.
    """
    feats = [feature] if feature else available_features()

    async def run() -> None:
        cache = _cache(live=True)
        async with ArcGISClient(cache=cache) as client:
            for feat in feats:
                ss = load_sources(feat)
                for s in ss.loadable:
                    console.print(f"  warming {s.id} {s.publisher} …")
                    info = await client.layer_info(s.url)
                    await client.count(s.url, s.where)
                    try:
                        await client.oid_range(s.url, info.oid_field, s.where)
                    except Exception as exc:  # noqa: BLE001
                        console.print(f"    [yellow]oid range unavailable: {exc}[/]")
                    await profile_fields(
                        client, s.url, info=info, where=s.where, sentinels=s.sentinels
                    )
            console.print(f"\n{cache.stats.summary()}")
            console.print(cache.describe())

    asyncio.run(run())


@cache_app.command("basemap")
def cache_basemap(
    feature: Annotated[str | None, typer.Argument(help="roads or hydrants")] = None,
    max_zoom: Annotated[int, typer.Option(help="Deepest zoom to keep (tiles stop at 14)")] = 14,
    max_tiles: Annotated[int, typer.Option(help="Stop a zoom level that needs more tiles")] = 4000,
) -> None:
    """Keep the basemap for each area of interest on disk, so the map draws offline.

    Run this on good wifi, after `wrangle fetch`. A county keeps every zoom to street
    level; a whole state stops where one zoom would need more than --max-tiles.
    """
    import json

    from shapely.geometry import shape

    from . import basemap
    from .download import boundary_path

    feats = [feature] if feature else available_features()
    bboxes = []
    for feat in feats:
        p = boundary_path(feat)
        if not p.exists():
            console.print(f"[yellow]{feat}: no area of interest yet — run `wrangle fetch`[/]")
            continue
        fc = json.loads(p.read_text(encoding="utf-8"))
        bboxes.append(shape(fc["features"][0]["geometry"]).bounds)
        console.print(f"  {feat}: {p.name}")

    def progress(done: int, total: int) -> None:
        if done % 250 == 0 or done == total:
            console.print(f"  {done:,} / {total:,} files")

    try:
        counts = asyncio.run(basemap.prefetch(bboxes, max_zoom, max_tiles, progress))
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from None
    missing = f", {counts['missing']:,} not available" if counts["missing"] else ""
    console.print(
        f"basemap: {counts['files']:,} files{missing}; "
        f"{counts['bytes'] / 1e6:.0f} MB in {basemap.root()}"
    )


if __name__ == "__main__":
    app()
