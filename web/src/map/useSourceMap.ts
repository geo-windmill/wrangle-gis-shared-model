// One MapLibre map showing agency layers over an area-of-interest outline.
// Shared by the loading dock and Convert: the caller owns the layer
// list (order, visibility); this hook keeps the map in step with it.
import type { Geometry } from "geojson";
import * as maplibregl from "maplibre-gl";
import type { ExpressionSpecification, Map as MLMap, StyleSpecification } from "maplibre-gl";
// MapLibre 6 runs its parser in a module worker shipped as a separate file; let Vite bundle it.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SourceState } from "../api";
import { BOUNDARY_COLOR, HIGHLIGHT_COLOR, PICK_COLOR, SOURCE_DASH, useColorOf, type MapClass } from "../colors";

maplibregl.setWorkerUrl(workerUrl);

// Free, keyless vector basemap (OpenStreetMap data). Every request for it goes through
// `wrangle serve`, which keeps a copy on disk (`wrangle cache basemap` fills it ahead of
// time), so it draws with the wifi off. With no copy and no wifi the map falls back to a
// plain background: every agency layer still draws from local data.
const BASEMAP_URL = "https://tiles.openfreemap.org/styles/dark";
const BASEMAP_HOST = "https://tiles.openfreemap.org/";
const viaServer = (url: string) =>
  url.startsWith(BASEMAP_HOST) ? `${window.location.origin}/api/basemap/${url.slice(BASEMAP_HOST.length)}` : url;
const BLANK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#141413" } }],
};

export type BBox = [number, number, number, number];

type Options = {
  feature: string;
  sources: SourceState[]; // every source that may be drawn, in registry order
  order: string[]; // source ids on the map; first draws on top
  hidden: Set<string>;
  showBoundary?: boolean;
  basemap: boolean;
  // A click on the map: where it landed, and how many metres a few pixels cover
  // at this zoom — the tolerance for "the feature under the pointer".
  onClickPoint: (lng: number, lat: number, toleranceM: number) => void;
  // Where a layer's GeoJSON comes from (default: the source as published). When the
  // URL changes — a re-conversion — the layer reloads in place.
  dataUrl?: (id: string) => string;
  // Colour every layer by a feature property `v` instead of by source.
  classes?: MapClass[];
};

