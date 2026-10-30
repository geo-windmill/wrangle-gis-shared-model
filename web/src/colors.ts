import { useCallback, useSyncExternalStore } from "react";

// One colour per source, assigned by the source's position in sources/*.yaml —
// so a colour always means the same publisher, whatever is toggled on.
//
// The first three slots pass every colour-blind check against each other. No set
// of five does (we ran the validator), so slots 4 and 5 also get a dashed line,
// and the layer list doubles as the legend.
export const SOURCE_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"];
export const SOURCE_DASH: (number[] | null)[] = [null, null, null, [3, 1.5], [1, 1.2]];

export const BOUNDARY_COLOR = "#f5f4ef";
export const HIGHLIGHT_COLOR = "#ffffff";
// A converted road picked by a click: on white roads, a colour no source uses.
export const PICK_COLOR = "#ffd23f";

// The converted map: every road (or hydrant) in one colour, white, whatever its class —
// the publishers keep their own colours before conversion (Sacramento orange, Yolo cyan),
// and white is the merged result. One class that every value falls into.
export type MapClass = { label: string; values: (string | null)[]; color: string; width?: number };
export const CONVERTED: MapClass[] = [{ label: "converted", values: [null], color: "#ffffff", width: 1 }];

export function sourceColor(index: number): string {
  return SOURCE_COLORS[index % SOURCE_COLORS.length];
}

// --- the colour each source draws in, everywhere ------------------------------------
// A viewer's own pick wins, then the colour the source declares in sources/*.yaml, then
// the palette slot. Picks are kept in this browser (localStorage), per feature type, and
// every view and the map re-read them the moment one changes.

type Declared = { id: string; color?: string | null };
const listeners = new Set<() => void>();
let version = 0;
const key = (feature: string) => `wrangle.colors.${feature}`;

export function pickedColors(feature: string): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(key(feature)) || "{}");
  } catch {
    return {};
  }
}

/** Set a viewer's colour for one source; `null` goes back to the source's own colour. */
export function setPickedColor(feature: string, id: string, color: string | null) {
  const all = pickedColors(feature);
  if (color) all[id] = color;
  else delete all[id];
  try {
    localStorage.setItem(key(feature), JSON.stringify(all));
  } catch {
    /* storage unavailable: the pick lasts until reload */
  }
  version++;
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** `colorOf(id)` for a list of sources, re-rendering whenever a pick changes. */
export function useColorOf(feature: string, sources: Declared[]): (id: string) => string {
  const v = useSyncExternalStore(subscribe, () => version);
  return useCallback(
    (id: string) => {
      const picked = pickedColors(feature)[id];
      if (picked) return picked;
      const i = sources.findIndex((s) => s.id === id);
      return sources[i]?.color || sourceColor(Math.max(0, i));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [feature, sources, v],
  );
}
