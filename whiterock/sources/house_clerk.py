"""U.S. House Clerk financial disclosures (public, STOCK Act).

The Clerk publishes a yearly index (FD.txt inside {year}FD.zip) listing every
filing with its type and DocID. Periodic Transaction Reports (type "P") are
PDFs; electronically filed ones carry a text layer we can parse. Paper filings
are scanned images and are recorded as "paper" (not parsed, never guessed).
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import date

import pdfplumber

from .. import config
from ..util import Http, parse_us_date, read_json, read_jsonl, write_json, write_jsonl

log = logging.getLogger(__name__)

TX_CACHE = config.DATA_DIR / "house_transactions.jsonl.gz"
INDEX_CACHE = config.DATA_DIR / "house_ptr_index.json"   # docid -> status

OWNER_CODES = {"SP": "spouse", "JT": "joint", "DC": "dependent"}
TYPE_CODES = {"P": "purchase", "S": "sale", "S (partial)": "sale_partial", "E": "exchange"}

AMOUNT_RANGES = {
    "$1,001 - $15,000": (1001, 15000),
    "$15,001 - $50,000": (15001, 50000),
    "$50,001 - $100,000": (50001, 100000),
    "$100,001 - $250,000": (100001, 250000),
    "$250,001 - $500,000": (250001, 500000),
    "$500,001 - $1,000,000": (500001, 1000000),
    "$1,000,001 - $5,000,000": (1000001, 5000000),
    "$5,000,001 - $25,000,000": (5000001, 25000000),
    "$25,000,001 - $50,000,000": (25000001, 50000000),
    "Over $50,000,000": (50000001, None),
}

_TX_LINE = re.compile(
    r"^(?:(?P<owner>SP|JT|DC)\s+)?(?P<asset>.*?)\s+(?P<type>S \(partial\)|P|S|E)\s+"
    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+(?P<notif>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<amount>Over \$50,000,000|\$[\d,]+(?:\s*-\s*(?:\$[\d,]+)?)?)\s*(?P<rest>.*)$"
)
_STOP_LINE = re.compile(r"^(F\W*S\W*:|S\W*O\W*:|D\W*:|C\W*:|L\W*:|\* For the complete list|I\W*V\W*D|I\W*P\W*O|Digitally Signed|ID Owner Asset)")
_TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,7})\)")
_ASSET_CODE = re.compile(r"\[([A-Z]{2})\]")
_MONEY_ANY = re.compile(r"\$[\d,]{3,}")


def fetch_index(http: Http, year: int) -> list[dict]:
    resp = http.get(config.HOUSE_FD_ZIP.format(year=year))
    resp.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    txt_name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
    lines = zf.read(txt_name).decode("utf-8", "replace").splitlines()
    header = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < len(header):
            continue
        rec = dict(zip(header, parts))
        rows.append({
            "prefix": rec.get("Prefix", "").strip(),
            "last": rec.get("Last", "").strip(),
            "first": rec.get("First", "").strip(),
            "suffix": rec.get("Suffix", "").strip(),
            "filing_type": rec.get("FilingType", "").strip(),
            "state_district": rec.get("StateDst", "").strip(),
            "year": int(rec.get("Year") or year),
            "filing_date": parse_us_date(rec.get("FilingDate", "")),
            "docid": rec.get("DocID", "").strip(),
        })
    return rows


def parse_ptr_text(text: str) -> list[dict]:
    """Parse the text layer of an electronic PTR into transactions."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    txs: list[dict] = []
    current: dict | None = None
    extra: list[str] = []

    def close() -> None:
        nonlocal current, extra
        if current is None:
            return
        asset = " ".join([current["asset_raw"]] + extra).strip()
        # Amount high may have wrapped to the continuation line.
        if current["amount_high"] is None and current["amount_low"] is not None and extra:
            found = _MONEY_ANY.findall(" ".join(extra))
            if found:
                current["amount_high"] = int(found[-1].replace("$", "").replace(",", ""))
                asset = asset.replace(found[-1], "").strip()
        tickers = _TICKER.findall(asset)
        code = _ASSET_CODE.search(asset)
        current["asset"] = re.sub(r"\s+", " ", asset)
        current["ticker"] = tickers[-1] if tickers else None
        current["asset_type"] = code.group(1) if code else None
        del current["asset_raw"]
        txs.append(current)
        current, extra = None, []

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        m = _TX_LINE.match(s)
        if m:
            close()
            amount_txt = m.group("amount").strip()
            low, high = _parse_amount(amount_txt)
            current = {
                "owner": OWNER_CODES.get(m.group("owner") or "", "self"),
                "asset_raw": m.group("asset").strip(),
                "tx_type": TYPE_CODES.get(m.group("type"), m.group("type")),
                "tx_date": parse_us_date(m.group("date")),
                "notification_date": parse_us_date(m.group("notif")),
                "amount_text": amount_txt,
                "amount_low": low,
                "amount_high": high,
                "cap_gains_over_200": ("Yes" in (m.group("rest") or "")) or None,
            }
            continue
        if current is not None:
            if _STOP_LINE.match(s):
                close()
                continue
            extra.append(s)
    close()
    return txs


