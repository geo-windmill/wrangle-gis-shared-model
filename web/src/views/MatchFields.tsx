// Match fields — one decision at a time: a model field, for one source.
//
//   ┌──────────────┬──────────────────────────────────────────────┐
//   │ name     ✓   │ road_class  for Yolo County       2 of 5     │
//   │ road_class   │ ▲ The AI and the name guess disagree.        │
//   │   Sac    ▲   │ ┌ AI pick ──────────┐ ┌ name guess ────────┐ │
//   │ › Yolo       │ │ values, reason    │ │ values             │ │
//   │   West   ✓   │ └───────────────────┘ └────────────────────┘ │
//   │ one_way      │ ┌ every other candidate, and why ┐ …          │
//   │              │ No field fits · About · Table    ← Back  Skip →│
//   └──────────────┴──────────────────────────────────────────────┘
//
// Step through a field's sources, then the next field. "Use" saves and moves on.
// Automap confirms only what two independent opinions agree on with the AI sure.
// The Overview grid is one click away at any time, and is the last step.
// Decisions are saved to each source's configuration record (SQLite: wrangle.db).
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type AiMatch,
  type AiResult,
  type Crosswalk,
  type CrosswalkCell,
  type Decisions,
  type Feature,
  type FieldProfile,
  type ModelField,
  type SourceState,
} from "../api";
import { useColorOf } from "../colors";
import { Hover, Splitter } from "../components/bits";
import { TableDrawer } from "../components/TableDrawer";
import { pct, ValueBars } from "../components/ValueBars";

type Props = { feature: Feature; queue: string[]; onBack: () => void; onNext: () => void };
type View = "review" | "overview";
type Cell = { field: string; source: string };

