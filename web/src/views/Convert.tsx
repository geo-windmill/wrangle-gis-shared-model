// Convert — every queued source into the shared model, then one layer, then a query.
//
//   ┌ sources ─────────────────┬ map: the unified layer, coloured by road_class ─┐
//   │ ● Sacramento County  ✓   │                                                  │
//   │ ● Yolo County  ▲ 1,273   │                                                  │
//   │   name       ███████ 99% ├ DuckDB ─────────────────┬ result ────────────────┤
//   │   road_class ██████  89% │ SELECT s.publisher, …   │ class × publisher      │
//   └──────────────────────────┴─────────────────────────┴────────────────────────┘
//
// "Convert" runs each source in turn by its reviewed decisions, writes one GeoParquet
// per source and a unified one, then runs the query over the unified file: preloaded
// queries, editable, and a Data tab with the converted rows themselves. Every null
// a conversion leaves says why; undecided values are named, never silently dropped.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ConvertReport, type ConvertStatus, type Feature, type FieldReport, type ModelField, type QueryResult, type RowsPage, type SavedQuery, type SourceState } from "../api";
import { CONVERTED, useColorOf, type MapClass } from "../colors";
import { Eye, Splitter } from "../components/bits";
import { pct } from "../components/ValueBars";
import { useSourceMap } from "../map/useSourceMap";

type Props = { feature: Feature; queue: string[]; onBack: () => void };
type Job = "waiting" | "converting" | "done" | "error";

// Why a row is null, in a word or two. Open reasons mean the review isn't finished.
const REASON: Record<string, string> = {
  blank: "blank",
  no_field: "no field fits",
  unpublished: "nobody publishes",
  unmatched: "not matched",
  decided_null: "decided null",
  undecided: "undecided",
  no_rule: "no date rule",
  unreadable: "unreadable",
  placeholder: "placeholder",
  placeholder_text: "placeholder",
  out_of_range: "out of range",
  refused: "refused",
};
const MODE: Record<string, string> = { published: "as published", cleaned: "cleaned", standardized: "standardized" };
const OPEN = new Set(["unmatched", "undecided", "no_rule"]);

