"""Daily adjusted closes for the universe and its benchmarks (yfinance, free)."""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from .. import config
from ..mapping.universe import ALL_BENCHMARKS, ALL_TICKERS

log = logging.getLogger(__name__)

CACHE = config.DATA_DIR / "prices.csv.gz"


def load() -> pd.DataFrame:
    if not CACHE.exists():
        return pd.DataFrame()
    df = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    df.index.name = "date"
    return df.sort_index()


def update(start: date | None = None) -> pd.DataFrame:
    import yfinance as yf  # imported lazily: optional at test time

    existing = load()
    symbols = sorted(set(ALL_TICKERS) | set(ALL_BENCHMARKS))
    if start is None:
        if not existing.empty:
            start = (existing.index.max() - timedelta(days=7)).date()
        else:
            start = config.PRICES_START
    log.info("Prices: downloading %d symbols from %s", len(symbols), start)
    raw = yf.download(symbols, start=start.isoformat(), auto_adjust=True, progress=False,
                      group_by="column", threads=True)
    if raw is None or raw.empty:
        log.warning("Prices: download returned nothing; keeping cache")
        return existing
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].rename(columns={"Close": symbols[0]})
    close = close.copy()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    close.index.name = "date"
    merged = close if existing.empty else pd.concat([existing[~existing.index.isin(close.index)], close]).sort_index()
    merged = merged.dropna(how="all")
    merged.to_csv(CACHE, float_format="%.4f")
    log.info("Prices: %d rows x %d symbols, through %s", len(merged), merged.shape[1], merged.index.max().date())
    return merged
