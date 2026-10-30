// A field's most common values as share-of-rows bars, plus "other values" and
// "blank" — what a person reads to judge whether a field means what it says.
// Shows 10; "Show more" opens the rest of what the API sent (up to 50).
import { useState, type ReactNode } from "react";
import { CodedValue } from "./bits";

type Value = { value: string; share: number; label?: string | null };

const FIRST = 10;

export function ValueBars({ values, populated, distinct }: { values: Value[]; populated: number; distinct: number }) {
  const [all, setAll] = useState(false);
  const shown = all ? values : values.slice(0, FIRST);
  const hidden = values.length - shown.length;
  const rest = Math.max(0, populated - shown.reduce((n, t) => n + t.share, 0));
  const blank = 1 - populated;
  const max = Math.max(...shown.map((t) => t.share), rest, blank, 0.0001);
  return (
    <div className="space-y-1">
      {shown.map((t) => (
        <Row key={t.value} label={<CodedValue raw={t.value} label={t.label} />} title={t.label ?? t.value} share={t.share} max={max} />
      ))}
      {rest > 0.0005 && <Row label={`${Math.max(0, distinct - shown.length).toLocaleString()} other values`} share={rest} max={max} muted />}
      {blank > 0.0005 && <Row label="blank" share={blank} max={max} muted />}
      {(hidden > 0 || all) && values.length > FIRST && (
        <button onClick={() => setAll(!all)} className="text-xs text-ink-3 hover:text-ink">
          {all
            ? "Show fewer"
            : `Show ${hidden} more${distinct > values.length ? ` (top ${values.length} of ${distinct.toLocaleString()})` : ""}`}
        </button>
      )}
    </div>
  );
}

function Row({ label, title, share, max, muted }: { label: ReactNode; title?: string; share: number; max: number; muted?: boolean }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_6rem_3rem] items-center gap-2 text-sm">
      <span className={`min-w-0 truncate ${muted ? "font-mono text-ink-3" : ""}`} title={title ?? (typeof label === "string" ? label : undefined)}>
        {label}
      </span>
      <span className="block h-2 rounded bg-surface">
        <span className={`block h-2 rounded ${muted ? "bg-ink-3/50" : "bg-good"}`} style={{ width: `${(share / max) * 100}%` }} />
      </span>
      <span className="tabular text-right text-ink-2">{pct(share)}</span>
    </div>
  );
}

export function pct(share: number) {
  const v = share * 100;
  if (v > 0 && v < 0.1) return "<0.1%"; // a handful of rows is not none
  return `${v > 0 && v < 1 ? v.toFixed(1) : Math.round(v)}%`;
}
