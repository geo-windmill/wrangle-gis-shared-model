// Edit values — one decision at a time: what one raw value means in the model.
//
//   ┌──────────────┬────────────────────┬──────────────────────────────────┐
//   │ name   as is │ CARTOCLASS         │ ART  · 8% · 5,889 rows           │
//   │ road_class   │ RES  77% → local   │ ┌ #1 minor_arterial ──── Use ┐   │
//   │ › Sac    ▲3  │ COL   9% → ?    ▲  │ │ Domain · Text · Reviewed · AI│   │
//   │   Yolo   ✓   │ › ART 8% → ?    ▲  │ └──────────────────────────────┘   │
//   │ one_way      │ ALY   2% → alley ✓ │ ┌ #2 principal_arterial ──────┐   │
//   │ speed_limit  │                    │ ∅ null · + another value          │
//   └──────────────┴────────────────────┴──────────────────────────────────┘
//
// The checklist is field → source; the middle column is that source's values; the
// right is the one value being decided, every option a card with the same evidence
// rows. "Auto-confirm" takes only values where two or more methods answered and all
// agree. Dates get one rule — a format — instead of a table.
//
// Every other field picks a mode first: as published (the default), cleaned (generic
// tidying, nothing to decide) or standardized (the value-by-value walk above).
// Decisions land in the source's configuration record (wrangle.db), under `modes`,
// `values` and `rules`.
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  api,
  type Candidate,
  type DateOption,
  type Decisions,
  type Feature,
  type Method,
  type ModelField,
  type ModelValue,
  type SourceState,
  type ValueCell,
  type ValueMode,
  type ValueRow,
} from "../api";
import { useColorOf } from "../colors";
import { CodedValue, DomainTag, Hover, labelText } from "../components/bits";
import { pct } from "../components/ValueBars";

type Props = { feature: Feature; queue: string[]; onBack: () => void; onNext: () => void };
type View = "review" | "overview";
type At = { field: string; source: string; raw: string | null }; // raw null: the cell's rule
type Unit = { c: ValueCell; r: ValueRow | null }; // one decision a person owes

