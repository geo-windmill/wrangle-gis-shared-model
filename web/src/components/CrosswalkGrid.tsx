// How the sources would merge: shared-model fields down the side, sources across,
// and in each cell the source field suggested to feed it. A first guess from names,
// types and fill rates — Match fields is where it gets checked.
//
// Hover ⓘ for why a field was suggested; hover the fill bar for its top values.
import { useEffect, useState } from "react";
import { api, type Crosswalk, type CrosswalkCell, type ModelField } from "../api";
import { Hover } from "./bits";
import { pct, ValueBars } from "./ValueBars";

type Props = { feature: string; ids: string[]; colorOf: (id: string) => string };

export function CrosswalkGrid({ feature, ids, colorOf }: Props) {
  const [data, setData] = useState<Crosswalk | null>(null);
  const key = ids.join(",");

  useEffect(() => {
    if (!ids.length) return setData(null);
    api.crosswalk(feature, ids).then(setData).catch(() => setData(null));
  }, [feature, key]); // `key` stands in for `ids`, which is a new array every render

  if (!data) return <div className="px-4 py-3 text-ink-3">Add layers to see how they line up.</div>;

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="text-sm">
        <thead className="sticky top-0 z-10 bg-raised">
          <tr>
            <th className="px-3 py-1.5 text-left font-medium text-ink-2">Model field</th>
            {data.sources.map((s) => (
              <th key={s.id} className="min-w-[12rem] px-3 py-1.5 text-left font-medium text-ink-2">
                <span className="mr-2 inline-block size-2.5 rounded-full" style={{ background: colorOf(s.id) }} />
                {s.publisher}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.fields.map((f) =>
            !f.sourced ? (
              <tr key={f.name} className="border-b border-line/50">
                <td className="px-3 py-2 font-mono text-ink-3">{f.name}</td>
                <td colSpan={data.sources.length} className="px-3 py-2 text-ink-3 italic">
                  no source publishes this
                </td>
              </tr>
            ) : (
              <tr key={f.name} className="border-b border-line/50 align-top">
                <td className="px-3 py-2" title={f.description}>
                  <div className="font-mono">{f.name}</div>
                  <div className="text-xs text-ink-3">{f.type}</div>
                </td>
                {data.sources.map((s) => (
                  <td key={s.id} className="px-3 py-2">
                    <Cell cell={data.cells[f.name]?.[s.id] ?? null} field={f} />
                  </td>
                ))}
              </tr>
            ),
          )}
        </tbody>
      </table>
      <div className="px-3 py-2 text-xs text-ink-3">Suggested from field names, types and fill rates — checked in Match fields.</div>
    </div>
  );
}

function Cell({ cell, field }: { cell: CrosswalkCell | null; field: ModelField }) {
  const p = cell?.pick;
  return (
    <>
      {p ? (
        <div>
          <div className="flex items-center gap-1.5">
            <span className="font-mono">{p.source_field}</span>
            <Hover label={`Why ${p.source_field}`} content={<Why cell={cell!} field={field} />}>
              <span className="text-ink-3 hover:text-ink">ⓘ</span>
            </Hover>
          </div>
          <Hover label={`Top values in ${p.source_field}`} content={<TopValues cell={cell!} />}>
            <span className="mt-1 flex items-center gap-2">
              <span className="block h-1.5 w-16 rounded bg-line">
                <span className="block h-1.5 rounded bg-good" style={{ width: `${Math.max(2, p.populated_share * 100)}%` }} />
              </span>
              <span className="tabular text-xs text-ink-3">{Math.round(p.populated_share * 100)}%</span>
            </span>
          </Hover>
        </div>
      ) : (
        <span className="text-ink-3">—</span>
      )}
      {cell?.skipped.map((k) => (
        <div key={k.source_field} className="mt-1 text-xs text-warning">
          skipped {k.source_field} ({pct(k.populated_share)})
        </div>
      ))}
    </>
  );
}

/** Why this field was suggested: the hint words it matched, and what it beat. */
function Why({ cell, field }: { cell: CrosswalkCell; field: ModelField }) {
  const p = cell.pick!;
  const kindPoints: Record<string, string> = { "whole word": "100%", "inside a word": "70%", shortened: "60%" };
  return (
    <div className="space-y-2">
      <div>
        <span className="font-mono">{p.source_field}</span> → <span className="font-mono">{field.name}</span>
        <span className="tabular float-right text-ink-3">{p.match_pct}% name match</span>
      </div>
      <div className="text-ink-2">
        Its {p.why.via === "alias" ? "alias" : "name"} splits into{" "}
        {p.why.words.map((w) => (
          <code key={w} className="mx-0.5 rounded bg-surface px-1">
            {w}
          </code>
        ))}
        {p.why.via === "alias" && " (alias matches count 80%)"}
      </div>
      <ul className="space-y-0.5">
        {p.why.matches.map((m) => (
          <li key={m.hint} className="flex justify-between gap-3">
            <span>
              hint <code className="rounded bg-surface px-1">{m.hint}</code> — {m.kind}
            </span>
            <span className="tabular text-ink-3">{kindPoints[m.kind]}</span>
          </li>
        ))}
      </ul>
      {p.why.matches.length > 1 && <div className="text-xs text-ink-3">Best match counts in full; extra matching hints only break ties.</div>}
      <div className="text-ink-2">
        {p.type} fits {field.type} · {pct(p.populated_share)} filled · {p.distinct.toLocaleString()} distinct values
      </div>
      {(cell.runners_up.length > 0 || cell.skipped.length > 0) && (
        <div className="border-t border-line pt-2">
          <div className="mb-1 text-xs text-ink-3">Passed over</div>
          {cell.skipped.map((k) => (
            <div key={k.source_field} className="flex justify-between gap-3 text-warning">
              <span className="font-mono">{k.source_field}</span>
              <span>only {pct(k.populated_share)} filled</span>
            </div>
          ))}
          {cell.runners_up.map((r) => (
            <div key={r.source_field} className="flex justify-between gap-3 text-ink-2">
              <span className="font-mono">{r.source_field}</span>
              <span className="tabular text-ink-3">
                {r.match_pct}% · {r.reason}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="border-t border-line pt-2 text-xs text-ink-3">A guess from names, not meaning. Match fields checks it against the values.</div>
    </div>
  );
}

/** The five most common values, as a share of every row. */
function TopValues({ cell }: { cell: CrosswalkCell }) {
  const p = cell.pick!;
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs text-ink-3">
        <span>
          Top values in <span className="font-mono">{p.source_field}</span>
        </span>
        <span>{p.distinct.toLocaleString()} distinct</span>
      </div>
      <ValueBars values={p.top_values} populated={p.populated_share} distinct={p.distinct} />
    </div>
  );
}

