"""Disclosure-likelihood model.

Question answered: for politician p and sector s, what is the probability that
within the next 60 days a purchase, a sale, or no related transaction will be
publicly disclosed?

Design:
  * Unit of analysis = (politician, sector, monthly reference date t).
  * Label = what was disclosed (by filing date) in (t, t + 60 days]:
    0 none, 1 purchase-dominant, 2 sale-dominant.
  * Features use only information available before t: the person's own
    trading history in the sector and overall, committee relevance, and the
    intensity of recent government actions in the sector.
  * Model = gradient-boosted trees (multinomial), evaluated on a strict
    time-based holdout (the most recent months), never on shuffled rows.
  * Population = people with at least one disclosed transaction in the data.
    Everyone else gets no score; their empty history is the honest answer.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from .. import config
from ..mapping.universe import SECTORS, committee_sectors

log = logging.getLogger(__name__)

FEATURES = [
    "n_sector_12m", "n_sector_36m", "n_buy_sector_12m", "n_sell_sector_12m",
    "n_all_12m", "n_all_36m", "n_sectors_active_12m", "days_since_sector", "days_since_any",
    "committee_relevant", "is_house", "spouse_share", "actions_30d", "actions_90d",
    "action_direction_30d", "person_buy_share",
]
CLASSES = ("none", "buy", "sell")
SECTOR_IDS = [s.id for s in SECTORS]


@dataclass
class DisclosureResult:
    model: HistGradientBoostingClassifier | None
    metrics: dict
    base_rates: dict
    n_train: int
    n_holdout: int


def _days(ts) -> np.ndarray:
    return pd.to_datetime(ts).values.astype("datetime64[D]").astype(np.int64)


def _count(arr: np.ndarray, lo: int, hi: int) -> int:
    """Count of values in [lo, hi)."""
    return int(np.searchsorted(arr, hi, side="left") - np.searchsorted(arr, lo, side="left"))


class SectorIntensity:
    """Fast trailing-window lookups over the (sector, date) action table."""

    def __init__(self, sector_days: pd.DataFrame):
        self.tables: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        if sector_days.empty:
            return
        for sid, g in sector_days.groupby("sector_id"):
            g = g.sort_values("date")
            d = _days(g["date"])
            n = g["n_docs"].to_numpy(dtype=float)
            signed = (g["mean_direction"].to_numpy(dtype=float) * n)
            self.tables[sid] = (d, np.concatenate([[0.0], np.cumsum(n)]), np.concatenate([[0.0], np.cumsum(signed)]))

    def window(self, sid: str, t: int, days: int) -> tuple[int, float]:
        tab = self.tables.get(sid)
        if tab is None:
            return 0, 0.0
        d, cn, cs = tab
        lo, hi = np.searchsorted(d, t - days, side="left"), np.searchsorted(d, t, side="left")
        return int(cn[hi] - cn[lo]), float(cs[hi] - cs[lo])


class PersonHistory:
    """Sorted filing-day arrays for one person, overall and per sector."""

    def __init__(self, ptx: pd.DataFrame):
        ptx = ptx.dropna(subset=["filing_date"]).sort_values("filing_date")
        self.chamber = ptx["chamber"].iloc[0]
        self.all = _days(ptx["filing_date"])
        side = ptx["side"].to_numpy()
        owner = ptx["owner"].to_numpy()
        self.buys = self.all[side == "buy"]
        self.spouse = self.all[owner == "spouse"]
        self.sector: dict[str, np.ndarray] = {}
        self.sector_buy: dict[str, np.ndarray] = {}
        self.sector_sell: dict[str, np.ndarray] = {}
        secs = ptx["sectors"].to_numpy()
        for sid in SECTOR_IDS:
            mask = np.fromiter((sid in s for s in secs), dtype=bool, count=len(secs))
            self.sector[sid] = self.all[mask]
            self.sector_buy[sid] = self.all[mask & (side == "buy")]
            self.sector_sell[sid] = self.all[mask & (side == "sell")]
        self.first = int(self.all[0]) if len(self.all) else None

    def features(self, sid: str, t: int, committee: set[str], intensity: SectorIntensity) -> dict:
        y1, y3 = t - 365, t - 3 * 365
        sec = self.sector[sid]
        n_all_12 = _count(self.all, y1, t)
        idx_sec = np.searchsorted(sec, t, side="left")
        idx_all = np.searchsorted(self.all, t, side="left")
        last_sector = (t - int(sec[idx_sec - 1])) if idx_sec > 0 else 1500
        last_any = (t - int(self.all[idx_all - 1])) if idx_all > 0 else 1500
        a30, d30 = intensity.window(sid, t, 30)
        a90, _ = intensity.window(sid, t, 90)
        return {
            "n_sector_12m": _count(sec, y1, t), "n_sector_36m": _count(sec, y3, t),
            "n_buy_sector_12m": _count(self.sector_buy[sid], y1, t), "n_sell_sector_12m": _count(self.sector_sell[sid], y1, t),
            "n_all_12m": n_all_12, "n_all_36m": _count(self.all, y3, t),
            "n_sectors_active_12m": sum(1 for s in SECTOR_IDS if _count(self.sector[s], y1, t) > 0),
            "days_since_sector": min(last_sector, 1500), "days_since_any": min(last_any, 1500),
            "committee_relevant": int(sid in committee), "is_house": int(self.chamber == "house"),
            "spouse_share": (_count(self.spouse, y1, t) / n_all_12) if n_all_12 else 0.0,
            "actions_30d": a30, "actions_90d": a90, "action_direction_30d": d30,
            "person_buy_share": (_count(self.buys, y1, t) / n_all_12) if n_all_12 else 0.5,
        }

    def label(self, sid: str, t: int) -> int:
        hi = t + config.DISCLOSURE_WINDOW_DAYS + 1
        buys, sells = _count(self.sector_buy[sid], t + 1, hi), _count(self.sector_sell[sid], t + 1, hi)
        if buys == 0 and sells == 0:
            return 0
        return 1 if buys >= sells else 2


def _people_committees(roster: dict) -> dict[str, set[str]]:
    return {p["id"]: committee_sectors(p.get("committees", [])) for p in roster["people"]}


def build_panel(tx: pd.DataFrame, roster: dict, sector_days: pd.DataFrame,
                end: pd.Timestamp | None = None) -> pd.DataFrame:
    end = end or pd.Timestamp.today().normalize()
    comms = _people_committees(roster)
    intensity = SectorIntensity(sector_days)
    start = pd.Timestamp(config.TRADES_START) + pd.DateOffset(months=6)
    last_ref = end - pd.Timedelta(days=config.DISCLOSURE_WINDOW_DAYS)
    ref_dates = pd.date_range(start, last_ref, freq="MS")
    ref_days = _days(ref_dates)
    rows = []
    for pid, ptx in tx.groupby("person_id"):
        h = PersonHistory(ptx)
        if h.first is None:
            continue
        cm = comms.get(pid, set())
        for t, ts in zip(ref_days, ref_dates):
            if int(t) < h.first:      # person not yet observable as a trader
                continue
            for sid in SECTOR_IDS:
                f = h.features(sid, int(t), cm, intensity)
                f.update(person_id=pid, sector_id=sid, ref_date=ts, label=h.label(sid, int(t)))
                rows.append(f)
    panel = pd.DataFrame(rows)
    log.info("Disclosure panel: %d rows, label mix %s", len(panel), panel["label"].value_counts().to_dict() if len(panel) else {})
    return panel


def train(panel: pd.DataFrame, holdout_months: int = 6) -> DisclosureResult:
    if panel.empty or panel["label"].nunique() < 2:
        return DisclosureResult(None, {"note": "not enough labelled data"}, {}, 0, 0)
    cutoff = panel["ref_date"].max() - pd.DateOffset(months=holdout_months)
    train_df, hold_df = panel[panel["ref_date"] <= cutoff], panel[panel["ref_date"] > cutoff]
    if train_df["label"].nunique() < 2:
        train_df, hold_df = panel, panel.iloc[0:0]
    model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=4,
                                           l2_regularization=1.0, min_samples_leaf=40, random_state=7)
    model.fit(train_df[FEATURES], train_df["label"])
    metrics: dict = {"holdout_start": cutoff.date().isoformat(), "n_train": int(len(train_df)), "n_holdout": int(len(hold_df))}
    base = train_df["label"].value_counts(normalize=True).reindex([0, 1, 2]).fillna(0.0)
    base_rates = {CLASSES[i]: round(float(base[i]), 4) for i in range(3)}
    if len(hold_df) and hold_df["label"].nunique() > 1:
        proba = _align(model.predict_proba(hold_df[FEATURES]), model.classes_)
        y = hold_df["label"].to_numpy()
        metrics["log_loss"] = round(float(log_loss(y, proba, labels=[0, 1, 2])), 4)
        metrics["log_loss_base_rate"] = round(float(log_loss(y, np.tile(base.to_numpy(), (len(y), 1)), labels=[0, 1, 2])), 4)
        for i, name in enumerate(CLASSES[1:], start=1):
            yb = (y == i).astype(int)
            if 0 < yb.sum() < len(yb):
                metrics[f"auc_{name}"] = round(float(roc_auc_score(yb, proba[:, i])), 4)
                metrics[f"brier_{name}"] = round(float(brier_score_loss(yb, proba[:, i])), 5)
                metrics[f"brier_{name}_base_rate"] = round(float(brier_score_loss(yb, np.full(len(yb), yb.mean()))), 5)
                metrics[f"calibration_{name}"] = calibration_table(yb, proba[:, i])
        metrics["holdout_positive_rate"] = {CLASSES[i]: round(float((y == i).mean()), 4) for i in range(3)}
    model.fit(panel[FEATURES], panel["label"])   # refit on everything for scoring the present
    return DisclosureResult(model, metrics, base_rates, len(train_df), len(hold_df))


def _align(proba: np.ndarray, classes: np.ndarray) -> np.ndarray:
    out = np.zeros((proba.shape[0], 3))
    for j, c in enumerate(classes):
        out[:, int(c)] = proba[:, j]
    return out


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 5) -> list[dict]:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = 0.0, 1.0 + 1e-9
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum() == 0:
            continue
        rows.append({"p_low": round(float(lo), 4), "p_high": round(float(min(hi, 1.0)), 4), "n": int(m.sum()),
                     "mean_predicted": round(float(p[m].mean()), 4), "observed": round(float(y[m].mean()), 4)})
    return rows


def score_now(result: DisclosureResult, tx: pd.DataFrame, roster: dict, sector_days: pd.DataFrame,
              asof: pd.Timestamp | None = None) -> pd.DataFrame:
    """Probabilities for every (trader, sector) as of today."""
    asof = asof or pd.Timestamp.today().normalize()
    t = int(_days(pd.DatetimeIndex([asof]))[0])
    comms = _people_committees(roster)
    intensity = SectorIntensity(sector_days)
    rows = []
    for pid, ptx in tx.groupby("person_id"):
        h = PersonHistory(ptx)
        if h.first is None:
            continue
        cm = comms.get(pid, set())
        for sid in SECTOR_IDS:
            f = h.features(sid, t, cm, intensity)
            f.update(person_id=pid, sector_id=sid)
            rows.append(f)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if result.model is None:
        for i, name in enumerate(CLASSES):
            df[f"p_{name}"] = result.base_rates.get(name, [0.9, 0.05, 0.05][i])
        return df
    proba = _align(result.model.predict_proba(df[FEATURES]), result.model.classes_)
    for i, name in enumerate(CLASSES):
        df[f"p_{name}"] = np.round(proba[:, i], 4)
    return df
