// The same spot, as every source describes it. Rows are the shared model's fields,
// columns are the sources with a feature under the click; each cell shows the raw
// value and the source field it came from (per the suggested crosswalk).
import { useState } from "react";
import type { Attribute, Compare } from "../api";
import { useDragPosition } from "./bits";
import { StatusChip } from "./StatusChip";

type Props = { data: Compare; colorOf: (id: string) => string; onClose: () => void };

export function ComparePanel({ data, colorOf, onClose }: Props) {
  const [allFields, setAllFields] = useState(false);
  const drag = useDragPosition();
  const here = data.sources.filter((s) => s.hit);
  const missing = data.sources.filter((s) => !s.hit);
  const sourced = data.fields.filter((f) => f.sourced);
  const unsourced = data.fields.filter((f) => !f.sourced);

  return (
    <div style={drag.style} className="absolute z-10 flex max-h-[calc(100%-1.5rem)] max-w-[calc(100%-1.5rem)] flex-col rounded-xl border border-line bg-panel/95 shadow-2xl backdrop-blur">
      <div {...drag.handle} className={`flex items-center gap-3 border-b border-line px-4 py-2.5 ${drag.handle.className}`}>
        <div className="flex-1 font-semibold">
          {here.length === 1 ? "1 source here" : `${here.length} sources here`}
          {missing.length > 0 && <span className="ml-2 text-sm font-normal text-ink-3">not here: {missing.map((s) => s.publisher).join(", ")}</span>}
        </div>
        <button onClick={() => setAllFields(!allFields)} className="text-sm text-ink-2 hover:text-ink">
          {allFields ? "Model fields" : "All fields"}
        </button>
        <button onClick={onClose} className="px-1 text-xl text-ink-3 hover:text-ink" aria-label="Close">
          ×
        </button>
      </div>
      <div className="overflow-auto">
        <table className="text-sm">
          <thead className="sticky top-0 bg-panel">
            <tr>
              <th className="min-w-[8rem]" />
              {here.map((s) => (
                <th key={s.id} className="min-w-[13rem] px-3 py-2 text-left font-medium">
                  <span className="mr-2 inline-block size-2.5 rounded-full" style={{ background: colorOf(s.id) }} />
                  {s.publisher}
                  <div className="tabular text-xs font-normal text-ink-3">{s.hit!.distance_m} m away</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {!allFields &&
              sourced.map((f) => (
                <tr key={f.name} className="border-t border-line/60 align-top">
                  <td className="px-3 py-1.5 font-mono text-ink-2">{f.name}</td>
                  {here.map((s) => (
                    <td key={s.id} className="px-3 py-1.5">
                      <Cell a={s.hit!.model[f.name]} showField />
                    </td>
                  ))}
                </tr>
              ))}
            {!allFields && unsourced.length > 0 && (
              <tr className="border-t border-line/60">
                <td className="px-3 py-1.5 font-mono text-ink-3">{unsourced.map((f) => f.name).join(", ")}</td>
                <td colSpan={here.length} className="px-3 py-1.5 text-ink-3 italic">
                  no source publishes these
                </td>
              </tr>
            )}
            {allFields && (
              <tr className="align-top">
                <td />
                {here.map((s) => (
                  <td key={s.id} className="px-3 py-1.5">
                    {s.hit!.attributes.map((a) => (
                      <div key={a.name} className="border-b border-line/40 py-1">
                        <div className="font-mono text-xs text-ink-3">{a.name}</div>
                        <Cell a={a} />
                      </div>
                    ))}
                  </td>
                ))}
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {!allFields && <div className="border-t border-line px-4 py-1.5 text-xs text-ink-3">Fields lined up by the suggested crosswalk</div>}
    </div>
  );
}

function Cell({ a, showField }: { a: Attribute | null | undefined; showField?: boolean }) {
  if (!a) return <span className="text-ink-3">—</span>;
  return (
    <div>
      {a.value === null ? (
        <span className="text-ink-3 italic">null</span>
      ) : a.blank ? (
        <StatusChip tone="warning" label={`blank ${JSON.stringify(a.value)}`} />
      ) : (
        <span className="font-mono break-all">{formatValue(a.value)}</span>
      )}
      {a.decoded && a.decoded !== String(a.value) && <span className="ml-2 text-ink-2">→ {a.decoded}</span>}
      {a.domain_spelling && <div className="text-xs text-ink-3">domain spells it {a.domain_spelling}</div>}
      {a.in_domain === false && <div className="text-xs text-critical">✕ not in its own domain</div>}
      {showField && <div className="font-mono text-xs text-ink-3">{a.name}</div>}
    </div>
  );
}

export function formatValue(v: unknown): string {
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 1000) / 1000);
  if (typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v)) return v.slice(0, 10);
  return String(v);
}
