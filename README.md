# wrangle

The demo app for **"Wrangling Public Geospatial Data into a Shared Model"** — FOSS4G
North America 2026, Sacramento.

It carries two real feature types — road centerlines and fire hydrants — from eight
public agency ArcGIS services into a small shared model, in four stages:
**Loading dock → Match fields → Edit values → Convert**. Every source is real and was
vetted live.

This is a purpose-built open demo of a workflow, not CivilGrid's product.

## What it does so far

**1 · Loading dock** — finding sources and looking at them, on one screen. Search the
vetted sources or paste one's URL, put layers on the map against the area of interest,
read each layer's warnings (blank fields, values outside the layer's own domain,
features outside the area), and queue the worth-having ones for ingest. Two views show
how the sources would merge before anything is mapped: **click the map** to see the same
road as every source describes it, and switch the table to **Crosswalk** to see which
source field would feed each shared-model field.

**2 · Match fields** — one decision at a time: a model field, for one source. Every
candidate field is a card, ranked, with the same evidence on each — the AI's opinion
(one AI call per source, an open model by default, judged on the values, with a confidence
and a reason),
the name-based guess and why it scored what it did, how full the field is, and its most
common values. Pick one, or "no field fits". **Automap** confirms only the cells where
the AI is sure and the name guess agrees; the **Overview** shows every decision at once.
Decisions land in each source's **configuration record**, one row in a SQLite file
(`wrangle.db`), with a history of every change: the reviewed record worth keeping, and
everything needed to load the source again. `GET /api/roads/record/R1` shows one.

**3 · Edit values** — for every matched field, how its values land. Each field picks a
mode: **as published** (the default: what the publisher's own map shows, so a code the
layer's coded-value domain declares lands as its label, Yolo's `19` as "19 - Local Urban"),
**cleaned** (also: spaces trimmed, blanks and placeholders like `N/A` → null, one spelling
per word, numbers outside the model's range → null) or
**standardized**: what each raw value means in the model, decided value by value.
To standardize, several methods answer on their own, cheapest first: the layer's own coded-value domain,
an exact or whole-word match against the vocabulary, a guarded abbreviation match
(`RMP` → ramp, never `SS` ↔ `SD`, never on values with digits), the model's strict caster
for numbers (`0` mph is below the minimum, so it lands as null), decisions a person already
made for the same value in another source, and the AI — one call per field and source,
shown the whole list of values. Every option for a value is a card with the same evidence
rows. **Auto-confirm** takes only values where two or more methods answered and all agree;
the rest wait for a person. Dates get one rule — a format, with the values it can't read
(`20080231`) and the placeholders (`1899-12-30`) shown — and text passes through as is.
Every vocabulary value shows what it means (`no` — two-way, no restriction), and when none
fits, **＋ New value** adds one to the field's vocabulary for every source (kept in
`wrangle.db`, not in the shipped model, so the AI is still asked about the shipped list).
The **Overview** lays each field out as model value × publisher, every raw value where it
lands. Modes and decisions are saved in the source's record, beside the field decisions.

**4 · Convert** — every queued source into the shared model by its reviewed decisions:
one GeoParquet per source and one unified GeoParquet under `out/`, each feature carrying
its provenance (`source_id`, `source_oid`). A field such as `road_class` holds the model's
values where it was standardized and the publisher's own text (labels, not codes) where it
was left as published or cleaned; `source_oid` leads back to the original row.
Nothing lands silently: every model field
reports how many rows it filled and why each empty one is empty — blank at the source,
no field fits, a value nobody decided yet, a date that can't be read. A source with
undecided values still converts, and says so. Then DuckDB answers "road miles by class,
by publisher" straight off the unified file, and the map shows the unified layer:
click a road to see each source's converted row there. Picking on the map narrows the **Data** tab to those rows: a click picks the road under the pointer, Ctrl-click adds to the pick, and Shift-drag picks every road in a box. Below the map, the **Query** tab is a SQL editor with a few
preloaded queries (fill rates, what each publisher wrote and where it landed, speed
limits, one bridge in every source); edit them or write your own (Ctrl+Enter runs).
DuckDB there can read and write only under `out/`, and an answer stops at 1,000 rows.
The **Data** tab pages through the converted rows themselves, per source. The query
needs DuckDB's spatial extension, which downloads once on first use.

Each review stage has **↺ Clear**, which starts that stage over for the queued sources:
in Match fields it clears the field matches (and the value decisions that hang off them),
in Edit values the value decisions, date rules and modes. The history keeps what was
cleared.

The configuration record is also what loads a source again: it holds the layer URL,
its filter and the model version it was reviewed against.

### Which AI