def _parse_amount(txt: str) -> tuple[int | None, int | None]:
    txt = re.sub(r"\s+", " ", txt).strip()
    if txt in AMOUNT_RANGES:
        return AMOUNT_RANGES[txt]
    nums = [int(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", txt)]
    if len(nums) == 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], None
    return None, None


def parse_ptr_pdf(pdf_bytes: bytes) -> tuple[str, list[dict], dict]:
    """Returns (status, transactions, meta). status in {parsed, paper, empty}."""
    meta: dict = {}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception as exc:  # corrupt / unreadable PDF
        return "error", [], {"error": str(exc)[:200]}
    if len(text.strip()) < 80:
        return "paper", [], meta
    name = re.search(r"Name:\s*(.+)", text)
    if name:
        meta["filer_name"] = name.group(1).strip()
    txs = parse_ptr_text(text)
    return ("parsed" if txs else "empty"), txs, meta


def update(years: list[int] | None = None, max_new: int | None = None) -> list[dict]:
    """Fetch new PTR filings, parse them, extend the cache. Returns all transactions."""
    today = date.today()
    years = years or list(range(config.TRADES_START.year, today.year + 1))
    http = Http(delay_s=config.HOUSE_DELAY_S)
    index: dict = read_json(INDEX_CACHE, {})
    txs = read_jsonl(TX_CACHE)
    new_count = 0
    for year in years:
        try:
            rows = fetch_index(http, year)
        except Exception as exc:
            log.warning("House index %s failed: %s", year, exc)
            continue
        ptrs = [r for r in rows if r["filing_type"] == "P" and r["docid"]]
        log.info("House %s: %d PTR filings in index", year, len(ptrs))
        for r in ptrs:
            if r["docid"] in index:
                continue
            if max_new is not None and new_count >= max_new:
                break
            try:
                resp = http.get(config.HOUSE_PTR_PDF.format(year=year, docid=r["docid"]))
                if resp.status_code != 200:
                    index[r["docid"]] = {"status": f"http_{resp.status_code}", **_filer(r)}
                    continue
                status, parsed, meta = parse_ptr_pdf(resp.content)
            except Exception as exc:
                log.warning("PTR %s failed: %s", r["docid"], exc)
                index[r["docid"]] = {"status": "error", **_filer(r)}
                continue
            index[r["docid"]] = {"status": status, "n": len(parsed), **_filer(r)}
            for t in parsed:
                t.update({
                    "chamber": "house",
                    "filer_last": r["last"], "filer_first": r["first"],
                    "state_district": r["state_district"],
                    "filing_date": r["filing_date"],
                    "docid": r["docid"],
                    "source_url": config.HOUSE_PTR_PDF.format(year=year, docid=r["docid"]),
                    "source": "house_clerk_ptr",
                })
                txs.append(t)
            new_count += 1
            if new_count % 50 == 0:
                write_json(INDEX_CACHE, index)
                write_jsonl(TX_CACHE, txs)
    write_json(INDEX_CACHE, index)
    write_jsonl(TX_CACHE, txs)
    log.info("House: %d new filings processed, %d transactions total", new_count, len(txs))
    return txs


def _filer(r: dict) -> dict:
    return {"last": r["last"], "first": r["first"], "state_district": r["state_district"],
            "filing_date": r["filing_date"], "year": r["year"]}