export default function EditValues({ feature, queue, onBack, onNext }: Props) {
  const f = feature.feature;
  const [sources, setSources] = useState<SourceState[]>([]);
  const [fields, setFields] = useState<ModelField[]>([]);
  const [matched, setMatched] = useState<Record<string, Decisions>>({});
  const [cells, setCells] = useState<ValueCell[] | null>(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("review");
  const [at, setAt] = useState<At | null>(null);
  const key = queue.join(",");

  const reload = useCallback(() => api.values(f, queue).then((r) => setCells(r.cells)), [f, key]);
  useEffect(() => {
    if (!queue.length) return;
    api.sources(f).then(setSources);
    api.crosswalk(f, queue).then((c) => setFields(c.fields));
    api.mappings(f, queue).then(setMatched);
    reload();
  }, [f, key, reload]);

  // --- helpers over the state ----------------------------------------------------------
  const cellOf = (field: string, source: string) => cells?.find((c) => c.field === field && c.source === source);
  const colorOf = useColorOf(f, sources);
  const publisher = (id: string) => sources.find((s) => s.id === id)?.publisher ?? id;
  const all = cells ?? [];
  // Every decision a person owes: each value of a standardized table, each date rule.
  const units = all.flatMap((c): Unit[] => (standardizing(c) ? (c.rows ?? []).map((r) => ({ c, r })) : c.kind === "date" ? [{ c, r: null }] : []));
  const isDone = (u: Unit) => (u.r ? u.r.decided : u.c.rule !== null);
  const done = units.filter(isDone).length;
  const auto = units.filter((u) => u.r && !u.r.decided && u.r.status === "agree").length;
  const tables = all.filter((c) => c.ai !== null && standardizing(c));
  const pending = tables.filter((c) => c.ai?.pending).length;
  const recorded = tables.find((c) => c.ai?.cached)?.ai?.recorded_at;

  // The walk: field by field, source by source, value by value.
  const order: At[] = useMemo(
    () =>
      fields.flatMap((m) =>
        queue.flatMap((sid): At[] => {
          const c = all.find((x) => x.field === m.name && x.source === sid);
          if (!c) return [];
          return standardizing(c) ? (c.rows ?? []).map((r) => ({ field: m.name, source: sid, raw: r.raw })) : [{ field: m.name, source: sid, raw: null }];
        }),
      ),
    [fields, cells, key],
  );
  const same = (a: At, b: At) => a.field === b.field && a.source === b.source && a.raw === b.raw;
  const doneAt = (x: At) => {
    const c = cellOf(x.field, x.source);
    if (!c) return false;
    if (x.raw === null) return c.kind === "date" ? c.rule !== null : !standardizing(c);
    return !!c.rows?.find((r) => r.raw === x.raw)?.decided;
  };
  const index = at ? order.findIndex((o) => same(o, at)) : -1;
  // The next open decision after `from`, wrapping round to the start so a skipped value
  // comes back; null when nothing else is open.
  const nextOpenAround = (from: number, except?: At) =>
    [...order.slice(from + 1), ...order.slice(0, Math.max(from, 0))].find((o) => !(except && same(o, except)) && !doneAt(o)) ?? null;

  // Once the data arrives, start at the top: every field has a mode to choose, even one
  // that counts as done because "as published" is the default.
  useEffect(() => {
    if (at || !order.length) return;
    setAt(order[0]);
  }, [order.length]);

  // --- actions -----------------------------------------------------------------------------
  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };
  const decide = (x: At, value: ModelValue) =>
    run(async () => {
      if (x.raw === null) await api.decideRule(f, x.source, x.field, value as string);
      else await api.decideValues(f, x.source, x.field, { [x.raw]: value });
      await reload();
      // Advance past what was just decided — `doneAt` still sees the old state. With
      // nothing left open, stay here: the finish button below leads on to Convert.
      const next = nextOpenAround(index, x);
      if (next) setAt(next);
    });
  const undecide = (x: At) =>
    run(async () => {
      if (x.raw === null) await api.decideRule(f, x.source, x.field, null);
      else await api.undecideValue(f, x.source, x.field, x.raw);
      await reload();
    });
  const setMode = (c: ValueCell, mode: ValueMode) =>
    run(async () => {
      await api.decideMode(f, c.source, c.field, mode);
      await reload();
      const first = mode === "standardized" ? ((c.rows ?? []).find((r) => !r.decided) ?? c.rows?.[0]) : undefined;
      setAt({ field: c.field, source: c.source, raw: first ? first.raw : null });
    });
  const keepAll = (c: ValueCell) =>
    run(async () => {
      const open = (c.rows ?? []).filter((r) => !r.decided);
      await api.decideValues(f, c.source, c.field, Object.fromEntries(open.map((r) => [r.raw, r.raw.trim()])));
      await reload();
    });
  const askAi = () =>
    run(async () => {
      setAsking(true);
      try {
        const r = await api.askValues(f, queue);
        if (r.errors.length) setError(r.errors.map((e) => `${e.source} ${e.field}: ${e.error}`).join(" · "));
        await reload();
      } finally {
        setAsking(false);
      }
    });
  const automap = () =>
    run(async () => {
      await api.automapValues(f, queue);
      await reload();
    });
  // A new value for the field's vocabulary, used straight away for this raw value.
  const addValue = (x: At, value: string, meaning: string) =>
    run(async () => {
      const added = await api.addValue(f, x.field, value, meaning);
      setFields((await api.crosswalk(f, queue)).fields);
      await decide(x, added.value);
    });
  const clearAll = () => {
    if (!window.confirm(`Clear every value decision, date rule and mode for ${queue.length === 1 ? "this source" : `these ${queue.length} sources`}? Field matches stay.`)) return;
    run(async () => {
      await api.clearValues(f, queue);
      await reload();
      setAt(order[0] ?? null);
      setView("review");
    });
  };

  if (!queue.length || (cells && !cells.length)) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-ink-2">
        <div>{queue.length ? "No model field is matched to a source field yet." : "Nothing queued for ingest yet."}</div>
        <button onClick={onBack} className="rounded-md border border-line px-3 py-1.5 hover:bg-raised">
          ← {queue.length ? "Match fields" : "Back to the loading dock"}
        </button>
      </div>
    );
  }

  const field = fields.find((x) => x.name === at?.field) ?? null;
  const cell = at ? cellOf(at.field, at.source) : undefined;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-line bg-panel px-5 py-2.5 whitespace-nowrap">
        <div className="text-lg font-semibold">Edit values</div>
        <div className="tabular text-ink-2">
          {done} of {units.length}
        </div>
        <div className="h-1.5 w-32 shrink-0 rounded bg-line">
          <div className="h-1.5 rounded bg-good" style={{ width: `${units.length ? (done / units.length) * 100 : 0}%` }} />
        </div>
        {error && <span className="truncate text-sm text-critical" title={error}>{error}</span>}
        <div className="flex-1" />
        {recorded && !pending && (
          <span className="truncate text-sm text-ink-3" title="Replayed from the recording; no API call was made">
            AI recorded {recorded.slice(0, 10)}
          </span>
        )}
        <button onClick={clearAll} title="Clear every value decision, date rule and mode for the queued sources" className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised">
          ↺ Clear
        </button>
        <button
          onClick={askAi}
          disabled={asking}
          title="One call per value table: the field's whole list of values"
          className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised disabled:opacity-50"
        >
          {asking ? "Asking…" : pending ? `Ask AI ${pending}` : "Ask AI again"}
        </button>
        <button
          onClick={automap}
          disabled={!auto}
          title="Confirm every value where two or more methods answered and all of them agree"
          className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised disabled:opacity-40"
        >
          Auto-confirm {auto || ""}
        </button>
        <div className="flex rounded-md border border-line p-0.5 text-sm">
          {(["review", "overview"] as const).map((v) => (
            <button key={v} onClick={() => setView(v)} className={`rounded px-2.5 py-1 ${v === view ? "bg-raised text-ink" : "text-ink-3 hover:text-ink-2"}`}>
              {v === "review" ? "One by one" : "Overview"}
            </button>
          ))}
        </div>
      </div>

      {!cells ? (
        <div className="p-6 text-ink-3">Reading values…</div>
      ) : view === "overview" ? (
        <Overview
          fields={fields}
          queue={queue}
          cells={all}
          publisher={publisher}
          colorOf={colorOf}
          complete={done === units.length}
          onNext={onNext}
          onOpen={(x) => {
            setAt(x);
            setView("review");
          }}
        />
      ) : (
        <div className="flex min-h-0 flex-1">
          <Checklist
            fields={fields}
            queue={queue}
            at={at}
            matched={matched}
            cellOf={cellOf}
            publisher={publisher}
            colorOf={colorOf}
            onPick={(field, source) => {
              const first = order.find((o) => o.field === field && o.source === source && !doneAt(o)) ?? order.find((o) => o.field === field && o.source === source);
              setAt(first ?? { field, source, raw: null });
            }}
            onOverview={() => setView("overview")}
          />
          {cell && field && at ? (
            <>
              {standardizing(cell) && (
                <ValueList cell={cell} raw={at.raw} unit={field.unit} onPick={(raw) => setAt({ ...at, raw })} onKeepAll={cell.rows?.some((r) => r.candidates.some((c) => c.methods.some((m) => m.method === "keep"))) ? () => keepAll(cell) : undefined} />
              )}
              <section className="min-w-0 flex-1 overflow-y-auto">
                <div className="mx-auto max-w-4xl p-6">
                  <CellHeader field={field} cell={cell} publisher={publisher(cell.source)} color={colorOf(cell.source)} />
                  {cell.mode && cell.modes.length > 0 && <ModePicker cell={cell} field={field} onPick={(m) => setMode(cell, m)} />}
                  {standardizing(cell) && at.raw !== null && cell.rows?.some((r) => r.raw === at.raw) && (
                    <ValueDecision
                      key={`${cell.field}/${cell.source}/${at.raw}`}
                      field={field}
                      cell={cell}
                      row={cell.rows!.find((r) => r.raw === at.raw)!}
                      onDecide={(v) => decide(at, v)}
                      onUndecide={() => undecide(at)}
                      onAddValue={(value, meaning) => addValue(at, value, meaning)}
                    />
                  )}
                  {cell.kind === "date" && <DateDecision cell={cell} onDecide={(fmt) => decide(at, fmt)} onUndecide={() => undecide(at)} />}
                  {cell.mode && cell.mode !== "standardized" && <Landing cell={cell} field={field} />}
                  <div className="mt-6 flex items-center gap-2 border-t border-line pt-4 text-sm">
                    <Hover label={`About ${field.name}`} content={<AboutField field={field} />}>
                      <span className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised">About {field.name}</span>
                    </Hover>
                    <span className="flex-1" />
                    <button onClick={() => index > 0 && setAt(order[index - 1])} disabled={index <= 0} className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised disabled:opacity-30">
                      ← Back
                    </button>
                    <button
                      onClick={() => index + 1 < order.length && setAt(order[index + 1])}
                      disabled={index + 1 >= order.length}
                      className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised disabled:opacity-30"
                    >
                      Skip →
                    </button>
                  </div>
                  {done === units.length && (
                    <button onClick={onNext} className="mt-3 rounded-md bg-ink px-3 py-1 text-sm font-medium text-surface">
                      ✓ Every value decided · Convert →
                    </button>
                  )}
                </div>
              </section>
            </>
          ) : (
            <section className="flex-1 p-6 text-ink-3">{at ? "Nothing to edit here." : ""}</section>
          )}
        </div>
      )}
    </div>
  );
}

