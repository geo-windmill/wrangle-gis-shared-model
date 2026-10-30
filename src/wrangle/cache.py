"""Record-and-replay cache for every HTTP response the demo makes.

This is the single most important resilience feature for a live demo.
On stage the app runs from disk; the LIVE toggle goes to the network when the wifi
is willing. Nothing else in the pipeline knows or cares which mode it is in.

Layout, human-readable on purpose so anyone can open it and see what was recorded.
The cache holds agency data, so it stays on your machine and is never committed:

    cache/
      index.json                          every entry, newest write wins
      services1.arcgis.com/
        <sha1[:16]>.json                  response body
        <sha1[:16]>.meta.json             url, params, status, timing, recorded_at

The key is a stable hash of method + url + sorted params, with volatile params
(`f`, tokens) normalised out so a replay is not defeated by parameter order.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import urllib.parse
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path(
    os.environ.get("WRANGLE_CACHE_DIR", Path(__file__).resolve().parents[2] / "cache")
)

# Params that must not change the cache key.
_VOLATILE_PARAMS = {"token", "_ts", "_"}


class Mode(StrEnum):
    """How the cache behaves. `REPLAY` is what runs on stage."""

    REPLAY = "replay"      # disk only; a miss is an error
    PREFER_CACHE = "prefer"  # disk, fall back to network and record
    LIVE = "live"          # network, record every response
    REFRESH = "refresh"    # network only, overwrite disk


@dataclass(frozen=True)
class CacheKey:
    method: str
    url: str
    params: tuple[tuple[str, str], ...]

    @classmethod
    def build(cls, url: str, params: dict[str, Any] | None = None, method: str = "GET") -> CacheKey:
        parsed = urllib.parse.urlsplit(url)
        merged: dict[str, Any] = dict(urllib.parse.parse_qsl(parsed.query))
        merged.update({k: v for k, v in (params or {}).items() if v is not None})
        merged.pop("f", None)  # always JSON in this app
        for k in _VOLATILE_PARAMS:
            merged.pop(k, None)
        bare = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        norm = tuple(sorted((str(k), str(v)) for k, v in merged.items()))
        return cls(method.upper(), bare.rstrip("/"), norm)

    @property
    def digest(self) -> str:
        blob = json.dumps([self.method, self.url, list(self.params)], sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()[:16]

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.url).netloc or "unknown-host"

    def describe(self) -> str:
        q = urllib.parse.urlencode(dict(self.params))
        return f"{self.method} {self.url}{'?' + q if q else ''}"


class CacheMiss(KeyError):
    """Raised when REPLAY mode is asked for something that was never recorded."""

    def __init__(self, key: CacheKey):
        self.key = key
        super().__init__(
            f"nothing recorded for {key.describe()} — "
            f"run with --live (or WRANGLE_CACHE_MODE=live) once to record it"
        )


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0
    bytes_read: int = 0
    bytes_written: int = 0
    hit_keys: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.hits + self.misses

    def summary(self) -> str:
        pct = f"{100 * self.hits / self.total:.0f}%" if self.total else "—"
        return (
            f"cache {self.hits} hit / {self.misses} miss ({pct}), "
            f"{self.writes} written, "
            f"{self.bytes_read / 1024:.0f} KB read, {self.bytes_written / 1024:.0f} KB written"
        )


class ResponseCache:
    """Content-addressed JSON response store."""

    def __init__(self, root: Path | str | None = None, mode: Mode | str | None = None):
        self.root = Path(root or DEFAULT_CACHE_DIR)
        env_mode = os.environ.get("WRANGLE_CACHE_MODE", Mode.PREFER_CACHE.value)
        self.mode = Mode(mode or env_mode)
        self.stats = CacheStats()
        self._index_path = self.root / "index.json"
        self._index: dict[str, dict[str, Any]] | None = None

    # --- policy --------------------------------------------------------------

    @property
    def may_read(self) -> bool:
        return self.mode in (Mode.REPLAY, Mode.PREFER_CACHE)

    @property
    def may_fetch(self) -> bool:
        return self.mode in (Mode.PREFER_CACHE, Mode.LIVE, Mode.REFRESH)

    @property
    def may_write(self) -> bool:
        return self.mode in (Mode.PREFER_CACHE, Mode.LIVE, Mode.REFRESH)

    @property
    def prefers_network(self) -> bool:
        return self.mode in (Mode.LIVE, Mode.REFRESH)

    # --- paths ---------------------------------------------------------------

    def _body_path(self, key: CacheKey) -> Path:
        return self.root / key.host / f"{key.digest}.json"

    def _meta_path(self, key: CacheKey) -> Path:
        return self.root / key.host / f"{key.digest}.meta.json"

    # --- read / write --------------------------------------------------------

    def has(self, key: CacheKey) -> bool:
        return self._body_path(key).exists()

    def get(self, key: CacheKey) -> Any | None:
        path = self._body_path(key)
        if not path.exists():
            return None
        raw = path.read_bytes()
        self.stats.hits += 1
        self.stats.bytes_read += len(raw)
        self.stats.hit_keys.append(key.digest)
        return json.loads(raw)

    def put(self, key: CacheKey, payload: Any, *, status: int = 200, elapsed_ms: int = 0) -> None:
        if not self.may_write:
            return
        body = self._body_path(key)
        body.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(payload, separators=(",", ":")).encode()
        body.write_bytes(raw)
        self._meta_path(key).write_text(
            json.dumps(
                {
                    "request": key.describe(),
                    "method": key.method,
                    "url": key.url,
                    "params": dict(key.params),
                    "status": status,
                    "elapsed_ms": elapsed_ms,
                    "bytes": len(raw),
                    "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        self.stats.writes += 1
        self.stats.bytes_written += len(raw)
        self._touch_index(key, len(raw))

    def miss(self, key: CacheKey) -> None:
        self.stats.misses += 1

    # --- index ---------------------------------------------------------------

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if self._index is None:
            if self._index_path.exists():
                self._index = json.loads(self._index_path.read_text(encoding="utf-8"))
            else:
                self._index = {}
        return self._index

    def _touch_index(self, key: CacheKey, size: int) -> None:
        idx = self._load_index()
        idx[key.digest] = {
            "request": key.describe(),
            "host": key.host,
            "bytes": size,
            "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        }
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(json.dumps(idx, indent=1, sort_keys=True), encoding="utf-8")

    def entries(self) -> dict[str, dict[str, Any]]:
        return dict(self._load_index())

    # --- measured metrics ----------------------------------------------------
    # Some numbers cannot be replayed from a cached response body. Throughput is
    # the example: replaying a recorded page measures disk speed, not the agency's
    # server, and a source card that promises a 1-second load for 72,559 features
    # is a lie told on stage. So the measurement is recorded alongside the body
    # and read back in replay mode.

    @property
    def _metrics_path(self) -> Path:
        return self.root / "metrics.json"

    def _load_metrics(self) -> dict[str, dict[str, Any]]:
        if self._metrics_path.exists():
            return json.loads(self._metrics_path.read_text(encoding="utf-8"))
        return {}

    def record_metric(self, subject: str, name: str, value: Any) -> None:
        if not self.may_write:
            return
        m = self._load_metrics()
        m.setdefault(subject, {})[name] = value
        m[subject]["measured_at"] = dt.datetime.now(dt.UTC).isoformat(
            timespec="seconds"
        )
        self._metrics_path.parent.mkdir(parents=True, exist_ok=True)
        self._metrics_path.write_text(
            json.dumps(m, indent=1, sort_keys=True), encoding="utf-8"
        )

    def get_metric(self, subject: str, name: str) -> Any | None:
        return self._load_metrics().get(subject, {}).get(name)

    def describe(self) -> str:
        idx = self._load_index()
        total = sum(e.get("bytes", 0) for e in idx.values())
        hosts = sorted({e.get("host", "?") for e in idx.values()})
        return (
            f"{len(idx)} recorded responses, {total / 1024 / 1024:.1f} MB, "
            f"{len(hosts)} hosts: {', '.join(hosts) or '—'}"
        )
