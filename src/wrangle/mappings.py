"""The reviewed configuration: one record per source, in a small SQLite database.

Each source the review touches gets one **configuration record**: everything needed
to load that source into the shared model again, and to tell what changed since.

    sources                       one row per (feature, source)
      feature, source_id          roads, R1
      publisher                   Sacramento County
      url, where_clause           what was reviewed: the layer and its filter
      model                       roads v1: the model version the decisions were made for
      config                      the decisions, as JSON (below)
      created_at, updated_at      when the record was started and last changed
      converted_at, verdict       the last conversion, and how it went

    config = {
      "fields":            {"name": "FULLSTREET", "road_class": "CLASS", "owner": null},
      "decided_by":        {"name": "automap", "road_class": "person", "owner": "person"},
      "values":            {"road_class": {"LOCAL": "local", "TO": null}},
      "values_decided_by": {"road_class": {"LOCAL": "person", "TO": "person"}},
      "rules":             {"source_date": "yyyymmdd"},
      "modes":             {"road_class": "standardized", "name": "cleaned"}
    }

    history                       every decision, in order: what changed, from what, to what,
      at, kind, target, raw,        and whether a person or automap made it
      old, new, decided_by

`modes` says how a field's values land: `published` (as the publisher wrote them),
`cleaned` (generic tidying, no vocabulary) or `standardized` (onto the model's own
values, one decision per raw value). A field with no mode lands as published —
unless it already has value decisions, which only standardizing uses.

A model field or raw value that is absent has not been decided yet; `null` is a
decision ("nothing in this layer carries it", "this code means nothing the model can
hold"). Changing which source field feeds a model field clears that field's value
decisions: they were made for different values. A record whose last decision is
cleared is removed; its history stays.

The record holds field names and code lists only, never agency features. It is also
the configuration for the next run: a field that has gone, a raw value nobody
decided (a new code arrives as undecided, never as a silent null), or a model that
moved on all show up against it.

The database is one file, `wrangle.db` next to the app. `WRANGLE_DB` points the app at
another one (a rehearsal copy, say); `WRANGLE_MAPPINGS_DIR` puts `wrangle.db` in that
folder instead.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

APP_DIR = Path(__file__).resolve().parents[2]
_FOLDER = os.environ.get("WRANGLE_MAPPINGS_DIR")
DB_PATH = Path(
    os.environ.get("WRANGLE_DB")
    or (Path(_FOLDER) / "wrangle.db" if _FOLDER else APP_DIR / "wrangle.db")
)

DecidedBy = Literal["person", "automap"]
SECTIONS = ("fields", "decided_by", "values", "values_decided_by", "rules", "modes")
Mode = Literal["published", "cleaned", "standardized"]
MODES: tuple[str, ...] = ("published", "cleaned", "standardized")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    feature      TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    publisher    TEXT NOT NULL,
    url          TEXT,
    where_clause TEXT,
    model        TEXT,
    config       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    converted_at TEXT,
    verdict      TEXT,
    PRIMARY KEY (feature, source_id)
);
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    feature    TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    at         TEXT NOT NULL,
    kind       TEXT NOT NULL,   -- field | value | rule | mode | vocabulary
    target     TEXT,
    raw        TEXT,            -- the raw value, for kind = value
    old        TEXT,            -- JSON; NULL means "not decided"
    new        TEXT,            -- JSON; NULL means "not decided" (cleared)
    decided_by TEXT
);
CREATE TABLE IF NOT EXISTS vocabulary (   -- values a person added to an enum field
    feature  TEXT NOT NULL,
    field    TEXT NOT NULL,
    value    TEXT NOT NULL,
    meaning  TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    PRIMARY KEY (feature, field, value)
);
"""


# --- connection ------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="microseconds")


def _json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _empty() -> dict[str, Any]:
    return {k: {} for k in SECTIONS}


# --- reading ---------------------------------------------------------------------------


