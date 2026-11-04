// Types and fetchers for the wrangle API (src/wrangle/api.py). One function per route.
import type { Geometry } from "geojson";

export type Feature = {
  feature: string;
  title: string;
  geometry: string;
  region: string;
  boundary: string | null;
  loadable: number;
  cards: number;
};

export type Assessment = {
  ok: boolean;
  reason: string | null;
  reason_detail: string | null;
  name?: string | null;
  geometry?: string | null;
  count?: number | null;
  field_count?: number;
  domain_count?: number;
  wkid?: number | null;
  last_edit?: string | null;
  max_record_count?: number | null;
  oid_field?: string | null;
  estimated_load_s?: number | null;
  notes?: string[];
};

export type Card = {
  id: string;
  publisher: string;
  attribution: string;
  color?: string | null;  // the source's own colour, if it declares one
  url: string;
  where: string;
  loadable: boolean;
  reason: string | null;
  reason_text: string;
  reason_detail: string | null;
  interest: string[];
  assessment?: Assessment;
  coverage?: { share: number; method: "features" | "extent" } | null;
};

export type SourceStatus = "ready" | "partial" | "error" | "missing" | "fetching";

export type FieldMeta = {
  name: string;
  type: string;
  alias: string | null;
  domain: Record<string, string> | null;
};

export type SourceState = {
  id: string;
  publisher: string;
  attribution: string;
  color?: string | null;  // the source's own colour, if it declares one
  status: SourceStatus;
  error: string | null;
  rows: number | null;
  oid_field: string | null;
  geometry: string | null;
  fetched_at: string | null;
  verdict: { outcome: string; expected: number; fetched: number; pages: number } | null;
  bbox: [number, number, number, number] | null;
  fields: FieldMeta[];
};

export type Warning = { level: "critical" | "warning" | "info"; text: string };

export type Checks = {
  rows: number;
  verdict: string;
  inside_share: number | null;
  no_geometry: number;
  empty_fields: string[];
  sparse_fields: { field: string; share: number }[];
  off_domain: { field: string; rows: number; examples: string[] }[];
  warnings: Warning[];
};

export type Rows = {
  total: number;
  offset: number;
  columns: string[];
  rows: Record<string, unknown>[];
};

export type Attribute = {
  name: string;
  alias: string | null;
  type: string | null;
  value: unknown;
  decoded: string | null;
  in_domain: boolean | null;
  domain_spelling?: string | null; // the domain declares this code in another case (St for ST)
  blank: boolean;
};

export type FeatureDetail = {
  source_id: string;
  oid: number;
  bbox: [number, number, number, number];
  attributes: Attribute[];
};

export type ModelField = {
  name: string;
  type?: string;
  sourced: boolean;
  description?: string;
  values?: string[] | null; // an enum's vocabulary
  meanings?: Record<string, string> | null; // what each value means ("no": two-way)
  added?: string[] | null; // values a person added to the vocabulary
  unit?: string | null;
  notes?: string | null;
};

export type Decisions = { fields: Record<string, string | null>; decided_by: Record<string, "person" | "automap"> };

export type HintMatch = { hint: string; word: string; rank: number; points: number; kind: string };

export type CrosswalkPick = {
  source_field: string;
  alias: string | null;
  type: string;
  score: number;
  match_pct: number; // the name score as 0-100%: 100 = whole-word match on a hint word
  hint_rank: number;
  populated_share: number;
  distinct: number;
  samples: string[];
  top_values: { value: string; share: number }[];
  why: { via: "name" | "alias"; words: string[]; matches: HintMatch[] };
};

/** A candidate the name guess did not pick, and why it is still worth a look. */
export type CrosswalkAlternative = {
  source_field: string;
  score: number;
  match_pct: number; // the name score as 0-100%: 100 = whole-word match on a hint word
  populated_share: number;
  reason: string;
  why: CrosswalkPick["why"];
};

export type CrosswalkCell = {
  pick: CrosswalkPick | null;
  skipped: CrosswalkAlternative[];
  runners_up: CrosswalkAlternative[];
};

export type Crosswalk = {
  fields: ModelField[];
  sources: { id: string; publisher: string }[];
  cells: Record<string, Record<string, CrosswalkCell | null>>;
};

export type Compare = {
  point: [number, number];
  tolerance_m: number;
  fields: ModelField[];
  sources: {
    id: string;
    publisher: string;
    hit: {
      oid: number;
      distance_m: number;
      bbox: [number, number, number, number];
      model: Record<string, Attribute | null>;
      attributes: Attribute[];
    } | null;
  }[];
};

export type FieldProfile = {
  name: string;
  alias: string | null;
  type: string;
  populated_share: number;
  distinct: number;
  top_values: { value: string; share: number; label: string | null }[];
  domain: Record<string, string> | null;
};

export type AiMatch = {
  target_field: string;
  source_field: string | null;
  confidence: "high" | "medium" | "low";
  reasoning: string;
};

export type AiResult = {
  source_id: string;
  pending?: boolean;
  error?: string;
  model?: string;
  cached?: boolean;
  recorded_at?: string;
  matches?: AiMatch[];
  dropped?: (AiMatch & { why: string })[];
};