export default function MatchFields({ feature, queue, onBack, onNext }: Props) {
  const f = feature.feature;
  const [sources, setSources] = useState<SourceState[]>([]);
  const [names, setNames] = useState<Crosswalk | null>(null); // the name guesses
  const [ai, setAi] = useState<Record<string, AiResult>>({});
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decided, setDecided] = useState<Record<string, Decisions>>({});
  const [profiles, setProfiles] = useState<Record<string, FieldProfile[]>>({});
  const [view, setView] = useState<View>("review");
  const [cell, setCell] = useState<Cell | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerFull, setDrawerFull] = useState(false);
  const [drawerH, setDrawerH] = useState(260);
  const [highlight, setHighlight] = useState<string[]>([]); // table columns being compared
  const key = queue.join(",");

  // `key` stands in for `queue`, which is a new array on every render.
  const reloadDecisions = useCallback(() => api.mappings(f, queue).then(setDecided), [f, key]);
  useEffect(() => {
    if (!queue.length) return;
    api.sources(f).then(setSources);
    api.crosswalk(f, queue).then(setNames);
    reloadDecisions();
    api.match(f, queue, true).then(setAi).catch((e) => setError((e as Error).message)); // recorded answers only: free
    for (const sid of queue) api.profile(f, sid).then((p) => setProfiles((m) => ({ ...m, [sid]: p })));
  }, [f, key, reloadDecisions]);

  const fields = names?.fields ?? [];
  const mappable = fields.filter((x) => x.sourced);
  const order: Cell[] = mappable.flatMap((m) => queue.map((sid) => ({ field: m.name, source: sid })));

  // --- helpers over the state ----------------------------------------------------
  const isDecided = (sid: string, field: string) => field in (decided[sid]?.fields ?? {});
  const aiFor = (sid: string, field: string): AiMatch | undefined => ai[sid]?.matches?.find((m) => m.target_field === field);
  const nameFor = (sid: string, field: string): CrosswalkCell | null => names?.cells[field]?.[sid] ?? null;
  const disagrees = (sid: string, field: string) => {
    const a = aiFor(sid, field);
    return !!a && a.source_field !== (nameFor(sid, field)?.pick?.source_field ?? null);
  };
  const needsLook = (sid: string, field: string) => !isDecided(sid, field) && (disagrees(sid, field) || aiFor(sid, field)?.confidence === "low");
  const doneFor = (field: string) => queue.filter((sid) => isDecided(sid, field)).length;
  const total = order.length;
  const done = order.filter((c) => isDecided(c.source, c.field)).length;
  const automappable = order.filter((c) => {
    const a = aiFor(c.source, c.field);
    return !isDecided(c.source, c.field) && a?.confidence === "high" && !disagrees(c.source, c.field);
  }).length;
  const colorOf = useColorOf(f, sources);
  const publisher = (id: string) => sources.find((s) => s.id === id)?.publisher ?? id;
  const anyAi = queue.some((sid) => ai[sid]?.matches);
  const recorded = queue.map((sid) => ai[sid]).find((r) => r?.cached);

  // Once the data arrives, start on the first undecided cell.
  useEffect(() => {
    if (cell || !order.length) return;
    setCell(order.find((c) => !isDecided(c.source, c.field)) ?? order[0]);
  }, [order.length, decided]);

  const at = cell ? order.findIndex((c) => c.field === cell.field && c.source === cell.source) : -1;
  const go = (i: number) => {
    if (i >= order.length) return setView("overview"); // past the last cell is the overview
    if (i >= 0) setCell(order[i]);
    setHighlight([]);
  };
  const nextUndecided = (from: number) => {
    const i = order.findIndex((c, j) => j > from && !isDecided(c.source, c.field));
    return i === -1 ? order.length : i;
  };

  // --- actions ---------------------------------------------------------------------
  // A refused write shows its reason in the header instead of failing silently.
  const attempt = async (run: () => Promise<unknown>) => {
    setError(null);
    try {
      await run();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const decide = (c: Cell, sourceField: string | null) =>
    attempt(async () => {
      await api.decide(f, c.source, c.field, sourceField);
      await reloadDecisions();
      go(nextUndecided(at));
    });
  const undecide = (c: Cell) =>
    attempt(async () => {
      await api.undecide(f, c.source, c.field);
      await reloadDecisions();
    });
  const askAi = async () => {
    setAsking(true);
    await attempt(async () => setAi(await api.match(f, queue, false)));
    setAsking(false);
  };
  const automap = () =>
    attempt(async () => {
      await api.automap(f, queue);
      await reloadDecisions();
    });
  const clearAll = () => {
    if (!window.confirm(`Clear every field match for ${queue.length === 1 ? "this source" : `these ${queue.length} sources`}? Their value decisions go too.`)) return;
    attempt(async () => {
      await api.clearFields(f, queue);
      await reloadDecisions();
      setCell(order[0] ?? null);
      setView("review");
    });
  };

  if (!queue.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-ink-2">
        <div>Nothing queued for ingest yet.</div>
        <button onClick={onBack} className="rounded-md border border-line px-3 py-1.5 hover:bg-raised">
          ← Back to the loading dock
        </button>
      </div>
    );
  }

  const field = fields.find((x) => x.name === cell?.field) ?? null;
  const queuedSources = queue.map((id) => sources.find((s) => s.id === id)).filter((s): s is SourceState => !!s);

  return (
    <div className="flex h-full flex-col">
      {/* header: progress, AI, automap, the two views */}
      <div className="flex items-center gap-3 border-b border-line bg-panel px-5 py-2.5 whitespace-nowrap">
        <div className="text-lg font-semibold">Match fields</div>
        <div className="tabular text-ink-2">
          {done} of {total}
        </div>
        <div className="h-1.5 w-32 shrink-0 rounded bg-line">
          <div className="h-1.5 rounded bg-good" style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
        </div>
        {error && <span className="truncate text-sm text-critical" title={error}>{error}</span>}
        <div className="flex-1" />
        {recorded && (
          <span className="truncate text-sm text-ink-3" title="Replayed from the recording; no API call was made">
            AI recorded {recorded.recorded_at?.slice(0, 10)}
          </span>
        )}
        <button onClick={clearAll} disabled={!done} title="Clear every field match for the queued sources" className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised disabled:opacity-40">
          ↺ Clear
        </button>
        <button onClick={askAi} disabled={asking} className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised disabled:opacity-50">
          {asking ? "Asking…" : anyAi ? "Ask AI again" : "Ask AI"}
        </button>
        <button
          onClick={automap}
          disabled={!automappable}
          title="Confirm every cell where the AI is high-confidence and the name guess agrees"
          className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised disabled:opacity-40"
        >
          Automap {automappable || ""}
        </button>
        <div className="flex rounded-md border border-line p-0.5 text-sm">
          {(["review", "overview"] as const).map((v) => (
            <button key={v} onClick={() => setView(v)} className={`rounded px-2.5 py-1 ${v === view ? "bg-raised text-ink" : "text-ink-3 hover:text-ink-2"}`}>
              {v === "review" ? "One by one" : "Overview"}
            </button>
          ))}
        </div>
      </div>

      {view === "overview" ? (
        <Overview
          fields={fields}
          queue={queue}
          publisher={publisher}
          colorOf={colorOf}
          decided={decided}
          aiFor={aiFor}
          nameFor={(sid, fld) => nameFor(sid, fld)?.pick?.source_field ?? null}
          disagrees={disagrees}
          onOpen={(c) => {
            setCell(c);
            setView("review");
          }}
          complete={done === total}
          onNext={onNext}
        />
      ) : (
        <div className="flex min-h-0 flex-1">
          <Checklist
            fields={fields}
            queue={queue}
            cell={cell}
            publisher={publisher}
            colorOf={colorOf}
            decided={decided}
            doneFor={doneFor}
            needsLook={needsLook}
            onPick={(c) => go(order.findIndex((o) => o.field === c.field && o.source === c.source))}
            onOverview={() => setView("overview")}
          />
          <section className="flex min-w-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto">
              {cell && field && (
                <Decision
                  cell={cell}
                  field={field}
                  step={queue.indexOf(cell.source) + 1}
                  steps={queue.length}
                  publisher={publisher(cell.source)}
                  color={colorOf(cell.source)}
                  profiles={profiles[cell.source]}
                  nameCell={nameFor(cell.source, cell.field)}
                  ai={aiFor(cell.source, cell.field)}
                  aiError={ai[cell.source]?.error}
                  decisions={decided[cell.source]}
                  onDecide={(sf) => decide(cell, sf)}
                  onUndecide={() => undecide(cell)}
                  onBack={() => go(at - 1)}
                  onSkip={() => go(at + 1)}
                  canBack={at > 0}
                  onTable={(cols) => {
                    setHighlight(cols);
                    setDrawerOpen(true);
                  }}
                />
              )}
            </div>
            {drawerOpen && !drawerFull && <Splitter axis="y" onDrag={(d) => setDrawerH((h) => Math.min(700, Math.max(120, h - d)))} />}
            {cell && (
              <TableDrawer
                feature={f}
                layers={queuedSources}
                active={cell.source}
                onActivate={(id) => cell && setCell({ ...cell, source: id })}
                colorOf={colorOf}
                pickedOid={null}
                onPick={() => undefined}
                height={drawerH}
                open={drawerOpen}
                onToggle={() => setDrawerOpen(!drawerOpen)}
                full={drawerFull}
                onFull={() => setDrawerFull(!drawerFull)}
                mode="layers"
                onMode={() => undefined}
                highlight={highlight}
              />
            )}
          </section>
        </div>
      )}
    </div>
  );
}

