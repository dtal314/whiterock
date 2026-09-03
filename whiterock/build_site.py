"""Assemble the static JSON the dashboard reads (site/data/*.json)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from . import __version__, config
from .features import sector_names
from .mapping.universe import SECTORS, SECTOR_BY_ID, MARKET_BENCHMARK
from .util import write_json

log = logging.getLogger(__name__)

DISCLAIMER = (
    "WhiteRock forecasts whether related trades are likely to be publicly disclosed later. "
    "It is built only from records that are already public: the Federal Register, House and Senate "
    "STOCK Act filings, the congress-legislators roster, and market prices. It cannot see any trade "
    "before it is disclosed, it holds no nonpublic information, and it is not investment advice."
)


def _pol_summary(tx: pd.DataFrame, roster: dict) -> dict[str, dict]:
    people = {p["id"]: p for p in roster["people"]}
    comm_names = roster.get("committees", {})
    out: dict[str, dict] = {}
    for pid, g in tx.groupby("person_id"):
        p = people.get(pid, {})
        by_sector: dict[str, dict] = {}
        for _, r in g.iterrows():
            for s in r["sectors"]:
                d = by_sector.setdefault(s, {"buys": 0, "sells": 0, "other": 0, "last": None, "tickers": {}})
                d["buys" if r["side"] == "buy" else "sells" if r["side"] == "sell" else "other"] += 1
                fd = r["filing_date"].date().isoformat() if pd.notna(r["filing_date"]) else None
                if fd and (d["last"] is None or fd > d["last"]):
                    d["last"] = fd
                if r["ticker"]:
                    d["tickers"][r["ticker"]] = d["tickers"].get(r["ticker"], 0) + 1
        for d in by_sector.values():
            d["tickers"] = sorted(d["tickers"].items(), key=lambda kv: -kv[1])[:6]
        top_tickers = g[g["ticker"].notna()]["ticker"].value_counts().head(8)
        out[pid] = {
            "id": pid,
            "name": g["person_name"].iloc[0] if not p else p["official_full"],
            "chamber": g["chamber"].iloc[0], "party": p.get("party") or g["party"].iloc[0],
            "state": p.get("state") or g["state"].iloc[0], "current": p.get("current"),
            "committees": [comm_names.get(c, c) for c in p.get("committees", []) if len(c) == 4],
            "n_transactions": int(len(g)), "n_buys": int((g["side"] == "buy").sum()),
            "n_sells": int((g["side"] == "sell").sum()),
            "n_in_universe": int(g["in_universe"].sum()),
            "spouse_share": round(float((g["owner"] == "spouse").mean()), 3),
            "first_filing": g["filing_date"].min().date().isoformat() if g["filing_date"].notna().any() else None,
            "last_filing": g["filing_date"].max().date().isoformat() if g["filing_date"].notna().any() else None,
            "top_tickers": [{"ticker": t, "n": int(n)} for t, n in top_tickers.items()],
            "by_sector": by_sector,
        }
    return out


def build(*, docs: list[dict], links: pd.DataFrame, tx: pd.DataFrame, roster: dict, disclosure_scores: pd.DataFrame,
          disclosure_metrics: dict, disclosure_base_rates: dict, live_events: pd.DataFrame, outperf_metrics: dict,
          n_outperf_events: int, prices: pd.DataFrame, source_status: dict, forthcoming: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    names = sector_names()
    pols = _pol_summary(tx, roster) if not tx.empty else {}

    # ---- politician forecasts per sector
    forecasts: dict[str, dict[str, dict]] = {}
    if not disclosure_scores.empty:
        for r in disclosure_scores.itertuples(index=False):
            forecasts.setdefault(r.person_id, {})[r.sector_id] = {
                "p_buy": float(r.p_buy), "p_sell": float(r.p_sell), "p_none": float(r.p_none),
                "n_sector_12m": int(r.n_sector_12m), "n_sector_36m": int(r.n_sector_36m),
                "committee_relevant": bool(r.committee_relevant), "days_since_sector": int(r.days_since_sector),
            }
    for pid, p in pols.items():
        p["forecasts"] = forecasts.get(pid, {})

    # ---- recent + forthcoming actions
    cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=config.RECENT_ACTION_DAYS)
    recent_links = links[(links["publication_date"] >= cutoff)] if not links.empty else links
    doc_by_id = {d["id"]: d for d in docs}
    for d in forthcoming:
        doc_by_id[d["id"]] = d
    ev_index: dict[tuple, list[dict]] = {}
    if not live_events.empty:
        for r in live_events.itertuples(index=False):
            ev_index.setdefault((r.sector_id, pd.Timestamp(r.date)), []).append({
                "ticker": r.ticker, "benchmark": r.benchmark,
                "p_outperform": {str(h): (None if pd.isna(getattr(r, f"p_outperform_{h}")) else float(getattr(r, f"p_outperform_{h}"))) for h in config.HORIZONS},
                "realized_excess": {str(h): (None if pd.isna(getattr(r, f"realized_excess_{h}")) else float(getattr(r, f"realized_excess_{h}"))) for h in config.HORIZONS},
                "realized_excess_to_date": float(r.realized_excess_to_date),
                "trading_days_elapsed": int(r.trading_days_elapsed),
                "mom_20": round(float(r.mom_20), 5),
            })
    actions_out = []
    grouped = recent_links.groupby("doc_id") if not recent_links.empty else []
    for doc_id, g in grouped:
        d = doc_by_id.get(doc_id)
        if not d:
            continue
        sectors_out = []
        for r in g.sort_values("relevance", ascending=False).itertuples(index=False):
            key = (r.sector_id, pd.Timestamp(r.publication_date))
            tickers = ev_index.get(key, [])
            pol_rows = _top_politicians(pols, r.sector_id, limit=8)
            sectors_out.append({
                "sector_id": r.sector_id, "sector": names[r.sector_id], "benchmark": SECTOR_BY_ID[r.sector_id].benchmark,
                "relevance": float(r.relevance), "direction": int(r.direction), "direction_score": float(r.direction_score),
                "why": {"agencies": list(r.matched_agencies), "keywords": list(r.matched_keywords), "direction_terms": list(r.matched_direction)},
                "tickers": tickers, "politicians": pol_rows,
            })
        actions_out.append({
            "id": doc_id, "title": d.get("title"), "abstract": (d.get("abstract") or "")[:600] or None,
            "type": d.get("type"), "action": d.get("action"), "agencies": d.get("agencies") or [],
            "publication_date": d.get("publication_date"), "signing_date": d.get("signing_date"),
            "url": d.get("url"), "significant": bool(d.get("significant")), "forthcoming": bool(d.get("forthcoming")),
            "president": d.get("president"), "eo_number": d.get("eo_number"), "sectors": sectors_out,
        })
    actions_out.sort(key=lambda a: (not a["forthcoming"], a["publication_date"] or ""), reverse=True)
    actions_out.sort(key=lambda a: (a["publication_date"] or ""), reverse=True)

    # ---- sectors
    sectors_out = []
    for s in SECTORS:
        sl = links[links["sector_id"] == s.id] if not links.empty else links
        rec = sl[sl["publication_date"] >= cutoff] if not sl.empty else sl
        traders = sorted(((pid, p["by_sector"][s.id]) for pid, p in pols.items() if s.id in p["by_sector"]),
                         key=lambda kv: -(kv[1]["buys"] + kv[1]["sells"]))
        sectors_out.append({
            "id": s.id, "name": s.name, "tickers": list(s.tickers), "benchmark": s.benchmark,
            "agencies": list(s.agencies), "committees": list(s.committees),
            "n_actions_total": int(sl["doc_id"].nunique()) if not sl.empty else 0,
            "n_actions_recent": int(rec["doc_id"].nunique()) if not rec.empty else 0,
            "recent_direction": round(float(rec["direction_score"].mean()), 3) if not rec.empty else None,
            "n_traders": len(traders),
            "top_traders": [{"id": pid, "name": pols[pid]["name"], "buys": d["buys"], "sells": d["sells"], "last": d["last"]} for pid, d in traders[:10]],
        })

    # ---- tickers
    tickers_out = {}
    latest = prices.index.max().date().isoformat() if not prices.empty else None
    for s in SECTORS:
        for t in s.tickers:
            entry = tickers_out.setdefault(t, {"ticker": t, "sectors": [], "benchmarks": [], "traders": {}, "n_trades": 0, "n_buys": 0, "n_sells": 0})
            entry["sectors"].append(s.id)
            entry["benchmarks"].append(s.benchmark)
    if not tx.empty:
        for r in tx[tx["ticker"].isin(tickers_out.keys())].itertuples(index=False):
            e = tickers_out[r.ticker]
            e["n_trades"] += 1
            e["n_buys"] += int(r.side == "buy")
            e["n_sells"] += int(r.side == "sell")
            tr = e["traders"].setdefault(r.person_id, {"id": r.person_id, "name": pols.get(r.person_id, {}).get("name", r.person_name), "buys": 0, "sells": 0, "last": None})
            tr["buys" if r.side == "buy" else "sells" if r.side == "sell" else "buys"] += int(r.side in ("buy", "sell"))
            fd = r.filing_date.date().isoformat() if pd.notna(r.filing_date) else None
            if fd and (tr["last"] is None or fd > tr["last"]):
                tr["last"] = fd
    for e in tickers_out.values():
        e["traders"] = sorted(e["traders"].values(), key=lambda x: -(x["buys"] + x["sells"]))[:12]
        # latest live scores for this ticker (most recent action)
        live = [row for rows in ev_index.values() for row in rows if row["ticker"] == e["ticker"]]
        e["latest_scores"] = live[0] if live else None
        e["n_recent_actions"] = len(live)

    # ---- summary
    summary = {
        "app": "WhiteRock", "version": __version__, "generated_at": now.isoformat(timespec="seconds"),
        "disclaimer": DISCLAIMER,
        "horizons": list(config.HORIZONS), "disclosure_window_days": config.DISCLOSURE_WINDOW_DAYS,
        "recent_action_days": config.RECENT_ACTION_DAYS, "market_benchmark": MARKET_BENCHMARK,
        "counts": {
            "documents": len(docs), "linked_documents": int(links["doc_id"].nunique()) if not links.empty else 0,
            "recent_actions": len(actions_out), "forthcoming": sum(1 for a in actions_out if a["forthcoming"]),
            "transactions": int(len(tx)), "transactions_in_universe": int(tx["in_universe"].sum()) if not tx.empty else 0,
            "politicians_with_trades": len(pols), "tickers": len(tickers_out), "sectors": len(SECTORS),
            "outperformance_events": n_outperf_events,
            "price_rows": int(len(prices)), "latest_price_date": latest,
        },
        "models": {
            "disclosure": {"metrics": disclosure_metrics, "base_rates": disclosure_base_rates},
            "outperformance": {str(h): m for h, m in outperf_metrics.items()},
        },
        "sources": source_status,
    }
    write_json(config.SITE_DATA_DIR / "summary.json", summary, indent=1)
    write_json(config.SITE_DATA_DIR / "actions.json", actions_out)
    write_json(config.SITE_DATA_DIR / "sectors.json", sectors_out)
    write_json(config.SITE_DATA_DIR / "politicians.json", sorted(pols.values(), key=lambda p: -p["n_transactions"]))
    write_json(config.SITE_DATA_DIR / "tickers.json", sorted(tickers_out.values(), key=lambda t: t["ticker"]))
    log.info("Site data written: %d actions, %d politicians, %d tickers", len(actions_out), len(pols), len(tickers_out))


def _top_politicians(pols: dict[str, dict], sector_id: str, limit: int) -> list[dict]:
    rows = []
    for pid, p in pols.items():
        f = p.get("forecasts", {}).get(sector_id)
        hist = p["by_sector"].get(sector_id)
        if not f and not hist:
            continue
        rows.append({
            "id": pid, "name": p["name"], "chamber": p["chamber"], "party": p["party"], "state": p["state"],
            "p_buy": f["p_buy"] if f else None, "p_sell": f["p_sell"] if f else None, "p_none": f["p_none"] if f else None,
            "hist_buys": hist["buys"] if hist else 0, "hist_sells": hist["sells"] if hist else 0,
            "last": hist["last"] if hist else None, "committee_relevant": f["committee_relevant"] if f else False,
        })
    rows.sort(key=lambda r: -((r["p_buy"] or 0) + (r["p_sell"] or 0)))
    return rows[:limit]
