// Status always carries an icon and a word, never colour alone.
type Tone = "good" | "warning" | "serious" | "critical" | "muted";

const TONES: Record<Tone, { cls: string; icon: string }> = {
  good: { cls: "text-good border-good/50 bg-good/10", icon: "●" },
  warning: { cls: "text-warning border-warning/50 bg-warning/10", icon: "◐" },
  serious: { cls: "text-serious border-serious/50 bg-serious/10", icon: "▲" },
  critical: { cls: "text-critical border-critical/60 bg-critical/10", icon: "✕" },
  muted: { cls: "text-ink-3 border-line bg-raised", icon: "○" },
};

export function StatusChip({ tone, label }: { tone: Tone; label: string }) {
  const t = TONES[tone];
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-sm font-medium ${t.cls}`}
    >
      <span aria-hidden>{t.icon}</span>
      {label}
    </span>
  );
}

export const STATUS_TONE: Record<string, Tone> = {
  ready: "good",
  partial: "warning",
  fetching: "muted",
  missing: "muted",
  error: "critical",
};
