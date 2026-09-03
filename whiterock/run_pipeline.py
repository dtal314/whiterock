"""WhiteRock pipeline: fetch public records, train, score, write site data.

    python -m whiterock.run_pipeline --update            # incremental (daily job)
    python -m whiterock.run_pipeline --no-fetch          # rebuild from caches only
    python -m whiterock.run_pipeline --update --max-new 20   # smoke test
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date

import pandas as pd

from . import config
from .build_site import build
from .features import build_action_links, build_sector_days, build_transactions
from .models import disclosure, outperformance
from .sources import executive, federal_register, house_clerk, legislators, prices, senate_efd
from .util import read_json, read_jsonl

log = logging.getLogger("whiterock")


def _status(name: str, ok: bool, detail: str, n: int | None = None, url: str | None = None) -> dict:
    return {"name": name, "ok": ok, "detail": detail, "count": n, "url": url}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="fetch new records from every source")
    ap.add_argument("--no-fetch", action="store_true", help="skip all network fetches; rebuild from caches")
    ap.add_argument("--max-new", type=int, default=None, help="cap new filings per chamber (smoke tests)")
    ap.add_argument("--skip-prices", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    fetch = args.update and not args.no_fetch
    t0 = time.time()
    status: dict[str, dict] = {}

    # ------------------------------------------------------------ sources
    if fetch:
        try:
            roster = legislators.update()
            status["legislators"] = _status("congress-legislators roster", True, "fetched", len(roster["people"]),
                                            "https://github.com/unitedstates/congress-legislators")
        except Exception as exc:
            log.exception("legislators failed")
            roster = legislators.load()
            status["legislators"] = _status("congress-legislators roster", False, f"fetch failed, using cache: {exc}", len(roster["people"]))
    else:
        roster = legislators.load()
        status["legislators"] = _status("congress-legislators roster", True, "cache", len(roster["people"]))

    docs = _run(fetch, "federal_register", "Federal Register (rules, proposed rules, presidential documents)",
                lambda: federal_register.update(), lambda: read_jsonl(federal_register.CACHE), status,
                "https://www.federalregister.gov/developers/documentation/api/v1")
    forthcoming = []
    if fetch:
        try:
            forthcoming = federal_register.fetch_public_inspection()
            status["public_inspection"] = _status("Federal Register public inspection (forthcoming)", True, "fetched", len(forthcoming),
                                                  "https://www.federalregister.gov/public-inspection/current")
        except Exception as exc:
            status["public_inspection"] = _status("Federal Register public inspection (forthcoming)", False, str(exc), 0)
    house = _run(fetch, "house", "House Clerk periodic transaction reports",
                 lambda: house_clerk.update(max_new=args.max_new), lambda: read_jsonl(house_clerk.TX_CACHE), status,
                 "https://disclosures-clerk.house.gov/FinancialDisclosure")
    senate = _run(fetch, "senate", "Senate eFD periodic transaction reports",
                  lambda: senate_efd.update(max_new=args.max_new), lambda: read_jsonl(senate_efd.TX_CACHE), status,
                  "https://efdsearch.senate.gov/search/")
    exec_rows = executive.load()
    status["executive"] = _status("Executive branch OGE 278e/278-T (manual public-record ledger)", True,
                                  "manual ledger; automated crawl not yet built", len(exec_rows), "https://www.oge.gov/")
    if fetch and not args.skip_prices:
        px = _run(True, "prices", "Daily prices (yfinance)", lambda: prices.update(), prices.load, status,
                  "https://github.com/ranaroussi/yfinance")
    else:
        px = prices.load()
        status["prices"] = _status("Daily prices (yfinance)", True, "cache", len(px))
    for k in ("house", "senate"):
        idx = read_json(house_clerk.INDEX_CACHE if k == "house" else senate_efd.INDEX_CACHE, {})
        idx = {key: v for key, v in idx.items() if not key.startswith("_")}
        paper = sum(1 for v in idx.values() if v.get("status") == "paper")
        status[k]["filings_indexed"] = len(idx)
        status[k]["filings_paper_unparsed"] = paper

    # ------------------------------------------------------------ tables
    tx = build_transactions(house, senate, exec_rows, roster)
    links = build_action_links(docs)
    sector_days = build_sector_days(links)
    log.info("Tables: %d transactions (%d in universe), %d sector links, %d sector-days",
             len(tx), int(tx["in_universe"].sum()) if len(tx) else 0, len(links), len(sector_days))

    # ------------------------------------------------------------ models
    if len(tx):
        panel = disclosure.build_panel(tx, roster, sector_days)
        dres = disclosure.train(panel)
        dscores = disclosure.score_now(dres, tx, roster, sector_days)
    else:
        dres = disclosure.DisclosureResult(None, {"note": "no transactions"}, {}, 0, 0)
        dscores = pd.DataFrame()
    log.info("Disclosure model: %s", dres.metrics)

    ores, events = outperformance.train(sector_days, px)
    log.info("Outperformance model: %s", {h: {k: v for k, v in m.items() if k != "calibration"} for h, m in ores.metrics.items()})

    # live events: recent published + forthcoming
    cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=config.RECENT_ACTION_DAYS)
    live_links = pd.concat([links[links["publication_date"] >= cutoff], build_action_links(forthcoming)], ignore_index=True) if len(links) else build_action_links(forthcoming)
    if len(live_links):
        live_links["forthcoming"] = live_links["forthcoming"].fillna(False).astype(bool)
        live_sd = live_links.groupby(["sector_id", "publication_date"]).agg(
            n_docs=("doc_id", "nunique"), mean_direction=("direction_score", "mean"), max_relevance=("relevance", "max"),
            n_significant=("significant", "sum"), n_presidential=("type", lambda s: int((s == "PRESDOCU").sum()))
        ).reset_index().rename(columns={"publication_date": "date"})
        live_events = outperformance.score_events(ores, live_sd, px, sector_days)
    else:
        live_events = pd.DataFrame()

    # ------------------------------------------------------------ site
    build(docs=docs, links=pd.concat([links, build_action_links(forthcoming)], ignore_index=True) if len(links) else links,
          tx=tx, roster=roster, disclosure_scores=dscores, disclosure_metrics=dres.metrics,
          disclosure_base_rates=dres.base_rates, live_events=live_events, outperf_metrics=ores.metrics,
          n_outperf_events=ores.n_events, prices=px, source_status=status, forthcoming=forthcoming)
    log.info("Pipeline done in %.0fs", time.time() - t0)
    return 0


def _run(fetch: bool, key: str, label: str, fetcher, loader, status: dict, url: str):
    if fetch:
        try:
            data = fetcher()
            status[key] = _status(label, True, f"fetched {date.today().isoformat()}", len(data), url)
            return data
        except Exception as exc:
            log.exception("%s failed", key)
            data = loader()
            status[key] = _status(label, False, f"fetch failed, using cache: {exc}", len(data), url)
            return data
    data = loader()
    status[key] = _status(label, True, "cache", len(data), url)
    return data


if __name__ == "__main__":
    sys.exit(main())
