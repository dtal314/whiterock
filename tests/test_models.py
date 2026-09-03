"""Synthetic end-to-end check: the models train, score, and produce sane probabilities."""
import numpy as np
import pandas as pd

from whiterock.features import build_action_links, build_sector_days, build_transactions
from whiterock.mapping.universe import SECTORS
from whiterock.models import disclosure, outperformance


def _roster():
    return {"people": [
        {"id": "A000001", "first": "Ann", "last": "Alpha", "official_full": "Ann Alpha", "chamber": "house", "party": "D",
         "state": "CA", "district": 1, "current": True, "term_end": "2027-01-03", "committees": ["HSAS"]},
        {"id": "B000002", "first": "Bob", "last": "Beta", "official_full": "Bob Beta", "chamber": "senate", "party": "R",
         "state": "TX", "district": None, "current": True, "term_end": "2029-01-03", "committees": ["SSBK"]},
    ], "committees": {"HSAS": "House Armed Services", "SSBK": "Senate Banking"}}


def _house_rows(rng):
    rows = []
    for i in range(80):
        d = pd.Timestamp("2023-02-01") + pd.Timedelta(days=int(rng.integers(0, 1100)))
        rows.append({"chamber": "house", "filer_last": "Alpha", "filer_first": "Ann", "state_district": "CA01",
                     "ticker": rng.choice(["LMT", "RTX", "JPM"]), "tx_type": rng.choice(["purchase", "sale"]),
                     "tx_date": d.date().isoformat(), "filing_date": (d + pd.Timedelta(days=20)).date().isoformat(),
                     "owner": "self", "amount_low": 1001, "amount_high": 15000, "source_url": "x", "source": "test"})
    return rows


def _senate_rows(rng):
    rows = []
    for i in range(40):
        d = pd.Timestamp("2023-03-01") + pd.Timedelta(days=int(rng.integers(0, 1100)))
        rows.append({"chamber": "senate", "filer_last": "Beta", "filer_first": "Bob", "state_district": None,
                     "ticker": rng.choice(["JPM", "BAC"]), "tx_type": "purchase",
                     "tx_date": d.date().isoformat(), "filing_date": (d + pd.Timedelta(days=10)).date().isoformat(),
                     "owner": "spouse", "amount_low": 15001, "amount_high": 50000, "source_url": "y", "source": "test"})
    return rows


def _docs(rng):
    docs = []
    for i in range(400):
        d = pd.Timestamp("2019-01-02") + pd.Timedelta(days=int(rng.integers(0, 2700)))
        docs.append({"id": f"D{i}", "title": "Adjusting Imports of Steel; defense procurement of missile systems for the Army",
                     "abstract": "tariff on steel; increase defense procurement", "type": "PRESDOCU",
                     "agencies": ["Executive Office of the President"], "publication_date": d.date().isoformat(),
                     "url": "u", "significant": False})
    return docs


def _prices(rng):
    idx = pd.bdate_range("2018-10-01", "2026-09-01")
    cols = sorted({t for s in SECTORS for t in s.tickers} | {s.benchmark for s in SECTORS} | {"SPY"})
    data = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, size=(len(idx), len(cols))), axis=0))
    return pd.DataFrame(data, index=idx, columns=cols)


def test_pipeline_models_end_to_end():
    rng = np.random.default_rng(0)
    roster = _roster()
    tx = build_transactions(_house_rows(rng), _senate_rows(rng), [], roster)
    assert set(tx["person_id"]) == {"A000001", "B000002"}
    assert tx["in_universe"].all()
    links = build_action_links(_docs(rng))
    assert {"steel_materials", "defense"} <= set(links["sector_id"])
    sd = build_sector_days(links)
    panel = disclosure.build_panel(tx, roster, sd, end=pd.Timestamp("2026-09-01"))
    assert len(panel) > 0 and panel["label"].max() >= 1
    dres = disclosure.train(panel, holdout_months=6)
    scores = disclosure.score_now(dres, tx, roster, sd, asof=pd.Timestamp("2026-09-01"))
    assert np.allclose(scores[["p_none", "p_buy", "p_sell"]].sum(axis=1), 1.0, atol=1e-6)
    px = _prices(rng)
    ores, events = outperformance.train(sd, px)
    assert len(events) > 0
    live = outperformance.score_events(ores, sd.tail(3), px, sd)
    assert not live.empty
    for h in (5, 20, 60):
        if h in ores.models:
            assert live[f"p_outperform_{h}"].between(0, 1).all()