def load_record(feature: str, source_id: str) -> dict[str, Any] | None:
    """The whole configuration record, or None if nothing has been decided."""
    with closing(_connect()) as con:
        row = con.execute(
            "SELECT * FROM sources WHERE feature = ? AND source_id = ?", (feature, source_id)
        ).fetchone()
    if row is None:
        return None
    rec = dict(row)
    rec["config"] = _normalise(json.loads(rec["config"]))
    return rec


def load_doc(feature: str, source_id: str) -> dict[str, Any]:
    """Just the decisions: fields, decided_by, values, values_decided_by, rules."""
    rec = load_record(feature, source_id)
    return rec["config"] if rec else _empty()


def load_mapping(feature: str, source_id: str) -> dict[str, str | None]:
    return load_doc(feature, source_id)["fields"]


def load_history(feature: str, source_id: str) -> list[dict[str, Any]]:
    """Every decision ever made for this source, oldest first."""
    with closing(_connect()) as con:
        rows = con.execute(
            "SELECT at, kind, target, raw, old, new, decided_by FROM history "
            "WHERE feature = ? AND source_id = ? ORDER BY id",
            (feature, source_id),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("old", "new"):
            d[k] = json.loads(d[k]) if d[k] is not None else None
        out.append(d)
    return out


def updated_at(feature: str, source_id: str) -> dt.datetime | None:
    """When a decision last changed, for telling whether a conversion is stale."""
    rec = load_record(feature, source_id)
    return dt.datetime.fromisoformat(rec["updated_at"]) if rec else None


def _normalise(cfg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: dict(cfg.get(k) or {}) for k in SECTIONS}
    # Raw values are keys: always strings, whatever produced them (`2`, `no`).
    for k in ("values", "values_decided_by"):
        out[k] = {f: {str(r): v for r, v in (m or {}).items()} for f, m in out[k].items()}
    return out


# --- deciding --------------------------------------------------------------------------


def save_decision(
    feature: str,
    source_id: str,
    publisher: str,
    target: str,
    source_field: str | None,
    decided_by: DecidedBy = "person",
) -> dict[str, Any]:
    doc = load_doc(feature, source_id)
    old = doc["fields"].get(target, _UNSET)
    if old is not _UNSET and old != source_field:
        _forget_values(doc, target)
    doc["fields"][target] = source_field
    doc["decided_by"][target] = decided_by
    log = [("field", target, None, old, source_field, decided_by)]
    return _write(feature, source_id, publisher, doc, log)


def clear_decision(feature: str, source_id: str, publisher: str, target: str) -> dict[str, Any]:
    doc = load_doc(feature, source_id)
    old = doc["fields"].pop(target, _UNSET)
    doc["decided_by"].pop(target, None)
    _forget_values(doc, target)
    log = [("field", target, None, old, _UNSET, None)] if old is not _UNSET else []
    return _write(feature, source_id, publisher, doc, log)


def save_values(
    feature: str,
    source_id: str,
    publisher: str,
    target: str,
    decisions: dict[str, Any],
    decided_by: DecidedBy = "person",
) -> dict[str, Any]:
    """Record raw value -> model value for one model field. `None` is a decision:
    the value means nothing the model can hold, and lands as null."""
    doc = load_doc(feature, source_id)
    vals = doc["values"].setdefault(target, {})
    by = doc["values_decided_by"].setdefault(target, {})
    doc["modes"].setdefault(target, "standardized")  # deciding values is standardizing
    log = []
    for raw, value in decisions.items():
        raw = str(raw)
        log.append(("value", target, raw, vals.get(raw, _UNSET), value, decided_by))
        vals[raw] = value
        by[raw] = decided_by
    return _write(feature, source_id, publisher, doc, log)


def clear_value(
    feature: str, source_id: str, publisher: str, target: str, raw: str
) -> dict[str, Any]:
    doc = load_doc(feature, source_id)
    old = doc["values"].get(target, {}).get(raw, _UNSET)
    for k in ("values", "values_decided_by"):
        doc[k].get(target, {}).pop(raw, None)
        if not doc[k].get(target, True):
            doc[k].pop(target)
    log = [("value", target, raw, old, _UNSET, None)] if old is not _UNSET else []
    return _write(feature, source_id, publisher, doc, log)


def save_rule(
    feature: str, source_id: str, publisher: str, target: str, rule: str | None
) -> dict[str, Any]:
    """A field with too many values for a table gets one rule, e.g. a date format.
    `None` clears it."""
    doc = load_doc(feature, source_id)
    old = doc["rules"].get(target, _UNSET)
    if rule is None:
        doc["rules"].pop(target, None)
    else:
        doc["rules"][target] = rule
    if rule is None and old is _UNSET:  # clearing a rule that was never set: nothing happened
        log = []
    else:
        log = [("rule", target, None, old, _UNSET if rule is None else rule, "person")]
    return _write(feature, source_id, publisher, doc, log)


def mode_of(doc: dict[str, Any], target: str) -> str:
    """How a field's values land. No choice yet: as published, unless values were
    already decided one by one (that is standardizing)."""
    chosen = (doc.get("modes") or {}).get(target)
    return chosen or ("standardized" if doc["values"].get(target) else "published")


def save_mode(
    feature: str, source_id: str, publisher: str, target: str, mode: Mode
) -> dict[str, Any]:
    """Choose how one field's values land. Value decisions are kept when leaving
    `standardized`, so coming back finds them again."""
    if mode not in MODES:
        raise ValueError(f"{mode!r} is not one of {MODES}")
    doc = load_doc(feature, source_id)
    old = doc["modes"].get(target, _UNSET)
    doc["modes"][target] = mode
    log = [("mode", target, None, old, mode, "person")]
    return _write(feature, source_id, publisher, doc, log)


def clear_all(feature: str, source_id: str, publisher: str, *, fields: bool) -> dict[str, Any]:
    """Start a source's review over. Values, rules and modes always go; `fields` also
    forgets the field matches (Match fields), which the values hang off. Every cleared
    decision is logged, so the history still shows what there was."""
    doc = load_doc(feature, source_id)
    log: list[tuple] = []
    if fields:
        log += [("field", t, None, old, _UNSET, None) for t, old in doc["fields"].items()]
        doc["fields"], doc["decided_by"] = {}, {}
    for t, vals in doc["values"].items():
        log += [("value", t, raw, old, _UNSET, None) for raw, old in vals.items()]
    log += [("rule", t, None, old, _UNSET, None) for t, old in doc["rules"].items()]
    log += [("mode", t, None, old, _UNSET, None) for t, old in doc["modes"].items()]
    for k in ("values", "values_decided_by", "rules", "modes"):
        doc[k] = {}
    return _write(feature, source_id, publisher, doc, log)


def load_added(feature: str) -> dict[str, list[tuple[str, str]]]:
    """Values a person added to the shared model's vocabularies, oldest first."""
    out: dict[str, list[tuple[str, str]]] = {}
    with closing(_connect()) as con:
        for r in con.execute(
            "SELECT field, value, meaning FROM vocabulary WHERE feature = ? ORDER BY added_at",
            (feature,),
        ):
            out.setdefault(r["field"], []).append((r["value"], r["meaning"]))
    return out


def add_value(feature: str, field: str, value: str, meaning: str) -> None:
    """Add a value to one enum field's vocabulary, for every source. Logged in the
    history under source `*`, since it belongs to the model, not to one source."""
    with closing(_connect()) as con, con:
        con.execute(
            "INSERT INTO vocabulary (feature, field, value, meaning, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (feature, field, value, meaning, _now()),
        )
        con.execute(
            "INSERT INTO history (feature, source_id, at, kind, target, raw, old, new, decided_by) "
            "VALUES (?, '*', ?, 'vocabulary', ?, NULL, NULL, ?, 'person')",
            (feature, _now(), field, _json(value)),
        )


def remove_value(feature: str, field: str, value: str) -> bool:
    """Take an added value out again. False when it was never added."""
    with closing(_connect()) as con, con:
        gone = con.execute(
            "DELETE FROM vocabulary WHERE feature = ? AND field = ? AND value = ?",
            (feature, field, value),
        ).rowcount
        if gone:
            con.execute(
                "INSERT INTO history (feature, source_id, at, kind, target, raw, old, new, "
                "decided_by) VALUES (?, '*', ?, 'vocabulary', ?, NULL, ?, NULL, 'person')",
                (feature, _now(), field, _json(value)),
            )
    return bool(gone)


def value_in_use(feature: str, field: str, value: str) -> list[str]:
    """The sources whose decisions use this model value."""
    with closing(_connect()) as con:
        rows = con.execute(
            "SELECT source_id, config FROM sources WHERE feature = ?", (feature,)
        ).fetchall()
    return [r["source_id"] for r in rows
            if value in json.loads(r["config"]).get("values", {}).get(field, {}).values()]


def mark_converted(feature: str, source_id: str, verdict: str) -> None:
    """Stamp the record with its latest conversion."""
    with closing(_connect()) as con, con:
        con.execute(
            "UPDATE sources SET converted_at = ?, verdict = ? WHERE feature = ? AND source_id = ?",
            (_now(), verdict, feature, source_id),
        )


# --- writing ---------------------------------------------------------------------------

_UNSET: Any = object()  # "not decided", as distinct from a decided `None`


def _forget_values(doc: dict[str, Any], target: str) -> None:
    for k in ("values", "values_decided_by", "rules", "modes"):
        doc[k].pop(target, None)


def _header(feature: str, source_id: str) -> dict[str, Any]:
    """What these decisions were made against. A source or model that has since gone
    from the registry still gets a record."""
    from .models import load_model  # local: keeps this module importable on its own
    from .registry import load_sources

    head: dict[str, Any] = {"url": None, "where_clause": None, "model": None}
    try:
        s = load_sources(feature).get(source_id)
        head["url"], head["where_clause"] = s.url, s.where
    except (FileNotFoundError, KeyError):
        pass
    try:
        m = load_model(feature)
        head["model"] = f"{m.model_name} v{m.version}"
    except FileNotFoundError:
        pass
    return head


def _write(
    feature: str, source_id: str, publisher: str, doc: dict[str, Any], log: list[tuple]
) -> dict[str, Any]:
    now = _now()
    with closing(_connect()) as con, con:
        for kind, target, raw, old, new, by in log:
            con.execute(
                "INSERT INTO history (feature, source_id, at, kind, target, raw, old, new, "
                "decided_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (feature, source_id, now, kind, target, raw,
                 None if old is _UNSET else _json(old), None if new is _UNSET else _json(new), by),
            )
        if not any(doc[k] for k in SECTIONS):  # nothing decided: no record, not an empty one
            con.execute(
                "DELETE FROM sources WHERE feature = ? AND source_id = ?", (feature, source_id)
            )
            return {"source": source_id, "publisher": publisher, **_empty()}
        head = _header(feature, source_id)
        con.execute(
            """INSERT INTO sources (feature, source_id, publisher, url, where_clause, model, config,
                                    created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (feature, source_id) DO UPDATE SET
                 publisher = excluded.publisher, url = excluded.url,
                 where_clause = excluded.where_clause, model = excluded.model,
                 config = excluded.config, updated_at = excluded.updated_at""",
            (feature, source_id, publisher, head["url"], head["where_clause"], head["model"],
             _json(doc), now, now),
        )
    return {"source": source_id, "publisher": publisher, **head, **doc}

