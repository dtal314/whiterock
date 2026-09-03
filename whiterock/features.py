"""Turn raw public records into the analysis tables both models share.

Outputs (pandas):
  * transactions: one row per disclosed transaction with person_id, sector list
  * action_links: one row per (Federal Register document, sector) link
  * sector_days: per (sector, date) action intensity aggregated from links
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from .mapping.universe import SECTORS, map_action, ticker_sectors
from .sources.legislators import Matcher

log = logging.getLogger(__name__)

MIN_LINK_RELEVANCE = 0.4


def build_transactions(house: list[dict], senate: list[dict], executive: list[dict], roster: dict) -> pd.DataFrame:
    matcher = Matcher(roster)
    people = {p["id"]: p for p in roster["people"]}
    rows = []
    unmatched: set[tuple] = set()
    for t in house + senate:
        state = (t.get("state_district") or "")[:2] or None
        p = matcher.match(t["chamber"], t.get("filer_last", ""), t.get("filer_first", ""), state)
        if p is None:
            key = (t["chamber"], t.get("filer_last"), t.get("filer_first"))
            unmatched.add(key)
            pid = f'{t["chamber"]}:{(t.get("filer_last") or "").lower()}_{(t.get("filer_first") or "").lower()}'
            name = f'{t.get("filer_first","")} {t.get("filer_last","")}'.strip()
            party = state_ = None
        else:
            pid, name, party, state_ = p["id"], p["official_full"], p.get("party"), p.get("state")
        ticker = (t.get("ticker") or "").upper() or None
        rows.append({
            "person_id": pid, "person_name": name, "chamber": t["chamber"], "party": party, "state": state_,
            "owner": t.get("owner") or "self", "ticker": ticker, "asset": t.get("asset"),
            "asset_type": t.get("asset_type"), "tx_type": t.get("tx_type"),
            "tx_date": t.get("tx_date"), "filing_date": t.get("filing_date"),
            "amount_low": t.get("amount_low"), "amount_high": t.get("amount_high"),
            "source_url": t.get("source_url"), "source": t.get("source"),
        })
    for t in executive:
        rows.append({
            "person_id": f'exec:{t["filer_name"].lower().replace(" ", "_")}', "person_name": t["filer_name"],
            "chamber": "executive", "party": t.get("party"), "state": None, "owner": t.get("owner", "self"),
            "ticker": (t.get("ticker") or "").upper() or None, "asset": t.get("asset"), "asset_type": None,
            "tx_type": t["tx_type"], "tx_date": t["tx_date"], "filing_date": t.get("filing_date") or t["tx_date"],
            "amount_low": t.get("amount_low"), "amount_high": t.get("amount_high"),
            "source_url": t["source_url"], "source": t.get("source", "oge_278_manual"),
        })
    if unmatched:
        log.info("Transactions: %d filer names not matched to the roster (kept under name ids)", len(unmatched))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["tx_date"] = pd.to_datetime(df["tx_date"], errors="coerce")
    df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df = df.dropna(subset=["tx_date"])
    df["side"] = df["tx_type"].map({"purchase": "buy", "sale": "sell", "sale_partial": "sell", "exchange": "other"}).fillna("other")
    df["sectors"] = df["ticker"].map(lambda t: ticker_sectors(t) if t else [])
    df["in_universe"] = df["sectors"].map(bool)
    return df


def build_action_links(docs: list[dict]) -> pd.DataFrame:
    rows = []
    for d in docs:
        links = map_action(d.get("title", ""), d.get("abstract"), d.get("action"), d.get("agencies") or [],
                           min_relevance=MIN_LINK_RELEVANCE)
        for l in links:
            rows.append({
                "doc_id": d["id"], "sector_id": l.sector_id, "relevance": l.relevance,
                "direction": l.direction, "direction_score": l.direction_score,
                "publication_date": d.get("publication_date"), "type": d.get("type"),
                "significant": bool(d.get("significant")), "forthcoming": bool(d.get("forthcoming")),
                "title": d.get("title"), "url": d.get("url"), "agencies": d.get("agencies") or [],
                "matched_agencies": l.matched_agencies, "matched_keywords": l.matched_keywords,
                "matched_direction": l.matched_direction,
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["publication_date"] = pd.to_datetime(df["publication_date"], errors="coerce")
    return df.dropna(subset=["publication_date"])


def build_sector_days(links: pd.DataFrame) -> pd.DataFrame:
    """Aggregate links into one row per (sector, publication date)."""
    if links.empty:
        return pd.DataFrame(columns=["sector_id", "date", "n_docs", "mean_direction", "max_relevance"])
    g = links[~links["forthcoming"]].groupby(["sector_id", "publication_date"])
    out = g.agg(n_docs=("doc_id", "nunique"), mean_direction=("direction_score", "mean"),
                max_relevance=("relevance", "max"), n_significant=("significant", "sum"),
                n_presidential=("type", lambda s: int((s == "PRESDOCU").sum()))).reset_index()
    return out.rename(columns={"publication_date": "date"})


def sector_intensity(sector_days: pd.DataFrame, sector_id: str, asof: pd.Timestamp, days: int) -> tuple[int, float]:
    """(#docs, signed direction sum) for a sector in the `days` before asof."""
    if sector_days.empty:
        return 0, 0.0
    sub = sector_days[(sector_days["sector_id"] == sector_id) & (sector_days["date"] < asof)
                      & (sector_days["date"] >= asof - pd.Timedelta(days=days))]
    return int(sub["n_docs"].sum()), float((sub["mean_direction"] * sub["n_docs"]).sum())


def sector_names() -> dict[str, str]:
    return {s.id: s.name for s in SECTORS}
