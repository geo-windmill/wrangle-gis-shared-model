# Vetted sources — locked 2026-09-08

Every layer below was confirmed live from a laptop on 2026-09-08: metadata fetch, count,
coded-domain read, value sampling, and a pagination-behavior suite (`1=1` acceptance,
over-ask clamping, deep offset, OID range/density, unordered-page stability, payload
delta from `outFields` subsetting).

**Re-vetted live 2026-10-30** (`wrangle source revet`): every CRS, `maxRecordCount` and OID
field unchanged. Only two counts moved, by ordinary edits: R1 72,559 → 72,596 and R4
13,592 → 13,602. The tables below keep the 2026-09-08 figures.

Re-vet the week before any live demo (`wrangle source revet`). The cache is the demo's source of truth;
these URLs are how the cache gets filled.

---

## Roads — Sacramento region (5 loadable + 2 greyed)

| # | Publisher | URL | Features | Fields | Domains | CRS | Notes |
|---|-----------|-----|---------:|-------:|--------:|-----|-------|
| R1 | **Sacramento County** (SCGISAdmin) | `services1.arcgis.com/5NARefyPVtAeuJPU/…/Centerlines/FeatureServer/0` | 72,559 | 33 | 0 | 2226 | Regional authority. Carries City of Sacramento streets (`CITYLEFT`/`CITYRIGHT`). Edited 2026-09-08. |
| R2 | **Yolo County** | `services2.arcgis.com/RETsakmE0SJfZXCd/…/Yolo_County_Open_Data_Street_Centerlines/FeatureServer/2` | 11,493 | 49 | **26** | 2226 | Richest source. Layer id is **2, not 0**. |
| R3 | **City of West Sacramento** | `gis.cityofwestsacramento.org/server/rest/services/centerline/FeatureServer/0` | 2,791 | 58 | 1 | 2226 | Self-hosted. `ONEWAY` is **Integer**. |
| R4 | **City of Elk Grove** | `webmaps.elkgrove.gov/arcgis/rest/services/OPEN_DATA_PORTAL/Streets/MapServer/0` | 13,592 | 34 | 0 | 2226 | Self-hosted **MapServer**, not FeatureServer. No `objectIdField` in metadata. |
| R5 | **Caltrans** functional class | `caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/CRS_Functional_Classification/FeatureServer/0` | 779,834 statewide → **24,952** filtered | 7 | 0 | 3857 | Use `where=County_label='Sacramento'`. `F_System` 1–7, **no domain published**. |

**Rejected (real reasons — R7 is shown greyed in the app):**

| # | Publisher | Reason |
|---|-----------|--------|
| R6 | **Caltrans All Roads** `…/CHhighway/All_Roads/FeatureServer/0` | 723,692 features, 10 fields, and the only non-system attribute is `RouteId`. Nothing to map. Greyed for *no mappable attributes*, not for failure. |
| R7 | **City of West Sacramento** as catalogued on ArcGIS Online | The AGOL item points at `gis-server.cityofwestsacramento.org` — **DNS does not resolve**. The working host is `gis.cityofwestsacramento.org`. Greyed for *catalogued URL is dead*. |

### Roads: what each source proves

- **Name splitting.** R1 and R4 carry `PREFIXTYPE / PREFIXDIR / STREETNAME / STREETTYPE /
  SUFFIXDIR` (5 parts). R2 carries **two** independent splits in one layer — the legacy
  `PREFIXDIR/STREETNAME/STREETTYPE/SUFFIXDIR` set *and* an NG911 set
  (`St_PreMod/St_PreDir/St_PreTyp/St_Name/St_PosType/St_PosDir/St_PosMod`). R5 has **no name
  field at all**.
  → Note: all of R1–R4 also publish `FULLSTREET`, populated 99–100%.
  Concatenation is the *wrong* answer here and the model should prefer `FULLSTREET`. The
  honest "hard win" is R2, where you must choose between two competing splits *and*
  `FULLSTREET`. The honest "field does not exist" case is R5.
