"""Buy signals: rank universe tickers by the congressional buying pattern.

Composite of five percentile-ranked components, all derived from data already
on the dashboard:
  * net disclosed buys in the last 90 days (buys minus sells, by filing date)
  * number of distinct members who disclosed a purchase in the last 90 days
  * recent government-action tailwind for the ticker's sectors
  * model probability of beating the sector benchmark over 60 trading days
  * expected buy disclosures in the next 60 days across all scored members

Output is a ranked list with every component shown, so a reader can see why
a ticker ranks where it does. It is a screen of public disclosure patterns,
not investment advice.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .mapping.universe import ALL_TICKERS, SECTOR_BY_ID, ticker_sectors

WEIGHTS = {"net_buy_90d": 0.35, "buyers_90d": 0.15, "action_tailwind": 0.20, "p_beat_60": 0.15, "expected_buy_disclosures": 0.15}


def _pct(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, method="average").fillna(0.5)


def build_signals(tx: pd.DataFrame, links: pd.DataFrame, live_events: pd.DataFrame,
                  disclosure_scores: pd.DataFrame, pols: dict[str, dict], asof: pd.Timestamp | None = None) -> dict:
    asof = asof or pd.Timestamp.today().normalize()
    rows = {t: {"ticker": t, "sectors": ticker_sectors(t)} for t in ALL_TICKERS}

    # ---- congressional buying pattern
    if not tx.empty:
        u = tx[tx["ticker"].isin(rows.keys()) & tx["filing_date"].notna()].copy()
        u["amount_mid"] = ((u["amount_low"].fillna(0) + u["amount_high"].fillna(u["amount_low"]).fillna(0)) / 2.0)
        for days, tag in ((90, "90d"), (365, "365d")):
            w = u[u["filing_date"] > asof - pd.Timedelta(days=days)]
            g = w.groupby("ticker")
            buys = g.apply(lambda x: int((x["side"] == "buy").sum()), include_groups=False)
            sells = g.apply(lambda x: int((x["side"] == "sell").sum()), include_groups=False)
            buyers = g.apply(lambda x: sorted(set(x.loc[x["side"] == "buy", "person_id"])), include_groups=False)
            amt = g.apply(lambda x: float(x.loc[x["side"] == "buy", "amount_mid"].sum()), include_groups=False)
            for t in rows:
                rows[t][f"buys_{tag}"] = int(buys.get(t, 0))
                rows[t][f"sells_{tag}"] = int(sells.get(t, 0))
                rows[t][f"net_buy_{tag}"] = int(buys.get(t, 0)) - int(sells.get(t, 0))
                rows[t][f"buyers_{tag}"] = len(buyers.get(t, []))
                rows[t][f"buy_amount_{tag}"] = float(amt.get(t, 0.0))
                if tag == "90d":
                    rows[t]["buyer_names"] = [pols.get(p, {}).get("name", p) for p in buyers.get(t, [])][:6]
                    last = w.loc[(w["ticker"] == t) & (w["side"] == "buy"), "filing_date"].max()
                    rows[t]["last_buy_filing"] = last.date().isoformat() if pd.notna(last) else None
    for t in rows:
        for k in ("buys_90d", "sells_90d", "net_buy_90d", "buyers_90d", "buy_amount_90d", "buys_365d", "sells_365d", "net_buy_365d", "buyers_365d", "buy_amount_365d"):
            rows[t].setdefault(k, 0)
        rows[t].setdefault("buyer_names", [])
        rows[t].setdefault("last_buy_filing", None)

    # ---- government-action tailwind (last RECENT_ACTION_DAYS, relevance-weighted direction)
    tail = {}
    if not links.empty:
        rec = links[links["publication_date"] >= asof - pd.Timedelta(days=config.RECENT_ACTION_DAYS)]
        for sid, g in rec.groupby("sector_id"):
            tail[sid] = {"score": float((g["relevance"] * g["direction_score"]).sum()), "n": int(g["doc_id"].nunique())}
    for t, r in rows.items():
        r["action_tailwind"] = round(sum(tail.get(s, {}).get("score", 0.0) for s in r["sectors"]), 3)
        r["recent_actions"] = int(sum(tail.get(s, {}).get("n", 0) for s in r["sectors"]))

    # ---- outperformance probabilities from the newest live event per ticker
    if not live_events.empty:
        latest = live_events.sort_values("date").groupby("ticker").tail(1).set_index("ticker")
        for t, r in rows.items():
            if t in latest.index:
                e = latest.loc[t]
                for h in config.HORIZONS:
                    v = e.get(f"p_outperform_{h}")
                    r[f"p_beat_{h}"] = None if v is None or pd.isna(v) else float(v)
                r["mom_20"] = float(e["mom_20"])
    for r in rows.values():
        for h in config.HORIZONS:
            r.setdefault(f"p_beat_{h}", None)
        r.setdefault("mom_20", None)

    # ---- expected buy disclosures next 60 days (sum of p_buy across scored members, by sector)
    exp = {}
    if not disclosure_scores.empty:
        exp = disclosure_scores.groupby("sector_id")["p_buy"].sum().to_dict()
    for r in rows.values():
        r["expected_buy_disclosures"] = round(float(sum(exp.get(s, 0.0) for s in r["sectors"])), 2)

    df = pd.DataFrame(list(rows.values()))
    comp = pd.DataFrame({
        "net_buy_90d": _pct(df["net_buy_90d"]),
        "buyers_90d": _pct(df["buyers_90d"]),
        "action_tailwind": _pct(df["action_tailwind"]),
        "p_beat_60": _pct(df["p_beat_60"].astype(float)),
        "expected_buy_disclosures": _pct(df["expected_buy_disclosures"]),
    })
    df["score"] = sum(WEIGHTS[k] * comp[k] for k in WEIGHTS).round(3)
    for k in WEIGHTS:
        df[f"pct_{k}"] = comp[k].round(3)
    df = df.sort_values(["score", "net_buy_90d"], ascending=False).reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)
    df["sector_names"] = df["sectors"].map(lambda ss: [SECTOR_BY_ID[s].name for s in ss])
    df["benchmark"] = df["sectors"].map(lambda ss: SECTOR_BY_ID[ss[0]].benchmark if ss else None)

    buys = df[(df["net_buy_90d"] > 0) | (df["buys_365d"] > 0)].head(20)
    sells = df[df["net_buy_90d"] < 0].sort_values("net_buy_90d").head(8)
    return {
        "asof": asof.date().isoformat(),
        "weights": WEIGHTS,
        "buy_signals": _records(buys),
        "sell_pressure": _records(sells),
        "all": _records(df[["rank", "ticker", "score", "net_buy_90d", "buyers_90d", "action_tailwind", "p_beat_60", "expected_buy_disclosures"]]),
    }


def _records(df: pd.DataFrame) -> list[dict]:
    out = []
    for rec in df.to_dict(orient="records"):
        out.append({k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in rec.items()})
    return out
