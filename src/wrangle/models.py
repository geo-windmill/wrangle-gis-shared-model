"""The shared models, loaded from the YAML in `schemas/`.

The YAML is the artefact the audience reads on stage; this module is what the rest
of the pipeline binds against. Keeping the definition in YAML and the behaviour in
Python means the slide and the code cannot drift apart.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_DIR = Path(__file__).parent / "schemas"

FieldType = Literal["string", "enum", "integer", "number", "date", "boolean"]
GeometryType = Literal["point", "linestring", "polygon"]


class TargetField(BaseModel):
    """One field in a shared model."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: FieldType
    description: str = ""
    nullable: bool = True
    values: list[str] | None = None
    # What each value means, for a person picking one ("no": two-way). Not sent to the AI.
    meanings: dict[str, str] = Field(default_factory=dict)
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    notes: str = ""
    # False means: we want this field and no locked source publishes it. The
    # column ships empty on purpose. See docs/SOURCES.md.
    sourced: bool = True
    # Generic words a publisher's field name or alias might use for this field.
    # They drive the loading dock's suggested crosswalk; they are deliberately not
    # any one publisher's field names.
    hints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enum_needs_values(self) -> TargetField:
        if self.type == "enum" and not self.values:
            raise ValueError(f"enum field {self.name!r} declares no values")
        if self.type != "enum" and self.values:
            raise ValueError(f"non-enum field {self.name!r} declares values")
        if set(self.meanings) - set(self.values or []):
            raise ValueError(f"{self.name!r} explains values it does not declare")
        return self

    @property
    def python_type(self) -> type:
        return {
            "string": str,
            "enum": str,
            "integer": int,
            "number": float,
            "date": dt.date,
            "boolean": bool,
        }[self.type]

    def coerce(self, value: Any) -> Any:
        """Cast a reconciled value onto this field, or raise.

        Deliberately strict. The cautionary tale is a shape-classification field
        mapped onto a date target: a silent coercion folded 676 rows to null and
        reported success. This raises instead, and the caller decides what to record.
        """
        if value is None or value == "" or (isinstance(value, str) and not value.strip()):
            if not self.nullable:
                raise ValueError(f"{self.name} is not nullable")
            return None

        if self.type == "enum":
            if value not in self.values:
                raise ValueError(
                    f"{value!r} is not in the {self.name} vocabulary {self.values}"
                )
            return value

        if self.type == "date":
            if isinstance(value, dt.datetime):
                return value.date()
            if isinstance(value, dt.date):
                return value
            if isinstance(value, (int, float)):
                # ArcGIS dates are epoch milliseconds.
                return dt.datetime.fromtimestamp(value / 1000, tz=dt.UTC).date()
            return dt.date.fromisoformat(str(value))

        if self.type == "boolean":
            if isinstance(value, bool):
                return value
            raise ValueError(f"{value!r} is not a bool for {self.name}")

        if self.type == "integer":
            x = float(value)  # 3, 3.0 and "3" are all 3; 2.5 is not an integer
            if not x.is_integer():
                raise ValueError(f"{value!r} is not a whole number for {self.name}")
            out = int(x)
        else:
            out = self.python_type(value)
        if self.min is not None and out < self.min:
            raise ValueError(f"{out} below min {self.min} for {self.name}")
        if self.max is not None and out > self.max:
            raise ValueError(f"{out} above max {self.max} for {self.name}")
        return out


class ProvenanceField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: FieldType
    description: str = ""


class TargetModel(BaseModel):
    """A shared model: roads or hydrants."""

    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(alias="model")
    version: int
    title: str
    geometry: GeometryType
    crs: str
    fields: list[TargetField]
    provenance: list[ProvenanceField]

    @model_validator(mode="after")
    def _unique_names(self) -> TargetModel:
        names = [f.name for f in self.fields] + [p.name for p in self.provenance]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate field names in {self.model_name}: {sorted(dupes)}")
        return self

    def extended(self, added: dict[str, list[tuple[str, str]]]) -> TargetModel:
        """This model with values a person added to an enum's vocabulary, each with what
        it means. The AI prompts keep the shipped model, so recordings stay valid."""
        if not added:
            return self
        fields = []
        for f in self.fields:
            more = [(v, m) for v, m in added.get(f.name, []) if v not in (f.values or [])]
            if f.type == "enum" and more:
                f = f.model_copy(update={
                    "values": [*f.values, *(v for v, _ in more)],
                    "meanings": {**f.meanings, **{v: m for v, m in more if m}},
                })
            fields.append(f)
        return self.model_copy(update={"fields": fields})

    def field(self, name: str) -> TargetField:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"{self.model_name} has no field {name!r}")

    @property
    def field_names(self) -> list[str]:
        """The real target field list. The hallucination guard validates against this."""
        return [f.name for f in self.fields]

    @property
    def mappable_field_names(self) -> list[str]:
        """Target fields worth asking a matcher about — the sourced ones."""
        return [f.name for f in self.fields if f.sourced]

    @property
    def unsourced_field_names(self) -> list[str]:
        return [f.name for f in self.fields if not f.sourced]

    def enum_vocabulary(self) -> dict[str, list[str]]:
        return {f.name: list(f.values) for f in self.fields if f.type == "enum"}

    def as_enum(self, field_name: str) -> type[Enum]:
        f = self.field(field_name)
        if f.type != "enum":
            raise TypeError(f"{field_name} is not an enum")
        return Enum(f"{self.model_name}_{field_name}", {v: v for v in f.values})

    def prompt_view(self) -> str:
        """The target schema as the field matcher sees it. One screen, on purpose."""
        lines = [f"model: {self.model_name} (v{self.version}) — {self.geometry}"]
        for f in self.fields:
            if not f.sourced:
                continue
            bits = [f"  {f.name} ({f.type}"]
            if f.unit:
                bits.append(f", {f.unit}")
            bits.append(")")
            head = "".join(bits)
            lines.append(f"{head}: {' '.join(f.description.split())}")
            if f.values:
                lines.append(f"      one of: {', '.join(f.values)}")
        return "\n".join(lines)


@cache
def load_model(name: str) -> TargetModel:
    """Load a shared model by name, e.g. `load_model("roads")`."""
    path = SCHEMA_DIR / f"{name}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in SCHEMA_DIR.glob("*.yaml"))
        raise FileNotFoundError(f"no shared model {name!r}; have {available}")
    return TargetModel.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def available_models() -> list[str]:
    return sorted(p.stem for p in SCHEMA_DIR.glob("*.yaml"))
