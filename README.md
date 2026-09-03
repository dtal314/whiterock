# WhiteRock

Political-market intelligence from lawful public records.

WhiteRock links emerging U.S. government actions to the publicly traded companies and
sectors they plausibly touch, shows which members of Congress have historically traded
those companies, and estimates two things:

1. the probability that a related purchase, sale, or no trade will be **publicly disclosed
   later** (next 60 days), and
2. the probability that each affected stock will **beat its sector benchmark** over the next
   5, 20 and 60 trading days.

Everything is a forecast about future public disclosures and future prices built only from
data that is already public. WhiteRock cannot see a trade before it is disclosed, holds no
nonpublic information, and is not investment advice.

## Live site

The dashboard is a static site published by GitHub Pages. A GitHub Actions job refreshes
the data every weekday morning, retrains both models, commits the refreshed data, and
redeploys. No server, no keys, no paid services.

## Data sources (all public, all keyless)

| Source | What it gives | Where |
| --- | --- | --- |
| Federal Register API | final rules, proposed rules, presidential documents since 2019, plus the public-inspection desk (forthcoming items) | federalregister.gov/developers |
| U.S. House Clerk | periodic transaction reports (STOCK Act) as PDFs, parsed from the text layer | disclosures-clerk.house.gov |
| U.S. Senate eFD | periodic transaction reports as HTML tables | efdsearch.senate.gov |
| congress-legislators | member roster, party, state, committee seats | github.com/unitedstates/congress-legislators |
| yfinance | daily adjusted closes for the ticker universe and benchmark ETFs | github.com/ranaroussi/yfinance |
| OGE Forms 278e / 278-T | executive-branch holdings and transactions (manual public-record ledger only in this version) | oge.gov |

Paper-filed congressional reports are scanned images. They are counted but never parsed
or guessed.

## How it works

```
sources/          fetch + parse each public record into data/*.jsonl.gz
mapping/universe  21 sectors, ~120 tickers, benchmark ETFs, agency + keyword + direction rules
features          transactions table, action-to-sector links, sector-day intensity
models/disclosure gradient-boosted multinomial model on (member, sector, month) panel
models/outperformance  logistic model per horizon on (sector-day event, ticker) rows
build_site        writes site/data/*.json for the dashboard
run_pipeline      orchestrates all of the above
site/             static dashboard (plain HTML, CSS, JS)
```

Both models are evaluated on a strict time-based holdout and the holdout numbers (AUC,
Brier score, log loss, calibration tables, base rates) are printed on the dashboard's
"Model and data" tab next to every probability.

## Run it locally

Python 3.12 or newer.

```bash
pip install -r requirements.txt
python -m pytest -q
python -m whiterock.run_pipeline --update        # fetch, train, score, write site/data
python -m http.server 8484 --directory site      # then open http://127.0.0.1:8484
```

`--update --max-new 20` limits new filings per chamber for a quick smoke test.
`--no-fetch` rebuilds from the committed caches without touching the network.

## Executive-branch ledger

`data/executive_transactions.jsonl` accepts one JSON object per line copied from a public
OGE filing. Required keys: `filer_name`, `role`, `ticker`, `tx_type` (purchase or sale),
`tx_date`, `source_url`. Rows without a source URL are dropped.

## Legal and ethical position

All inputs are public records that Congress and the executive branch publish by law
(STOCK Act, Ethics in Government Act) or that the Government Publishing Office releases in
the public domain. The tool forecasts the likelihood of a future public disclosure; it does
not and cannot detect trades that have not been disclosed. Politicians are public figures
named in their own filings; spouses and dependents appear only as owner categories, never by
name.