export default function Convert({ feature, queue, onBack }: Props) {
  const f = feature.feature;
  const [sources, setSources] = useState<SourceState[]>([]);
  const [fields, setFields] = useState<ModelField[]>([]);
  const [status, setStatus] = useState<ConvertStatus | null>(null);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const key = queue.join(",");

  const reload = useCallback(() => api.convertStatus(f, queue).then(setStatus), [f, key]);
  useEffect(() => {
    if (!queue.length) return;
    api.sources(f).then(setSources);
    api.crosswalk(f, queue).then((c) => setFields(c.fields));
    reload();
  }, [f, key, reload]);

  const reportOf = (id: string) => status?.sources.find((s) => s.id === id)?.report ?? null;
  const converted = queue.filter((id) => reportOf(id));
  const colorOf = useColorOf(f, sources);

  const convertAll = async () => {
    setRunning(true);
    setError(null);
    setJobs(Object.fromEntries(queue.map((id) => [id, "waiting"])));
    try {
      // One at a time, so the list shows each source land in turn.
      const done: string[] = [];
      for (const id of queue) {
        setJobs((j) => ({ ...j, [id]: "converting" }));
        try {
          await api.convert(f, id);
          setJobs((j) => ({ ...j, [id]: "done" }));
          done.push(id);
        } catch (e) {
          setJobs((j) => ({ ...j, [id]: "error" }));
          setError(`${id}: ${(e as Error).message}`);
        }
        await reload();
      }
      // Only what converted just now: a failed source's last output is out of date.
      if (!done.length) return;
      await api.unify(f, done);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  // --- the map: converted layers, all one colour --------------------------------------------
  const classes = CONVERTED;
  const versions = Object.fromEntries((status?.sources ?? []).map((s) => [s.id, s.report?.converted_at ?? ""]));
  const dataUrl = useCallback((id: string) => api.convertedUrl(f, id, versions[id] ?? ""), [f, JSON.stringify(versions)]);
  const order = useMemo(() => converted, [converted.join(",")]);
  // A click shows the converted row(s) under the pointer, one per source, as they landed.
  const mapApi = useRef<ReturnType<typeof useSourceMap> | null>(null);
  const shown = converted.filter((id) => !hidden.has(id));
  const pick = useCallback(
    async (lng: number, lat: number, tol: number) => {
      const m = mapApi.current;
      if (!m) return;
      const { hits } = await api.convertedAt(f, lng, lat, tol, shown).catch(() => ({ hits: [] }));
      m.outline(hits.map((h) => h.geometry));
      m.popup(lng, lat, hits.length ? popupContent(hits, fields, colorOf) : null);
    },
    [f, shown.join(","), fields, colorOf],
  );
  const mapHook = useSourceMap({
    feature: f,
    sources,
    order,
    hidden,
    basemap: true,
    onClickPoint: pick,
    dataUrl,
    classes,
  });
  mapApi.current = mapHook;
  const { container } = mapHook;

  if (!queue.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-ink-2">
        <div>Nothing queued for ingest yet.</div>
      </div>
    );
  }

  const open_rows = converted.reduce((n, id) => n + (reportOf(id)?.open_rows ?? 0), 0);
  // A conversion older than its decisions (or its download) no longer shows what was decided.
  const stale = converted.filter((id) => status?.sources.find((x) => x.id === id)?.stale);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-line bg-panel px-5 py-2.5 whitespace-nowrap">
        <div className="text-lg font-semibold">Convert</div>
        <div className="tabular text-ink-2">
          {converted.length} of {queue.length}
        </div>
        {status?.unified && <span className="font-mono text-sm text-ink-3">{status.unified.path}</span>}
        {stale.length > 0 && !running && (
          <button onClick={convertAll} className="rounded-md border border-warning px-2.5 py-1 text-sm text-warning hover:bg-raised" title="A decision or the download changed since the last conversion">
            ↻ {stale.length === converted.length ? "Out of date" : `${stale.length} out of date`} · Convert again
          </button>
        )}
        {open_rows > 0 && !running && !stale.length && (
          <button onClick={onBack} className="text-sm text-warning hover:underline" title="Rows left null because a value or rule is not decided yet">
            ▲ {open_rows.toLocaleString()} rows open · Edit values
          </button>
        )}
        {error && <span className="truncate text-sm text-critical" title={error}>{error}</span>}
        <div className="flex-1" />
        <button onClick={convertAll} disabled={running} className="rounded-md bg-ink px-4 py-1.5 font-medium text-surface disabled:opacity-50">
          {running ? "Converting…" : converted.length ? "Convert again" : "Convert"}
        </button>
      </div>

      <div className="flex min-h-0 flex-1">
        <nav className="flex w-[26rem] shrink-0 flex-col overflow-y-auto border-r border-line bg-panel">
          {queue.map((id) => {
            const s = status?.sources.find((x) => x.id === id);
            const r = s?.report ?? null;
            const job = jobs[id];
            const isOpen = open === id;
            return (
              <div key={id} className="border-b border-line/60">
                <div className={`flex items-center gap-2 px-4 py-2.5 ${isOpen ? "bg-raised" : "hover:bg-raised/50"}`}>
                  <button
                    onClick={() => setHidden((h) => (h.has(id) ? new Set([...h].filter((x) => x !== id)) : new Set(h).add(id)))}
                    disabled={!r}
                    className="text-ink-3 hover:text-ink disabled:opacity-30"
                    aria-label={hidden.has(id) ? "Show on map" : "Hide on map"}
                  >
                    <Eye open={!hidden.has(id)} />
                  </button>
                  <button onClick={() => setOpen(isOpen ? null : id)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                    <span className="size-2.5 shrink-0 rounded-full" style={{ background: colorOf(id) }} />
                    <span className="flex-1 truncate">{s?.publisher ?? id}</span>
                    <Verdict job={job} report={r} stale={!!s?.stale} />
                  </button>
                </div>
                {isOpen && r && <SourceReport report={r} fields={fields} onEdit={onBack} />}
              </div>
            );
          })}
        </nav>

        <section className="flex min-w-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1">
            {/* h-full, not absolute: MapLibre's own CSS makes its container position: relative */}
            <div ref={container} className="h-full w-full" />
            {classes && converted.length > 0 && <Legend field={`${f}.parquet`} classes={classes} point={feature.geometry === "point"} />}
          </div>
          {/* Always there, so the map is sized once and the area of interest fits. */}
          <BottomPanel
            f={f}
            queries={status?.queries ?? []}
            built={status?.unified?.built ?? null}
            fields={fields}
            field={status?.map_field ?? null}
            order={queue.map((id) => status?.sources.find((x) => x.id === id)?.publisher ?? id)}
            sources={converted.map((id) => ({ id, publisher: status?.sources.find((x) => x.id === id)?.publisher ?? id, color: colorOf(id) }))}
          />
        </section>
      </div>
    </div>
  );
}

function Verdict({ job, report, stale }: { job?: Job; report: ConvertReport | null; stale: boolean }) {
  if (job === "converting") return <span className="text-sm text-ink-2">converting…</span>;
  if (job === "waiting") return <span className="text-sm text-ink-3">waiting</span>;
  if (job === "error") return <span className="text-sm text-critical">failed</span>;
  if (!report) return <span className="text-sm text-ink-3">—</span>;
  const rows = <span className="tabular text-ink-3">{report.rows.toLocaleString()}</span>;
  if (stale) return <span className="text-sm text-warning" title="A decision or the download changed since this conversion">↻ {rows}</span>;
  if (report.verdict === "partial") return <span className="text-sm text-critical" title={`Fetched ${report.fetched.fetched} of ${report.fetched.expected}`}>▲ partial</span>;
  if (report.verdict === "gaps")
    return (
      <span className="flex items-center gap-2 text-sm">
        {rows}
        <span className="tabular text-warning" title="Rows with a value nobody has decided yet">
          ▲ {report.open_rows.toLocaleString()}
        </span>
      </span>
    );
  return (
    <span className="flex items-center gap-2 text-sm">
      {rows}
      <span className="text-good">✓</span>
    </span>
  );
}

/** Every model field of one source: what filled it, and why each empty row is empty. */
function SourceReport({ report, fields, onEdit }: { report: ConvertReport; fields: ModelField[]; onEdit: () => void }) {
  const byName = Object.fromEntries(fields.map((m) => [m.name, m]));
  return (
    <div className="space-y-2.5 px-4 pt-1 pb-4 text-sm">
      {report.fields.map((fr) => (
        <FieldLine key={fr.field} fr={fr} rows={report.rows} unsourced={byName[fr.field]?.sourced === false} onEdit={onEdit} />
      ))}
      <div className="text-xs text-ink-3">converted {report.converted_at.slice(0, 16).replace("T", " ")} UTC</div>
    </div>
  );
}

function FieldLine({ fr, rows, unsourced, onEdit }: { fr: FieldReport; rows: number; unsourced: boolean; onEdit: () => void }) {
  const share = rows ? fr.filled / rows : 0;
  const reasons = Object.entries(fr.nulls).filter(([k]) => !(unsourced && k === "unpublished"));
  return (
    <div className={unsourced || fr.how === "none" ? "opacity-50" : ""}>
      <div className="grid grid-cols-[7.5rem_minmax(0,1fr)_3rem] items-center gap-2">
        <span className="truncate font-mono">{fr.field}</span>
        <span className="block h-2 rounded bg-surface">
          <span className="block h-2 rounded bg-good" style={{ width: `${share * 100}%` }} />
        </span>
        <span className="tabular text-right text-ink-2">{pct(share)}</span>
      </div>
      <div className="pl-[8rem] text-xs text-ink-3">
        {unsourced ? (
          "nobody publishes it"
        ) : fr.how === "none" ? (
          "no field fits"
        ) : (
          <>
            {fr.source_field && <span className="font-mono text-ink-2">{fr.source_field}</span>}
            {fr.mode && <span> · {MODE[fr.mode]}</span>}
            {fr.rule && <span> · {fr.rule}</span>}
            {reasons.map(([k, n]) => (
              <span key={k} className={OPEN.has(k) ? "text-warning" : ""}>
                {" · "}
                {OPEN.has(k) && "▲ "}
                {REASON[k] ?? k} {pct(n / rows)}
              </span>
            ))}
          </>
        )}
      </div>
      {fr.undecided.length > 0 && (
        <button onClick={onEdit} className="mt-0.5 ml-[8rem] block truncate text-left text-xs text-warning hover:underline" title="Decide these in Edit values">
          {fr.undecided
            .slice(0, 4)
            .map((u) => u.raw)
            .join(", ")}
          {fr.undecided.length > 4 && ` +${fr.undecided.length - 4}`} →
        </button>
      )}
    </div>
  );
}

function Legend({ field, classes, point }: { field: string; classes: MapClass[]; point: boolean }) {
  return (
    <div className="absolute bottom-8 left-3 rounded-lg border border-line bg-panel/90 px-3 py-2 text-sm">
      <div className="mb-1 font-mono text-xs text-ink-3">{field}</div>
      {classes.map((c) => (
        <div key={c.label} className="flex items-center gap-2" title={c.values.map((v) => v ?? "null").join(", ")}>
          {point ? (
            <span className="size-2.5 rounded-full" style={{ background: c.color }} />
          ) : (
            <span className="h-0 w-5 rounded" style={{ borderTop: `${Math.max(1.5, (c.width ?? 1) * 1.5)}px solid ${c.color}` }} />
          )}
          <span className="text-ink-2">{c.label}</span>
        </div>
      ))}
    </div>
  );
}

/** Below the map: the query editor, and the converted rows themselves. Resizable. */
function BottomPanel(props: {
  f: string;
  queries: SavedQuery[];
  built: number | null;
  fields: ModelField[];
  field: string | null;
  order: string[];
  sources: { id: string; publisher: string; color: string }[];
}) {
  const [tab, setTab] = useState<"query" | "data">("query");
  const [height, setHeight] = useState(288);
  return (
    <>
      <Splitter axis="y" onDrag={(d) => setHeight((h) => Math.min(Math.max(h - d, 140), window.innerHeight - 200))} />
      <div className="flex shrink-0 flex-col bg-panel" style={{ height }}>
        <div className="flex items-center gap-1 border-b border-line px-3 py-1 text-sm">
          {(["query", "data"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`rounded px-2.5 py-1 ${t === tab ? "bg-raised text-ink" : "text-ink-3 hover:text-ink-2"}`}>
              {t === "query" ? "Query" : "Data"}
            </button>
          ))}
        </div>
        <div className="flex min-h-0 flex-1">
          {/* Both stay mounted, so switching tabs keeps the SQL, the answer and the page. */}
          <div className={tab === "query" ? "flex min-w-0 flex-1" : "hidden"}>
            <QueryPanel f={props.f} queries={props.queries} built={props.built} fields={props.fields} field={props.field} order={props.order} />
          </div>
          <div className={tab === "data" ? "flex min-w-0 flex-1" : "hidden"}>
            <DataPanel f={props.f} built={props.built} sources={props.sources} />
          </div>
        </div>
      </div>
    </>
  );
}