- **Functional class, four vocabularies.**
  - R2 `YCFC`: FHWA-style string codes with domain labels — `02` = "02 - Other Principal
    Arterial Rural", `11` = "11 - Principal Arterial Interstate Urban". The domain label decodes it.
  - R3 `YCFC`: **the same codes, a different label format** — `02` = "Other Principal
    Arterial - Rural (02)". Two publishers of one code set disagree on label text.
  - R5 `F_System`: bare integers 1–7, **no domain**. The service will not tell you what the
    numbers mean; that lives in FHWA documentation. All 7 values present in Sacramento County.
  - R1/R4 `CARTOCLASS`: `ALY / ART / BIK / COL / HWY / PED / RES / RMP / TO`.
- **One-way, three encodings.** R1 and R4: `B / FT / TF`. R2: `B / FT` **plus a rogue `2`
  that is not in the service's own declared domain** (`B/FT/TF`). R3: Integer `0 / 1 / 2`.
  → The real spread is `B/FT/TF` vs `B/FT/2` vs `0/1/2`.
  `Y/N` belongs to `PRIVATE`, not to one-way.
- **Speed limit.** R1 `SAFETY_LIM` (100%, includes `0`), R2 `Speed_Limit` (29%, includes `0`),
  R3 `SPEED` (partial). `0` is unknown, not zero: it must become null, not a speed.
- **Proposed geometry is in the layer.** R3 `STATUS` is `EXISTING`/`PROPOSED` at 100%.
  Whether to load proposed roads is a filtering decision the pipeline must expose, not hide.
- **Fields that exist and are entirely empty.** R3 `TRAVELDIRE` and `SOURCE` are `' '` in
  all 2,791 rows.

### Roads: the two "confident mistake" candidates — both real, neither staged

A field matcher needs a naturally occurring confident-but-wrong match to be tested against.
There are two.

1. **Two class columns, and they disagree.** R1/R4 publish both `CLASS` (`A, ALLEY, BIKE, C,
   FRONT, H, LOCAL, PED, RAMP, TO, TRAIL`) and `CARTOCLASS` (`ALY, ART, BIK, COL, HWY, PED,
   RES, RMP, TO`), with **no domain on either**. They look like two spellings of one list, but
   they are independent axes: on R1, `CLASS='C'` occurs with `CARTOCLASS` `RES` (2,574 rows) and
   `ART` (580) as well as `COL`, and the two disagree on **6,142 of 72,574 roads**. A name match
   and the AI both take `CLASS`, the AI only at medium confidence. Nothing in the service says
   which is right; that takes someone who knows the data. For the demo we use `CLASS`: its
   arterial / collector / local / highway categories read as a functional class, which is what
   `road_class` holds, while `CARTOCLASS` reads as a class for drawing the map.
2. **`RoadClass` is 99% empty.** R2 publishes `RoadClass` with a clean NG911 domain
   (`Primary/Secondary/Local/Ramp/…`) — a near-perfect name match for `road_class`. It is
   populated in **135 of 11,493 rows (1%)**. The usable fields are `Yolo_Road_Classification`
   (98%) and `YCFC` (100%). Same trap on `Ownership`: perfect name, **32 of 11,493 rows**.

Both are precisely the case for feeding sample values *and* population rates to the matcher.

---

## Hydrants — California (3 loadable + 2 greyed)

| # | Publisher | URL | Features | Fields | CRS | Notes |
|---|-----------|-----|---------:|-------:|-----|-------|
| H1 | **City of West Sacramento** | `gis.cityofwestsacramento.org/server/rest/services/fire_hydrants/FeatureServer/0` | 3,008 | 21 | 2226 | Self-hosted. Cleanest status vocabulary. |
| H2 | **El Dorado County** | `services.arcgis.com/UHg8l1wC48WQyDSO/…/FireHydrants/FeatureServer/0` | 13,950 | 35 | 2226 | Field names truncated to 10 chars. |
| H3 | **City of Los Angeles** (NavigateLA) | `services7.arcgis.com/Z89o6uh7LaCe9h2P/…/Hydrants_for_NavigateLA/FeatureServer/0` | 60,372 | 6 | **4326** | Compound `SIZE_CODE`. Already WGS84. |

