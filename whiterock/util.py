"""Small shared helpers: HTTP with retries, compressed JSON lines, dates."""
from __future__ import annotations

import gzip
import json
import logging
import math
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

import requests

from . import config

log = logging.getLogger("whiterock")


class Http:
    """requests.Session wrapper with retries and per-call pacing."""

    def __init__(self, delay_s: float = 0.0, timeout: int = 60):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self.delay_s = delay_s
        self.timeout = timeout
        self._last = 0.0

    def _pace(self) -> None:
        wait = self.delay_s - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, url: str, retries: int = 3, **kw: Any) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(retries):
            self._pace()
            try:
                resp = self.session.get(url, timeout=self.timeout, **kw)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"{resp.status_code} for {url}", response=resp)
                return resp
            except (requests.RequestException, OSError) as exc:  # network hiccups
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"GET failed after {retries} tries: {url}: {last_exc}")

    def post(self, url: str, retries: int = 3, **kw: Any) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(retries):
            self._pace()
            try:
                resp = self.session.post(url, timeout=self.timeout, **kw)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"{resp.status_code} for {url}", response=resp)
                return resp
            except (requests.RequestException, OSError) as exc:
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"POST failed after {retries} tries: {url}: {last_exc}")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    n = 0
    with opener(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
            n += 1
    return n


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, obj: Any, indent: int | None = None) -> None:
    """Strict JSON: NaN and infinities become null so browsers can parse the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_clean(obj), fh, ensure_ascii=False, indent=indent, default=_json_default, allow_nan=False)


def _clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):  # numpy scalar
        try:
            return _clean(o.item())
        except (ValueError, TypeError):
            return o
    return o


def _json_default(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if hasattr(o, "item"):  # numpy scalars
        return o.item()
    raise TypeError(f"not JSON serializable: {type(o)}")


def parse_us_date(text: str) -> str | None:
    """'07/13/2026' -> '2026-07-13' (None if it does not parse)."""
    text = (text or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def chunks(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
