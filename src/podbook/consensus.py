"""Weekly Yahoo consensus logger for the coverage list.

Yahoo only shows today's estimates and their 7/30/60/90-day history, so the
only way to get a revisions history is to save a snapshot every week. Output
goes to data/yahoo/consensus/YYYY-MM-DD/, which is git-ignored. Yahoo data
must not be committed.

This is a live overlay only. It cannot be backtested and is never labelled
as if it were.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd

from podbook.mapping import to_yahoo

log = logging.getLogger(__name__)

TABLE_FIELDS = ("eps_trend", "eps_revisions", "earnings_estimate", "revenue_estimate", "growth_estimates")
DICT_FIELDS = ("analyst_price_targets",)


def snapshot_ticker(ticker: str, yf_module: Any) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Every consensus table Yahoo has for one ticker, plus the fields that failed."""
    t = yf_module.Ticker(to_yahoo(ticker))
    tables: dict[str, pd.DataFrame] = {}
    failed: list[str] = []
    for field in TABLE_FIELDS:
        try:
            val = getattr(t, field)
        except Exception as exc:  # yfinance raises on missing modules
            log.debug("%s %s failed: %s", ticker, field, exc)
            val = None
        if isinstance(val, pd.DataFrame) and not val.empty:
            df = val.reset_index()
            df = df.rename(columns={df.columns[0]: "period"})
            df.insert(0, "ticker", ticker)
            tables[field] = df
        else:
            failed.append(field)
    for field in DICT_FIELDS:
        try:
            val = getattr(t, field)
        except Exception:
            val = None
        if isinstance(val, dict) and val:
            tables[field] = pd.DataFrame([{"ticker": ticker, **val}])
        else:
            failed.append(field)
    return tables, failed


def log_consensus(
    coverage_csv: Path,
    out_dir: Path,
    date: object | None = None,
    yf_module: Any | None = None,
    force: bool = False,
    pause: float = 0.5,
) -> Path:
    """Save one dated snapshot for every name on the coverage list."""
    if yf_module is None:
        import yfinance as yf_module
    day = pd.Timestamp(date or pd.Timestamp.today()).date().isoformat()
    target = Path(out_dir) / day
    if target.exists() and not force:
        raise FileExistsError(f"Snapshot for {day} already exists at {target}")
    tickers = pd.read_csv(coverage_csv)["ticker"].astype(str).tolist()

    collected: dict[str, list[pd.DataFrame]] = {f: [] for f in (*TABLE_FIELDS, *DICT_FIELDS)}
    failures: dict[str, list[str]] = {}
    for ticker in tickers:
        tables, failed = snapshot_ticker(ticker, yf_module)
        for field, df in tables.items():
            collected[field].append(df)
        if failed:
            failures[ticker] = failed
        time.sleep(pause)

    target.mkdir(parents=True, exist_ok=True)
    for field, frames in collected.items():
        if frames:
            pd.concat(frames, ignore_index=True).assign(snapshot_date=day).to_csv(target / f"{field}.csv", index=False)
    manifest = {
        "snapshot_date": day,
        "n_tickers": len(tickers),
        "failures": failures,
        "yfinance_version": getattr(yf_module, "__version__", "unknown"),
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2))
    log.info("Saved consensus snapshot for %d tickers to %s (%d with gaps)", len(tickers), target, len(failures))
    return target
