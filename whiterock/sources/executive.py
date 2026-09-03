"""Executive branch (President, cabinet, senior officials).

Executive officials file OGE Form 278e (annual) and 278-T (periodic
transaction reports) with the Office of Government Ethics. OGE publishes them
as PDFs behind a search form without a stable bulk feed, so this MVP does not
crawl them automatically. Instead it reads a hand-maintained public-record
ledger at data/executive_transactions.jsonl, where each row must carry a
source_url pointing at the public filing it was copied from.

Rows without a source_url are dropped: nothing about executive officials is
ever inferred or invented by this system.
"""
from __future__ import annotations

import logging

from .. import config
from ..util import read_jsonl

log = logging.getLogger(__name__)

LEDGER = config.DATA_DIR / "executive_transactions.jsonl"

REQUIRED = ("filer_name", "role", "ticker", "tx_type", "tx_date", "source_url")


def load() -> list[dict]:
    rows = read_jsonl(LEDGER)
    good = []
    for r in rows:
        if all(r.get(k) for k in REQUIRED):
            r.setdefault("chamber", "executive")
            r.setdefault("owner", "self")
            r.setdefault("source", "oge_278_manual")
            good.append(r)
        else:
            log.warning("executive ledger row dropped (missing fields): %s", r)
    return good
