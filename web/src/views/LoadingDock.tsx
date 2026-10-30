// The loading dock — find sources, look at them, and queue them for ingest, on one screen.
//
//   ┌──────────┬──────────────────────────────┬────────────┐
//   │ Layers   │  Map                         │ Ingest     │
//   │          ├──────────────────────────────┤ queue      │
//   │          │  Table (a tab per layer)     │            │
//   └──────────┴──────────────────────────────┴────────────┘
//
// Layers come only from the sources vetted in docs/SOURCES.md: search them, paste
// one's URL, or browse the list. Look at each against the area of interest, read
// its warnings, and queue the ones worth ingesting. Every pane can be resized.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Card, type Checks, type Compare, type Feature, type FeatureDetail, type SourceState } from "../api";
import { BOUNDARY_COLOR, SOURCE_DASH, setPickedColor } from "../colors";
import { AttributePanel } from "../components/AttributePanel";
import { Eye, IconBtn, Splitter, Swatch } from "../components/bits";
import { ComparePanel } from "../components/ComparePanel";
import { CrosswalkGrid } from "../components/CrosswalkGrid";
import { StatusChip } from "../components/StatusChip";
import { TableDrawer } from "../components/TableDrawer";
import { unionBox, useSourceMap, type BBox } from "../map/useSourceMap";

type Picked = { sourceId: string; detail: FeatureDetail };
type RowStatus = "downloading" | "loading" | "ready" | "partial" | "error";

type Props = {
  feature: Feature;
  queue: string[]; // owned by the app: Match fields reads it
  setQueue: (fn: (q: string[]) => string[]) => void;
  onNext: () => void;
};

