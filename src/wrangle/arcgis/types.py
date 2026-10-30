"""Typed views over ArcGIS REST layer JSON.

Everything here is parsed from the single `?f=json` layer request. The talk
claims one HTTP request tells you whether a layer is worth bothering with; this
module is that claim, in code.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Values that mean "absent" even though the service returns them as data.
# El Dorado County (H2) has zero SQL nulls and uses a single space instead;
# a population metric that trusts IS NULL calls a 4%-populated field complete.
DEFAULT_NULL_SENTINELS: tuple[str, ...] = ("", " ")


class CodedValue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: Any
    name: str


class CodedDomain(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    type: str | None = None
    coded_values: list[CodedValue] = Field(default_factory=list, alias="codedValues")

    def decode(self, code: Any) -> str | None:
        """Tier 1 of value reconciliation: free and exact.

        Returns None when the code is absent from the domain. That happens on real
        data — Yolo County's OneWay domain declares B/FT/TF and the table contains
        a 2 — so callers must handle the miss rather than assume coverage.
        """
        for cv in self.coded_values:
            if cv.code == code:
                return cv.name
            if str(cv.code) == str(code):
                return cv.name
        return None

    def as_dict(self) -> dict[Any, str]:
        return {cv.code: cv.name for cv in self.coded_values}


class LayerField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: str
    alias: str | None = None
    length: int | None = None
    domain: CodedDomain | None = None

    @property
    def short_type(self) -> str:
        return self.type.replace("esriFieldType", "")

    @property
    def is_oid(self) -> bool:
        return self.type == "esriFieldTypeOID"

    @property
    def is_geometry(self) -> bool:
        return self.type == "esriFieldTypeGeometry"

    @property
    def is_system(self) -> bool:
        """Fields no shared model will ever want."""
        return (
            self.is_oid
            or self.is_geometry
            or self.type == "esriFieldTypeGlobalID"
            or self.name.lower() in {"globalid", "shape__length", "shape__area"}
            or self.name.startswith("Shape.")
        )

    @property
    def coded_values(self) -> dict[Any, str]:
        return self.domain.as_dict() if self.domain else {}


class Extent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    xmin: float | None = None
    ymin: float | None = None
    xmax: float | None = None
    ymax: float | None = None
    spatial_reference: dict[str, Any] = Field(default_factory=dict, alias="spatialReference")

    @property
    def wkid(self) -> int | None:
        return self.spatial_reference.get("latestWkid") or self.spatial_reference.get("wkid")

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        vals = (self.xmin, self.ymin, self.xmax, self.ymax)
        return vals if all(v is not None for v in vals) else None

    def plausible_for_wkid(self) -> bool:
        """Does the coordinate magnitude match the declared CRS?

        A declared CRS that does not match the coordinate magnitudes is a
        classic publisher error. Cheap sanity check, catches the common case.
        """
        box = self.bbox
        if box is None or self.wkid is None:
            return True
        span = max(abs(box[0]), abs(box[2]))
        if self.wkid == 4326:
            return span <= 180.0
        if self.wkid == 3857:
            return 180.0 < span <= 20_037_509.0
        return span > 180.0  # projected: feet or metres, never degrees


class LayerInfo(BaseModel):
    """A feature layer's self-description, from one request."""

    model_config = ConfigDict(extra="ignore")

    url: str = ""
    id: int | None = None
    name: str | None = None
    type: str | None = None
    geometry_type: str | None = Field(default=None, alias="geometryType")
    object_id_field: str | None = Field(default=None, alias="objectIdField")
    max_record_count: int | None = Field(default=None, alias="maxRecordCount")
    standard_max_record_count: int | None = Field(
        default=None, alias="standardMaxRecordCount"
    )
    capabilities: str | None = None
    fields: list[LayerField] = Field(default_factory=list)
    extent: Extent | None = None
    editing_info: dict[str, Any] = Field(default_factory=dict, alias="editingInfo")
    advanced_query_capabilities: dict[str, Any] = Field(
        default_factory=dict, alias="advancedQueryCapabilities"
    )
    supports_pagination_flag: bool | None = Field(
        default=None, alias="supportsPagination"
    )

    # --- identity ------------------------------------------------------------

    @property
    def oid_field(self) -> str:
        """The OBJECTID field name, however the service chooses to hide it.

        Elk Grove (R4) is a MapServer whose layer JSON omits objectIdField
        entirely. Never hardcode "OBJECTID"; fall back to field type, and only
        then to the conventional name.
        """
        if self.object_id_field:
            return self.object_id_field
        for f in self.fields:
            if f.is_oid:
                return f.name
        return "OBJECTID"

    @property
    def geometry(self) -> str | None:
        return {
            "esriGeometryPoint": "point",
            "esriGeometryPolyline": "linestring",
            "esriGeometryPolygon": "polygon",
            "esriGeometryMultipoint": "multipoint",
        }.get(self.geometry_type or "")

    @property
    def wkid(self) -> int | None:
        return self.extent.wkid if self.extent else None

    # --- capability ----------------------------------------------------------

    @property
    def supports_pagination(self) -> bool:
        if self.advanced_query_capabilities:
            return bool(self.advanced_query_capabilities.get("supportsPagination"))
        return bool(self.supports_pagination_flag)

    @property
    def supports_order_by(self) -> bool:
        return bool(self.advanced_query_capabilities.get("supportsOrderBy"))

    @property
    def supports_statistics(self) -> bool:
        return bool(self.advanced_query_capabilities.get("supportsStatistics", True))

    @property
    def can_query(self) -> bool:
        return "query" in (self.capabilities or "Query").lower()

    @property
    def page_size(self) -> int:
        """Where the page-size ratchet starts. Every locked source says 2000."""
        return self.max_record_count or self.standard_max_record_count or 1000

    # --- attributes ----------------------------------------------------------

    @property
    def data_fields(self) -> list[LayerField]:
        """Fields a shared model could plausibly want."""
        return [f for f in self.fields if not f.is_system]

    @property
    def field_names(self) -> list[str]:
        """The real source field list. The hallucination guard validates against this."""
        return [f.name for f in self.fields]

    def field(self, name: str) -> LayerField:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"{self.name} has no field {name!r}")

    @property
    def domains(self) -> dict[str, CodedDomain]:
        return {f.name: f.domain for f in self.fields if f.domain and f.domain.coded_values}

    @property
    def last_edit(self) -> dt.date | None:
        ms = self.editing_info.get("lastEditDate")
        if not ms:
            return None
        return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).date()