export function useSourceMap({ feature, sources, order, hidden, showBoundary = true, basemap, onClickPoint, dataUrl, classes }: Options) {
  const urls = useRef<Record<string, string>>({});
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const basemapLayers = useRef<string[]>([]);
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const [ready, setReady] = useState(false);
  const [boundaryBox, setBoundaryBox] = useState<BBox | null>(null);
  // Sources whose GeoJSON has arrived and been parsed. Until then a row says "loading".
  const [drawn, setDrawn] = useState<Set<string>>(new Set());

  // Colour follows the source's position in the registry, never its list position.
  const colorIndex = useCallback((id: string) => Math.max(0, sources.findIndex((s) => s.id === id)), [sources]);
  const colorOf = useColorOf(feature, sources);

  // --- create the map ---------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [style, boundary] = await Promise.all([
        loadBasemap(),
        fetch(api.boundaryUrl(feature)).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
      if (cancelled || !container.current) return;
      const m = new maplibregl.Map({
        container: container.current,
        style,
        center: [-121.5, 38.5],
        zoom: 8,
        attributionControl: { compact: false },
        transformRequest: (url) => ({ url: viaServer(url) }),
      });
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      m.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-right");
      m.on("sourcedata", (e) => {
        if (e.sourceId?.startsWith("src-") && e.isSourceLoaded) {
          const id = e.sourceId.slice(4);
          setDrawn((d) => (d.has(id) ? d : new Set(d).add(id)));
        }
      });
      // Set up as soon as the style is parsed ("style.load"). MapLibre's "load",
      // and isStyleLoaded(), also wait for every basemap tile, which on
      // conference wifi takes seconds the agency layers and the outline don't need.
      let set = false;
      const setUp = () => {
        if (set) return;
        set = true;
        basemapLayers.current = m.getStyle().layers.map((l) => l.id);
        if (boundary) {
          m.addSource("boundary", { type: "geojson", data: boundary, attribution: boundary.features[0].properties.attribution });
          m.addLayer({ id: "boundary-fill", type: "fill", source: "boundary", paint: { "fill-color": BOUNDARY_COLOR, "fill-opacity": 0.04 } });
          m.addLayer({
            id: "boundary-line",
            type: "line",
            source: "boundary",
            paint: { "line-color": BOUNDARY_COLOR, "line-width": 2, "line-opacity": 0.7, "line-dasharray": [4, 2] },
          });
          const box = bboxOf(boundary);
          setBoundaryBox(box);
          m.fitBounds(box, { padding: 40, duration: 0 });
        }
        // what a click picked, outlined on top of everything (see `outline`)
        m.addSource("pick", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        m.addLayer({ id: "pick-line", type: "line", source: "pick", paint: { "line-color": PICK_COLOR, "line-width": 6, "line-opacity": 0.85 } });
        setReady(true);
      };
      m.once("style.load", setUp);
      m.once("load", setUp); // belt and braces
      // MapLibre re-measures on window resize only; a panel opening beside or
      // below the map changes its size too.
      const ro = new ResizeObserver(() => m.resize());
      ro.observe(container.current);
      m.once("remove", () => ro.disconnect());
      map.current = m;
    })();
    return () => {
      cancelled = true;
      map.current?.remove();
      map.current = null;
      setReady(false);
      setDrawn(new Set());
    };
  }, [feature]);

  // --- add each layer the first time it is asked for -------------------------
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    for (const id of order) {
      const s = sources.find((x) => x.id === id);
      if (!s || !(s.status === "ready" || s.status === "partial")) continue;
      const url = dataUrl ? dataUrl(id) : api.geojsonUrl(feature, id);
      const existing = m.getSource(`src-${id}`) as maplibregl.GeoJSONSource | undefined;
      if (existing) {
        if (urls.current[id] !== url) existing.setData(url);
        urls.current[id] = url;
        continue;
      }
      urls.current[id] = url;
      const i = colorIndex(id);
      const color = classes ? classColor(classes) : colorOf(id);
      m.addSource(`src-${id}`, { type: "geojson", data: url, attribution: s.attribution });
      if (s.geometry === "point") {
        m.addLayer({
          id: `lyr-${id}`,
          type: "circle",
          source: `src-${id}`,
          paint: {
            "circle-color": color,
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 1.5, 12, 3.5, 16, 6],
            "circle-stroke-color": "#141413",
            "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9, 0, 13, 1],
          },
        });
        m.addLayer({
          id: `hl-${id}`,
          type: "circle",
          source: `src-${id}`,
          filter: ["==", ["id"], -1],
          paint: { "circle-color": "transparent", "circle-radius": 10, "circle-stroke-color": HIGHLIGHT_COLOR, "circle-stroke-width": 3 },
        });
      } else {
        const dash = classes ? null : SOURCE_DASH[i % SOURCE_DASH.length];
        const w = classes ? classWidth(classes) : 1;
        m.addLayer({
          id: `lyr-${id}`,
          type: "line",
          source: `src-${id}`,
          // The widest class draws on top: highways over the local streets they cross.
          layout: { "line-cap": dash ? "butt" : "round", "line-join": "round", ...(classes ? { "line-sort-key": w } : {}) },
          paint: {
            "line-color": color,
            "line-width": ["interpolate", ["linear"], ["zoom"], 8, ["*", 0.8, w], 12, ["*", 1.8, w], 16, ["*", 3.5, w]],
            ...(dash ? { "line-dasharray": dash } : {}),
          },
        });
        m.addLayer({
          id: `hl-${id}`,
          type: "line",
          source: `src-${id}`,
          filter: ["==", ["id"], -1],
          paint: { "line-color": HIGHLIGHT_COLOR, "line-width": 6, "line-opacity": 0.9 },
        });
      }
    }
  }, [order, sources, ready, feature, colorIndex, colorOf, dataUrl, classes]);

  // --- a colour picked in the layer list repaints that layer at once ---------------
  useEffect(() => {
    const m = map.current;
    if (!m || !ready || classes) return;
    for (const s of sources) {
      const lyr = m.getLayer(`lyr-${s.id}`);
      if (!lyr) continue;
      m.setPaintProperty(`lyr-${s.id}`, lyr.type === "circle" ? "circle-color" : "line-color", colorOf(s.id));
    }
  }, [colorOf, sources, ready, classes]);

  // --- visibility and paint order follow the layer list ----------------------
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    for (const s of sources) {
      const show = order.includes(s.id) && !hidden.has(s.id);
      for (const prefix of ["lyr", "hl"]) {
        if (m.getLayer(`${prefix}-${s.id}`)) m.setLayoutProperty(`${prefix}-${s.id}`, "visibility", show ? "visible" : "none");
      }
    }
    for (const id of [...order].reverse()) if (m.getLayer(`lyr-${id}`)) m.moveLayer(`lyr-${id}`);
    for (const id of order) if (m.getLayer(`hl-${id}`)) m.moveLayer(`hl-${id}`);
  }, [order, hidden, sources, ready, drawn]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    for (const id of ["boundary-fill", "boundary-line"]) {
      if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", showBoundary ? "visible" : "none");
    }
  }, [showBoundary, ready]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    for (const id of basemapLayers.current) {
      if (id !== "background" && m.getLayer(id)) m.setLayoutProperty(id, "visibility", basemap ? "visible" : "none");
    }
  }, [basemap, ready]);

  // --- click to pick, pointer on hover ----------------------------------------
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    // Thin lines are unclickable without a little slack around the pointer.
    const box = (p: maplibregl.Point): [maplibregl.PointLike, maplibregl.PointLike] => [
      [p.x - 5, p.y - 5],
      [p.x + 5, p.y + 5],
    ];
    const layers = () => order.filter((id) => !hidden.has(id)).map((id) => `lyr-${id}`).filter((id) => m.getLayer(id));
    const onClick = (e: maplibregl.MapMouseEvent) => {
      // Metres per pixel at this latitude and zoom (MapLibre tiles are 512 px).
      const mpp = (40075016.686 * Math.cos((e.lngLat.lat * Math.PI) / 180)) / (512 * 2 ** m.getZoom());
      // A few pixels of slack, but never so much that "the road you clicked"
      // becomes "some road nearby" when zoomed out.
      onClickPoint(e.lngLat.lng, e.lngLat.lat, Math.min(400, Math.max(5, 6 * mpp)));
    };
    const onMove = (e: maplibregl.MapMouseEvent) => {
      m.getCanvas().style.cursor = m.queryRenderedFeatures(box(e.point), { layers: layers() }).length ? "pointer" : "";
    };
    m.on("click", onClick);
    m.on("mousemove", onMove);
    return () => {
      m.off("click", onClick);
      m.off("mousemove", onMove);
    };
  }, [order, hidden, ready, onClickPoint]);

  // --- imperative helpers -----------------------------------------------------
  /** Outline one feature per source; sources left out are cleared. */
  const highlight = useCallback(
    (oids: Record<string, number>) => {
      const m = map.current;
      if (!m) return;
      for (const s of sources) {
        if (m.getLayer(`hl-${s.id}`)) m.setFilter(`hl-${s.id}`, ["==", ["id"], oids[s.id] ?? -1]);
      }
    },
    [sources],
  );

  const fitTo = useCallback((box: BBox, maxZoom = 17) => {
    const m = map.current;
    if (!m) return;
    // A fixed margin swallows a small map; keep it to a few percent of the view.
    const { clientWidth: w, clientHeight: h } = m.getContainer();
    const padding = Math.round(Math.max(12, Math.min(60, Math.min(w, h) * 0.06)));
    m.fitBounds(box, { padding, maxZoom, duration: 600 });
  }, []);

  /** Outline what a click picked, above every layer; an empty list clears it. */
  const outline = useCallback((geometries: Geometry[]) => {
    const m = map.current;
    const src = m?.getSource("pick") as maplibregl.GeoJSONSource | undefined;
    if (!m || !src) return;
    src.setData({ type: "FeatureCollection", features: geometries.map((g) => ({ type: "Feature", properties: {}, geometry: g })) });
    m.moveLayer("pick-line");
  }, []);

  /** A popup at a point (null closes it). Closing it clears the outline too. */
  const popup = useCallback(
    (lng: number, lat: number, content: HTMLElement | null) => {
      const m = map.current;
      popupRef.current?.remove();
      popupRef.current = null;
      if (!m || !content) return;
      const p = new maplibregl.Popup({ maxWidth: "380px", closeOnClick: false }).setLngLat([lng, lat]).setDOMContent(content).addTo(m);
      p.on("close", () => {
        if (popupRef.current === p) outline([]);
      });
      popupRef.current = p;
    },
    [outline],
  );

  return { container, ready, drawn, boundaryBox, colorIndex, colorOf, highlight, fitTo, outline, popup };
}