/** An editable SQL box with preloaded queries, run by DuckDB over the converted files.
 *  The first preloaded query is drawn pivoted: model values down, publishers across.
 *  No total across publishers: they overlap (Elk Grove sits inside Sacramento County,
 *  Caltrans on top of it), and the unified layer stacks them on purpose. */
function QueryPanel({ f, queries, built, fields, field, order }: { f: string; queries: SavedQuery[]; built: number | null; fields: ModelField[]; field: string | null; order: string[] }) {
  const [pick, setPick] = useState(0);
  const [sql, setSql] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const preset = queries[pick];
  const edited = !!preset && sql !== preset.sql;

  const run = async (text: string) => {
    if (!built || !text.trim()) return;
    setRunning(true);
    setError(null);
    try {
      setResult(await api.query(f, text));
    } catch (e) {
      setResult(null);
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };
  const choose = (i: number) => {
    setPick(i);
    setSql(queries[i].sql);
    run(queries[i].sql);
  };

  // Load the first query once the presets arrive; rerun what's in the box after each conversion.
  useEffect(() => {
    if (queries.length && !sql) setSql(queries[0].sql);
  }, [queries.length]);
  useEffect(() => {
    run(sql || queries[0]?.sql || "");
  }, [built, queries.length]);

  const pivoted = !!preset?.pivot && !edited && result && result.columns.length >= 3;
  return (
    <>
      <div className="flex w-[32rem] shrink-0 flex-col border-r border-line">
        <div className="flex items-center gap-2 px-3 py-1.5 text-sm">
          <select value={pick} onChange={(e) => choose(Number(e.target.value))} className="min-w-0 flex-1 truncate rounded border border-line bg-surface px-2 py-1 text-ink">
            {queries.map((q, i) => (
              <option key={q.title} value={i}>
                {q.title}
              </option>
            ))}
          </select>
          {edited && (
            <button onClick={() => setSql(preset.sql)} className="text-ink-3 hover:text-ink-2" title="Back to the query as preloaded">
              ↺
            </button>
          )}
          <button onClick={() => run(sql)} disabled={!built || running} title="Run (Ctrl+Enter)" className="rounded-md bg-ink px-3 py-1 font-medium text-surface disabled:opacity-40">
            {running ? "…" : "▶ Run"}
          </button>
        </div>
        <textarea
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              run(sql);
            }
          }}
          spellCheck={false}
          className="min-h-0 flex-1 resize-none bg-transparent px-4 pb-3 font-mono text-[0.8rem] leading-snug text-ink-2 outline-none"
        />
        <div className="border-t border-line px-4 py-1 text-xs text-ink-3">
          DuckDB{result ? ` · ${result.ms} ms` : ""} · reads <span className="font-mono">out/</span> only
        </div>
      </div>
      <div className="min-w-0 flex-1 overflow-auto">
        {!built ? (
          <div className="p-4 text-ink-3">Convert to see the answer.</div>
        ) : error ? (
          <pre className="p-4 font-mono text-sm whitespace-pre-wrap text-critical">{error}</pre>
        ) : !result ? null : pivoted ? (
          <Pivot result={result} fields={fields} field={field} order={order} />
        ) : (
          <Table columns={result.columns} rows={result.rows} footer={result.truncated ? "First 1,000 rows" : `${result.rows.length.toLocaleString()} rows`} />
        )}
      </div>
    </>
  );
}