By default the AI suggestions come from **Qwen3 14B, an open-weight model (Apache 2.0)
running on your own machine** through [Ollama](https://ollama.com): no key, no cost, and
nothing leaves your machine. **Claude** is an option. Every model gets the same prompt, the
same structured output and the same guards, and everything else in the app works without any.

```bash
ollama pull qwen3:14b      # about 9 GB, once
```

That's all the default needs. To choose another model, set it in `.env`:

```ini
WRANGLE_AI_MODEL=gpt-oss:20b     # any Ollama model that supports structured output
# WRANGLE_AI=anthropic           # Claude: also ANTHROPIC_API_KEY and `uv pip install -e ".[ai]"`
```

**Why qwen3:14b.** We gave four open models the same prompts Claude had answered for two
counties and compared their answers. qwen3:14b picked the same 12 of 12 fields as Claude,
agreed on 27 of 32 values, and was as cautious as Claude where caution matters (it is only
medium-confident on Sacramento County's `CLASS`, so a person checks it). `gpt-oss:20b` was
faster but more confident, so automap accepted more with nobody looking; `qwen3:8b` was less
accurate; `olmo2:13b` (the most open: training data and code too) named fields that don't
exist, which the guard dropped.

**Speed.** A 14B model needs about 12 GB of memory with the app's 16k-token context (the app
asks for it: Ollama's default of 4,096 silently cuts a large layer's prompt). With a GPU
Ollama supports (NVIDIA, AMD, Apple), each call takes seconds. On a laptop CPU alone, expect
10–15 minutes per call. An integrated Intel or AMD GPU can help: start Ollama with
`OLLAMA_VULKAN=1` and `OLLAMA_IGPU_ENABLE=1` (about 4 times faster on an Intel Arc 140V).
Either way you wait once.

Each answer is recorded under `cache/ai/`, keyed on the model and the exact prompt, and
replayed after that: the screen says when it did. A different model is asked fresh, once.

## Run it

You need Python 3.11+ with [uv](https://docs.astral.sh/uv/), and Node 20.19+ (or 22.12+).
The AI is optional (see [Which AI](#which-ai)): without it every stage still works, you just
decide each field yourself.

```bash
git clone https://github.com/geo-windmill/wrangle-gis-shared-model && cd wrangle-gis-shared-model
```

```bash
uv venv --python 3.12
```

```bash
uv pip install -e ".[geo,api,ai,dev]"
```

Activate the environment, so the `wrangle` command is on your path (in every new terminal).
On macOS or Linux:

```bash
source .venv/bin/activate
```

On Windows (PowerShell, or `.venv/Scripts/activate` in Git Bash):

```powershell
.venv\Scripts\Activate.ps1
```

Download the public data — about 200,000 features from eight agencies, a few minutes
on decent wifi. Nothing from the agencies is in this repo; see [DATA.md](DATA.md).

```bash
wrangle fetch
```

Build the UI once, then run the app at http://localhost:8000:

```bash
cd web && npm install && npm run build && cd ..
```

```bash
wrangle serve
```

The server listens on this machine only (127.0.0.1). The decisions you make are saved in
`wrangle.db` next to the code. The app downloads the agency layers, the basemap and DuckDB's
spatial extension; the only thing it sends out is the AI prompt, and only if you choose Claude.

For UI development, run `wrangle serve` and `npm run dev` in `web/` side by side, and
open http://localhost:5173.

## The command line

Nothing here touches the network:

```bash
wrangle model show roads
```

```bash
wrangle source list
```

```bash
wrangle source show roads R2
```

These do:

```bash
wrangle source assess roads --live
```

```bash
wrangle source profile roads R2 --live
```

## Running from cache

Every HTTP response is recorded to `cache/` the first time it is fetched, so a second
run — and a live demo — works with the wifi off. Set the mode with `--live` or
`WRANGLE_CACHE_MODE`:

| Mode | Reads disk | Hits network | Use |
|---|---|---|---|
| `replay` | yes | **no** | On stage. A miss is a loud error, not a silent network call. |
| `prefer` (default) | yes | on a miss | Development. |
| `live` | no | yes | Recording. |
| `refresh` | no | yes | Re-vetting; overwrites what is on disk. |

```bash
wrangle cache status
```

The cache is content-addressed by method + URL + normalised params, and stores a
readable `.meta.json` beside every body so you can see what was recorded and when.

### The basemap, offline

The map's basemap goes through `wrangle serve` too, and every file of it (style, vector
tiles, fonts, icons) is kept under `cache/basemap/` the first time the map shows it. To
keep it ahead of time for the whole area of interest, run this on good wifi:

```bash
wrangle cache basemap
```

It keeps every zoom down to street level for the roads area (Sacramento and Yolo
counties) and stops at a state-wide view for the hydrants (California): about 6,000
files, 110 MB. In `replay` mode a part of the map that was never kept stays blank rather
than waiting on the network.

## Settings

Set in the shell or in `.env` (copy `.env.example`); the shell wins. All optional.

| Variable | What it does |
|---|---|
| `WRANGLE_AI` | `ollama` (default) or `anthropic` |
| `WRANGLE_AI_MODEL` | the Ollama model to ask (default `qwen3:14b`) |
| `WRANGLE_CACHE_MODE` | `replay`, `prefer` (default), `live` or `refresh` |
| `WRANGLE_CACHE_DIR` | where recorded responses live (default `cache/`) |
| `WRANGLE_DATA_DIR` | where `wrangle fetch` puts layers (default `data/`) |
| `WRANGLE_OUT_DIR` | where Convert writes (default `out/`) |
| `WRANGLE_DB` | the decisions database (default `wrangle.db`) |
| `WRANGLE_MAPPINGS_DIR` | a folder to keep `wrangle.db` in instead; `WRANGLE_DB` wins |

A separate database and output folder (`WRANGLE_DB`, `WRANGLE_OUT_DIR`) make a rehearsal
copy that leaves your own decisions alone.

## Re-vetting

The locked sources in `src/wrangle/sources/*.yaml` pin the counts, CRS,
`maxRecordCount` and OID field observed when they were vetted (first 2026-09-08, last
2026-10-30). This re-checks them live and
exits non-zero on any drift. Run it the week before any live demo.

```bash
wrangle source revet
```

## Layout

```
src/wrangle/
  schemas/roads.yaml       the shared models — the artefact the audience reads
  schemas/hydrants.yaml
  sources/roads.yaml       the locked sources, the area of interest, and every
  sources/hydrants.yaml      rejection with its reason
  models.py                loads + validates the shared models; strict coercion
  registry.py              loads the locked sources; drift detection
  cache.py                 record-and-replay
  download.py              every feature of a source -> GeoParquet
  checks.py                the warnings a layer carries into the loading dock
  crosswalk.py             the first-guess crosswalk: source field -> model field
  profiles.py              a field judged by its values: fill, variety, top values
  ai.py                    which AI answers (Ollama or Claude); reads .env
  matcher.py               AI field matching: the prompt, the guard, the recording
  values.py                Edit values: every method's answer for every raw value
  value_matcher.py         AI value matching, guarded by the vocabulary and the caster
  cleaning.py              the "as published" and "cleaned" value modes
  mappings.py              the configuration records (SQLite) and their history
  convert.py               Convert: each source into the model, one unified layer, the queries
  basemap.py               the basemap through the server, kept on disk for offline maps
  api.py                   the HTTP API behind the UI
  arcgis/types.py          layer JSON, coded domains, field profiles, verdicts
  arcgis/client.py         async client + the pagination ladder
  arcgis/assess.py         the one-fetch assessment behind a source card
  cli.py                   the command line
web/src/
  views/LoadingDock.tsx    find, look, and queue sources on one screen
  views/MatchFields.tsx    one decision at a time: ranked candidates, evidence, overview
  views/EditValues.tsx     one raw value at a time, per field and source; value modes
  views/Convert.tsx        convert, unify, the SQL editor and the data, with every null explained
  map/useSourceMap.ts      the MapLibre map: layers, order, picking, framing
  components/              compare panel, crosswalk grid, table drawer, value bars
data/
  boundaries/              the area-of-interest outlines (US Census, committed)
  raw/                     what `wrangle fetch` downloads (not committed)
tests/                     including synthetic failures no live source reproduces
docs/SOURCES.md            the vetting record: what each source proves
cache/                     every recorded response, incl. cache/ai/ and cache/basemap/ (not committed)
out/                       converted GeoParquet and reports (not committed)
wrangle.db                 your decisions: a local SQLite file (not committed)
```

Run the tests and the linter (with the venv active, and the `dev` extra installed)
before sending a change:

```bash
python -m pytest -q
```

```bash
ruff check src tests
```

## Notes worth knowing before reading the code

- **`one_way` is a three-state enum, not a boolean.** Real sources encode `B/FT/TF`
  and `0/1/2`. A boolean collapses forward and reverse and destroys direction.
- **Some model fields have no source at all** — `lanes`, `surface`, `flow_class`,
  `install_year`. They are declared anyway and ship empty. An empty column tells you
  what to go ask for.
- **The null sentinel is not always `NULL`.** El Dorado County's hydrants have zero
  SQL nulls and use a single space; `IS NULL` reports a 4%-populated field as complete.
- **A service's declared domain is not a promise.** Yolo County declares coded
  domains, and 81% of its `STREETTYPE` values and a stray `OneWay = 2` fall outside
  them. The attribute panel flags these on click.
- **OIDs do not start at 1.** West Sacramento starts at 4,383,991.
- **Never assume layer `/0`.** Yolo County's centerlines are layer 2.
- **Never assume the OID field is named `OBJECTID`.** Elk Grove's MapServer does not
  say what it is called.
- **Esri polygons list islands as extra rings.** Treat every ring after the first as
  a hole and California's Channel Islands become holes in the mainland.

## License

The code is licensed under the [Apache License 2.0](LICENSE). The license covers the code
only: agency data stays under each agency's own terms ([DATA.md](DATA.md)).
