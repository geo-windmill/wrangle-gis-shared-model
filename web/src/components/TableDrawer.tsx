// The drawer under the map, two ways to read the data:
//   By layer   — one layer's own table, picked from a dropdown, paged from the API
//   Crosswalk  — every layer at once, lined up on the shared model (CrosswalkGrid)
// It can grow to fill the whole browser window (Esc returns).
import { useEffect, useState, type ReactNode } from "react";
import { api, type Rows, type SourceState } from "../api";

type Mode = "layers" | "crosswalk";

type Props = {
  feature: string;
  layers: SourceState[]; // every layer on the map, in list order
  active: string | null;
  onActivate: (id: string) => void;
  colorOf: (id: string) => string;
  pickedOid: number | null;
  onPick: (sourceId: string, oid: number) => void;
  height: number; // px when open; the parent owns it so a splitter can resize it
  open: boolean;
  onToggle: () => void;
  full: boolean;
  onFull: () => void;
  mode: Mode;
  onMode: (m: Mode) => void;
  crosswalk?: ReactNode; // omit to hide the Crosswalk view
  highlight?: string[]; // columns to tint, e.g. the fields being compared
};

const LIMIT = 100;

export function TableDrawer(props: Props) {
  const { feature, layers, active, colorOf, pickedOid, onPick, height, open, onToggle, full, mode } = props;
  const [rows, setRows] = useState<Rows | null>(null);
  const [offset, setOffset] = useState(0);
  const source = layers.find((t) => t.id === active) ?? layers[0] ?? null;
  const ready = !!source && (source.status === "ready" || source.status === "partial");

  useEffect(() => setOffset(0), [source?.id]);
  useEffect(() => {
    setRows(null);
    if (!source || !ready || !open || mode !== "layers") return;
    api.rows(feature, source.id, offset, LIMIT).then(setRows).catch(() => setRows(null));
  }, [feature, source?.id, offset, ready, open, mode]);

  const oid = source?.oid_field ?? "OBJECTID";

  // Esc leaves full screen.
  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && props.onFull();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full, props.onFull]);

  return (
    <div className={`flex flex-col bg-panel ${full ? "fixed inset-0 z-40" : "shrink-0"}`} style={open && !full ? { height } : undefined}>
      <div className="flex items-center gap-2 border-b border-line px-2 py-1.5 whitespace-nowrap">
        <button onClick={onToggle} className="px-2 text-ink-3 hover:text-ink" aria-label={open ? "Collapse table" : "Expand table"}>
          {open ? "▾" : "▴"}
        </button>
        {props.crosswalk !== undefined && (
        <div className="flex shrink-0 rounded-md border border-line p-0.5 text-sm">
          {(["layers", "crosswalk"] as const).map((m) => (
            <button
              key={m}
              onClick={() => {
                props.onMode(m);
                if (!open) onToggle();
              }}
              className={`rounded px-2 py-0.5 ${m === mode ? "bg-raised text-ink" : "text-ink-3 hover:text-ink-2"}`}
            >
              {m === "layers" ? "By layer" : "Crosswalk"}
            </button>
          ))}
        </div>
        )}
        {mode === "layers" && source && (
          <LayerPicker layers={layers} active={source} colorOf={colorOf} onPick={props.onActivate} />
        )}
        <div className="flex-1" />
        {rows && open && mode === "layers" && (
          <div className="tabular flex shrink-0 items-center gap-3 text-sm text-ink-3">
            {(offset + 1).toLocaleString()}–{Math.min(offset + LIMIT, rows.total).toLocaleString()} of {rows.total.toLocaleString()}
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))} className="rounded border border-line px-2 disabled:opacity-30" aria-label="Previous page">
              ‹
            </button>
            <button disabled={offset + LIMIT >= rows.total} onClick={() => setOffset(offset + LIMIT)} className="rounded border border-line px-2 disabled:opacity-30" aria-label="Next page">
              ›
            </button>
          </div>
        )}
        <button
          onClick={() => {
            props.onFull();
            if (!open) onToggle();
          }}
          className="rounded border border-line px-2 text-ink-3 hover:text-ink"
          title={full ? "Exit full screen (Esc)" : "Full screen"}
          aria-label={full ? "Exit full screen" : "Full screen"}
        >
          {full ? "⤡" : "⤢"}
        </button>
      </div>
      {open && mode === "crosswalk" && props.crosswalk}
      {open && mode === "layers" && rows && source && (
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="tabular text-sm whitespace-nowrap">
            <thead className="sticky top-0 bg-raised">
              <tr>
                {rows.columns.map((c) => (
                  <th key={c} className={`px-3 py-1.5 text-left font-mono font-medium ${props.highlight?.includes(c) ? "bg-good/20 text-ink" : "text-ink-2"}`}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.rows.map((r) => {
                const id = Number(r[oid]);
                return (
                  <tr
                    key={id}
                    onClick={() => onPick(source.id, id)}
                    className={`cursor-pointer border-b border-line/50 ${pickedOid === id ? "bg-ink/15" : "hover:bg-raised"}`}
                  >
                    {rows.columns.map((c) => (
                      <td key={c} className={`max-w-[20rem] truncate px-3 py-1 font-mono ${props.highlight?.includes(c) ? "bg-good/10" : ""}`}>
                        <Cell v={r[c]} />
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {open && mode === "layers" && source && !ready && <div className="px-4 py-3 text-ink-3">Not downloaded yet.</div>}
    </div>
  );
}

/** Which layer's table to show: a dropdown with each layer's map colour. */
function LayerPicker({
  layers,
  active,
  colorOf,
  onPick,
}: {
  layers: SourceState[];
  active: SourceState;
  colorOf: (id: string) => string;
  onPick: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative" onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && setOpen(false)}>
      <button
        onClick={() => setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-md border border-line bg-surface px-3 py-0.5 text-sm hover:border-ink-3"
      >
        <span className="size-2.5 rounded-full" style={{ background: colorOf(active.id) }} />
        <span className="max-w-[16rem] truncate">{active.publisher}</span>
        <span className="text-ink-3">▾</span>
      </button>
      {open && (
        <ul role="listbox" className="absolute top-full left-0 z-20 mt-1 min-w-full overflow-hidden rounded-md border border-line bg-raised shadow-xl">
          {layers.map((l) => (
            <li key={l.id}>
              <button
                role="option"
                aria-selected={l.id === active.id}
                onClick={() => {
                  onPick(l.id);
                  setOpen(false);
                }}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-surface ${l.id === active.id ? "text-ink" : "text-ink-2"}`}
              >
                <span className="size-2.5 shrink-0 rounded-full" style={{ background: colorOf(l.id) }} />
                <span className="flex-1">{l.publisher}</span>
                <span className="tabular pl-4 text-ink-3">{l.rows?.toLocaleString()}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Cell({ v }: { v: unknown }) {
  if (v === null || v === undefined) return <span className="text-ink-3/60">null</span>;
  if (typeof v === "string" && !v.trim()) return <span className="text-warning">"{v}"</span>;
  if (typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v)) return <>{v.slice(0, 10)}</>;
  return <>{String(v)}</>;
}