**Greyed-out cards:**

| # | Publisher | Reason |
|---|-----------|--------|
| H4 | **Carmichael Water District** `gis.carmichaelwd.org/arcgis/rest/services/Hydrants/FeatureServer/0` | Publicly listed on ArcGIS Online, self-hosted, returns **`error 499 Token Required`**. Listed ≠ available. |
| H5 | **Sacramento Metro Fire** `services7.arcgis.com/YFJtK05B4hdhORWd/…/MetroFire_Hydrants/FeatureServer/0` | 25,638 features but only 5 fields, and the semantics are **swapped row to row**: `location_name` holds `1078`, `Dead Hydrant `, `Wharf - Supplied by Fire Pump`, `Hose Connection-57344`; `description` holds `STEAMER`, ` HYD`, `55305A`, `Fed from FDC at SE Entrance off Sunrise Vista`. Not mappable by field. Greyed for *needs value-level parsing, not field matching* — the honest answer is "do not ingest this one." |

### Hydrants: what each source proves

- **Shapefile lineage, 10-character field names.** H2: `Operationa` (= operational status),
  `Hydrant_Ty`, `Manufactur`, `XY_Confide`, `Last_Updat`, `Last_Edito`, `GPS_Latitu`,
  `GPS_Longit`. Truncation destroys exactly the information a name-matcher needs.
- **The null sentinel is a single space.** H2 has **zero SQL NULLs**. `Operationa IS NULL`
  returns **0**; `Operationa = ' '` returns **13,362 of 13,950**. A naive null check reports
  the field 100% populated when it is 4%. `Owner`: 1,726 rows are `' '`. Any completeness or
  population metric must treat `' '` (and `''`) as absent, per source.
- **Case chaos inside one field.** H2 `Hydrant_Ty`: `draft`, `DRY_BARREL`, `NORMAL`,
  `WET_BARREL`, `WHARF`. Folding case is mechanical; whether `draft` means `DRY_BARREL` is a
  judgement a person makes.
- **Two vocabularies in one field.** H2 `Maintained`: `EID`, `GDPUD` (agency acronyms) mixed
  with `N`, `U`, `Y` (flags). One field answering two questions.
- **Owner as unexpanded acronyms.** H2 `Owner`: `EID` (El Dorado Irrigation District),
  `GDPUD` (Georgetown Divide PUD), `PRIVATE`, `ROYLEQUE`.
- **Status.** H1 `STATUS`: `Operational / Out of Service / Removed` (96%). H2 `Operationa`:
  `OPERATIONAL` only (4%). Nothing in H3. → `status` must be nullable in the shared model.
- **Size is a compound spec, not a number.** H3 `SIZE_CODE` (100%): `2 1/2 D`,
  `2 1/2 DX4S`, `2 1/2 S BU`, `2.5X4DX2.5AV`, `4 S BU`, `4D`, `4D X 2 1/2AV`,
  `4S`. Outlet size, count and type in one string, with **fractional and decimal spellings
  of the same measurement** (`2 1/2` vs `2.5`). This is the live case for a
  digit-run guard: `4S` must not fuzzy-match `2 1/2 X 4D`. H2 `LAT_SIZE` is clean
  (`4/6/8/10`) at 36%.
- **Whitespace variants.** H3 `CORNER`: both `" E"` and `"E"`; also `ES`/`WS` alongside
  `E`/`S`. Trim before you count distinct values.

---

## Pagination behavior — measured, all loadable layers