function Pivot({ result, fields, field, order }: { result: QueryResult; fields: ModelField[]; field: string | null; order: string[] }) {
  const [hi, setHi] = useState<string | null>(null);
  const pivot = useMemo(() => {
    const [pubCol, valCol, numCol] = [0, 1, 2]; // publisher, model value, measure
    const pubs = [...new Set(result.rows.map((r) => String(r[pubCol])))];
    pubs.sort((a, b) => order.indexOf(a) - order.indexOf(b)); // queue order, like the list
    const vocab = fields.find((m) => m.name === field)?.values ?? [];
    const seen = [...new Set(result.rows.map((r) => r[valCol] as string | null))];
    const vals = [...vocab.filter((v) => seen.includes(v)), ...seen.filter((v) => v !== null && !vocab.includes(v as string)), ...(seen.includes(null) ? [null] : [])];
    const cell = (v: string | null, p: string) => result.rows.find((r) => r[valCol] === v && String(r[pubCol]) === p)?.[numCol] as number | undefined;
    return { pubs, vals, cell };
  }, [result, fields, field, order]);
  const fmt = (n: number | undefined) => (n === undefined ? "" : n.toLocaleString(undefined, { maximumFractionDigits: 1 }));
  return (
    <table className="text-sm">
      <thead className="sticky top-0 bg-raised">
        <tr>
          <th className="px-3 py-1.5 text-left font-mono font-normal text-ink-3">{field}</th>
          {pivot.pubs.map((p) => (
            <th key={p} className="px-3 py-1.5 text-right font-medium text-ink-2">
              {p}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {pivot.vals.map((v) => {
          const k = String(v);
          return (
            <tr key={k} onMouseEnter={() => setHi(k)} onMouseLeave={() => setHi(null)} className={`border-b border-line/40 ${hi === k ? "bg-raised/60" : ""}`}>
              <td className={`px-3 py-1 font-mono ${v === null ? "text-ink-3" : ""}`}>{v ?? "∅ null"}</td>
              {pivot.pubs.map((p) => (
                <td key={p} className="tabular px-3 py-1 text-right">
                  {fmt(pivot.cell(v, p))}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const ID = /(^|_)(oid|id|objectid)$/i; // identifiers read as written, no thousands separator

/** Any answer, as a plain table. Numbers right-aligned; nulls marked. */
function Table({ columns, rows, footer }: { columns: string[]; rows: (string | number | boolean | null)[][]; footer?: string }) {
  return (
    <>
      <table className="text-sm">
        <thead className="sticky top-0 bg-raised">
          <tr>
            {columns.map((c) => (
              <th key={c} className="px-3 py-1.5 text-left font-mono font-normal whitespace-nowrap text-ink-3">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-line/40 hover:bg-raised/60">
              {r.map((v, j) => (
                <td key={j} className={`max-w-[24rem] truncate px-3 py-1 whitespace-nowrap ${typeof v === "number" ? "tabular text-right" : "font-mono"} ${v === null ? "text-ink-3" : ""}`} title={v === null ? "null" : String(v)}>
                  {v === null ? "∅" : typeof v === "number" && !ID.test(columns[j]) ? v.toLocaleString(undefined, { maximumFractionDigits: 3 }) : String(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {footer && <div className="px-3 py-2 text-xs text-ink-3">{footer}</div>}
    </>
  );
}

const PAGE = 100;

/** The converted rows themselves, a page at a time: what landed in each model field,
 *  with where it came from. Geometry is on the map, so it's left out here. */
function DataPanel({ f, built, sources }: { f: string; built: number | null; sources: { id: string; publisher: string; color: string }[] }) {
  const [source, setSource] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<RowsPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!built) return;
    setError(null);
    api.convertedRows(f, source, offset, PAGE).then(setPage, (e) => setError((e as Error).message));
  }, [f, built, source, offset]);
  const pickSource = (id: string | null) => {
    setSource(id);
    setOffset(0);
  };
  if (!built) return <div className="p-4 text-ink-3">Convert to see the data.</div>;
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-1.5 border-b border-line px-3 py-1.5 text-sm">
        <button onClick={() => pickSource(null)} className={`rounded px-2 py-0.5 ${source === null ? "bg-raised text-ink" : "text-ink-3 hover:text-ink-2"}`}>
          All
        </button>
        {sources.map((s) => (
          <button key={s.id} onClick={() => pickSource(s.id)} className={`flex items-center gap-1.5 rounded px-2 py-0.5 ${source === s.id ? "bg-raised text-ink" : "text-ink-3 hover:text-ink-2"}`}>
            <span className="size-2 rounded-full" style={{ background: s.color }} />
            {s.publisher}
          </button>
        ))}
        <span className="flex-1" />
        {page && (
          <>
            <span className="tabular text-ink-3">
              {page.total ? `${(offset + 1).toLocaleString()}–${Math.min(offset + PAGE, page.total).toLocaleString()} of ${page.total.toLocaleString()}` : "no rows"}
            </span>
            <button onClick={() => setOffset((o) => Math.max(0, o - PAGE))} disabled={offset === 0} className="px-1.5 text-ink-2 disabled:opacity-30" aria-label="Previous page">
              ‹
            </button>
            <button onClick={() => setOffset((o) => o + PAGE)} disabled={offset + PAGE >= page.total} className="px-1.5 text-ink-2 disabled:opacity-30" aria-label="Next page">
              ›
            </button>
          </>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">{error ? <div className="p-4 text-critical">{error}</div> : page && <Table columns={page.columns} rows={page.rows} />}</div>
    </div>
  );
}

/** The popup on the converted map: each source's row under the click, model field by
 *  model field, as it landed. Built as DOM, since MapLibre owns the popup. */
function popupContent(
  hits: { id: string; publisher: string; row: Record<string, string | number | boolean | null> }[],
  fields: ModelField[],
  colorOf: (id: string) => string,
): HTMLElement {
  const el = (tag: string, cls: string, text?: string) => {
    const e = document.createElement(tag);
    e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const root = el("div", "space-y-3 text-sm");
  for (const h of hits) {
    const block = el("div", "");
    const head = el("div", "mb-1 flex items-center gap-2 font-medium");
    const dot = el("span", "size-2.5 shrink-0 rounded-full");
    dot.style.background = colorOf(h.id);
    head.append(dot, el("span", "", h.publisher), el("span", "font-mono text-xs text-ink-3", `#${h.row.source_oid}`));
    const grid = el("dl", "grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3");
    for (const fld of fields) {
      if (!fld.sourced) continue;
      const v = h.row[fld.name];
      grid.append(el("dt", "font-mono text-ink-3", fld.name), el("dd", v === null || v === undefined ? "text-ink-3" : "font-mono", v === null || v === undefined ? "∅" : String(v)));
    }
    block.append(head, grid);
    root.append(block);
  }
  return root;
}