class FieldProfile(BaseModel):
    """What the matcher gets about one source field, beyond its name and type.

    Sample values and a population rate are the whole reason the demo's matcher
    beats a name-only matcher. Yolo County's RoadClass is a perfect name match on
    1% of rows; nothing but the population rate reveals that.
    """

    name: str
    type: str
    alias: str | None = None
    coded_values: dict[str, str] = Field(default_factory=dict)
    sample_values: list[Any] = Field(default_factory=list)
    distinct_count: int | None = None
    populated: int | None = None
    total: int | None = None

    @property
    def population_rate(self) -> float | None:
        if self.total in (None, 0) or self.populated is None:
            return None
        return self.populated / self.total

    @property
    def is_effectively_empty(self) -> bool:
        rate = self.population_rate
        return rate is not None and rate < 0.05


class Verdict(BaseModel):
    """Every fetch ends with one of these. A job nobody watched is not a job that finished."""

    outcome: str  # complete | truncated | partial | failed
    expected: int | None = None
    fetched: int = 0
    pages: int = 0
    duplicates_dropped: int = 0
    final_page_size: int | None = None
    strategy: str = "offset"
    errors: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.outcome == "complete"

    def summary(self) -> str:
        rate = f"{self.fetched / self.elapsed_s:.0f} f/s" if self.elapsed_s else "—"
        return (
            f"{self.outcome}: {self.fetched}/{self.expected if self.expected is not None else '?'}"
            f" in {self.pages} pages via {self.strategy}"
            f" ({rate}, page={self.final_page_size}, dupes={self.duplicates_dropped})"
        )
