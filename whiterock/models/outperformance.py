"""Benchmark-outperformance model.

Question answered: after a government action linked to sector s, will ticker
x beat its sector benchmark ETF over the next 5, 20 and 60 trading days?

Design:
  * Event = (sector, publication date) aggregated across all linked Federal
    Register documents that day; one row per ticker in the sector.
  * Label(h) = excess return of ticker vs benchmark from the first close on or
    after the publication date to h trading days later, > 0.
  * Features: mapped direction and relevance, document mix, sector identity,
    and the ticker's own recent excess momentum (all known at event time).
  * Model = regularized logistic regression per horizon, evaluated on a
    strict time-based holdout (last 12 months of events).
Expect modest skill: markets price public actions quickly. The dashboard
shows the holdout numbers next to every probability so nobody over-reads them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .. import config
from ..mapping.universe import SECTOR_BY_ID, SECTORS
from .disclosure import calibration_table

log = logging.getLogger(__name__)

SECTOR_IDS = [s.id for s in SECTORS]
BASE_FEATURES = ["direction", "relevance", "n_docs", "n_significant", "n_presidential", "dir_x_rel",
                 "mom_20", "mom_60", "actions_30d"]
FEATURES = BASE_FEATURES + [f"sec_{s}" for s in SECTOR_IDS]


@dataclass
class OutperformanceResult:
    models: dict[int, object] = field(default_factory=dict)
    metrics: dict[int, dict] = field(default_factory=dict)
    n_events: int = 0


def _excess_paths(prices: pd.DataFrame) -> pd.DataFrame:
    """Log-price table (ticker and benchmark), forward-filled within gaps."""
    return np.log(prices.sort_index().ffill(limit=5))


def _event_rows(sector_days: pd.DataFrame, logp: pd.DataFrame, horizons=config.HORIZONS, with_labels=True) -> pd.DataFrame:
    idx = logp.index
    rows = []
    for _, ev in sector_days.iterrows():
        sector = SECTOR_BY_ID[ev["sector_id"]]
        bench = sector.benchmark
        if bench not in logp.columns:
            continue
        pos = idx.searchsorted(ev["date"])
        if pos >= len(idx):
            continue
        t0 = idx[pos]
        if pos < 61:
            continue
        for ticker in sector.tickers:
            if ticker not in logp.columns or np.isnan(logp.at[t0, ticker]):
                continue
            row = {
                "sector_id": sector.id, "date": ev["date"], "t0": t0, "ticker": ticker, "benchmark": bench,
                "direction": float(ev["mean_direction"]), "relevance": float(ev["max_relevance"]),
                "n_docs": int(ev["n_docs"]), "n_significant": int(ev.get("n_significant", 0)),
                "n_presidential": int(ev.get("n_presidential", 0)),
                "actions_30d": int(ev.get("actions_30d", 0)),
            }
            row["dir_x_rel"] = row["direction"] * row["relevance"]
            for k in (20, 60):
                row[f"mom_{k}"] = float((logp.at[t0, ticker] - logp.iat[pos - k, logp.columns.get_loc(ticker)])
                                        - (logp.at[t0, bench] - logp.iat[pos - k, logp.columns.get_loc(bench)]))
            if with_labels:
                for h in horizons:
                    if pos + h < len(idx):
                        t1 = idx[pos + h]
                        ex = (logp.at[t1, ticker] - logp.at[t0, ticker]) - (logp.at[t1, bench] - logp.at[t0, bench])
                        row[f"excess_{h}"] = float(ex)
                    else:
                        row[f"excess_{h}"] = np.nan
            rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for s in SECTOR_IDS:
        df[f"sec_{s}"] = (df["sector_id"] == s).astype(int)
    # Momentum is undefined when a ticker has too little price history (new listings):
    # treat that as zero momentum rather than dropping the event.
    df[["mom_20", "mom_60"]] = df[["mom_20", "mom_60"]].fillna(0.0)
    df[BASE_FEATURES] = df[BASE_FEATURES].fillna(0.0)
    return df


def _with_intensity(sector_days: pd.DataFrame) -> pd.DataFrame:
    """Add trailing-30-day action count per sector (excluding the event day)."""
    out = []
    for sid, g in sector_days.groupby("sector_id"):
        g = g.sort_values("date").copy()
        s = g.set_index("date")["n_docs"]
        g["actions_30d"] = [int(s[(s.index < d) & (s.index >= d - pd.Timedelta(days=30))].sum()) for d in g["date"]]
        out.append(g)
    return pd.concat(out) if out else sector_days.assign(actions_30d=0)


def train(sector_days: pd.DataFrame, prices: pd.DataFrame, holdout_months: int = 12) -> tuple[OutperformanceResult, pd.DataFrame]:
    res = OutperformanceResult()
    if sector_days.empty or prices.empty:
        return res, pd.DataFrame()
    logp = _excess_paths(prices)
    events = _event_rows(_with_intensity(sector_days), logp)
    res.n_events = len(events)
    if events.empty:
        return res, events
    cutoff = events["date"].max() - pd.DateOffset(months=holdout_months)
    for h in config.HORIZONS:
        lab = events.dropna(subset=[f"excess_{h}"]).copy()
        lab["y"] = (lab[f"excess_{h}"] > 0).astype(int)
        tr, ho = lab[lab["date"] <= cutoff], lab[lab["date"] > cutoff]
        if len(tr) < 200 or tr["y"].nunique() < 2:
            continue
        model = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=2000))
        model.fit(tr[FEATURES], tr["y"])
        m: dict = {"n_train": len(tr), "n_holdout": len(ho), "holdout_start": cutoff.date().isoformat(),
                   "base_rate_train": round(float(tr["y"].mean()), 4)}
        if len(ho) > 50 and ho["y"].nunique() > 1:
            p = model.predict_proba(ho[FEATURES])[:, 1]
            m["auc"] = round(float(roc_auc_score(ho["y"], p)), 4)
            m["brier"] = round(float(brier_score_loss(ho["y"], p)), 5)
            m["brier_base_rate"] = round(float(brier_score_loss(ho["y"], np.full(len(ho), tr["y"].mean()))), 5)
            m["base_rate_holdout"] = round(float(ho["y"].mean()), 4)
            m["calibration"] = calibration_table(ho["y"].to_numpy(), p)
            # Directional sanity check: mean realized excess when the model says > 0.55 vs < 0.45.
            hi, lo = ho[p > 0.55], ho[p < 0.45]
            m["mean_excess_when_confident_up"] = round(float(hi[f"excess_{h}"].mean()), 5) if len(hi) else None
            m["mean_excess_when_confident_down"] = round(float(lo[f"excess_{h}"].mean()), 5) if len(lo) else None
            m["n_confident_up"], m["n_confident_down"] = int(len(hi)), int(len(lo))
        model.fit(lab[FEATURES], lab["y"])   # refit on all labelled events for live scoring
        res.models[h] = model
        res.metrics[h] = m
    return res, events


def score_events(res: OutperformanceResult, sector_days_now: pd.DataFrame, prices: pd.DataFrame,
                 history_sector_days: pd.DataFrame) -> pd.DataFrame:
    """Score live events (recent or forthcoming actions). Adds realized excess where elapsed."""
    if sector_days_now.empty or prices.empty:
        return pd.DataFrame()
    logp = _excess_paths(prices)
    hist = history_sector_days.set_index(["sector_id", "date"])["n_docs"] if not history_sector_days.empty else None
    sd = sector_days_now.copy()
    sd["actions_30d"] = [
        int(hist.loc[sid][(hist.loc[sid].index < d) & (hist.loc[sid].index >= d - pd.Timedelta(days=30))].sum())
        if hist is not None and sid in hist.index.get_level_values(0) else 0
        for sid, d in zip(sd["sector_id"], sd["date"])
    ]
    # For forthcoming or very recent events the anchor is the latest close.
    last = logp.index.max()
    sd["date"] = sd["date"].where(sd["date"] <= last, last)
    ev = _event_rows(sd, logp, with_labels=True)
    if ev.empty:
        return ev
    for h in config.HORIZONS:
        if h in res.models:
            ev[f"p_outperform_{h}"] = np.round(res.models[h].predict_proba(ev[FEATURES])[:, 1], 4)
        else:
            ev[f"p_outperform_{h}"] = np.nan
        ev[f"realized_excess_{h}"] = ev[f"excess_{h}"].round(5)
    # Excess so far (from t0 to the latest close), always available.
    ev["realized_excess_to_date"] = [
        round(float((logp.at[last, t] - logp.at[t0, t]) - (logp.at[last, b] - logp.at[t0, b])), 5)
        for t, b, t0 in zip(ev["ticker"], ev["benchmark"], ev["t0"])
    ]
    ev["trading_days_elapsed"] = [int(logp.index.get_loc(last) - logp.index.get_loc(t0)) for t0 in ev["t0"]]
    return ev