// --- Edit values -----------------------------------------------------------------------

export type ModelValue = string | number | null;

/** One method's answer for one raw value. The methods are described in src/wrangle/values.py. */
export type Method = {
  method: "reviewed" | "domain" | "text" | "abbrev" | "cast" | "parse" | "keep" | "ai";
  detail: string;
  how?: string;
  confidence?: "high" | "medium" | "low";
};

export type Candidate = { value: ModelValue; methods: Method[] };

export type ValueRow = {
  raw: string;
  rows: number;
  share: number;
  label: string | null;
  declared: boolean | null; // null: the layer declares no domain
  candidates: Candidate[];
  status: "agree" | "single" | "conflict" | "none";
  decided: boolean;
  decision: ModelValue;
  decided_by: "person" | "automap" | null;
};

export type DateOption = {
  format: string;
  about: string;
  read_share: number;
  filled_share: number;
  failed: { rows: number; examples: string[] };
  placeholder: { rows: number; examples: string[] };
  range: [string, string];
  examples: { raw: string; date: string }[];
};

export type ValueCell = {
  field: string;
  source: string;
  source_field: string;
  kind: "table" | "date" | "text" | "number";
  total: number;
  blank: number;
  distinct: number;
  rule: string | null;
  mode: ValueMode | null; // null for a date: read with a format instead
  modes: ValueMode[];
  domain: boolean; // the layer publishes labels for this field's codes
  landing?: { steps: Record<string, number>; rows: LandingRow[] };
  ai: { model?: string; cached?: boolean; recorded_at?: string; pending?: boolean; error?: string; dropped?: unknown[] } | null;
  rows?: ValueRow[];
  options?: DateOption[];
  preview?: { filled_share: number; distinct: number; examples: string[] };
};

export type ValueMode = "published" | "cleaned" | "standardized";
export type LandingRow = { raw: string; rows: number; published: ModelValue; cleaned: ModelValue; steps: string[] };

// --- Convert ---------------------------------------------------------------------------

export type FieldReport = {
  field: string;
  source_field: string | null;
  how: "table" | "date" | "text" | "number" | "none" | "unmatched" | "unpublished" | "published" | "cleaned";
  rule?: string | null;
  mode?: ValueMode | null;
  cleaned?: Record<string, number>; // cleaning step -> rows
  filled: number;
  nulls: Record<string, number>; // reason -> rows; see REASONS in src/wrangle/convert.py
  undecided: { raw: string; rows: number }[];
};

export type ConvertReport = {
  source_id: string;
  publisher: string;
  rows: number;
  fetched: { outcome: string; expected: number; fetched: number };
  verdict: "complete" | "gaps" | "partial";
  open_rows: number;
  no_geometry: number;
  fields: FieldReport[];
  converted_at: string;
};

export type ConvertStatus = {
  sources: { id: string; publisher: string; report: ConvertReport | null; stale: boolean }[];
  unified: { path: string; built: number } | null;
  map_field: string | null;
  queries: SavedQuery[];
};

/** A query preloaded in the editor. `pivot`: draw its answer as model values × publishers. */
export type SavedQuery = { title: string; sql: string; pivot?: boolean };

export type QueryResult = {
  sql: string;
  columns: string[];
  rows: (string | number | boolean | null)[][];
  truncated: boolean;
  ms: number;
};

/** A page of the unified layer, as it landed in the model. */
export type RowsPage = {
  columns: string[];
  rows: (string | number | boolean | null)[][];
  total: number;
  offset: number;
};

