# WhiteRock: project brief for Claude sessions

Dan's personal political-market intelligence tool (started 2026-09-03 as "Capitol Signal",
renamed WhiteRock the same day). Built as a public static site on GitHub Pages so a second
person can use it, with a daily GitHub Actions data pipeline. Free Python only, no keys.

## Laws that apply here

- No spending: every source is keyless and free; Actions runs on the free tier. Never add a
  paid API without Dan's explicit go.
- Security baseline: no secrets anywhere in this tree (it is a PUBLIC repo inside Dropbox).
  A future Congress.gov key goes in a GitHub Actions secret, never a file.
- Never claim the system sees undisclosed trades. Every probability is a forecast of a
  FUTURE PUBLIC DISCLOSURE. Keep the disclaimer on the site.
- No em-dashes in any copy (site, README, commit messages).
- Test incrementally; Dan gates each slice by what he sees on the live URL.
- The logo (black rock on white) came from the free Codex image lane
  (`tools/make_logo.py`); `tools/make_icons.py` derives the shipped sizes.

## Recommendations (Dan's 2026-09-03 additions)

Dan asked for "a simple page of recommended buys based on the pattern of congress people
buying stocks", then "track the recommendations, how they perform, when you recommended
it, amount to buy", and "call it recommendations". Implemented as `whiterock/signals.py`
(ranking) + `whiterock/recommendations.py` (ledger in `data/recommendations.json`,
$10,000 notional, conviction tiers, 60-trading-day hold, marked to benchmark daily).
The ledger is append-only in practice: never delete or rewrite past entries.

## Layout

- `whiterock/` package (sources, mapping, features, models, build_site, run_pipeline)
- `site/` static dashboard; `site/data/*.json` is written by the pipeline
- `data/` committed caches (jsonl.gz, indexes, prices.csv.gz); `data/cache/` ignored
- `_NO_SYNC/` Dropbox-ignored scratch (logs); `tests/` pytest

## Commands

```
python -m pytest -q
python -m whiterock.run_pipeline --update          # full incremental run
python -m whiterock.run_pipeline --no-fetch        # rebuild site from caches
python -m http.server 8484 --directory site        # local preview
```

Use Python 3.14 on this PC (`%LOCALAPPDATA%\Programs\Python\Python314\python.exe`);
bare `python` may resolve to a 3.12 install without the dependencies.

## Deployment

GitHub repo `dtal314/whiterock` (public; Pages cannot serve a private repo on the free
plan). Workflow `.github/workflows/pipeline.yml`: weekday cron + manual dispatch + push to
main. It runs tests, updates data, commits refreshed data with `[skip ci]`, and deploys
`site/` to Pages.

## Open items

See BACKLOG.md.
