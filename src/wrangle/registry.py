"""The locked source registry, loaded from the YAML in `sources/`.

`sources/*.yaml` is the output of the vetting run documented in docs/SOURCES.md.
Rejected sources stay in the file with their reason, because a greyed-out source
card with an honest reason is part of the talk.
"""

from __future__ import annotations

import datetime as dt
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

SOURCE_DIR = Path(__file__).parent / "sources"

SourceStatus = Literal["loadable", "rejected"]

# Why a source was rejected. Every one of these was observed live on 2026-09-08.
REJECTION_REASONS = {
    "token_required": "Publicly listed but returns 499 Token Required to an anonymous request.",
    "catalogued_url_dead": "The catalogue's URL does not resolve; a different host works.",
    "no_mappable_attributes": "The service works, but has no attributes worth mapping.",
    "needs_value_level_parsing": "Field semantics vary row to row; no field mapping can be right.",
    "http_403": "Blocked at the edge for anonymous requests.",
    "waf_blocked": "A web application firewall rejects the query, not the request.",
}


class Expectation(BaseModel):
    """Assertions the vetting CLI re-checks. Drift means re-vet before the talk."""

    model_config = ConfigDict(extra="forbid")

    count: int | None = None
    count_filtered: int | None = None
    crs: int | None = None
    max_record_count: int | None = None
    oid_field: str | None = None
    coded_domain_fields: int | None = None


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    publisher: str
    attribution: str = ""
    url: str
    status: SourceStatus = "loadable"
    where: str = "1=1"
    reason: str | None = None
    reason_detail: str | None = None
    null_sentinels: list[str] = Field(default_factory=lambda: ["", " "])
    expect: Expectation = Field(default_factory=Expectation)
    interest: list[str] = Field(default_factory=list)
    # False keeps a vetted rejection in the record without giving it a card on the
    # loading dock. The talk shows one greyed card per feature type, not every rejection.
    catalog: bool = True
    # The colour this source draws in, everywhere in the app. Optional: without it the
    # source takes the next palette colour. Viewers can still change it on the map.
    color: str | None = None

    @property
    def loadable(self) -> bool:
        return self.status == "loadable"

    @property
    def sentinels(self) -> tuple[str, ...]:
        return tuple(self.null_sentinels)

    @property
    def expected_count(self) -> int | None:
        """The count that matters: filtered if a `where` narrows the layer."""
        if self.where != "1=1" and self.expect.count_filtered is not None:
            return self.expect.count_filtered
        return self.expect.count

    @property
    def reason_text(self) -> str:
        if not self.reason:
            return ""
        return REJECTION_REASONS.get(self.reason, self.reason)

    def drift(self, observed: dict[str, Any]) -> list[str]:
        """Compare a live assessment against what was locked in."""
        out: list[str] = []
        exp = self.expected_count
        if exp is not None and observed.get("count") is not None:
            got = observed["count"]
            if got != exp:
                delta = got - exp
                out.append(f"count {exp:,} -> {got:,} ({delta:+,})")
        if self.expect.crs is not None and observed.get("wkid") not in (None, self.expect.crs):
            out.append(f"crs EPSG:{self.expect.crs} -> EPSG:{observed['wkid']}")
        if (
            self.expect.max_record_count is not None
            and observed.get("max_record_count") not in (None, self.expect.max_record_count)
        ):
            out.append(
                f"maxRecordCount {self.expect.max_record_count} -> {observed['max_record_count']}"
            )
        if self.expect.oid_field is not None and observed.get("oid_field") not in (
            None,
            self.expect.oid_field,
        ):
            out.append(f"oid field {self.expect.oid_field} -> {observed['oid_field']}")
        if (
            self.expect.coded_domain_fields is not None
            and observed.get("domain_count") is not None
            and observed["domain_count"] != self.expect.coded_domain_fields
        ):
            out.append(
                f"coded-domain fields {self.expect.coded_domain_fields} "
                f"-> {observed['domain_count']}"
            )
        return out


class Boundary(BaseModel):
    """The area of interest drawn under every layer, so the audience can see
    which sources cover the region and which spill past it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    where: str
    attribution: str = ""
    # Generalisation tolerance in degrees. A county outline does not need
    # survey precision to be read from the back of a room.
    max_allowable_offset: float = 0.0005


class SourceSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: str
    region: str = ""
    vetted: dt.date | None = None
    boundary: Boundary | None = None
    sources: list[Source]

    @property
    def cards(self) -> list[Source]:
        """The sources that get a card on the loading dock: every loadable one, plus the
        rejections the talk chooses to show."""
        return [s for s in self.sources if s.catalog]

    @property
    def loadable(self) -> list[Source]:
        return [s for s in self.sources if s.loadable]

    @property
    def rejected(self) -> list[Source]:
        return [s for s in self.sources if not s.loadable]

    def get(self, source_id: str) -> Source:
        for s in self.sources:
            if s.id.lower() == source_id.lower():
                return s
        raise KeyError(
            f"no source {source_id!r} in {self.feature}; "
            f"have {[s.id for s in self.sources]}"
        )

    @property
    def total_expected_features(self) -> int:
        return sum(s.expected_count or 0 for s in self.loadable)


@cache
def load_sources(feature: str) -> SourceSet:
    """Load a locked source set, e.g. `load_sources("roads")`."""
    path = SOURCE_DIR / f"{feature}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in SOURCE_DIR.glob("*.yaml"))
        raise FileNotFoundError(f"no source set {feature!r}; have {available}")
    return SourceSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def available_features() -> list[str]:
    return sorted(p.stem for p in SOURCE_DIR.glob("*.yaml"))
