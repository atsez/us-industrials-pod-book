# US Industrials and Aerospace Pod Book

Work in progress. This README will be rewritten once results exist.

The plan is a point-in-time stock screen across US industrials and aerospace and defence, checked by first replicating post-earnings drift (Bernard and Thomas, 1989), then one long and one short from the final ranking combined into a sector-neutral, beta-neutral book that is tracked live.

Free data only. Fundamentals come from SEC EDGAR with filing dates, so every number is known on the date it is used. Prices come from Yahoo, with Stooq as a fallback. No Yahoo data is committed to this repo, only the code that fetches it.

## Status

Data layer complete and HYPOTHESES.md signed off (17 November 2026). No results yet.

## Setup (macOS)

```bash
git clone https://github.com/atsez/us-industrials-pod-book.git ~/code/us-industrials-pod-book
cd ~/code/us-industrials-pod-book
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest

# EDGAR asks for a name and contact email in every request
export SEC_USER_AGENT="Your Name your@email.com"
export TIINGO_API_KEY="your free Tiingo token"   # for delisted companies
```

Put both lines in `~/.zshrc` so they are set in every Terminal window.

Keep the repo outside Documents, Desktop and Downloads. macOS blocks scheduled jobs from reading those folders.

## Updating

One-time setup, so every later update is a single command:

```bash
chmod +x ~/code/us-industrials-pod-book/scripts/update.sh
echo 'alias podbook-update="$HOME/code/us-industrials-pod-book/scripts/update.sh"' >> ~/.zshrc
source ~/.zshrc
```

After that, download the new zip (do not unzip it) and run `podbook-update`. It installs the newest zip in Downloads, runs the tests and the pipeline, then opens the report folders.

## First run

```bash
podbook fetch-edgar        # bulk submissions zip (~1.5 GB) then companyfacts per candidate
podbook fetch-delisted     # Tiingo prices for delisted companies (needs TIINGO_API_KEY)
podbook fetch-prices       # Yahoo, Stooq fallback, writes data/logs/price_gaps.csv
podbook build-universe     # month-end membership panel and data completeness report
podbook review-universe    # data/reports/universe_review.md
```

Then read `data/reports/universe_review.md`, settle the SIC rules in `src/podbook/sic.py`, record them in HYPOTHESES.md, and only then:

```bash
podbook freeze-coverage    # writes reference/coverage_list.csv once
podbook log-consensus      # first Yahoo snapshot
```

## Research steps (after HYPOTHESES.md is signed off)

```bash
podbook research                            # drift replication, factor panel and in-sample backtest
podbook backtest --period out_of_sample     # locked: runs once
```

`podbook research` runs `build-events`, `build-factors` and `backtest` in turn; each can also be run on its own.

Reports go to `data/reports/`. The out-of-sample run writes a lock record to `data/locks/`, and a rerun needs `--reason`, which is logged.

## Weekly consensus logger

```bash
sed "s|REPO_PATH|$PWD|" scripts/com.podbook.consensus.plist > ~/Library/LaunchAgents/com.podbook.consensus.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.podbook.consensus.plist
launchctl kickstart gui/$(id -u)/com.podbook.consensus   # optional test run now
```

Runs every Saturday at 09:00, or on wake if the Mac was asleep. Snapshots go to `data/yahoo/consensus/YYYY-MM-DD/` and the log to `data/logs/consensus.log`.

## Universe rules

* US-listed companies filing 10-K and 10-Q whose EDGAR SIC code is in the aerospace and defence, machinery or electrical equipment lists in `src/podbook/sic.py`.
* Market cap of at least 1 billion dollars at each month end, from the latest share count filed on or before that date times the price traded that day (Yahoo's split-adjusted close is un-split first).
* Companies that were later acquired or delisted stay in for the months they traded. Candidates come from EDGAR's bulk submissions file, which includes every company that ever filed, not from today's ticker list.
* Where a listed company has no price or no share count, it is kept as a gap and sized by the public float in its 10-K, so the report can state what share of the true universe has complete data.

## Known limits

* EDGAR gives only a company's current SIC code, not its history.
* SIC has no conglomerate code; conglomerates are classified by their main business.
* Yahoo rarely has prices for delisted companies. The size of that gap is measured and reported, not hidden.
* XBRL fundamentals start in 2009, which sets the earliest usable date.
* Consensus revisions are a live overlay from weekly snapshots. They are not backtested.

## Tests

`pytest` runs on synthetic data with no network access. The mandatory checks are that no value filed after a rebalance date is used, that the CIK to ticker mapping has no overlapping windows (including reused tickers), and that delisted companies stay in the universe while they were listed.
