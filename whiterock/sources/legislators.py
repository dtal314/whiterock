"""Member roster and committee seats from the public congress-legislators
dataset (github.com/unitedstates/congress-legislators, public domain)."""
from __future__ import annotations

import logging
import re
import unicodedata

import yaml

from .. import config
from ..util import Http, read_json, write_json

log = logging.getLogger(__name__)

CACHE = config.DATA_DIR / "legislators.json"


def _fetch_yaml(http: Http, name: str):
    resp = http.get(f"{config.LEGISLATORS_BASE}/{name}")
    resp.raise_for_status()
    return yaml.safe_load(resp.text)


def update() -> dict:
    """Returns {"people": [..], "committees": {thomas_id: name}}."""
    http = Http(delay_s=0.2)
    current = _fetch_yaml(http, "legislators-current.yaml")
    historical = _fetch_yaml(http, "legislators-historical.yaml")
    membership = _fetch_yaml(http, "committee-membership-current.yaml")
    committees = _fetch_yaml(http, "committees-current.yaml")

    comm_names: dict[str, str] = {}
    for c in committees:
        comm_names[c["thomas_id"]] = c["name"]
        for sub in c.get("subcommittees") or []:
            comm_names[c["thomas_id"] + sub["thomas_id"]] = f'{c["name"]}: {sub["name"]}'

    seats: dict[str, list[str]] = {}
    for cid, members in membership.items():
        for m in members:
            bid = m.get("bioguide")
            if bid:
                seats.setdefault(bid, []).append(cid)

    people = []
    cutoff = config.TRADES_START.isoformat()
    for src, is_current in ((current, True), (historical, False)):
        for p in src:
            terms = p.get("terms") or []
            if not terms:
                continue
            last = terms[-1]
            if not is_current and (last.get("end") or "") < cutoff:
                continue
            bid = p["id"].get("bioguide")
            name = p["name"]
            people.append({
                "id": bid,
                "first": name.get("first", ""),
                "last": name.get("last", ""),
                "official_full": name.get("official_full") or f'{name.get("first","")} {name.get("last","")}',
                "nickname": name.get("nickname"),
                "chamber": "senate" if last.get("type") == "sen" else "house",
                "party": last.get("party"),
                "state": last.get("state"),
                "district": last.get("district"),
                "current": is_current,
                "term_end": last.get("end"),
                "committees": seats.get(bid, []),
            })
    out = {"people": people, "committees": comm_names}
    write_json(CACHE, out)
    log.info("Legislators: %d people, %d committee ids", len(people), len(comm_names))
    return out


def load() -> dict:
    return read_json(CACHE, {"people": [], "committees": {}})


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


class Matcher:
    """Match disclosure filer names (Last/First/state) to bioguide ids."""

    def __init__(self, roster: dict):
        self.people = roster["people"]
        self.by_chamber_last: dict[tuple[str, str], list[dict]] = {}
        for p in self.people:
            self.by_chamber_last.setdefault((p["chamber"], _norm(p["last"])), []).append(p)

    def match(self, chamber: str, last: str, first: str, state: str | None = None) -> dict | None:
        last_n = _norm(last)
        # Multi-word / hyphenated last names: try full, then each part.
        candidates = list(self.by_chamber_last.get((chamber, last_n), []))
        if not candidates:
            for part in re.split(r"[\s\-]+", last_n):
                candidates += self.by_chamber_last.get((chamber, part), [])
        if not candidates:
            # Names like "Wittman, Robert J." sometimes carry suffixes in the last field.
            stripped = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", last_n).strip()
            candidates = list(self.by_chamber_last.get((chamber, stripped), []))
        if not candidates:
            return None
        if state:
            st = state[:2].upper()
            narrowed = [c for c in candidates if (c.get("state") or "").upper() == st]
            if narrowed:
                candidates = narrowed
        if len(candidates) > 1 and first:
            f = _norm(first).split(" ")[0] if _norm(first) else ""
            narrowed = [c for c in candidates if _norm(c["first"]).startswith(f[:3]) or _norm(c.get("nickname") or "").startswith(f[:3])]
            if narrowed:
                candidates = narrowed
        if len(candidates) > 1:
            current = [c for c in candidates if c["current"]]
            if current:
                candidates = current
        return candidates[0]