export default function LoadingDock({ feature, queue, setQueue, onNext }: Props) {
  const f = feature.feature;
  const [cards, setCards] = useState<Card[]>([]);
  const [sources, setSources] = useState<SourceState[]>([]);
  const [dock, setDock] = useState<string[]>([]); // layers on the map; first draws on top
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [checks, setChecks] = useState<Record<string, Checks>>({});
  const [activeTab, setActiveTab] = useState<string | null>(null); // whose table shows
  const [picked, setPicked] = useState<Picked | null>(null); // one feature, from a table row
  const [compare, setCompare] = useState<Compare | null>(null); // every source, from a map click
  const [drawerMode, setDrawerMode] = useState<"layers" | "crosswalk">("layers");
  const [dialog, setDialog] = useState<{ filter: string; preselect: string[] } | null>(null);
  const [basemap, setBasemap] = useState(true);
  const [showBoundary, setShowBoundary] = useState(true);
  const sizes = usePaneSizes();

  // --- data --------------------------------------------------------------------
  useEffect(() => {
    api.catalog(f).then(setCards).catch(console.error);
  }, [f]);
  const refresh = useCallback(() => api.sources(f).then(setSources), [f]);
  useEffect(() => {
    refresh();
  }, [refresh]);
  useEffect(() => {
    if (!sources.some((s) => s.status === "fetching")) return;
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [sources, refresh]);

  useEffect(() => {
    for (const id of dock) {
      const s = sources.find((x) => x.id === id);
      if (checks[id] || !s || !(s.status === "ready" || s.status === "partial")) continue;
      api.checks(f, id).then((c) => setChecks((m) => ({ ...m, [id]: c })));
    }
  }, [dock, sources, checks, f]);

  // --- map -----------------------------------------------------------------------
  // The map's helpers and the visible layers change as data loads; handlers read the latest.
  const mapRef = useRef<ReturnType<typeof useSourceMap> | null>(null);
  const visibleRef = useRef<string[]>([]);
  const pick = useCallback(
    async (sourceId: string, oid: number, zoom = false) => {
      const detail = await api.feature(f, sourceId, oid);
      setCompare(null);
      setPicked({ sourceId, detail });
      mapRef.current?.highlight({ [sourceId]: oid });
      if (zoom) mapRef.current?.fitTo(detail.bbox);
    },
    [f],
  );
  // A map click asks every visible layer for its feature at that spot.
  const clickPoint = useCallback(
    async (lng: number, lat: number, toleranceM: number) => {
      const ids = visibleRef.current;
      if (!ids.length) return;
      const data = await api.compare(f, lng, lat, ids, toleranceM);
      const hits = data.sources.filter((s) => s.hit);
      setPicked(null);
      setCompare(hits.length ? data : null);
      mapRef.current?.highlight(Object.fromEntries(hits.map((s) => [s.id, s.hit!.oid])));
    },
    [f],
  );
  const map = useSourceMap({ feature: f, sources, order: dock, hidden, showBoundary, basemap, onClickPoint: clickPoint });
  mapRef.current = map;
  visibleRef.current = dock.filter((id) => !hidden.has(id) && map.drawn.has(id));
  const colorOf = map.colorOf;
  const byId = useMemo(() => Object.fromEntries(sources.map((s) => [s.id, s])), [sources]);

  const statusOf = (id: string): RowStatus => {
    const s = byId[id];
    if (!s || s.status === "error") return "error";
    if (s.status === "fetching" || s.status === "missing") return "downloading";
    if (!map.drawn.has(id)) return "loading";
    return s.status;
  };

  // Zoom out as each new layer finishes drawing, so the view grows with the data
  // instead of waiting for the slowest one — small layers frame first, big ones widen it.
  const framing = useRef<Set<string>>(new Set());
  useEffect(() => {
    const arrived = [...framing.current].filter((id) => map.drawn.has(id));
    if (!arrived.length) return;
    arrived.forEach((id) => framing.current.delete(id));
    const visible = dock.filter((id) => !hidden.has(id) && map.drawn.has(id));
    const box = unionBox(visible.map((id) => byId[id]?.bbox as BBox | null));
    if (box) map.fitTo(box, 14);
  }, [map.drawn, dock, hidden, byId, map]);

  // --- actions ---------------------------------------------------------------------
  const load = async (ids: string[]) => {
    const fresh = ids.filter((id) => !dock.includes(id));
    if (!fresh.length) return;
    for (const id of fresh) if (byId[id]?.status === "missing") await api.fetchSource(f, id).catch(console.error);
    fresh.forEach((id) => framing.current.add(id));
    setDock((d) => [...fresh, ...d]);
    setActiveTab(fresh[0]);
    refresh();
  };
  const remove = (id: string) => {
    setDock((d) => d.filter((x) => x !== id));
    setQueue((q) => q.filter((x) => x !== id));
    setActiveTab((a) => (a === id ? null : a));
    if (picked?.sourceId === id) setPicked(null);
    setCompare(null);
  };
  const removeAll = () => {
    setDock([]);
    setQueue(() => []);
    setActiveTab(null);
    setHidden(new Set());
    setPicked(null);
    setCompare(null);
    map.highlight({});
  };
  const move = (id: string, dir: -1 | 1) =>
    setDock((o) => {
      const i = o.indexOf(id);
      const j = i + dir;
      if (j < 0 || j >= o.length) return o;
      const next = [...o];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  const toggle = (id: string) =>
    setHidden((h) => {
      const n = new Set(h);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <div className="flex h-full">
      {/* ---------------- left: add layers + the layer list ---------------- */}
      <aside className="flex shrink-0 flex-col bg-panel" style={{ width: sizes.left }}>
        <div className="space-y-2 border-b border-line p-4">
          <AddLayer cards={cards} onOpen={(filter, preselect) => setDialog({ filter, preselect })} />
          <button onClick={() => setDialog({ filter: "", preselect: [] })} className="text-sm text-ink-2 hover:text-ink">
            Browse all {cards.length} sources →
          </button>
        </div>

        {dock.length > 0 && (
          <div className="flex items-center justify-between px-4 pt-2.5 pb-1 text-sm text-ink-3">
            <span className="tabular">{dock.length} on map</span>
            <button onClick={removeAll} className="hover:text-ink">
              Remove all
            </button>
          </div>
        )}
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {dock.map((id, i) => {
            const s = byId[id];
            if (!s) return null;
            const ci = map.colorIndex(id);
            return (
              <LayerRow
                key={id}
                source={s}
                status={statusOf(id)}
                checks={checks[id]}
                color={map.colorOf(id)}
                onColor={(c) => setPickedColor(f, id, c)}
                dash={SOURCE_DASH[ci % SOURCE_DASH.length]}
                hidden={hidden.has(id)}
                queued={queue.includes(id)}
                first={i === 0}
                last={i === dock.length - 1}
                onToggle={() => toggle(id)}
                onZoom={() => s.bbox && map.fitTo(s.bbox as BBox, 15)}
                onTable={() => {
                  setActiveTab(id);
                  setDrawerMode("layers");
                  sizes.setDrawerOpen(true);
                }}
                onUp={() => move(id, -1)}
                onDown={() => move(id, 1)}
                onQueue={() => setQueue((q) => (q.includes(id) ? q.filter((x) => x !== id) : [...q, id]))}
                onRemove={() => remove(id)}
              />
            );
          })}
          {/* the area of interest stays pinned: every layer is judged against it */}
          <li className="flex items-center gap-3 border-b border-line px-4 py-2.5">
            <button onClick={() => setShowBoundary(!showBoundary)} className="text-ink-2 hover:text-ink" aria-label={showBoundary ? "Hide area" : "Show area"}>
              <Eye open={showBoundary} />
            </button>
            <Swatch color={BOUNDARY_COLOR} dash={[4, 2]} dim={!showBoundary} />
            <span className="min-w-0 flex-1 truncate text-ink-2">{feature.boundary}</span>
            {map.boundaryBox && <IconBtn label="⤢" title="Zoom to area" onClick={() => map.fitTo(map.boundaryBox!, 12)} />}
          </li>
          <li className="flex items-center gap-3 px-4 py-2.5">
            <button onClick={() => setBasemap(!basemap)} className="text-ink-2 hover:text-ink" aria-label={basemap ? "Hide basemap" : "Show basemap"}>
              <Eye open={basemap} />
            </button>
            <span className="text-ink-3">Basemap</span>
          </li>
        </ul>
      </aside>
      <Splitter axis="x" onDrag={(d) => sizes.setLeft((w) => w + d)} />

      {/* ---------------- centre: map + table ---------------- */}
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="relative min-h-0 flex-1">
          {/* MapLibre forces position:relative on its container, so size it, don't inset it. */}
          <div ref={map.container} className="h-full w-full" />
          {compare && (
            <ComparePanel
              data={compare}
              colorOf={colorOf}
              onClose={() => {
                setCompare(null);
                map.highlight({});
              }}
            />
          )}
          {picked && (
            <AttributePanel
              sourceId={picked.sourceId}
              publisher={byId[picked.sourceId]?.publisher ?? ""}
              color={colorOf(picked.sourceId)}
              detail={picked.detail}
              onClose={() => {
                setPicked(null);
                map.highlight({});
              }}
            />
          )}
        </div>
        {dock.length > 0 && sizes.drawerOpen && !sizes.drawerFull && <Splitter axis="y" onDrag={(d) => sizes.setDrawer((h) => h - d)} />}
        {dock.length > 0 && (
        <TableDrawer
          feature={f}
          layers={dock.map((id) => byId[id]).filter(Boolean)}
          active={activeTab}
          onActivate={setActiveTab}
          colorOf={colorOf}
          pickedOid={picked?.sourceId === activeTab ? picked.detail.oid : null}
          onPick={(sid, oid) => pick(sid, oid, true)}
          height={sizes.drawer}
          open={sizes.drawerOpen}
          onToggle={() => sizes.setDrawerOpen(!sizes.drawerOpen)}
          full={sizes.drawerFull}
          onFull={() => sizes.setDrawerFull(!sizes.drawerFull)}
          mode={drawerMode}
          onMode={setDrawerMode}
          crosswalk={<CrosswalkGrid feature={f} ids={dock.filter((id) => map.drawn.has(id))} colorOf={colorOf} />}
        />
        )}
      </section>

      <Splitter axis="x" onDrag={(d) => sizes.setRight((w) => w - d)} />
      <IngestQueue width={sizes.right} onNext={onNext} queue={queue} byId={byId} checks={checks} colorOf={colorOf} onRemove={(id) => setQueue((q) => q.filter((x) => x !== id))} />

      {dialog && (
        <SourcesDialog
          cards={cards}
          onMap={dock}
          initialFilter={dialog.filter}
          preselect={dialog.preselect}
          colorOf={colorOf}
          onClose={() => setDialog(null)}
          onLoad={(ids) => {
            setDialog(null);
            load(ids);
          }}
        />
      )}
    </div>
  );
}

// --- pane sizes, remembered per browser ------------------------------------------------

function usePaneSizes() {
  const saved = useMemo(() => {
    try {
      return JSON.parse(localStorage.getItem("wrangle.panes") ?? "{}");
    } catch {
      return {};
    }
  }, []);
  const [left, setLeftRaw] = useState<number>(saved.left ?? 340);
  const [right, setRightRaw] = useState<number>(saved.right ?? 300);
  const [drawer, setDrawerRaw] = useState<number>(saved.drawer ?? 280);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [drawerFull, setDrawerFull] = useState(false);
  const clamp = (lo: number, hi: number) => (v: number) => Math.min(hi, Math.max(lo, v));
  const setLeft = (fn: (w: number) => number) => setLeftRaw((w) => clamp(240, 640)(fn(w)));
  const setRight = (fn: (w: number) => number) => setRightRaw((w) => clamp(220, 560)(fn(w)));
  const setDrawer = (fn: (h: number) => number) => setDrawerRaw((h) => clamp(120, 700)(fn(h)));

  useEffect(() => {
    try {
      localStorage.setItem("wrangle.panes", JSON.stringify({ left, right, drawer }));
    } catch {
      /* private window: sizes just aren't remembered */
    }
  }, [left, right, drawer]);

  return { left, right, drawer, drawerOpen, drawerFull, setLeft, setRight, setDrawer, setDrawerOpen, setDrawerFull };
}

// --- one row in the layer list ------------------------------------------------------------

function LayerRow(props: {
  source: SourceState;
  status: RowStatus;
  checks?: Checks;
  color: string;
  onColor: (color: string) => void;
  dash: number[] | null;
  hidden: boolean;
  queued: boolean;
  first: boolean;
  last: boolean;
  onToggle: () => void;
  onZoom: () => void;
  onTable: () => void;
  onUp: () => void;
  onDown: () => void;
  onQueue: () => void;
  onRemove: () => void;
}) {
  const { source: s, status, checks, hidden } = props;
  const [open, setOpen] = useState(false);
  const serious = checks?.warnings.filter((w) => w.level !== "info").length ?? 0;
  const info = checks?.warnings.filter((w) => w.level === "info").length ?? 0;
  const busy = status === "downloading" || status === "loading";
  const usable = status === "ready" || status === "partial";

  return (
    <li className="border-b border-line px-4 py-2.5">
      <div className="flex items-center gap-3">
        <button onClick={props.onToggle} className="text-ink-2 hover:text-ink" aria-label={hidden ? "Show layer" : "Hide layer"}>
          <Eye open={!hidden} />
        </button>
        <label className="relative cursor-pointer" title="Change colour">
          <Swatch color={props.color} dash={props.dash} point={s.geometry === "point"} dim={hidden} />
          <input
            type="color"
            value={props.color}
            onChange={(e) => props.onColor(e.target.value)}
            className="absolute inset-0 cursor-pointer opacity-0"
            aria-label={`Colour of ${s.publisher}`}
          />
        </label>
        <div className="min-w-0 flex-1">
          <div className={`truncate font-medium ${hidden ? "text-ink-3" : ""}`}>{s.publisher}</div>
          <div className="tabular text-sm text-ink-3">
            {busy ? `${status}…` : status === "error" ? "failed" : `${s.rows?.toLocaleString()} features`}
          </div>
        </div>
        {(serious > 0 || info > 0) && (
          <button
            onClick={() => setOpen(!open)}
            className={`tabular rounded-full border px-2 py-0.5 text-sm ${serious ? "border-warning/50 bg-warning/10 text-warning" : "border-line text-ink-3"}`}
            aria-label="Warnings"
          >
            {serious ? `▲ ${serious}` : `i ${info}`}
          </button>
        )}
        {status === "partial" && <StatusChip tone="warning" label="partial" />}
        {status === "error" && <StatusChip tone="critical" label="error" />}
      </div>
      {open && checks && (
        <ul className="mt-2 space-y-1 rounded-md bg-surface/60 px-3 py-2 text-sm">
          {checks.warnings.map((w) => (
            <li key={w.text} className={`flex gap-2 ${w.level === "info" ? "text-ink-2" : w.level === "critical" ? "text-critical" : "text-warning"}`}>
              <span aria-hidden>{w.level === "info" ? "i" : "▲"}</span>
              <span>{w.text}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2 flex items-center gap-1 text-sm text-ink-3">
        <IconBtn label="⤢" title="Zoom to layer" onClick={props.onZoom} />
        <IconBtn label="Table" onClick={props.onTable} />
        <IconBtn label="↑" title="Draw above" onClick={props.onUp} disabled={props.first} />
        <IconBtn label="↓" title="Draw below" onClick={props.onDown} disabled={props.last} />
        <IconBtn label="×" title="Remove" onClick={props.onRemove} />
        <button
          onClick={props.onQueue}
          disabled={!usable}
          className={`ml-auto rounded border px-2 py-0.5 disabled:opacity-30 ${
            props.queued ? "border-good/60 text-good" : "border-line text-ink-2 hover:bg-surface hover:text-ink"
          }`}
        >
          {props.queued ? "✓ Queued" : "+ Queue"}
        </button>
      </div>
    </li>
  );
}

// --- adding layers: one box for a search or a URL --------------------------------------------

function AddLayer({ cards, onOpen }: { cards: Card[]; onOpen: (filter: string, preselect: string[]) => void }) {
  const [q, setQ] = useState("");
  const [focus, setFocus] = useState(false);
  const [note, setNote] = useState<{ tone: "critical" | "muted"; text: string } | null>(null);
  const isUrl = /^https?:\/\//i.test(q.trim());
  const hits = q.trim() && !isUrl ? cards.filter((c) => `${c.publisher} ${c.id} ${hostOf(c.url)}`.toLowerCase().includes(q.trim().toLowerCase())) : [];

  const submitUrl = () => {
    const want = normalise(q);
    // A layer URL matches exactly; a service URL (no layer id) matches its layers.
    const hit = cards.find((c) => normalise(c.url) === want) ?? cards.find((c) => normalise(c.url).startsWith(want + "/"));
    if (!hit) return setNote({ tone: "muted", text: "Not a vetted source — this demo only loads those." });
    if (!hit.loadable) return setNote({ tone: "critical", text: `${hit.publisher}: ${hit.reason_text}` });
    setNote(null);
    setQ("");
    onOpen("", [hit.id]);
  };

  return (
    <div className="relative">
      <input
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setNote(null);
        }}
        onKeyDown={(e) => e.key === "Enter" && isUrl && submitUrl()}
        onFocus={() => setFocus(true)}
        onBlur={() => setTimeout(() => setFocus(false), 150)}
        placeholder="Search publishers or paste a URL"
        aria-label="Search publishers or paste a service URL"
        className="w-full rounded-md border border-line bg-surface px-3 py-2 placeholder:text-ink-3 focus:border-ink-3 focus:outline-none"
      />
      {isUrl && !note && <div className="mt-1 text-sm text-ink-3">Press Enter to check this URL.</div>}
      {note && <div className={`mt-2 text-sm ${note.tone === "critical" ? "text-critical" : "text-ink-2"}`}>{note.text}</div>}
      {focus && hits.length > 0 && (
        <ul className="absolute top-full right-0 left-0 z-20 mt-1 overflow-hidden rounded-md border border-line bg-raised shadow-xl">
          {hits.map((c) => (
            <li key={c.id}>
              <button
                onMouseDown={() => {
                  onOpen(c.publisher, c.loadable ? [c.id] : []);
                  setQ("");
                }}
                className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-surface"
              >
                <span className="flex-1 truncate">{c.publisher}</span>
                <span className={`tabular text-sm ${c.loadable ? "text-ink-3" : "text-serious"}`}>
                  {c.loadable ? c.assessment?.count?.toLocaleString() : "rejected"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --- every vetted source, one row each ------------------------------------------------------

function SourcesDialog(props: {
  cards: Card[];
  onMap: string[];
  initialFilter: string;
  preselect: string[];
  colorOf: (id: string) => string;
  onClose: () => void;
  onLoad: (ids: string[]) => void;
}) {
  const { cards, onMap, onClose } = props;
  const [filter, setFilter] = useState(props.initialFilter);
  const [picked, setPicked] = useState<string[]>(props.preselect.filter((id) => !onMap.includes(id)));
  const [openRow, setOpenRow] = useState<string | null>(null);
  const rows = cards.filter((c) => `${c.publisher} ${c.id} ${hostOf(c.url)}`.toLowerCase().includes(filter.trim().toLowerCase()));
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  // "Select all" covers the rows the filter shows that can still be added.
  const addable = rows.filter((c) => c.loadable && !onMap.includes(c.id)).map((c) => c.id);
  const allPicked = addable.length > 0 && addable.every((id) => picked.includes(id));
  const toggleAll = () =>
    setPicked((p) => (allPicked ? p.filter((id) => !addable.includes(id)) : [...new Set([...p, ...addable])]));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 p-6" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-label="Sources"
        className="flex max-h-full w-full max-w-[1150px] flex-col rounded-xl border border-line bg-panel shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-4 border-b border-line px-5 py-4">
          <div className="text-xl font-semibold">Sources</div>
          <input
            autoFocus
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter"
            className="ml-auto w-64 rounded-md border border-line bg-surface px-3 py-2 placeholder:text-ink-3 focus:border-ink-3 focus:outline-none"
          />
          <button onClick={onClose} className="px-2 text-2xl text-ink-3 hover:text-ink" aria-label="Close">
            ×
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="tabular w-full text-sm">
            <thead className="sticky top-0 bg-raised text-left text-ink-2">
              <tr>
                <th className="w-10 px-4 py-2">
                  <input
                    type="checkbox"
                    checked={allPicked}
                    disabled={!addable.length}
                    onChange={toggleAll}
                    className="size-4 accent-white"
                    aria-label="Select all"
                  />
                </th>
                <th className="px-2 py-2 font-medium">Publisher</th>
                <th className="px-2 py-2 text-right font-medium">Features</th>
                <th className="px-2 py-2 text-right font-medium">Fields</th>
                <th className="px-2 py-2 text-right font-medium">Domains</th>
                <th className="px-2 py-2 font-medium">CRS</th>
                <th className="px-2 py-2 font-medium">Edited</th>
                <th className="px-2 py-2 text-right font-medium">Load</th>
                <th className="px-4 py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const a = c.assessment;
                const onMapAlready = onMap.includes(c.id);
                const expanded = openRow === c.id;
                return [
                  <tr
                    key={c.id}
                    className={`border-b border-line/60 ${c.loadable ? "cursor-pointer hover:bg-raised/60" : "text-ink-3"}`}
                    onClick={() => c.loadable && !onMapAlready && toggle(c.id)}
                  >
                    <td className="px-4 py-2.5">
                      {c.loadable && (
                        <input
                          type="checkbox"
                          disabled={onMapAlready}
                          checked={onMapAlready || picked.includes(c.id)}
                          onChange={() => toggle(c.id)}
                          onClick={(e) => e.stopPropagation()}
                          className="size-4 accent-white"
                          aria-label={`Select ${c.publisher}`}
                        />
                      )}
                    </td>
                    <td className="px-2 py-2.5">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenRow(expanded ? null : c.id);
                        }}
                        className="flex items-center gap-2 text-left"
                      >
                        {c.loadable && <span className="size-2.5 shrink-0 rounded-full" style={{ background: props.colorOf(c.id) }} />}
                        <span className={c.loadable ? "font-medium text-ink" : ""}>{c.publisher}</span>
                        <span className="text-ink-3">{expanded ? "▾" : "▸"}</span>
                      </button>
                      <div className="font-mono text-xs text-ink-3">{hostOf(c.url)}</div>
                    </td>
                    <td className="px-2 py-2.5 text-right">{a?.count?.toLocaleString() ?? "—"}</td>
                    <td className="px-2 py-2.5 text-right">{a?.field_count ?? "—"}</td>
                    <td className="px-2 py-2.5 text-right">{a?.domain_count ?? "—"}</td>
                    <td className="px-2 py-2.5">{a?.wkid ? `EPSG:${a.wkid}` : "—"}</td>
                    <td className="px-2 py-2.5">{a?.last_edit ?? "—"}</td>
                    <td className="px-2 py-2.5 text-right">{a?.estimated_load_s ? `${a.estimated_load_s} s` : "—"}</td>
                    <td className="px-4 py-2.5">
                      {onMapAlready ? (
                        <StatusChip tone="muted" label="on map" />
                      ) : !c.loadable ? (
                        <StatusChip tone="serious" label={reasonLabel(c.reason)} />
                      ) : null}
                    </td>
                  </tr>,
                  expanded && (
                    <tr key={`${c.id}-detail`} className="border-b border-line/60 bg-surface/40">
                      <td />
                      <td colSpan={8} className="px-2 py-3 text-ink-2">
                        {!c.loadable && <p className="mb-2 text-ink">{c.reason_detail ?? c.reason_text}</p>}
                        {c.where !== "1=1" && <p className="mb-2 font-mono text-sm">where {c.where}</p>}
                        <ul className="list-disc space-y-1 pl-5">
                          {c.interest.map((i) => (
                            <li key={i}>{i}</li>
                          ))}
                        </ul>
                        <p className="mt-2 font-mono text-xs break-all text-ink-3">{c.url}</p>
                      </td>
                    </tr>
                  ),
                ];
              })}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-end border-t border-line px-5 py-3">
          <button disabled={!picked.length} onClick={() => props.onLoad(picked)} className="rounded-md bg-ink px-4 py-2 font-medium text-surface disabled:opacity-40">
            Add {picked.length || ""} to map
          </button>
        </div>
      </div>
    </div>
  );
}

// --- the ingest queue ------------------------------------------------------------------

function IngestQueue(props: {
  width: number;
  onNext: () => void;
  queue: string[];
  byId: Record<string, SourceState>;
  checks: Record<string, Checks>;
  colorOf: (id: string) => string;
  onRemove: (id: string) => void;
}) {
  const { queue, byId, checks } = props;
  const total = queue.reduce((n, id) => n + (byId[id]?.rows ?? 0), 0);
  return (
    <aside className="flex shrink-0 flex-col bg-panel" style={{ width: props.width }}>
      <div className="border-b border-line px-4 py-3 font-semibold">Ingest queue</div>
      <ul className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        {queue.length === 0 && <li className="text-ink-3">Empty</li>}
        {queue.map((id) => {
          const s = byId[id];
          const c = checks[id];
          if (!s) return null;
          const offDomain = c?.off_domain.reduce((n, o) => n + o.rows, 0) ?? 0;
          return (
            <li key={id} className="rounded-lg border border-line bg-surface/40 p-3">
              <div className="flex items-center gap-2">
                <span className="size-2.5 rounded-full" style={{ background: props.colorOf(id) }} />
                <span className="flex-1 truncate font-medium">{s.publisher}</span>
                <button onClick={() => props.onRemove(id)} className="px-1 text-ink-3 hover:text-ink" aria-label={`Remove ${s.publisher} from queue`}>
                  ×
                </button>
              </div>
              <div className="tabular mt-1 text-sm text-ink-2">
                {s.rows?.toLocaleString()} features
                {c?.inside_share != null && ` · ${Math.round(c.inside_share * 100)}% in area`}
              </div>
              {offDomain > 0 && <div className="tabular text-sm text-warning">▲ {offDomain.toLocaleString()} off-domain values</div>}
            </li>
          );
        })}
      </ul>
      <div className="space-y-2 border-t border-line p-4">
        <div className="tabular text-sm text-ink-2">{total.toLocaleString()} features</div>
        <button
          disabled={!queue.length}
          onClick={props.onNext}
          className="w-full rounded-md bg-ink px-4 py-2 font-medium text-surface disabled:opacity-40"
        >
          Match fields →
        </button>
      </div>
    </aside>
  );
}

// --- helpers ------------------------------------------------------------------

function normalise(url: string): string {
  try {
    const u = new URL(url.trim());
    return `${u.host.toLowerCase()}${u.pathname.replace(/\/+$/, "")}`;
  } catch {
    return "";
  }
}

function hostOf(url: string) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function reasonLabel(reason: string | null) {
  return (
    {
      catalogued_url_dead: "URL dead",
      token_required: "token required",
      no_mappable_attributes: "nothing to map",
      needs_value_level_parsing: "do not ingest",
    }[reason ?? ""] ??
    reason ??
    "rejected"
  );
}