// --- left: the checklist, field → source ----------------------------------------------------

function Checklist(props: {
  fields: ModelField[];
  queue: string[];
  at: At | null;
  matched: Record<string, Decisions>;
  cellOf: (field: string, source: string) => ValueCell | undefined;
  publisher: (id: string) => string;
  colorOf: (id: string) => string;
  onPick: (field: string, source: string) => void;
  onOverview: () => void;
}) {
  return (
    <nav className="flex w-60 shrink-0 flex-col border-r border-line bg-panel">
      <ul className="min-h-0 flex-1 overflow-y-auto py-2">
        {props.fields.map((fld) => {
          const cells = props.queue.map((sid) => props.cellOf(fld.name, sid)).filter((c): c is ValueCell => !!c);
          if (!fld.sourced || !cells.length)
            return (
              <li key={fld.name} className="flex justify-between px-4 py-1.5 text-sm text-ink-3">
                <span className="font-mono">{fld.name}</span>
                <span>{fld.sourced ? "no source" : "empty"}</span>
              </li>
            );
          const passes = cells.every((c) => c.kind !== "date" && !standardizing(c));
          const modes = [...new Set(cells.map((c) => c.mode))];
          const finished = cells.filter(cellDone).length;
          const open = props.at?.field === fld.name;
          return (
            <li key={fld.name}>
              <button
                onClick={() => props.onPick(fld.name, (cells.find((c) => !cellDone(c)) ?? cells[0]).source)}
                className={`flex w-full items-center justify-between gap-2 px-4 py-2 text-left ${open ? "text-ink" : "text-ink-2 hover:bg-raised/50"}`}
              >
                <span className="truncate font-mono">{fld.name}</span>
                <span className={`tabular text-sm ${!passes && finished === cells.length ? "text-good" : "text-ink-3"}`}>
                  {passes ? (modes.length === 1 && modes[0] ? MODES[modes[0]].short : "mixed") : `${finished === cells.length ? "✓ " : ""}${finished}/${cells.length}`}
                </span>
              </button>
              {open && (
                <ul className="pb-1">
                  {props.queue.map((sid) => {
                    const c = props.cellOf(fld.name, sid);
                    const here = props.at?.source === sid;
                    if (!c) {
                      const fits = props.matched[sid]?.fields[fld.name];
                      return (
                        <li key={sid} className="flex items-center gap-2 py-1.5 pr-4 pl-7 text-sm text-ink-3">
                          <span className="size-2 shrink-0 rounded-full opacity-40" style={{ background: props.colorOf(sid) }} />
                          <span className="flex-1 truncate">{props.publisher(sid)}</span>
                          <span title={fits === null ? "No field fits — ships empty" : "Not matched yet"}>{fits === null ? "—" : "?"}</span>
                        </li>
                      );
                    }
                    const left = (c.rows ?? []).filter((r) => !r.decided);
                    const looks = left.filter((r) => r.status === "conflict" || r.status === "none").length;
                    return (
                      <li key={sid}>
                        <button
                          onClick={() => props.onPick(fld.name, sid)}
                          className={`flex w-full items-center gap-2 py-1.5 pr-4 pl-7 text-left text-sm ${here ? "bg-raised text-ink" : "text-ink-2 hover:bg-raised/50"}`}
                        >
                          <span className="size-2 shrink-0 rounded-full" style={{ background: props.colorOf(sid) }} />
                          <span className="flex-1 truncate">{props.publisher(sid)}</span>
                          {c.mode && c.mode !== "standardized" ? (
                            <span className="text-ink-3">{MODES[c.mode].short}</span>
                          ) : cellDone(c) ? (
                            <span className="text-good">✓</span>
                          ) : looks ? (
                            <span className="tabular text-warning">▲ {looks}</span>
                          ) : (
                            <span className="tabular text-ink-3">{c.kind === "date" ? "" : left.length}</span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      <button onClick={props.onOverview} className="border-t border-line px-4 py-2.5 text-left text-sm text-ink-2 hover:bg-raised">
        Overview →
      </button>
    </nav>
  );
}

/** A table in standardized mode: the only cells with values to decide one by one. */
function standardizing(c: ValueCell) {
  return c.kind === "table" && c.mode === "standardized";
}

function cellDone(c: ValueCell) {
  if (standardizing(c)) return (c.rows ?? []).every((r) => r.decided);
  if (c.kind === "date") return c.rule !== null;
  return true;
}

// --- middle: one source's values ----------------------------------------------------------

function ValueList({ cell, raw, unit, onPick, onKeepAll }: { cell: ValueCell; raw: string | null; unit?: string | null; onPick: (raw: string) => void; onKeepAll?: () => void }) {
  const rows = cell.rows ?? [];
  const max = Math.max(...rows.map((r) => r.share), 0.0001);
  return (
    <div className="flex w-96 shrink-0 flex-col border-r border-line">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2 text-sm text-ink-3">
        <span className="font-mono text-ink-2">{cell.source_field}</span>
        <span className="flex-1" title={cell.domain ? "Labels come from the layer's coded-value domain; the tag is the code" : "The layer publishes no domain for this field"}>
          · {rows.length} {cell.domain ? "codes, with labels" : "values, no labels"}
        </span>
        {onKeepAll && rows.some((r) => !r.decided) && (
          <button onClick={onKeepAll} className="rounded border border-line px-2 py-0.5 text-ink-2 hover:bg-raised" title="Keep every open value as published">
            Keep all
          </button>
        )}
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto py-1">
        {rows.map((r) => {
          const top = r.candidates[0];
          const here = r.raw === raw;
          const look = !r.decided && (r.status === "conflict" || r.status === "none");
          return (
            <li key={r.raw}>
              <button
                onClick={() => onPick(r.raw)}
                title={r.label ? `${r.label} · code ${r.raw}` : r.raw}
                className={`grid w-full grid-cols-[minmax(0,1.5fr)_2.75rem_minmax(0,1fr)_1rem] items-center gap-2 px-4 py-1.5 text-left text-sm ${here ? "bg-raised" : "hover:bg-raised/50"}`}
              >
                <CodedValue raw={r.raw} label={r.label} />
                <span className="relative h-4">
                  <span className="absolute inset-y-1 left-0 rounded bg-ink-3/40" style={{ width: `${(r.share / max) * 100}%` }} />
                  <span className="tabular relative block text-right text-xs leading-4 text-ink-2">{pct(r.share)}</span>
                </span>
                <span className={`truncate font-mono ${r.decided ? "text-ink" : r.status === "agree" ? "text-good/70" : "text-ink-3"}`}>
                  {r.decided ? fmt(r.decision, unit) : look ? "?" : top ? fmt(top.value, unit) : "?"}
                </span>
                <span className="text-right">{r.decided ? <span className="text-good">✓</span> : look ? <span className="text-warning">▲</span> : null}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {cell.blank > 0 && (
        <div className="border-t border-line px-4 py-2 text-xs text-ink-3">
          blank {pct(cell.blank / cell.total)} → <span className="font-mono">∅</span>
        </div>
      )}
    </div>
  );
}

// --- right: one decision ----------------------------------------------------------------------

function CellHeader({ field, cell, publisher, color }: { field: ModelField; cell: ValueCell; publisher: string; color: string }) {
  return (
    <div className="mb-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <span className="font-mono text-2xl">{field.name}</span>
      <span className="text-ink-3">for</span>
      <span className="flex items-center gap-2 text-lg">
        <span className="size-3 rounded-full" style={{ background: color }} />
        {publisher}
      </span>
      <span className="text-ink-3">
        ← <span className="font-mono text-ink-2">{cell.source_field}</span>
      </span>
      <span className="flex-1" />
      <span className="text-sm text-ink-3">
        {field.type}
        {field.unit && ` · ${field.unit}`}
      </span>
    </div>
  );
}

// The evidence rows every card shows, per kind of model field. `text` covers abbrev,
// `cast` covers parse: one row each, so the cards line up.
const ROWS: Record<string, { key: string; label: string; methods: Method["method"][] }[]> = {
  enum: [
    { key: "reviewed", label: "Reviewed", methods: ["reviewed"] },
    { key: "domain", label: "Domain", methods: ["domain"] },
    { key: "text", label: "Text", methods: ["text", "abbrev"] },
    { key: "ai", label: "AI", methods: ["ai"] },
  ],
  number: [
    { key: "reviewed", label: "Reviewed", methods: ["reviewed"] },
    { key: "cast", label: "Number", methods: ["cast", "parse"] },
    { key: "ai", label: "AI", methods: ["ai"] },
  ],
  string: [
    { key: "reviewed", label: "Reviewed", methods: ["reviewed"] },
    { key: "keep", label: "As is", methods: ["keep"] },
  ],
};

const NEW = "__new_value__"; // the "new value" entry in the picker: no vocabulary value is spelled so

function ValueDecision(props: { field: ModelField; cell: ValueCell; row: ValueRow; onDecide: (v: ModelValue) => void; onUndecide: () => void; onAddValue: (value: string, meaning: string) => void }) {
  const { field, row } = props;
  const [extra, setExtra] = useState<ModelValue[]>([]);
  const [typed, setTyped] = useState("");
  const [adding, setAdding] = useState(false);
  const [newValue, setNewValue] = useState("");
  const [newMeaning, setNewMeaning] = useState("");
  const meaning = (v: ModelValue) => (typeof v === "string" ? field.meanings?.[v] : undefined);
  const kind = field.type === "enum" ? "enum" : field.type === "string" ? "string" : "number";
  const rows = ROWS[kind];
  const ai = row.candidates.flatMap((c) => c.methods).find((m) => m.method === "ai");

  // The methods' answers, ranked by the server; then null; then anything added by hand.
  const options: Candidate[] = [...row.candidates];
  const has = (v: ModelValue) => options.some((o) => sameValue(o.value, v));
  if (!has(null)) options.push({ value: null, methods: [] });
  for (const v of extra) if (!has(v)) options.push({ value: v, methods: [] });
  if (row.decided && !has(row.decision)) options.push({ value: row.decision, methods: [] });
  const others = (field.values ?? []).filter((v) => !has(v));

  const verdict =
    row.decided
      ? `✓ ${fmt(row.decision, field.unit)} · ${row.decided_by === "automap" ? "auto-confirmed" : "by you"}`
      : { agree: "", single: "", conflict: "▲ Methods disagree", none: "▲ No match" }[row.status];

  return (
    <div>
      <div className="mb-4 rounded-xl border border-line bg-panel p-4">
        <div className="flex flex-wrap items-baseline gap-x-3">
          {row.label && row.label !== row.raw ? (
            <span className="flex items-center gap-2 text-2xl">
              {labelText(row.raw, row.label)}
              <DomainTag code={row.raw} />
            </span>
          ) : (
            <span className="font-mono text-2xl">{row.raw}</span>
          )}
          <span className="flex-1" />
          <span className="tabular text-sm text-ink-3">
            {row.rows.toLocaleString()} rows · {pct(row.share)}
          </span>
        </div>
        <dl className="mt-2 grid grid-cols-[3.5rem_minmax(0,1fr)] gap-x-3 text-sm">
          <dt className="text-ink-3">Code</dt>
          <dd className="font-mono">{row.raw}</dd>
          <dt className="text-ink-3">Label</dt>
          <dd className={row.label ? "" : "text-ink-3"}>
            {row.label ? `“${row.label}”` : "—"}
          </dd>
        </dl>
        {row.declared === false && <div className="mt-1 text-sm text-warning">▲ Not in the layer's domain</div>}
        {verdict && <div className={`mt-2 text-sm ${row.decided ? "text-good" : "text-warning"}`}>{verdict}</div>}
      </div>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(19rem,1fr))] gap-4">
        {options.map((o, i) => {
          const chosen = row.decided && sameValue(row.decision, o.value);
          const isAi = !!ai && o.methods.includes(ai);
          return (
            <div key={String(o.value)} className={`min-w-0 rounded-xl border p-4 ${chosen ? "border-good/60 bg-good/5" : i === 0 && o.methods.length ? "border-ink-3" : "border-line bg-panel"}`}>
              <div className="mb-3 flex items-start gap-3">
                <span className={`tabular rounded-md px-2 py-0.5 text-sm font-semibold ${i === 0 && o.methods.length ? "bg-ink text-surface" : "bg-raised text-ink-2"}`}>#{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <div className={`truncate font-mono text-lg ${o.value === null ? "text-ink-2" : ""}`}>{fmt(o.value, field.unit)}</div>
                  {meaning(o.value) && <div className="truncate text-sm text-ink-3">{meaning(o.value)}</div>}
                </div>
                {chosen ? (
                  <button onClick={props.onUndecide} className="rounded-md border border-good/60 px-3 py-1 text-good" title="Undo">
                    ✓ Chosen
                  </button>
                ) : (
                  <button onClick={() => props.onDecide(o.value)} className="rounded-md bg-ink px-4 py-1 font-medium text-surface">
                    Use
                  </button>
                )}
              </div>
              <dl className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
                {rows.map((ev) => (
                  <Evidence key={ev.key} label={ev.label} mine={o.methods.filter((m) => ev.methods.includes(m.method))} elsewhere={row.candidates.filter((c) => c !== o && c.methods.some((m) => ev.methods.includes(m.method)))} unit={field.unit} />
                ))}
              </dl>
              {isAi && ai && <p className="mt-3 text-sm text-ink-2 italic">“{ai.detail}”</p>}
            </div>
          );
        })}
      </div>

      {field.type === "enum" && !adding && (
        <select
          value=""
          onChange={(e) => {
            if (e.target.value === NEW) setAdding(true);
            else if (e.target.value) setExtra((x) => [...x, e.target.value]);
          }}
          className="mt-4 block w-full max-w-md rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink-2"
          aria-label="Another model value"
        >
          <option value="">+ Another value…</option>
          {others.map((v) => (
            <option key={v} value={v}>
              {v}
              {meaning(v) ? ` — ${meaning(v)}` : ""}
            </option>
          ))}
          <option value={NEW}>＋ New value for {field.name}…</option>
        </select>
      )}
      {field.type === "enum" && adding && (
        <form
          className="mt-4 flex max-w-2xl flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!newValue.trim()) return;
            props.onAddValue(newValue.trim(), newMeaning.trim());
            setAdding(false);
            setNewValue("");
            setNewMeaning("");
          }}
        >
          <input autoFocus value={newValue} onChange={(e) => setNewValue(e.target.value)} placeholder="value, e.g. two_way" className="w-48 rounded-md border border-line bg-surface px-2 py-1.5 font-mono text-sm" />
          <input value={newMeaning} onChange={(e) => setNewMeaning(e.target.value)} placeholder="what it means" className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1.5 text-sm" />
          <button className="rounded-md bg-ink px-3 py-1.5 text-sm font-medium text-surface" title={`Adds it to ${field.name} for every source, and uses it here`}>
            Add and use
          </button>
          <button type="button" onClick={() => setAdding(false)} className="px-2 text-sm text-ink-3 hover:text-ink-2">
            Cancel
          </button>
        </form>
      )}
      {field.type !== "enum" && (
        <form
          className="mt-4 flex max-w-md gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!typed.trim()) return;
            props.onDecide(field.type === "string" ? typed.trim() : Number(typed));
            setTyped("");
          }}
        >
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={field.type === "string" ? "Replace with…" : `Another number${field.unit ? ` (${field.unit})` : ""}…`}
            inputMode={field.type === "string" ? "text" : "decimal"}
            className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1.5 text-sm"
          />
          <button className="rounded-md border border-line px-3 py-1.5 text-sm text-ink-2 hover:bg-raised">Use</button>
        </form>
      )}
    </div>
  );
}

/** One evidence row: ✓ when this method gives this card's value, what it says instead
 *  when it gives another, — when it said nothing. */
function Evidence({ label, mine, elsewhere, unit }: { label: string; mine: Method[]; elsewhere: Candidate[]; unit?: string | null }) {
  return (
    <>
      <dt className="text-ink-3">{label}</dt>
      <dd className="min-w-0 truncate" title={mine.map((m) => m.detail).join(" · ") || undefined}>
        {mine.length ? (
          <>
            <span className="text-good">✓ </span>
            {mine.map((m) => (m.method === "ai" ? <Confidence key="ai" c={m.confidence!} /> : <span key={m.method + m.detail}>{describe(m)}</span>)).reduce<ReactNode[]>((a, x, i) => (i ? [...a, " · ", x] : [x]), [])}
          </>
        ) : elsewhere.length ? (
          <span className="text-ink-3">says {elsewhere.map((c) => fmt(c.value, unit)).join(", ")}</span>
        ) : (
          <span className="text-ink-3">—</span>
        )}
      </dd>
    </>
  );
}

function describe(m: Method) {
  if (m.method === "domain") return `“${m.detail.split(" = ").slice(1).join(" = ")}”${m.how && m.how !== "exact" ? ` · ${m.how}` : ""}`;
  if (m.method === "text") return m.detail; // exact, words
  if (m.method === "abbrev") return "abbreviation";
  return m.detail;
}

function Confidence({ c }: { c: "high" | "medium" | "low" }) {
  const tone = { high: "text-good", medium: "text-warning", low: "text-serious" }[c];
  return <span className={tone}>{c}</span>;
}

// --- dates: one rule, chosen like a value ---------------------------------------------------

function DateDecision({ cell, onDecide, onUndecide }: { cell: ValueCell; onDecide: (fmt: string) => void; onUndecide: () => void }) {
  const options = cell.options ?? [];
  if (!options.length) return <div className="text-warning">▲ No date format reads any of these values.</div>;
  return (
    <div>
      <div className={`mb-4 text-sm ${cell.rule ? "text-good" : "text-ink-2"}`}>
        {cell.rule ? `✓ Read as ${cell.rule} · by you` : `${cell.distinct.toLocaleString()} distinct values, read with one rule. Pick how.`}
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(19rem,1fr))] gap-4">
        {options.map((o: DateOption, i) => {
          const chosen = cell.rule === o.format;
          return (
            <div key={o.format} className={`min-w-0 rounded-xl border p-4 ${chosen ? "border-good/60 bg-good/5" : i === 0 ? "border-ink-3" : "border-line bg-panel"}`}>
              <div className="mb-3 flex items-start gap-3">
                <span className={`tabular rounded-md px-2 py-0.5 text-sm font-semibold ${i === 0 ? "bg-ink text-surface" : "bg-raised text-ink-2"}`}>#{i + 1}</span>
                <div className="min-w-0 flex-1 truncate font-mono text-lg" title={o.about}>
                  {o.format}
                </div>
                {chosen ? (
                  <button onClick={onUndecide} className="rounded-md border border-good/60 px-3 py-1 text-good" title="Undo">
                    ✓ Chosen
                  </button>
                ) : (
                  <button onClick={() => onDecide(o.format)} className="rounded-md bg-ink px-4 py-1 font-medium text-surface">
                    Use
                  </button>
                )}
              </div>
              <dl className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
                <dt className="text-ink-3">Reads</dt>
                <dd className={o.read_share < 0.99 ? "text-warning" : ""}>{pct(o.read_share)} of filled rows</dd>
                <dt className="text-ink-3">Can't</dt>
                <dd className="truncate">{o.failed.rows ? <span className="text-warning">{o.failed.rows.toLocaleString()} rows · {o.failed.examples.join(", ")}</span> : <span className="text-ink-3">—</span>}</dd>
                <dt className="text-ink-3">Fake</dt>
                <dd className="truncate" title="Before 1900 or in the future: a placeholder, lands as null">
                  {o.placeholder.rows ? <span className="text-warning">{o.placeholder.rows.toLocaleString()} rows · {o.placeholder.examples.join(", ")} → ∅</span> : <span className="text-ink-3">—</span>}
                </dd>
                <dt className="text-ink-3">Range</dt>
                <dd className="tabular">
                  {o.range[0]} – {o.range[1]}
                </dd>
              </dl>
              <div className="mt-3 space-y-0.5 font-mono text-sm">
                {o.examples.map((e) => (
                  <div key={e.raw} className="flex gap-2">
                    <span className="truncate text-ink-2">{e.raw}</span>
                    <span className="text-ink-3">→</span>
                    <span>{e.date}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// --- modes: how a field's values land ---------------------------------------------------------

const MODES: Record<ValueMode, { label: string; short: string; about: (f: ModelField) => string }> = {
  published: { label: "As published", short: "as published", about: () => "What the publisher wrote; a coded value as its label." },
  cleaned: {
    label: "Cleaned",
    short: "cleaned",
    about: (f) =>
      f.type === "integer" || f.type === "number"
        ? "Read as a number · blanks and placeholders → ∅ · outside the model's range → ∅"
        : "Trimmed · blanks and placeholders → ∅ · codes → their labels · one spelling per word",
  },
  standardized: { label: "Standardized", short: "standardized", about: () => "Onto the model's own values, one decision per value." },
};

function ModePicker({ cell, field, onPick }: { cell: ValueCell; field: ModelField; onPick: (m: ValueMode) => void }) {
  return (
    <div className="mb-4">
      <div className="flex w-fit rounded-md border border-line p-0.5 text-sm">
        {cell.modes.map((m) => (
          <button
            key={m}
            onClick={() => m !== cell.mode && onPick(m)}
            title={MODES[m].about(field)}
            className={`rounded px-3 py-1 ${m === cell.mode ? "bg-raised text-ink" : "text-ink-3 hover:text-ink-2"}`}
          >
            {MODES[m].label}
          </button>
        ))}
      </div>
    </div>
  );
}

// What cleaning did, in a word each. The ones that end in ∅ say why a value is null.
const STEP: Record<string, string> = {
  trim: "spaces trimmed",
  blank: "blank → ∅",
  placeholder: "placeholder → ∅",
  decode: "code → label",
  case: "case made consistent",
  unreadable: "not a number → ∅",
  out_of_range: "out of range → ∅",
};

/** As published or cleaned: nothing to decide, so show where every value lands. */
function Landing({ cell, field }: { cell: ValueCell; field: ModelField }) {
  const mode = cell.mode!;
  const unit = field.unit;
  // Not standardized, an enum field holds the publisher's words, not the model's.
  const asText = field.type === "enum";
  const steps = Object.entries(cell.landing?.steps ?? {});
  const rows = cell.landing?.rows ?? [];
  const labelOf = (raw: string) => cell.rows?.find((r) => r.raw === raw)?.label ?? null;
  const table = cell.kind === "table";
  const p = cell.preview;
  return (
    <div className="rounded-xl border border-line bg-panel p-4">
      {asText && (
        <div className="mb-3 text-sm text-ink-2">
          Lands as text, not the model's values.
        </div>
      )}
      {mode === "cleaned" && (
        <div className="mb-3 flex flex-wrap gap-1.5 text-sm">
          {steps.length ? (
            steps.map(([k, n]) => (
              <span key={k} className="rounded border border-line px-2 py-0.5 text-ink-2">
                {STEP[k] ?? k} <span className="tabular text-ink-3">{n.toLocaleString()} rows</span>
              </span>
            ))
          ) : (
            <span className="text-ink-3">Nothing to clean.</span>
          )}
        </div>
      )}
      {table ? (
        <table className="w-full text-sm">
          <thead className="text-left text-ink-3">
            <tr>
              <th className="py-1 pr-3 font-normal">Code</th>
              <th className="py-1 pr-3 font-normal">{cell.domain ? "Label (layer's domain)" : "Label"}</th>
              <th className="py-1 pr-3 text-right font-normal">Rows</th>
              <th className="py-1 font-normal">Lands as</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const v = mode === "published" ? r.published : r.cleaned;
              const label = labelOf(r.raw);
              return (
                <tr key={r.raw} className="border-t border-line/50">
                  <td className="py-1 pr-3 font-mono">{r.raw}</td>
                  <td className="py-1 pr-3 text-ink-2">{label ?? <span className="text-ink-3">{cell.domain ? "not in the domain" : "—"}</span>}</td>
                  <td className="tabular py-1 pr-3 text-right text-ink-3">{r.rows.toLocaleString()}</td>
                  <td className={`py-1 ${v === null ? "text-ink-3" : ""}`}>
                    <span className="font-mono">{fmt(v, unit)}</span>
                    {mode === "cleaned" && v === null && r.steps.length > 0 && <span className="ml-2 text-xs text-ink-3">{STEP[r.steps[r.steps.length - 1]]}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : mode === "cleaned" && rows.length ? (
        <div className="space-y-0.5 font-mono text-sm">
          {rows.map((r) => (
            <div key={r.raw} className="flex gap-2">
              <span className="truncate whitespace-pre text-ink-2">“{r.raw}”</span>
              <span className="text-ink-3">→</span>
              <span>{fmt(r.cleaned, unit)}</span>
            </div>
          ))}
        </div>
      ) : (
        p && (
          <div className="space-y-0.5 font-mono text-sm">
            {p.examples.map((e) => (
              <div key={e}>{e}</div>
            ))}
          </div>
        )
      )}
      {!table && p && (
        <div className="mt-2 text-sm text-ink-3">
          {pct(p.filled_share)} filled · {p.distinct.toLocaleString()} distinct
        </div>
      )}
      {table && !cell.domain && <div className="mt-3 text-sm text-ink-3">This layer publishes no labels for these codes: they land as codes.</div>}
      {cell.blank > 0 && (
        <div className="mt-1 text-sm text-ink-3">
          blank {pct(cell.blank / cell.total)} → {mode === "published" ? "kept as published" : "∅"}
        </div>
      )}
    </div>
  );
}

/** What the model field means: description, vocabulary, notes. Shown on demand. */
function AboutField({ field }: { field: ModelField }) {
  return (
    <div className="space-y-3">
      <div>
        <span className="font-mono">{field.name}</span>
        <span className="text-ink-3">
          {" "}
          · {field.type}
          {field.unit && ` · ${field.unit}`}
        </span>
      </div>
      {field.description && <p>{field.description}</p>}
      {field.values && (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-sm">
          {field.values.map((v) => (
            <div key={v} className="contents">
              <dt className="font-mono">
                {v}
                {field.added?.includes(v) && <span className="ml-1 text-xs text-ink-3" title="Added by a person, not in the shipped model">added</span>}
              </dt>
              <dd className="text-ink-3">{field.meanings?.[v] ?? ""}</dd>
            </div>
          ))}
        </dl>
      )}
      {field.notes && <p className="text-ink-2">{field.notes}</p>}
    </div>
  );
}

// --- the overview: one field, every source, every value where it lands ------------------------

function Overview(props: {
  fields: ModelField[];
  queue: string[];
  cells: ValueCell[];
  publisher: (id: string) => string;
  colorOf: (id: string) => string;
  complete: boolean;
  onNext: () => void;
  onOpen: (x: At) => void;
}) {
  const tabled = props.fields.filter((m) => props.cells.some((c) => c.field === m.name && standardizing(c)));
  const [name, setName] = useState(tabled[0]?.name ?? "");
  const field = tabled.find((m) => m.name === name) ?? tabled[0];
  if (!field) return <div className="p-6 text-ink-3">No field is standardized yet: every value lands as published or cleaned.</div>;
  const cols = props.queue.map((sid) => props.cells.find((c) => c.field === field.name && c.source === sid && standardizing(c))).filter((c): c is ValueCell => !!c);

  // Rows: the vocabulary for an enum, otherwise every value anything landed on; then null.
  const landing = (r: ValueRow): { v: ModelValue; state: "decided" | "suggested" } | null =>
    r.decided ? { v: r.decision, state: "decided" } : r.status === "agree" || r.status === "single" ? { v: r.candidates[0].value, state: "suggested" } : null;
  const seen = cols.flatMap((c) => (c.rows ?? []).map(landing).filter((x) => x && x.v !== null).map((x) => x!.v));
  const outs: ModelValue[] =
    field.type === "enum"
      ? [...(field.values ?? [])]
      : [...new Map(seen.map((v) => [String(v), v])).values()].sort((a, b) => (typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b))));
  const rowsFor = (c: ValueCell, v: ModelValue | "open") =>
    (c.rows ?? []).filter((r) => {
      const l = landing(r);
      return v === "open" ? !l : !!l && sameValue(l.v, v);
    });

  const Chip = ({ c, r }: { c: ValueCell; r: ValueRow }) => {
    const l = landing(r);
    return (
      <button
        onClick={() => props.onOpen({ field: c.field, source: c.source, raw: r.raw })}
        title={`${r.label ? `${r.label} · code ${r.raw}` : r.raw} · ${pct(r.share)}${l?.state === "suggested" ? " · suggested, not decided" : ""}`}
        className={`max-w-[16rem] truncate rounded border px-1.5 py-0.5 text-xs ${r.label ? "" : "font-mono "}${
          !l ? "border-warning/60 text-warning" : l.state === "decided" ? "border-good/50 bg-good/10 text-ink" : "border-dashed border-ink-3 text-ink-3"
        }`}
      >
        {r.label ? (
          <span className="flex items-center gap-1">
            <span className="truncate">{labelText(r.raw, r.label)}</span>
            <DomainTag code={r.raw} />
          </span>
        ) : (
          r.raw
        )}
      </button>
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-line px-5 py-2 text-sm">
        {tabled.map((m) => (
          <button key={m.name} onClick={() => setName(m.name)} className={`rounded-md px-2.5 py-1 font-mono ${m.name === field.name ? "bg-raised text-ink" : "text-ink-3 hover:text-ink-2"}`}>
            {m.name}
          </button>
        ))}
        <div className="flex-1" />
        <span className="flex items-center gap-3 text-xs text-ink-3">
          <span className="rounded border border-good/50 bg-good/10 px-1.5 font-mono text-ink">B</span> decided
          <span className="rounded border border-dashed border-ink-3 px-1.5 font-mono">B</span> suggested
          <span className="rounded border border-warning/60 px-1.5 font-mono text-warning">B</span> open
        </span>
        {props.complete && (
          <button onClick={props.onNext} className="ml-3 rounded-md bg-ink px-3 py-1 font-medium text-surface">
            ✓ Every value decided · Convert →
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="text-sm">
          <thead className="sticky top-0 z-10 bg-raised">
            <tr>
              <th className="px-4 py-2 text-left font-medium text-ink-2">{field.name}</th>
              {cols.map((c) => (
                <th key={c.source} className="min-w-[11rem] px-3 py-2 text-left font-medium text-ink-2">
                  <span className="mr-2 inline-block size-2.5 rounded-full" style={{ background: props.colorOf(c.source) }} />
                  {props.publisher(c.source)}
                  <div className="font-mono text-xs font-normal text-ink-3">{c.source_field}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cols.some((c) => rowsFor(c, "open").length) && (
              <tr className="border-b border-line/50 align-top">
                <td className="px-4 py-2 text-warning">▲ open</td>
                {cols.map((c) => (
                  <td key={c.source} className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {rowsFor(c, "open").map((r) => (
                        <Chip key={r.raw} c={c} r={r} />
                      ))}
                    </div>
                  </td>
                ))}
              </tr>
            )}
            {[...outs, null].map((v) => (
              <tr key={String(v)} className="border-b border-line/50 align-top">
                <td className={`px-4 py-2 font-mono ${v === null ? "text-ink-3" : ""}`}>{fmt(v, field.unit)}</td>
                {cols.map((c) => (
                  <td key={c.source} className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {rowsFor(c, v).map((r) => (
                        <Chip key={r.raw} c={c} r={r} />
                      ))}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// --- formatting --------------------------------------------------------------------------------

function fmt(v: ModelValue, unit?: string | null) {
  if (v === null) return "∅ null";
  return typeof v === "number" && unit ? `${v} ${unit}` : String(v);
}

function sameValue(a: ModelValue, b: ModelValue) {
  return a === null || b === null ? a === b : String(a) === String(b);
}