Suite: `1=1` vs `OBJECTID>=0`; over-ask at 3× `maxRecordCount`; deep offset near the tail;
`min`/`max` OID via `outStatistics`; unordered two-page overlap; payload delta.

| Layer | `maxRecordCount` | `1=1` | Over-ask 6000 | Deep offset | OID range | density | Unordered pages stable | 500 rows: all fields vs OID only |
|---|---:|---|---|---|---|---:|---|---|
| R1 Sacramento County | 2000 | ok | clamps to 2000 | ok, 362 ms | 1..72,559 | 1.00 | yes | 384 KB/777 ms vs 96 KB/540 ms |
| R2 Yolo County | 2000 | ok | clamps to 2000 | ok, 368 ms | 1..12,775 | 0.90 | yes | 718 KB/741 ms vs 170 KB/629 ms |
| R3 West Sacramento | 2000 | ok | clamps to 2000 | n/a (2,791) | 4,383,991..4,386,787 | 1.00 | yes | 659 KB/658 ms vs 145 KB/445 ms |
| R4 Elk Grove | 2000 | ok | clamps to 2000 | ok, 268 ms | 6,605,529..6,619,120 | 1.00 | yes | 439 KB/718 ms vs 138 KB/390 ms |
| R5 Caltrans FC | 2000 | ok | clamps to 2000 | ok, 500 ms @ 779,334 | 1..779,834 | 1.00 | yes | 555 KB/2,125 ms vs 486 KB/946 ms |
| H1 West Sacramento | 2000 | ok | clamps to 2000 | n/a (3,008) | 1..3,008 | 1.00 | yes | 231 KB/508 ms vs 44 KB/337 ms |
| H2 El Dorado County | 2000 | ok | clamps to 2000 | ok, 392 ms | 1..13,950 | 1.00 | yes | 330 KB/544 ms vs 45 KB/460 ms |
| H3 Los Angeles | 2000 | ok | clamps to 2000 | ok | 1..60,372 | 1.00 | yes | — |

Measured throughput, one page of 1000 with geometry at `outSR=4326`: R1 —, R2 916 f/s,
R3 990 f/s, R4 1,167 f/s, R5 539 f/s, H1 1,192 f/s, H2 1,166 f/s, H3 1,504 f/s.
Caltrans `SHN_Lines` measured 138 f/s — the slowest thing tested.

**Conclusions for the client's fetch ladder:**

1. Every locked source accepts `1=1`, reports `supportsPagination: true`, and clamps an
   over-ask to `maxRecordCount` rather than erroring. **No live source reproduces a WAF
   that blocks `1=1` or a service that refuses offset paging**, though both happen in the
   wild. The ladder is still built and unit-tested against synthetic failures, because the
   pattern is the transferable lesson.
2. `maxRecordCount` is 2000 everywhere. Start there; ratchet down on failure, never back up.
3. **OIDs do not start at 1.** R3 starts at 4,383,991 and R4 at 6,605,529. OID windowing must
   read `min`/`max` from `outStatistics` and never assume a base or contiguity — R2's density
   is 0.90, so 12,775 OIDs cover 11,493 features.
4. `orderByFields=<oid>` on every page. Overlap was stable without it in all layers today, but
   stability is not contractual, and seen-OID dedupe is cheap.
5. Request only the fields you mapped. On R1 that is a **4× payload reduction**; on H2, 7×.
   R5 is the exception — 7 fields total, so subsetting buys throughput, not payload.
6. R5 needs the `County_label='Sacramento'` filter or it is 780k features.
7. R4 (Elk Grove, MapServer) reports **no `objectIdField`** in its layer JSON. Do not assume
   the field is named `OBJECTID` — fall back to the field whose `type` is
   `esriFieldTypeOID`.

## Notes

- Elk Grove and Sacramento County share schema lineage (both regional SACOG derivatives).
  They are kept as separate cards because R4 is a self-hosted MapServer with a different
  `CLASS` vocabulary, but they are not independent evidence of schema divergence.
