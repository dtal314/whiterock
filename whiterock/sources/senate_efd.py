"""U.S. Senate electronic Financial Disclosure (eFD) search (public, STOCK Act).

The site requires accepting an on-screen notice before searching; we do that
with the same form a human clicks. Electronic PTRs render as HTML tables with
a Ticker column. Paper filings are scanned and are recorded as "paper".
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from bs4 import BeautifulSoup

from .. import config
from ..util import Http, parse_us_date, read_json, read_jsonl, write_json, write_jsonl

log = logging.getLogger(__name__)

TX_CACHE = config.DATA_DIR / "senate_transactions.jsonl.gz"
INDEX_CACHE = config.DATA_DIR / "senate_ptr_index.json"   # report uuid -> status

OWNER_MAP = {"self": "self", "spouse": "spouse", "joint": "joint", "child": "dependent"}
TYPE_MAP = {"purchase": "purchase", "sale (full)": "sale", "sale (partial)": "sale_partial", "exchange": "exchange"}
_LINK = re.compile(r'href="(/search/view/(ptr|paper)/([0-9a-f-]{36})/)"')


class SenateSession:
    def __init__(self) -> None:
        self.http = Http(delay_s=config.SENATE_DELAY_S)
        self._agreed = False

    def agree(self) -> None:
        home = f"{config.SENATE_BASE}/search/home/"
        resp = self.http.get(home)
        m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', resp.text)
        if not m:
            raise RuntimeError("Senate eFD: csrf token not found")
        self.http.post(home, data={"csrfmiddlewaretoken": m.group(1), "prohibition_agreement": "1"},
                       headers={"Referer": home})
        self._agreed = True

    def search_ptrs(self, start: date, end: date | None = None) -> list[dict]:
        if not self._agreed:
            self.agree()
        csrf = self.http.session.cookies.get("csrftoken", "")
        rows: list[dict] = []
        offset = 0
        while True:
            payload = {
                "start": str(offset), "length": "100",
                "report_types": "[11]", "filer_types": "[1]",
                "submitted_start_date": start.strftime("%m/%d/%Y") + " 00:00:00",
                "submitted_end_date": (end.strftime("%m/%d/%Y") + " 23:59:59") if end else "",
                "candidate_state": "", "senator_state": "", "office_id": "",
                "first_name": "", "last_name": "",
            }
            resp = self.http.post(
                f"{config.SENATE_BASE}/search/report/data/", data=payload,
                headers={"Referer": f"{config.SENATE_BASE}/search/", "X-CSRFToken": csrf,
                         "X-Requested-With": "XMLHttpRequest"},
            )
            data = resp.json()
            batch = data.get("data") or []
            for r in batch:
                m = _LINK.search(r[3])
                if not m:
                    continue
                rows.append({
                    "first": r[0].strip(), "last": r[1].strip(), "office": r[2].strip(),
                    "kind": m.group(2), "report_id": m.group(3), "path": m.group(1),
                    "filing_date": parse_us_date(r[4]),
                })
            offset += len(batch)
            if not batch or offset >= int(data.get("recordsTotal") or 0):
                break
        return rows

    def fetch_ptr(self, path: str) -> list[dict]:
        resp = self.http.get(config.SENATE_BASE + path)
        resp.raise_for_status()
        return parse_ptr_html(resp.text)


def parse_ptr_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for tbl in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in tbl.find_all("th")]
        if "ticker" not in headers or "asset name" not in headers:
            continue
        idx = {h: i for i, h in enumerate(headers)}
        for tr in tbl.find_all("tr")[1:]:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < len(headers):
                continue
            ticker = cells[idx["ticker"]].strip()
            amount = cells[idx["amount"]].strip()
            low, high = _amount(amount)
            out.append({
                "owner": OWNER_MAP.get(cells[idx["owner"]].strip().lower(), cells[idx["owner"]].strip().lower() or "self"),
                "ticker": None if ticker in ("--", "") else ticker.upper(),
                "asset": cells[idx["asset name"]].strip(),
                "asset_type": cells[idx["asset type"]].strip() if "asset type" in idx else None,
                "tx_type": TYPE_MAP.get(cells[idx["type"]].strip().lower(), cells[idx["type"]].strip().lower()),
                "tx_date": parse_us_date(cells[idx["transaction date"]]),
                "amount_text": amount, "amount_low": low, "amount_high": high,
                "comment": cells[idx["comment"]].strip() if "comment" in idx and cells[idx["comment"]].strip() != "--" else None,
            })
    return out


def _amount(txt: str) -> tuple[int | None, int | None]:
    nums = [int(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", txt)]
    if txt.lower().startswith("over") and nums:
        return nums[0] + 1, None
    if len(nums) == 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], None
    return None, None


def update(start: date | None = None, max_new: int | None = None) -> list[dict]:
    index: dict = read_json(INDEX_CACHE, {})
    txs = read_jsonl(TX_CACHE)
    meta = index.get("_meta") or {}
    if start is None:
        # Only a fully processed search advances the watermark, so a capped smoke
        # run never hides the backlog from the next full run.
        done_through = meta.get("searched_through")
        start = (date.fromisoformat(done_through) - timedelta(days=10)) if done_through else config.TRADES_START
    sess = SenateSession()
    try:
        rows = sess.search_ptrs(start)
    except Exception as exc:
        log.warning("Senate eFD search failed: %s", exc)
        return txs
    log.info("Senate: %d PTR filings since %s", len(rows), start)
    new_count = 0
    capped = False
    for r in rows:
        if r["report_id"] in index:
            continue
        if max_new is not None and new_count >= max_new:
            capped = True
            break
        base = {"first": r["first"], "last": r["last"], "filing_date": r["filing_date"], "office": r["office"]}
        if r["kind"] == "paper":
            index[r["report_id"]] = {"status": "paper", **base}
            continue
        try:
            parsed = sess.fetch_ptr(r["path"])
        except Exception as exc:
            log.warning("Senate PTR %s failed: %s", r["report_id"], exc)
            index[r["report_id"]] = {"status": "error", **base}
            continue
        index[r["report_id"]] = {"status": "parsed" if parsed else "empty", "n": len(parsed), **base}
        for t in parsed:
            t.update({
                "chamber": "senate", "filer_last": r["last"], "filer_first": r["first"],
                "state_district": None, "filing_date": r["filing_date"],
                "docid": r["report_id"], "source_url": config.SENATE_BASE + r["path"],
                "source": "senate_efd_ptr",
            })
            txs.append(t)
        new_count += 1
        if new_count % 50 == 0:
            write_json(INDEX_CACHE, index)
            write_jsonl(TX_CACHE, txs)
    if not capped:
        index["_meta"] = {"searched_through": date.today().isoformat()}
    write_json(INDEX_CACHE, index)
    write_jsonl(TX_CACHE, txs)
    log.info("Senate: %d new filings processed, %d transactions total", new_count, len(txs))
    return txs
