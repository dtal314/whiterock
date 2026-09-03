"""Paths, dates, and constants shared by the pipeline."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
SITE_DIR = ROOT / "site"
SITE_DATA_DIR = SITE_DIR / "data"
MODEL_DIR = DATA_DIR / "models"

for _d in (DATA_DIR, CACHE_DIR, SITE_DATA_DIR, MODEL_DIR):
    _d.mkdir(parents=True, exist_ok=True)

USER_AGENT = os.environ.get(
    "WHITEROCK_USER_AGENT",
    "WhiteRock/0.1 (open-source research tool; public records only)",
)

# History windows. Trades: STOCK Act electronic PTRs are clean from 2023 on.
TRADES_START = date(2023, 1, 1)
# Government actions: Federal Register history for the event study.
ACTIONS_START = date(2019, 1, 1)
# Prices: long enough to cover the action history plus a 60-day tail.
PRICES_START = date(2018, 10, 1)

# Forecast horizons in trading days.
HORIZONS = (5, 20, 60)
# A "related disclosure" counts if it appears within this many calendar days
# after the reference date.
DISCLOSURE_WINDOW_DAYS = 60
# How far back "recent actions" reach on the dashboard.
RECENT_ACTION_DAYS = 45

# Polite crawl pacing (seconds between requests to the same host).
HOUSE_DELAY_S = 0.4
SENATE_DELAY_S = 0.6
FR_DELAY_S = 0.2

HOUSE_FD_ZIP = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PTR_PDF = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{docid}.pdf"
SENATE_BASE = "https://efdsearch.senate.gov"
FR_API = "https://www.federalregister.gov/api/v1"
LEGISLATORS_BASE = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"
