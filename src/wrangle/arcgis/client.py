"""An async ArcGIS REST client written for this talk.

Deliberately small and deliberately opinionated. The behaviours here are the ones
the vetting run on 2026-09-08 proved necessary (docs/SOURCES.md):

  * every response goes through the record-and-replay cache
  * an ArcGIS `error` object in a 200 body is an error, not a result
  * page size starts at the service's own maxRecordCount and ratchets DOWN only
  * offset pagination falls back to OBJECTID windowing
  * OIDs are never assumed to start at 1 or to be contiguous
  * seen-OID dedupe, because a service can return the right count of wrong rows
  * every fetch ends with an explicit Verdict
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..cache import CacheKey, CacheMiss, ResponseCache
from .types import LayerField, LayerInfo, Verdict

USER_AGENT = "wrangle/0.1 (FOSS4G NA 2026 talk demo)"

# The ratchet floor. Below this a service is not worth waiting for.
MIN_PAGE_SIZE = 50
# Codes that mean "your page was too big", as opposed to "you are not allowed".
_TRANSIENT_CODES = {500, 502, 503, 504, 408}


class ArcGISError(RuntimeError):
    """An error the service reported, whatever HTTP status it chose to use."""

    def __init__(self, message: str, *, code: int | None = None, url: str = ""):
        self.code = code
        self.url = url
        super().__init__(f"[{code}] {message}" if code else message)

    @property
    def needs_token(self) -> bool:
        # Carmichael Water District (H4) is publicly listed and answers 499.
        return self.code in (498, 499)

    @property
    def is_transient(self) -> bool:
        return self.code in _TRANSIENT_CODES


class ArcGISClient:
    """Reads ArcGIS REST feature layers, through the cache."""

    def __init__(
        self,
        cache: ResponseCache | None = None,
        *,
        timeout: float = 90.0,
        concurrency: int = 4,
        retries: int = 2,
    ):
        self.cache = cache or ResponseCache()
        self.timeout = timeout
        self.retries = retries
        self._sem = asyncio.Semaphore(concurrency)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ArcGISClient:
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # --- transport -----------------------------------------------------------

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """One GET, cached. Raises ArcGISError for a service-reported error."""
        params = {**(params or {}), "f": "json"}
        key = CacheKey.build(url, params)

        if self.cache.may_read and not self.cache.prefers_network:
            hit = self.cache.get(key)
            if hit is not None:
                return _raise_if_error(hit, url)

        if not self.cache.may_fetch:
            self.cache.miss(key)
            raise CacheMiss(key)

        if self._client is None:
            raise RuntimeError("use ArcGISClient as an async context manager")

        payload, status, elapsed_ms = await self._fetch_with_retry(url, params)
        self.cache.put(key, payload, status=status, elapsed_ms=elapsed_ms)
        return _raise_if_error(payload, url)

    async def _fetch_with_retry(
        self, url: str, params: dict[str, Any]
    ) -> tuple[Any, int, int]:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                async with self._sem:
                    t0 = time.perf_counter()
                    resp = await self._client.get(url, params=params)
                    elapsed_ms = int((time.perf_counter() - t0) * 1000)
                # A "restricted" services directory can 403 the browser while
                # ?f=json answers 200. So we
                # only trust the body, and only give up on a hard client error.
                if resp.status_code >= 400 and resp.status_code not in _TRANSIENT_CODES:
                    try:
                        return resp.json(), resp.status_code, elapsed_ms
                    except ValueError:
                        raise ArcGISError(
                            f"HTTP {resp.status_code}: {resp.text[:200]}",
                            code=resp.status_code,
                            url=url,
                        ) from None
                resp.raise_for_status()
                return resp.json(), resp.status_code, elapsed_ms
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
                last = exc
                if attempt < self.retries:
                    await asyncio.sleep(0.5 * (2**attempt))
        raise ArcGISError(f"{type(last).__name__}: {last}", url=url) from last

    # --- discovery -----------------------------------------------------------

    async def layer_info(self, url: str) -> LayerInfo:
        """The one request that tells you whether a layer is worth bothering with."""
        payload = await self.get_json(url)
        info = LayerInfo.model_validate(payload)
        info.url = url.rstrip("/")
        return info

    async def list_layers(self, service_url: str) -> list[dict[str, Any]]:
        """Enumerate a service's layers.

        Yolo County's centerline layer is id 2. Never assume /0.
        """
        payload = await self.get_json(service_url.rstrip("/"))
        return list(payload.get("layers") or []) + list(payload.get("tables") or [])

    async def count(self, url: str, where: str = "1=1") -> int:
        """Feature count, with a fallback for services that dislike `1=1`.

        Some servers sit behind a web application firewall that rejects any where
        clause containing the literal `1=1`. No locked source does this today; the
        fallback exists because the failure is silent and cheap to survive.
        """
        try:
            payload = await self.get_json(
                f"{url}/query", {"where": where, "returnCountOnly": "true"}
            )
            if "count" in payload:
                return int(payload["count"])
        except ArcGISError:
            if where != "1=1":
                raise
        info = await self.layer_info(url)
        payload = await self.get_json(
            f"{url}/query", {"where": f"{info.oid_field}>=0", "returnCountOnly": "true"}
        )
        return int(payload["count"])

    async def oid_range(self, url: str, oid_field: str, where: str = "1=1") -> tuple[int, int]:
        """min/max OBJECTID, for the windowing fallback.

        West Sacramento starts at 4,383,991 and Elk Grove at 6,605,529. Read the
        range; never assume a base.
        """
        stats = json.dumps(
            [
                {"statisticType": "min", "onStatisticField": oid_field,
                 "outStatisticFieldName": "oid_min"},
                {"statisticType": "max", "onStatisticField": oid_field,
                 "outStatisticFieldName": "oid_max"},
            ]
        )
        payload = await self.get_json(
            f"{url}/query",
            {"where": where, "outStatistics": stats, "returnGeometry": "false"},
        )
        attrs = payload["features"][0]["attributes"]
        return int(attrs["oid_min"]), int(attrs["oid_max"])

    async def distinct_values(
        self, url: str, field: str, *, where: str = "1=1", limit: int = 250
    ) -> list[Any]:
        """Distinct values for one field — the input to value reconciliation."""
        payload = await self.get_json(
            f"{url}/query",
            {
                "where": where,
                "outFields": field,
                "returnDistinctValues": "true",
                "returnGeometry": "false",
                "orderByFields": field,
                "resultRecordCount": limit,
            },
        )
        return [f.get("attributes", {}).get(field) for f in payload.get("features", [])]

    async def populated_count(
        self, url: str, f: LayerField, *, where: str = "1=1", sentinels: tuple[str, ...] = ("", " ")
    ) -> int:
        """How many rows actually carry a value for this field.

        Treats service-specific null sentinels as absent. El Dorado County has
        zero SQL nulls and uses a single space; `IS NULL` reports 0 and a naive
        metric calls a 4%-populated field complete.
        """
        clauses = [f"{f.name} IS NOT NULL"]
        if f.short_type == "String":
            clauses += [f"{f.name} <> '{s}'" for s in sentinels]
        payload = await self.get_json(
            f"{url}/query",
            {"where": f"({where}) AND {' AND '.join(clauses)}", "returnCountOnly": "true"},
        )
        return int(payload.get("count", 0))

    # --- the pagination ladder -----------------------------------------------

    async def iter_features(
        self,
        url: str,
        *,
        info: LayerInfo | None = None,
        where: str = "1=1",
        out_fields: list[str] | None = None,
        out_sr: int = 4326,
        return_geometry: bool = True,
        expected: int | None = None,
        max_features: int | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Yield pages of features, coping with whatever the service does.

        Rung 1: resultOffset paging, ordered by OID, page size from the service.
        Rung 2: on a page failure, halve the page size. Never grow it back.
        Rung 3: if offset paging fails outright, switch to OBJECTID windows.

        The Verdict is attached to `self.last_verdict` when the iterator finishes.
        """
        info = info or await self.layer_info(url)
        oid = info.oid_field
        fields = _resolve_out_fields(info, out_fields)
        expected = expected if expected is not None else await self.count(url, where)

        started = time.perf_counter()
        seen: set[Any] = set()
        page_size = info.page_size
        pages = 0
        dupes = 0
        errors: list[str] = []
        strategy = "offset"

        async def emit(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal dupes
            fresh = []
            for feat in batch:
                key = (feat.get("attributes") or {}).get(oid)
                if key is not None:
                    if key in seen:
                        dupes += 1
                        continue
                    seen.add(key)
                fresh.append(feat)
            return fresh

        base = {
            "where": where,
            "outFields": ",".join(fields),
            "returnGeometry": "true" if return_geometry else "false",
            "outSR": out_sr,
        }
        if info.supports_order_by:
            base["orderByFields"] = oid

        # --- rung 1 and 2: offset paging with a downward ratchet -------------
        offset = 0
        offset_broken = False
        while not offset_broken:
            if max_features is not None and len(seen) >= max_features:
                break
            try:
                payload = await self.get_json(
                    f"{url}/query",
                    {**base, "resultOffset": offset, "resultRecordCount": page_size},
                )
            except ArcGISError as exc:
                if exc.needs_token:
                    errors.append(str(exc))
                    break
                if page_size > MIN_PAGE_SIZE:
                    page_size = max(MIN_PAGE_SIZE, page_size // 2)
                    errors.append(f"page shrunk to {page_size} after: {exc}")
                    continue
                errors.append(f"offset paging failed: {exc}")
                offset_broken = True
                break

            batch = payload.get("features") or []
            pages += 1
            if not batch:
                break
            fresh = await emit(batch)
            if fresh:
                yield fresh
            offset += len(batch)
            if len(batch) < page_size and not payload.get("exceededTransferLimit"):
                break

        # --- rung 3: OBJECTID windows ---------------------------------------
        if offset_broken and (expected is None or len(seen) < expected):
            strategy = "oid-window"
            try:
                lo, hi = await self.oid_range(url, oid, where)
                window = max(page_size, 1)
                cursor = lo
                while cursor <= hi:
                    if max_features is not None and len(seen) >= max_features:
                        break
                    top = cursor + window - 1
                    clause = f"({where}) AND {oid} >= {cursor} AND {oid} <= {top}"
                    try:
                        payload = await self.get_json(
                            f"{url}/query", {**base, "where": clause}
                        )
                    except ArcGISError as exc:
                        if window > MIN_PAGE_SIZE:
                            window = max(MIN_PAGE_SIZE, window // 2)
                            errors.append(f"window shrunk to {window} after: {exc}")
                            continue
                        errors.append(f"oid window {cursor}-{top} failed: {exc}")
                        cursor = top + 1
                        continue
                    batch = payload.get("features") or []
                    pages += 1
                    fresh = await emit(batch)
                    if fresh:
                        yield fresh
                    cursor = top + 1
            except (ArcGISError, KeyError, IndexError) as exc:
                errors.append(f"oid windowing unavailable: {exc}")

        self.last_verdict = Verdict(
            outcome=_outcome(len(seen), expected, errors),
            expected=expected,
            fetched=len(seen),
            pages=pages,
            duplicates_dropped=dupes,
            final_page_size=page_size,
            strategy=strategy,
            errors=errors,
            elapsed_s=round(time.perf_counter() - started, 2),
        )

    async def fetch_all(
        self, url: str, **kwargs: Any
    ) -> tuple[list[dict[str, Any]], Verdict]:
        """Convenience wrapper: every feature, plus the verdict."""
        out: list[dict[str, Any]] = []
        async for page in self.iter_features(url, **kwargs):
            out.extend(page)
        return out, self.last_verdict


# --- helpers ----------------------------------------------------------------


def _raise_if_error(payload: Any, url: str) -> Any:
    """An ArcGIS `error` object in a 200 body is still an error."""
    if isinstance(payload, dict) and "error" in payload:
        err = payload["error"] or {}
        details = err.get("details") or []
        msg = err.get("message") or "unknown ArcGIS error"
        if details:
            msg = f"{msg} ({'; '.join(str(d) for d in details)})"
        raise ArcGISError(msg, code=err.get("code"), url=url)
    return payload


def _resolve_out_fields(info: LayerInfo, requested: list[str] | None) -> list[str]:
    """Ask for the OID plus only what was mapped.

    A large layer often needs only a few of its fields, and every field left out is
    bytes not sent for every feature. On Sacramento County this is a 4x payload
    reduction, measured.
    """
    oid = info.oid_field
    if not requested:
        return ["*"]
    real = set(info.field_names)
    keep = [f for f in requested if f in real]
    if oid not in keep:
        keep.insert(0, oid)
    return keep or ["*"]


def _outcome(fetched: int, expected: int | None, errors: list[str]) -> str:
    if expected is None:
        return "partial" if errors else "complete"
    if fetched == 0 and expected > 0:
        return "failed"
    if fetched >= expected:
        return "complete" if not errors else "partial"
    return "truncated"
