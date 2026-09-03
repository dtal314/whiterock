"""Recommendation ledger: record what the signal ranking recommended, when,
for how much, and how it performed afterwards.

Rules (all deliberately simple and public):
  * Each pipeline run looks at the top-ranked buy signals. A ticker with a
    composite score of at least MIN_SCORE that is not already open becomes a
    new recommendation, up to MAX_OPEN open positions.
  * Entry price = the last adjusted close available when the recommendation
    is made (a real order could only fill at the next open, so this is a
    slightly optimistic benchmark and is labelled as such).
  * Amount = a slice of a notional $10,000 model portfolio sized by conviction:
    score >= 0.80 -> $1,500, >= 0.70 -> $1,000, otherwise $500, never more
    than the cash the portfolio still has.
  * Every run marks each open recommendation to market against its sector
    benchmark ETF and closes it after HOLD_DAYS trading days (the model's
    longest horizon). Closed entries keep their final result forever.
The ledger lives in data/recommendations.json and is committed by the daily
job, so the track record is public and cannot be quietly rewritten.
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from . import config
from .util import read_json, write_json

log = logging.getLogger(__name__)

LEDGER = config.DATA_DIR / "recommendations.json"
NOTIONAL = 10000
MIN_SCORE = 0.60
MAX_OPEN = 10
HOLD_DAYS = max(config.HORIZONS)
TIERS = ((0.80, 1500), (0.70, 1000), (0.0, 500))


def _amount(score: float) -> int:
    for floor, amt in TIERS:
        if score >= floor:
            return amt
    return TIERS[-1][1]


def _price(prices: pd.DataFrame, ticker: str, when: pd.Timestamp) -> tuple[pd.Timestamp | None, float | None]:
    if ticker not in prices.columns:
        return None, None
    s = prices[ticker].dropna()
    s = s[s.index <= when]
    if s.empty:
        return None, None
    return s.index[-1], float(s.iloc[-1])


def update_ledger(signals: dict, prices: pd.DataFrame, asof: pd.Timestamp | None = None) -> dict:
    ledger = read_json(LEDGER, {"notional": NOTIONAL, "rules": {}, "entries": []})
    ledger["notional"] = NOTIONAL
    ledger["rules"] = {"min_score": MIN_SCORE, "max_open": MAX_OPEN, "hold_trading_days": HOLD_DAYS,
                       "tiers": [{"min_score": f, "amount": a} for f, a in TIERS]}
    entries: list[dict] = ledger["entries"]
    if prices.empty:
        return _finish(ledger)
    last_day = prices.index.max()
    asof = min(asof or pd.Timestamp.today().normalize(), last_day)
    idx = prices.index

    # ---- mark open positions to market, close the expired ones
    for e in entries:
        if e["status"] != "open":
            continue
        entry_day = pd.Timestamp(e["entry_date"])
        cur_day, cur_px = _price(prices, e["ticker"], last_day)
        _, cur_bx = _price(prices, e["benchmark"], last_day)
        if cur_px is None or cur_bx is None:
            continue
        days_held = int(idx.searchsorted(cur_day) - idx.searchsorted(entry_day))
        e.update({
            "last_date": cur_day.date().isoformat(), "last_price": round(cur_px, 4),
            "return": round(cur_px / e["entry_price"] - 1, 5),
            "benchmark_return": round(cur_bx / e["benchmark_entry_price"] - 1, 5),
            "trading_days_held": days_held,
        })
        e["excess_return"] = round(e["return"] - e["benchmark_return"], 5)
        e["value"] = round(e["amount"] * (1 + e["return"]), 2)
        e["pnl"] = round(e["value"] - e["amount"], 2)
        if days_held >= HOLD_DAYS:
            e.update({"status": "closed", "exit_date": e["last_date"], "exit_price": e["last_price"],
                      "closed_reason": f"held {HOLD_DAYS} trading days"})

    # ---- open new recommendations from today's ranking
    open_tickers = {e["ticker"] for e in entries if e["status"] == "open"}
    n_open = len(open_tickers)
    cash = NOTIONAL - sum(e["amount"] for e in entries if e["status"] == "open") \
        + sum(e["pnl"] for e in entries if e["status"] == "closed")
    next_id = 1 + max((e["id"] for e in entries), default=0)
    for cand in signals.get("buy_signals", []):
        if n_open >= MAX_OPEN or cash < TIERS[-1][1]:
            break
        if cand["score"] < MIN_SCORE or cand["ticker"] in open_tickers:
            continue
        day, px = _price(prices, cand["ticker"], asof)
        bday, bx = _price(prices, cand["benchmark"], asof)
        if px is None or bx is None:
            continue
        amount = int(min(_amount(cand["score"]), cash))   # never spend cash the portfolio does not have
        cash -= amount
        entries.append({
            "id": next_id, "ticker": cand["ticker"], "sectors": cand.get("sector_names", []),
            "benchmark": cand["benchmark"], "status": "open",
            "recommended_on": date.today().isoformat(), "entry_date": day.date().isoformat(),
            "entry_price": round(px, 4), "benchmark_entry_price": round(bx, 4),
            "amount": amount, "shares": round(amount / px, 4), "score_at_rec": cand["score"],
            "why": {
                "congress_90d": f'{cand["buys_90d"]} buys / {cand["sells_90d"]} sells by {cand["buyers_90d"]} members',
                "buyers": cand.get("buyer_names", []), "action_tailwind": cand["action_tailwind"],
                "p_beat_60": cand.get("p_beat_60"), "expected_buy_disclosures": cand["expected_buy_disclosures"],
            },
            "last_date": day.date().isoformat(), "last_price": round(px, 4), "return": 0.0,
            "benchmark_return": 0.0, "excess_return": 0.0, "trading_days_held": 0,
            "value": float(amount), "pnl": 0.0,
        })
        open_tickers.add(cand["ticker"])
        n_open += 1
        next_id += 1
        log.info("Recommendation opened: %s at %.2f, $%d", cand["ticker"], px, amount)
    return _finish(ledger)


def _finish(ledger: dict) -> dict:
    entries = ledger["entries"]
    open_ = [e for e in entries if e["status"] == "open"]
    closed = [e for e in entries if e["status"] == "closed"]
    invested_open = sum(e["amount"] for e in open_)
    stats = {
        "n_open": len(open_), "n_closed": len(closed), "n_total": len(entries),
        "open_amount": invested_open, "open_value": round(sum(e["value"] for e in open_), 2),
        "open_pnl": round(sum(e["pnl"] for e in open_), 2),
        "closed_pnl": round(sum(e["pnl"] for e in closed), 2),
        "hit_rate_closed": (round(float(np.mean([e["excess_return"] > 0 for e in closed])), 3) if closed else None),
        "avg_excess_closed": (round(float(np.mean([e["excess_return"] for e in closed])), 5) if closed else None),
        "avg_return_closed": (round(float(np.mean([e["return"] for e in closed])), 5) if closed else None),
        "avg_excess_open": (round(float(np.mean([e["excess_return"] for e in open_])), 5) if open_ else None),
        "cash": round(NOTIONAL - invested_open + sum(e["pnl"] for e in closed), 2),
    }
    stats["portfolio_value"] = round(stats["cash"] + stats["open_value"], 2)
    stats["portfolio_return"] = round(stats["portfolio_value"] / NOTIONAL - 1, 5)
    ledger["stats"] = stats
    ledger["updated"] = date.today().isoformat()
    write_json(LEDGER, ledger, indent=1)
    return ledger
