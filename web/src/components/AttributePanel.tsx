// One feature's attributes exactly as the publisher sent them, with coded values
// decoded beside (never instead of) the raw value, and blanks and off-domain
// values flagged.
import type { FeatureDetail } from "../api";
import { useDragPosition } from "./bits";
import { StatusChip } from "./StatusChip";

type Props = { sourceId: string; publisher: string; color: string; detail: FeatureDetail; onClose: () => void };

export function AttributePanel({ sourceId, publisher, color, detail, onClose }: Props) {
  const drag = useDragPosition();
  return (
    <div style={drag.style} className="absolute z-10 flex max-h-[calc(100%-1.5rem)] w-[30rem] flex-col rounded-xl border border-line bg-panel/95 shadow-2xl backdrop-blur">
      <div {...drag.handle} className={`flex items-center gap-3 border-b border-line px-4 py-3 ${drag.handle.className}`}>
        <span className="size-3 rounded-full" style={{ background: color }} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold">
            {sourceId} · {publisher}
          </div>
          <div className="tabular text-sm text-ink-3">OID {detail.oid} · raw attributes, exactly as published</div>
        </div>
        <button onClick={onClose} className="px-2 text-xl text-ink-3 hover:text-ink" aria-label="Close">
          ×
        </button>
      </div>
      <div className="overflow-y-auto">
        <table className="w-full text-sm">
          <tbody>
            {detail.attributes.map((a) => (
              <tr key={a.name} className="border-b border-line/60 align-top">
                <td className="w-[45%] px-4 py-1.5">
                  <div className="font-mono text-ink">{a.name}</div>
                  {a.alias && a.alias !== a.name && <div className="text-xs text-ink-3">{a.alias}</div>}
                </td>
                <td className="px-4 py-1.5">
                  {a.value === null ? (
                    <span className="text-ink-3 italic">null</span>
                  ) : a.blank ? (
                    <StatusChip tone="warning" label={`blank ${JSON.stringify(a.value)}`} />
                  ) : (
                    <span className="font-mono break-all">{String(a.value)}</span>
                  )}
                  {a.decoded && a.decoded !== String(a.value) && <div className="text-ink-2">→ {a.decoded}</div>}
                  {a.domain_spelling && <div className="text-xs text-ink-3">domain spells it {a.domain_spelling}</div>}
                  {a.in_domain === false && (
                    <div className="mt-1">
                      <StatusChip tone="critical" label="not in its own domain" />
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