/** A data expression picking each feature's class colour from its `v` property. */
function classMatch(classes: MapClass[], pick: (c: MapClass) => string | number, fallback: string | number): ExpressionSpecification {
  const pairs = classes.flatMap((c) => c.values.filter((v): v is string => v !== null).flatMap((v) => [v, pick(c)]));
  const unknown = classes.find((c) => c.values.includes(null));
  const other = unknown ? pick(unknown) : fallback;
  // A "match" needs at least one value to match; one class for everything is just its colour.
  if (!pairs.length) return other as unknown as ExpressionSpecification;
  return ["match", ["coalesce", ["get", "v"], ""], ...pairs, other] as unknown as ExpressionSpecification;
}
const classColor = (classes: MapClass[]) => classMatch(classes, (c) => c.color, "#4a4945");
const classWidth = (classes: MapClass[]) => classMatch(classes, (c) => c.width ?? 1, 1);

export function unionBox(boxes: (BBox | null | undefined)[]): BBox | null {
  const real = boxes.filter((b): b is BBox => !!b);
  if (!real.length) return null;
  return [
    Math.min(...real.map((b) => b[0])),
    Math.min(...real.map((b) => b[1])),
    Math.max(...real.map((b) => b[2])),
    Math.max(...real.map((b) => b[3])),
  ];
}

async function loadBasemap(): Promise<StyleSpecification> {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 3000);
    const r = await fetch(viaServer(BASEMAP_URL), { signal: ctl.signal });
    clearTimeout(t);
    if (r.ok) return (await r.json()) as StyleSpecification;
  } catch {
    /* offline: plain background */
  }
  return BLANK_STYLE;
}

function bboxOf(fc: { features: { geometry: { coordinates: unknown } }[] }): BBox {
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  const walk = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number") {
      const [x, y] = c as number[];
      x0 = Math.min(x0, x);
      y0 = Math.min(y0, y);
      x1 = Math.max(x1, x);
      y1 = Math.max(y1, y);
    } else if (Array.isArray(c)) c.forEach(walk);
  };
  for (const f of fc.features) walk(f.geometry.coordinates);
  return [x0, y0, x1, y1];
}
