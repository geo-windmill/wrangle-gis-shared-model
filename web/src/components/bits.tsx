// Small shared pieces: a layer swatch that matches how the layer draws, a button,
// a show/hide eye, and a drag handle for resizing panes.
import { useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Show `content` in a small floating card while the pointer (or keyboard focus)
 * is on `children`; a click pins it open. Rendered at the page root so a
 * scrolling table can't clip it.
 */
export function Hover({ content, children, label }: { content: ReactNode; children: ReactNode; label: string }) {
  const anchor = useRef<HTMLButtonElement>(null);
  // Opens below the anchor, or above it when the lower half of the window is used up.
  const [at, setAt] = useState<{ x: number; top?: number; bottom?: number } | null>(null);
  const [pinned, setPinned] = useState(false);
  const show = () => {
    const r = anchor.current?.getBoundingClientRect();
    if (!r) return;
    const x = Math.min(r.left, window.innerWidth - 360);
    setAt(r.bottom > window.innerHeight / 2 ? { x, bottom: window.innerHeight - r.top + 6 } : { x, top: r.bottom + 6 });
  };
  const hide = () => !pinned && setAt(null);
  return (
    <>
      <button
        ref={anchor}
        type="button"
        aria-label={label}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={() => {
          setPinned(false);
          setAt(null);
        }}
        onClick={() => {
          setPinned(!pinned);
          show();
        }}
        className="text-left"
      >
        {children}
      </button>
      {at &&
        createPortal(
          <div
            style={{ left: at.x, top: at.top, bottom: at.bottom }}
            className="fixed z-50 max-h-[80vh] w-[340px] overflow-y-auto rounded-lg border border-line bg-raised p-3 text-sm text-ink shadow-2xl"
          >
            {content}
          </div>,
          document.body,
        )}
    </>
  );
}

/**
 * A raw value as a person reads it: the domain's label first when the layer
 * declares one, then the code it stands for, tagged as coming from the domain.
 * Without a label, just the raw value.
 */
export function CodedValue({ raw, label, className = "" }: { raw: string; label?: string | null; className?: string }) {
  const text = label ? labelText(raw, label) : "";
  if (!text || text === raw) return <span className={`truncate font-mono ${className}`}>{raw}</span>;
  return (
    <span className={`flex min-w-0 items-center gap-1.5 ${className}`} title={`${label} · code ${raw}`}>
      <span className="truncate">{text}</span>
      <DomainTag code={raw} />
    </span>
  );
}

/** A domain label without its own code, which the tag beside it already shows:
 *  "17 - Collector Urban" and "Collector - Urban (17)" both read "Collector…". */
export function labelText(raw: string, label: string) {
  const esc = raw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const out = label
    .replace(new RegExp(`^\\s*${esc}\\s*[-–=:]\\s*`), "")
    .replace(new RegExp(`\\s*\\(${esc}\\)\\s*$`), "")
    .trim();
  return out || label;
}

/** The code behind a label, marked as coming from the layer's coded-value domain. */
export function DomainTag({ code }: { code: string }) {
  return (
    <span
      title={`Code ${code} · from the layer's coded-value domain`}
      className="inline-flex shrink-0 items-center gap-0.5 rounded border border-line px-1 font-mono text-[0.7rem] leading-4 text-ink-3"
    >
      <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden>
        <path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z" />
        <circle cx="8" cy="8" r="1.5" fill="currentColor" />
      </svg>
      {code}
    </span>
  );
}

export function Eye({ open }: { open: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      {open ? <circle cx="12" cy="12" r="3" /> : <path d="M4 4l16 16" />}
    </svg>
  );
}

/**
 * Drag a floating panel by its header. Returns the panel's position and the
 * handlers to put on the header. Clicks on buttons inside the header still work.
 */
export function useDragPosition(start = { x: 12, y: 12 }) {
  const [pos, setPos] = useState(start);
  const grab = useRef<{ dx: number; dy: number } | null>(null);
  const handle = {
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => {
      if ((e.target as HTMLElement).closest("button")) return;
      grab.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y };
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    onPointerMove: (e: React.PointerEvent<HTMLElement>) => {
      if (!grab.current) return;
      // Keep the header reachable: never let the panel leave the top-left corner.
      setPos({ x: Math.max(0, e.clientX - grab.current.dx), y: Math.max(0, e.clientY - grab.current.dy) });
    },
    onPointerUp: () => {
      grab.current = null;
    },
    className: "cursor-move select-none",
  };
  return { style: { left: pos.x, top: pos.y }, handle };
}

/** A thin bar you drag to resize the pane beside it. Reports movement in pixels. */
export function Splitter({ axis, onDrag }: { axis: "x" | "y"; onDrag: (delta: number) => void }) {
  const last = useRef(0);
  return (
    <div
      role="separator"
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      onPointerDown={(e) => {
        last.current = axis === "x" ? e.clientX : e.clientY;
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
        const now = axis === "x" ? e.clientX : e.clientY;
        onDrag(now - last.current);
        last.current = now;
      }}
      className={`shrink-0 bg-line transition-colors hover:bg-ink-3 active:bg-ink-2 ${
        axis === "x" ? "w-1 cursor-col-resize" : "h-1 cursor-row-resize"
      }`}
    />
  );
}
export function Swatch({ color, dash, point, dim }: { color: string; dash?: number[] | null; point?: boolean; dim?: boolean }) {
  if (point) return <span className="size-3.5 shrink-0 rounded-full" style={{ background: color, opacity: dim ? 0.3 : 1 }} />;
  const da = dash ? dash.map((d) => d * 3).join(" ") : undefined;
  return (
    <svg width="28" height="10" className="shrink-0" style={{ opacity: dim ? 0.3 : 1 }} aria-hidden>
      <line x1="2" y1="5" x2="26" y2="5" stroke={color} strokeWidth="3" strokeDasharray={da} strokeLinecap={dash ? "butt" : "round"} />
    </svg>
  );
}

export function IconBtn({ label, title, onClick, disabled }: { label: string; title?: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      className="rounded border border-line px-2 py-0.5 hover:bg-surface hover:text-ink disabled:opacity-30"
    >
      {label}
    </button>
  );
}