/** A write. Throws with the server's message, so a refused value says why. */
async function send(path: string, method: "PUT" | "DELETE" | "POST", body?: unknown) {
  const r = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `${r.status}`);
  return r.json();
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const api = {
  features: () => get<Feature[]>("/api/features"),
  catalog: (f: string, refresh = false) =>
    get<Card[]>(`/api/${f}/catalog${refresh ? "?refresh=true" : ""}`),
  sources: (f: string) => get<SourceState[]>(`/api/${f}/sources`),
  boundaryUrl: (f: string) => `/api/${f}/boundary`,
  geojsonUrl: (f: string, id: string) => `/api/${f}/sources/${id}/geojson`,
  rows: (f: string, id: string, offset = 0, limit = 100) =>
    get<Rows>(`/api/${f}/sources/${id}/rows?offset=${offset}&limit=${limit}`),
  feature: (f: string, id: string, oid: number) =>
    get<FeatureDetail>(`/api/${f}/sources/${id}/features/${oid}`),
  checks: (f: string, id: string) => get<Checks>(`/api/${f}/sources/${id}/checks`),
  crosswalk: (f: string, ids: string[]) => get<Crosswalk>(`/api/${f}/crosswalk?ids=${ids.join(",")}`),
  compare: (f: string, lon: number, lat: number, ids: string[], toleranceM: number) =>
    get<Compare>(`/api/${f}/compare?lon=${lon}&lat=${lat}&ids=${ids.join(",")}&tolerance_m=${Math.round(toleranceM)}`),
  profile: (f: string, id: string) => get<FieldProfile[]>(`/api/${f}/sources/${id}/profile`),
  match: (f: string, ids: string[], cachedOnly: boolean) =>
    send(`/api/${f}/match?ids=${ids.join(",")}&cached_only=${cachedOnly}`, "POST") as Promise<Record<string, AiResult>>,
  mappings: (f: string, ids: string[]) => get<Record<string, Decisions>>(`/api/${f}/mappings?ids=${ids.join(",")}`),
  automap: (f: string, ids: string[]) =>
    send(`/api/${f}/automap?ids=${ids.join(",")}`, "POST") as Promise<{
      confirmed: { source_id: string; target: string; source_field: string | null }[];
    }>,
  decide: (f: string, id: string, target: string, sourceField: string | null) =>
    send(`/api/${f}/mappings/${id}/${target}`, "PUT", { source_field: sourceField }),
  undecide: (f: string, id: string, target: string) => send(`/api/${f}/mappings/${id}/${target}`, "DELETE"),
  /** Start Match fields over for these sources: field matches and the values that hang off them. */
  /** Add a value to an enum field's vocabulary, for every source. Returns it as stored. */
  addValue: (f: string, field: string, value: string, meaning: string) =>
    send(`/api/${f}/vocabulary/${field}`, "POST", { value, meaning }) as Promise<{ field: string; value: string }>,
  removeValue: (f: string, field: string, value: string) => send(`/api/${f}/vocabulary/${field}/${encodeURIComponent(value)}`, "DELETE"),
  clearFields: (f: string, ids: string[]) => send(`/api/${f}/mappings?ids=${ids.join(",")}`, "DELETE"),
  /** Start Edit values over for these sources: values, date rules and modes; field matches stay. */
  clearValues: (f: string, ids: string[]) => send(`/api/${f}/values?ids=${ids.join(",")}`, "DELETE"),
  values: (f: string, ids: string[]) => get<{ cells: ValueCell[] }>(`/api/${f}/values?ids=${ids.join(",")}`),
  askValues: (f: string, ids: string[]) =>
    send(`/api/${f}/values/ask?ids=${ids.join(",")}`, "POST") as Promise<{
      asked: number;
      errors: { source: string; field: string; error: string }[];
    }>,
  automapValues: (f: string, ids: string[]) => send(`/api/${f}/values/automap?ids=${ids.join(",")}`, "POST"),
  decideValues: (f: string, id: string, target: string, values: Record<string, ModelValue>) =>
    send(`/api/${f}/values/${id}/${target}`, "PUT", { values }),
  undecideValue: (f: string, id: string, target: string, raw: string) =>
    send(`/api/${f}/values/${id}/${target}?raw=${encodeURIComponent(raw)}`, "DELETE"),
  decideMode: (f: string, id: string, target: string, mode: ValueMode) =>
    send(`/api/${f}/modes/${id}/${target}`, "PUT", { mode }),
  decideRule: (f: string, id: string, target: string, rule: string | null) =>
    send(`/api/${f}/rules/${id}/${target}`, "PUT", { rule }),
  convertStatus: (f: string, ids: string[]) => get<ConvertStatus>(`/api/${f}/convert?ids=${ids.join(",")}`),
  convert: (f: string, id: string) => send(`/api/${f}/convert/${id}`, "POST") as Promise<ConvertReport>,
  unify: (f: string, ids: string[]) =>
    send(`/api/${f}/unify?ids=${ids.join(",")}`, "POST") as Promise<{ path: string; rows: number; sources: Record<string, number> }>,
  query: (f: string, sql: string) => send(`/api/${f}/query`, "POST", { sql }) as Promise<QueryResult>,
  /** The converted row under a click: each source's nearest feature within `tol` metres. */
  convertedAt: (f: string, lon: number, lat: number, tol: number, ids: string[]) =>
    get<{ hits: { id: string; publisher: string; row: Record<string, string | number | boolean | null>; geometry: Geometry }[] }>(
      `/api/${f}/converted/at?lon=${lon}&lat=${lat}&tolerance_m=${tol}&ids=${ids.join(",")}`,
    ),
  /** Every converted feature touching a lon/lat box: `source:oid` keys and their lines. */
  convertedIn: (f: string, bbox: [number, number, number, number], ids: string[]) =>
    get<{ keys: string[]; geometries: Geometry[]; total: number }>(`/api/${f}/converted/in?bbox=${bbox.join(",")}&ids=${ids.join(",")}`),
  /** A page of converted rows; `keys` (`source:oid`) keeps only the features picked on the map. */
  convertedRows: (f: string, source: string | null, offset = 0, limit = 100, keys?: string[]) =>
    get<RowsPage>(
      `/api/${f}/converted/rows?offset=${offset}&limit=${limit}${source ? `&source=${source}` : ""}${keys ? `&keys=${encodeURIComponent(keys.join(","))}` : ""}`,
    ),
  convertedUrl: (f: string, id: string, version: string) => `/api/${f}/converted/${id}/geojson?v=${encodeURIComponent(version)}`,
  fetchSource: (f: string, id: string) => send(`/api/${f}/sources/${id}/fetch`, "POST"),
};