// --- left: the checklist, with the current field's sources nested under it ------------------

function Checklist(props: {
  fields: ModelField[];
  queue: string[];
  cell: Cell | null;
  publisher: (id: string) => string;
  colorOf: (id: string) => string;
  decided: Record<string, Decisions>;
  doneFor: (f: string) => number;
  needsLook: (sid: string, f: string) => boolean;
  onPick: (c: Cell) => void;
  onOverview: () => void;
}) {
  return (
    <nav className="flex w-60 shrink-0 flex-col border-r border-line bg-panel">
      <ul className="min-h-0 flex-1 overflow-y-auto py-2">
        {props.fields.map((fld) => {
          if (!fld.sourced)
            return (
              <li key={fld.name} className="flex justify-between px-4 py-1.5 text-sm text-ink-3">
                <span className="font-mono">{fld.name}</span>
                <span>empty</span>
              </li>
            );
          const n = props.doneFor(fld.name);
          const all = n === props.queue.length;
          const open = props.cell?.field === fld.name;
          return (
            <li key={fld.name}>
              <button
                onClick={() => props.onPick({ field: fld.name, source: props.queue.find((s) => !(fld.name in (props.decided[s]?.fields ?? {}))) ?? props.queue[0] })}
                className={`flex w-full items-center justify-between gap-2 px-4 py-2 text-left ${open ? "text-ink" : "text-ink-2 hover:bg-raised/50"}`}
              >
                <span className="truncate font-mono">{fld.name}</span>
                <span className={`tabular text-sm ${all ? "text-good" : "text-ink-3"}`}>
                  {all ? "✓ " : ""}
                  {n}/{props.queue.length}
                </span>
              </button>
              {open && (
                <ul className="pb-1">
                  {props.queue.map((sid) => {
                    const d = props.decided[sid];
                    const isDone = d && fld.name in d.fields;
                    const here = props.cell?.source === sid;
                    return (
                      <li key={sid}>
                        <button
                          onClick={() => props.onPick({ field: fld.name, source: sid })}
                          className={`flex w-full items-center gap-2 py-1.5 pr-4 pl-7 text-left text-sm ${here ? "bg-raised text-ink" : "text-ink-2 hover:bg-raised/50"}`}
                        >
                          <span className="size-2 shrink-0 rounded-full" style={{ background: props.colorOf(sid) }} />
                          <span className="flex-1 truncate">{props.publisher(sid)}</span>
                          {isDone ? (
                            <span className="text-good">{d.decided_by[fld.name] === "automap" ? "✓ auto" : "✓"}</span>
                          ) : props.needsLook(sid, fld.name) ? (
                            <span className="text-warning">▲</span>
                          ) : null}
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

// --- centre: one decision ---------------------------------------------------------------

function Decision(props: {
  cell: Cell;
  field: ModelField;
  step: number;
  steps: number;
  publisher: string;
  color: string;
  profiles: FieldProfile[] | undefined;
  nameCell: CrosswalkCell | null;
  ai: AiMatch | undefined;
  aiError?: string;
  decisions: Decisions | undefined;
  onDecide: (sourceField: string | null) => void;
  onUndecide: () => void;
  onBack: () => void;
  onSkip: () => void;
  canBack: boolean;
  onTable: (cols: string[]) => void;
}) {
  const { field, profiles, nameCell, ai } = props;
  const [extra, setExtra] = useState<string[]>([]);
  useEffect(() => setExtra([]), [props.cell.field, props.cell.source]);

  const isDecided = !!props.decisions && field.name in props.decisions.fields;
  const decision = props.decisions?.fields[field.name];
  const aiPick = ai?.source_field ?? null;
  const namePick = nameCell?.pick?.source_field ?? null;
  const disagree = !!ai && aiPick !== namePick;

  // The two opinions come first; every other candidate follows, each saying why
  // it is in the running.
  const featured = [...new Set([aiPick, namePick].filter((x): x is string => !!x))];
  const more = useMemo(() => {
    const ids: string[] = [];
    nameCell?.runners_up.forEach((r) => ids.push(r.source_field));
    nameCell?.skipped.forEach((s) => ids.push(s.source_field));
    extra.forEach((x) => ids.push(x));
    return [...new Set(ids)].filter((x) => !featured.includes(x));
  }, [nameCell, extra, featured.join()]);
  const byName = Object.fromEntries((profiles ?? []).map((p) => [p.name, p]));
  const others = (profiles ?? []).filter((p) => !featured.includes(p.name) && !more.includes(p.name));

  // What the name guess knows about any candidate: its score, which hints it
  // matched and how, and why it was not the pick.
  const nameInfo = (name: string) => {
    if (nameCell?.pick?.source_field === name) return { ...nameCell.pick, reason: "name guess" };
    return [...(nameCell?.runners_up ?? []), ...(nameCell?.skipped ?? [])].find((x) => x.source_field === name) ?? null;
  };
  // One ranking for every card: the AI's pick, then the name guess, then the rest
  // by name score. Fields added by hand come last.
  const ranked = [...featured, ...more].sort((a, b) => {
    const tier = (n: string) => (n === aiPick ? 0 : n === namePick ? 1 : nameInfo(n) ? 2 : 3);
    return tier(a) - tier(b) || (nameInfo(b)?.score ?? 0) - (nameInfo(a)?.score ?? 0);
  });

  const card = (name: string, rank: number) => {
    const p = byName[name];
    if (!p) return null;
    const chosen = isDecided && decision === name;
    const info = nameInfo(name);
    const isAi = name === aiPick;
    return (
      <div key={name} className={`min-w-0 rounded-xl border p-4 ${chosen ? "border-good/60 bg-good/5" : rank === 1 ? "border-ink-3" : "border-line bg-panel"}`}>
        <div className="mb-3 flex items-start gap-3">
          <span className={`tabular rounded-md px-2 py-0.5 text-sm font-semibold ${rank === 1 ? "bg-ink text-surface" : "bg-raised text-ink-2"}`}>#{rank}</span>
          <div className="min-w-0 flex-1 truncate font-mono text-lg" title={p.alias ?? undefined}>
            {p.name}
          </div>
          {chosen ? (
            <button onClick={props.onUndecide} className="rounded-md border border-good/60 px-3 py-1 text-good" title="Undo">
              ✓ Chosen
            </button>
          ) : (
            <button onClick={() => props.onDecide(name)} className="rounded-md bg-ink px-4 py-1 font-medium text-surface">
              Use
            </button>
          )}
        </div>

        {/* the same evidence rows on every card, so they compare at a glance */}
        <dl className="mb-3 grid grid-cols-[4rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
          <dt className="text-ink-3">AI</dt>
          <dd>{isAi && ai ? <>picked · <Confidence c={ai.confidence} /> confidence</> : <span className="text-ink-3">not picked</span>}</dd>
          <dt className="text-ink-3">Name</dt>
          <dd className="min-w-0">
            {info ? (
              <>
                <span className="tabular" title={`score ${info.score.toFixed(2)}`}>{info.match_pct}% name match</span>
                <span className="text-ink-3">
                  {" · "}
                  {info.why.matches.map((m) => `${m.hint} (${m.kind})`).join(", ") || "no hint words"}
                  {info.why.via === "alias" && " · via alias"}
                </span>
                {info.reason !== "name guess" && <div className="text-xs text-ink-3">{info.reason === "nearly empty" ? "passed over: nearly empty" : `passed over: ${info.reason}`}</div>}
              </>
            ) : (
              <span className="text-ink-3">no name match · added by you</span>
            )}
          </dd>
          <dt className="text-ink-3">Fill</dt>
          <dd className={p.populated_share < 0.05 ? "text-warning" : ""}>
            {pct(p.populated_share)} filled · {p.distinct.toLocaleString()} distinct · {p.type}
          </dd>
          {p.domain && (
            <>
              <dt className="text-ink-3">Domain</dt>
              <dd>{Object.keys(p.domain).length} coded values declared</dd>
            </>
          )}
          {p.alias && (
            <>
              <dt className="text-ink-3">Alias</dt>
              <dd className="truncate">“{p.alias}”</dd>
            </>
          )}
        </dl>

        {isAi && ai && <p className="mb-3 text-sm text-ink-2 italic">“{ai.reasoning}”</p>}
        <ValueBars values={p.top_values} populated={p.populated_share} distinct={p.distinct} />
      </div>
    );
  };

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-1 flex items-baseline gap-3">
        <span className="font-mono text-2xl">{field.name}</span>
        <span className="text-ink-3">for</span>
        <span className="flex items-center gap-2 text-lg">
          <span className="size-3 rounded-full" style={{ background: props.color }} />
          {props.publisher}
        </span>
        <span className="flex-1" />
        <span className="tabular text-sm text-ink-3">
          source {props.step} of {props.steps}
        </span>
      </div>
      <div className="mb-4 text-sm">
        {isDecided ? (
          <span className="text-good">
            ✓ Decided: {decision ?? "no field fits"} · {props.decisions?.decided_by[field.name] === "automap" ? "automapped" : "by you"}
          </span>
        ) : props.aiError ? (
          <span className="text-critical">The AI failed for this source: {props.aiError}</span>
        ) : !ai ? (
          <span className="text-ink-3">No AI answer yet — the name guess is shown. Use “Ask AI” above.</span>
        ) : disagree ? (
          <span className="text-warning">▲ The AI and the name guess disagree. Compare the values and pick one.</span>
        ) : (
          <span className="text-ink-2">The AI and the name guess agree.</span>
        )}
      </div>

      {!profiles && <div className="text-ink-3">Reading fields…</div>}
      {profiles && (
        <>
          {featured.length === 0 && ai?.source_field === null && (
            <div className="rounded-xl border border-line bg-panel p-4">
              <div>
                AI: <span className="italic">no field fits</span> · <Confidence c={ai.confidence} />
              </div>
              <p className="mt-1 text-sm text-ink-2 italic">“{ai.reasoning}”</p>
            </div>
          )}
          {featured.length === 0 && !ai && <div className="text-ink-3">Nothing looks like a match.</div>}
          <div className="grid grid-cols-[repeat(auto-fill,minmax(22rem,1fr))] gap-4">
            {ranked.map((name, i) => card(name, i + 1))}
          </div>
          {disagree && aiPick === null && namePick && (
            <p className="mt-3 text-sm text-ink-2">
              AI: <span className="italic">no field fits</span> · <Confidence c={ai!.confidence} /> — “{ai!.reasoning}”
            </p>
          )}

          {others.length > 0 && (
            <select
              value=""
              onChange={(e) => e.target.value && setExtra((x) => [...x, e.target.value])}
              className="mt-4 block w-full max-w-md rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink-2"
              aria-label="Compare another field"
            >
              <option value="">+ Compare any other field…</option>
              {others.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name} — {pct(p.populated_share)} filled
                </option>
              ))}
            </select>
          )}
        </>
      )}

      <div className="mt-6 flex items-center gap-2 border-t border-line pt-4 text-sm">
        <button
          onClick={() => (isDecided && decision === null ? props.onUndecide() : props.onDecide(null))}
          className={`rounded-md border px-3 py-1.5 ${isDecided && decision === null ? "border-good/60 text-good" : "border-line text-ink-2 hover:bg-raised"}`}
        >
          {isDecided && decision === null ? "✓ No field fits" : "No field fits"}
        </button>
        <Hover label={`About ${field.name}`} content={<AboutField field={field} />}>
          <span className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised">About {field.name}</span>
        </Hover>
        <button onClick={() => props.onTable([...featured, ...more])} className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised">
          Table
        </button>
        <span className="flex-1" />
        <button onClick={props.onBack} disabled={!props.canBack} className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised disabled:opacity-30">
          ← Back
        </button>
        <button onClick={props.onSkip} className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised">
          Skip →
        </button>
      </div>
    </div>
  );
}

function Confidence({ c }: { c: AiMatch["confidence"] }) {
  const tone = { high: "text-good", medium: "text-warning", low: "text-serious" }[c];
  return <span className={tone}>{c}</span>;
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
        <div className="flex flex-wrap gap-1">
          {field.values.map((v) => (
            <span key={v} className="rounded border border-line px-1.5 font-mono text-xs">
              {v}
            </span>
          ))}
        </div>
      )}
      {field.notes && <p className="text-ink-2">{field.notes}</p>}
    </div>
  );
}

// --- the overview: every decision on one screen -------------------------------------------

type Filter = "all" | "undecided" | "disagree" | "automapped";

function Overview(props: {
  fields: ModelField[];
  queue: string[];
  publisher: (id: string) => string;
  colorOf: (id: string) => string;
  decided: Record<string, Decisions>;
  aiFor: (sid: string, f: string) => AiMatch | undefined;
  nameFor: (sid: string, f: string) => string | null;
  disagrees: (sid: string, f: string) => boolean;
  onOpen: (c: Cell) => void;
  complete: boolean;
  onNext: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const shows = (sid: string, fld: string) => {
    const d = props.decided[sid];
    const done = d && fld in d.fields;
    if (filter === "undecided") return !done;
    if (filter === "disagree") return !done && props.disagrees(sid, fld);
    if (filter === "automapped") return done && d.decided_by[fld] === "automap";
    return true;
  };
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-line px-5 py-2 text-sm">
        <span className="text-ink-3">Show</span>
        {(["all", "undecided", "disagree", "automapped"] as const).map((x) => (
          <button key={x} onClick={() => setFilter(x)} className={`rounded-md px-2.5 py-1 ${x === filter ? "bg-raised text-ink" : "text-ink-3 hover:text-ink-2"}`}>
            {x}
          </button>
        ))}
        <div className="flex-1" />
        {props.complete ? (
          <button onClick={props.onNext} className="rounded-md bg-ink px-3 py-1 font-medium text-surface">
            ✓ Every field decided · Edit values →
          </button>
        ) : (
          <span className="text-ink-3">Click any cell to decide it</span>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="text-sm">
          <thead className="sticky top-0 z-10 bg-raised">
            <tr>
              <th className="px-4 py-2 text-left font-medium text-ink-2">Model field</th>
              {props.queue.map((sid) => (
                <th key={sid} className="min-w-[13rem] px-3 py-2 text-left font-medium text-ink-2">
                  <span className="mr-2 inline-block size-2.5 rounded-full" style={{ background: props.colorOf(sid) }} />
                  {props.publisher(sid)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {props.fields.map((fld) =>
              !fld.sourced ? (
                <tr key={fld.name} className="border-b border-line/50">
                  <td className="px-4 py-2 font-mono text-ink-3">{fld.name}</td>
                  <td colSpan={props.queue.length} className="px-3 py-2 text-ink-3 italic">
                    no source publishes this — ships empty
                  </td>
                </tr>
              ) : (
                <tr key={fld.name} className="border-b border-line/50 align-top">
                  <td className="px-4 py-2.5 font-mono">{fld.name}</td>
                  {props.queue.map((sid) => {
                    if (!shows(sid, fld.name)) return <td key={sid} />;
                    const d = props.decided[sid];
                    const done = d && fld.name in d.fields;
                    const a = props.aiFor(sid, fld.name);
                    return (
                      <td key={sid} className="p-1">
                        <button
                          onClick={() => props.onOpen({ field: fld.name, source: sid })}
                          className="w-full rounded-md border border-transparent px-2.5 py-2 text-left hover:border-line hover:bg-raised/50"
                        >
                          {done ? (
                            <div>
                              <span className="text-good">✓ </span>
                              {d.fields[fld.name] ? <span className="font-mono">{d.fields[fld.name]}</span> : <span className="text-ink-3 italic">no field fits</span>}
                              <div className="text-xs text-ink-3">{d.decided_by[fld.name] === "automap" ? "automapped" : "by you"}</div>
                            </div>
                          ) : (
                            <div className="space-y-0.5">
                              <div className="text-ink-2">
                                <span className="mr-2 text-xs text-ink-3">name</span>
                                <span className="font-mono">{props.nameFor(sid, fld.name) ?? "—"}</span>
                              </div>
                              <div>
                                <span className="mr-2 text-xs text-ink-3">AI</span>
                                <span className="font-mono">{a ? (a.source_field ?? "none") : "—"}</span>
                                {a && (
                                  <span className="ml-1 text-xs">
                                    · <Confidence c={a.confidence} />
                                  </span>
                                )}
                              </div>
                              {props.disagrees(sid, fld.name) && <div className="text-xs text-warning">▲ disagree</div>}
                            </div>
                          )}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
