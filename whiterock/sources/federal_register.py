"""Federal Register API (keyless, public domain).

Two feeds:
  * published documents (rules, proposed rules, presidential documents)
  * the Public Inspection desk, i.e. documents scheduled for publication
    in the coming days. This is the only lawful "forthcoming" signal.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .. import config
from ..util import Http, read_jsonl, write_jsonl

log = logging.getLogger(__name__)

FIELDS = (
    "document_number", "title", "abstract", "type", "action", "agencies",
    "publication_date", "signing_date", "html_url", "significant", "president",
    "executive_order_number", "subtype",
)
DOC_TYPES = ("RULE", "PRORULE", "PRESDOCU")

CACHE = config.DATA_DIR / "fr_documents.jsonl.gz"


def _params(start: date, end: date, page: int) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = [
        ("per_page", "1000"), ("page", str(page)), ("order", "oldest"),
        ("conditions[publication_date][gte]", start.isoformat()),
        ("conditions[publication_date][lte]", end.isoformat()),
    ]
    params += [("conditions[type][]", t) for t in DOC_TYPES]
    params += [("fields[]", f) for f in FIELDS]
    return params


TYPE_CODES = {"rule": "RULE", "proposed rule": "PRORULE", "presidential document": "PRESDOCU", "notice": "NOTICE"}


def type_code(value: str | None) -> str | None:
    if not value:
        return None
    return TYPE_CODES.get(value.strip().lower(), value.strip().upper().replace(" ", ""))


def _normalize(doc: dict) -> dict:
    agencies = doc.get("agencies") or []
    return {
        "id": doc.get("document_number"),
        "title": (doc.get("title") or "").strip(),
        "abstract": (doc.get("abstract") or "").strip() or None,
        "type": type_code(doc.get("type")),
        "action": (doc.get("action") or "").strip() or None,
        "agencies": [a.get("name") for a in agencies if isinstance(a, dict) and a.get("name")],
        "publication_date": doc.get("publication_date"),
        "signing_date": doc.get("signing_date"),
        "url": doc.get("html_url"),
        "significant": bool(doc.get("significant")),
        "president": (doc.get("president") or {}).get("name") if isinstance(doc.get("president"), dict) else None,
        "eo_number": doc.get("executive_order_number"),
        "pres_doc_type": doc.get("subtype"),
        "source": "federal_register",
    }


def fetch_window(http: Http, start: date, end: date) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        resp = http.get(f"{config.FR_API}/documents.json", params=_params(start, end, page))
        if resp.status_code == 404:  # FR returns 404 when a page is past the end
            break
        resp.raise_for_status()
        payload = resp.json()
        results = payload.get("results") or []
        out.extend(_normalize(d) for d in results)
        if not payload.get("next_page_url") or not results:
            break
        page += 1
        if page > 20:
            log.warning("FR window %s..%s exceeded 20 pages; splitting recommended", start, end)
            break
    return out


def update(start: date | None = None, end: date | None = None) -> list[dict]:
    """Incrementally extend the local cache of FR documents. Returns all docs."""
    existing = read_jsonl(CACHE)
    known = {d["id"] for d in existing}
    end = end or date.today()
    if start is None:
        if existing:
            last = max(d["publication_date"] for d in existing if d.get("publication_date"))
            start = date.fromisoformat(last) - timedelta(days=3)  # small overlap for late edits
        else:
            start = config.ACTIONS_START
    http = Http(delay_s=config.FR_DELAY_S)
    cursor = start
    added = 0
    while cursor <= end:
        window_end = min(end, cursor + timedelta(days=45))
        docs = fetch_window(http, cursor, window_end)
        for d in docs:
            if d["id"] and d["id"] not in known:
                existing.append(d)
                known.add(d["id"])
                added += 1
        log.info("FR %s..%s: %d docs (%d new)", cursor, window_end, len(docs), added)
        cursor = window_end + timedelta(days=1)
    existing.sort(key=lambda d: (d.get("publication_date") or "", d["id"]))
    write_jsonl(CACHE, existing)
    return existing


def fetch_public_inspection() -> list[dict]:
    """Documents on public inspection now (scheduled for future publication)."""
    http = Http(delay_s=config.FR_DELAY_S)
    resp = http.get(f"{config.FR_API}/public-inspection-documents/current.json")
    resp.raise_for_status()
    out = []
    for d in resp.json().get("results") or []:
        agencies = d.get("agencies") or []
        out.append({
            "id": d.get("document_number"),
            "title": (d.get("title") or d.get("subject") or "").strip(),
            "abstract": None,
            "type": type_code(d.get("type")),
            "action": None,
            "agencies": [a.get("name") for a in agencies if isinstance(a, dict) and a.get("name")],
            "publication_date": d.get("publication_date"),
            "filed_at": d.get("filed_at"),
            "url": d.get("html_url"),
            "significant": False,
            "forthcoming": True,
            "source": "federal_register_public_inspection",
        })
    return out
