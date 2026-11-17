"""Daily prices from Yahoo (fallback Stooq), with split handling and a gap log.

Market cap needs the price that actually traded on the day, because shares
outstanding from filings are the actual share count at the time. Yahoo's
'Close' is adjusted for later splits, so it is un-split here before use.
Using the split-adjusted close would understate the market cap of every
company that later split, and push them out of the universe.

Stooq gives no split history, so a Stooq series cannot be un-split. It is
kept for returns only and flagged, and its market cap falls back to the
public-float proxy in the universe build.
"""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from podbook.mapping import to_stooq, to_yahoo

log = logging.getLogger(__name__)

PRICE_COLUMNS = ["date", "close_raw", "close", "adj_close", "volume", "split_ratio", "source"]
MAX_INTERNAL_GAP_DAYS = 10  # calendar days between consecutive closes before it counts as a gap


def unadjust_for_splits(close: pd.Series, splits: pd.Series) -> pd.Series:
    """Undo split adjustment on a Yahoo close series.

    splits holds the split ratio on each ex-date (2.0 for 2-for-1, 0.1 for a
    1-for-10 reverse split) and 0 or NaN elsewhere. Before a 2-for-1 split the
    traded price was twice the adjusted price.
    """
    raw = close.astype(float).copy()
    events = splits[(splits.notna()) & (splits > 0) & (splits != 1)]
    for ex_date, ratio in events.items():
        raw.loc[raw.index < ex_date] *= float(ratio)
    return raw


def normalise_yahoo_history(hist: pd.DataFrame) -> pd.DataFrame:
    """Yahoo Ticker.history(auto_adjust=False, actions=True) -> PRICE_COLUMNS."""
    if hist is None or hist.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    h = hist.copy()
    idx = pd.DatetimeIndex(h.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    h.index = idx.normalize()
    splits = h["Stock Splits"] if "Stock Splits" in h.columns else pd.Series(0.0, index=h.index)
    adj = h["Adj Close"] if "Adj Close" in h.columns else pd.Series(float("nan"), index=h.index)
    out = pd.DataFrame(
        {
            "date": h.index,
            "close_raw": unadjust_for_splits(h["Close"], splits).to_numpy(),
            "close": h["Close"].to_numpy(dtype=float),
            "adj_close": adj.to_numpy(dtype=float),
            "volume": h["Volume"].to_numpy(dtype=float) if "Volume" in h.columns else float("nan"),
            "split_ratio": splits.fillna(0).to_numpy(dtype=float),
            "source": "yahoo",
        }
    )
    return out.dropna(subset=["close"]).reset_index(drop=True)


def fetch_yahoo(ticker: str, yf_module: Any | None = None) -> pd.DataFrame:
    if yf_module is None:
        import yfinance as yf_module  # imported lazily so tests never need it
    hist = yf_module.Ticker(to_yahoo(ticker)).history(period="max", auto_adjust=False, actions=True)
    return normalise_yahoo_history(hist)


def fetch_stooq(ticker: str, session: Any | None = None) -> pd.DataFrame:
    session = session or requests.Session()
    url = f"https://stooq.com/q/d/l/?s={to_stooq(ticker)}&i=d"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    text = resp.text.strip()
    if not text or text.lower().startswith("no data") or "Date" not in text.splitlines()[0]:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    df = pd.read_csv(io.StringIO(text), parse_dates=["Date"])
    return pd.DataFrame(
        {
            "date": df["Date"],
            "close_raw": float("nan"),  # no split history, so no traded price
            "close": df["Close"].astype(float),
            "adj_close": df["Close"].astype(float),
            "volume": df.get("Volume", pd.Series(float("nan"), index=df.index)).astype(float),
            "split_ratio": float("nan"),
            "source": "stooq",
        }
    )


def summarise_prices(
    prices: pd.DataFrame,
    expected_from: pd.Timestamp,
    expected_to: pd.Timestamp,
    max_gap_days: int = MAX_INTERNAL_GAP_DAYS,
) -> dict:
    """Describe a price series against the window the company was filing."""
    if prices.empty:
        return {"status": "no_data", "first_price": pd.NaT, "last_price": pd.NaT, "n_obs": 0,
                "max_gap_days": None, "missing_start_days": None, "missing_end_days": None}
    d = pd.to_datetime(prices["date"]).sort_values()
    first, last = d.iloc[0], d.iloc[-1]
    max_gap = int(d.diff().dt.days.max()) if len(d) > 1 else 0
    missing_start = max(0, (first - expected_from).days)
    missing_end = max(0, (expected_to - last).days)
    if last < expected_from or first > expected_to:
        status = "ticker_mismatch"  # prices do not overlap the filing window at all
    elif max_gap > max_gap_days or missing_start > 30 or missing_end > 30:
        status = "partial"
    else:
        status = "complete"
    return {"status": status, "first_price": first, "last_price": last, "n_obs": int(len(d)),
            "max_gap_days": max_gap, "missing_start_days": missing_start, "missing_end_days": missing_end}


def fetch_prices_for_universe(
    companies: pd.DataFrame,
    mapping: pd.DataFrame,
    out_dir: Path,
    log_path: Path,
    analysis_start: str,
    today: pd.Timestamp | None = None,
    yf_module: Any | None = None,
    stooq_session: Any | None = None,
    pause: float = 0.2,
    max_age_days: float | None = 7,
    tiingo_token: str | None = None,
    tiingo_session: Any | None = None,
    tiingo_max_calls: int = 30,
) -> pd.DataFrame:
    """Fetch every candidate's prices, write one CSV per CIK and a gap log."""
    from podbook.mapping import ticker_for

    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manual_ciks = set(mapping.loc[mapping["source"] == "manual", "cik"])
    tiingo_budget = [tiingo_max_calls]
    rows = []
    for c in companies.itertuples(index=False):
        expected_from = max(pd.Timestamp(c.first_periodic), pd.Timestamp(analysis_start))
        # A company taken over stops trading soon after its last 10-Q, so prices
        # are only expected up to that filing, not up to the grace period after it.
        expected_to = min(pd.Timestamp(c.last_periodic), today)
        ticker = ticker_for(mapping, c.cik)
        path = out_dir / f"{c.cik}.csv"
        record = {"cik": c.cik, "name": c.name, "ticker": ticker, "source": None,
                  "expected_from": expected_from, "expected_to": expected_to}
        if c.cik in manual_ciks:
            # Hand-entered tickers belong to delisted companies; Tiingo fills those
            # (podbook fetch-delisted). Yahoo would return whoever uses the ticker now.
            if path.exists():
                prices = pd.read_csv(path, parse_dates=["date"])
                record["source"] = None if prices.empty else prices["source"].iloc[0]
                record.update(summarise_prices(prices, expected_from, expected_to))
            else:
                record.update(status="awaiting_fetch_delisted")
            rows.append(record)
            continue
        if ticker is None:
            record.update(status="no_ticker")
            rows.append(record)
            continue
        if path.exists() and max_age_days is not None and (time.time() - path.stat().st_mtime) / 86400 <= max_age_days:
            prices = pd.read_csv(path, parse_dates=["date"])
        else:
            prices = _fetch_with_fallback(ticker, yf_module, stooq_session)
            expected_days = (expected_to - expected_from).days
            if tiingo_token and len(prices) < 20 and expected_days > 365 and tiingo_budget[0] > 0:
                # Yahoo sometimes returns a day or two for a stock with years of
                # history (Atkore, 17 November 2026). Try Tiingo before giving up.
                try:
                    tiingo_budget[0] -= 1
                    alt = fetch_tiingo(ticker, tiingo_token, tiingo_session)
                    if len(alt) > len(prices):
                        prices = alt
                except TiingoLimitError:
                    tiingo_budget[0] = 0
            valid_to = mapping[(mapping["cik"] == c.cik) & (mapping["ticker"] == ticker)]["valid_to"].max()
            if pd.notna(valid_to) and not prices.empty:
                prices = prices[prices["date"] <= valid_to]
            prices.to_csv(path, index=False)
            time.sleep(pause)
        record["source"] = None if prices.empty else prices["source"].iloc[0]
        record.update(summarise_prices(prices, expected_from, expected_to))
        if record["status"] == "ticker_mismatch":
            path.unlink(missing_ok=True)  # do not let the wrong company's prices into the panel
        rows.append(record)
    gaps = pd.DataFrame(rows)
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    gaps.to_csv(log_path, index=False)
    return gaps


def _fetch_with_fallback(ticker: str, yf_module: Any | None, stooq_session: Any | None) -> pd.DataFrame:
    try:
        prices = fetch_yahoo(ticker, yf_module)
        if not prices.empty:
            return prices
    except Exception as exc:  # yfinance raises a variety of errors for dead symbols
        log.warning("Yahoo failed for %s: %s", ticker, exc)
    try:
        return fetch_stooq(ticker, stooq_session)
    except Exception as exc:
        log.warning("Stooq failed for %s: %s", ticker, exc)
        return pd.DataFrame(columns=PRICE_COLUMNS)


def load_price_panel(prices_dir: Path, ciks: list[str] | None = None) -> pd.DataFrame:
    """All per-company price files stacked into one long table."""
    frames = []
    for path in sorted(Path(prices_dir).glob("*.csv")):
        cik = path.stem
        if ciks is not None and cik not in ciks:
            continue
        df = pd.read_csv(path, parse_dates=["date"])
        if df.empty:
            continue
        df.insert(0, "cik", cik)
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["cik", *PRICE_COLUMNS])
    return pd.concat(frames, ignore_index=True)


TIINGO_PRICES_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"


class TiingoLimitError(RuntimeError):
    """The free plan's hourly or daily request limit was hit."""


def fetch_tiingo(ticker: str, token: str, session: Any | None = None, start: str = "2005-01-01") -> pd.DataFrame:
    """Daily prices from Tiingo, which keeps delisted US stocks.

    Tiingo's close is the price that traded that day, adjClose is adjusted for
    splits and dividends, and splitFactor marks split days, so all three of our
    price columns come straight from it.
    """
    session = session or requests.Session()
    resp = session.get(TIINGO_PRICES_URL.format(ticker=to_yahoo(ticker).lower()),
                       params={"startDate": start, "token": token}, timeout=60)
    if resp.status_code == 404:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    if resp.status_code == 429:
        raise TiingoLimitError("Tiingo request limit reached")
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list) or not data:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    df = pd.DataFrame(data)
    dates = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None).dt.normalize()
    return pd.DataFrame(
        {
            "date": dates,
            "close_raw": df["close"].astype(float),
            "close": df["close"].astype(float),
            "adj_close": df["adjClose"].astype(float),
            "volume": df.get("volume", pd.Series(float("nan"), index=df.index)).astype(float),
            "split_ratio": df.get("splitFactor", pd.Series(1.0, index=df.index)).astype(float),
            "source": "tiingo",
        }
    ).dropna(subset=["close_raw"]).reset_index(drop=True)


def fetch_delisted(
    companies: pd.DataFrame,
    mapping: pd.DataFrame,
    out_dir: Path,
    log_path: Path,
    token: str,
    analysis_start: str,
    session: Any | None = None,
    pause: float = 1.0,
    refresh: bool = False,
) -> pd.DataFrame:
    """Prices from Tiingo for every company with hand-entered tickers.

    Each ticker is fetched and cut to its validity window, so a ticker later
    reused by another company contributes only the old company's prices. A
    result that does not overlap the company's filing window is rejected.
    Safe to rerun: companies already fetched are skipped, so hitting the free
    plan's hourly limit just means running it again later.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manual = mapping[mapping["source"] == "manual"]
    comp = companies.set_index("cik")
    rows = []
    for cik, windows in manual.groupby("cik"):
        path = out_dir / f"{cik}.csv"
        stamp = out_dir / f"{cik}.tiingo.txt"
        # The ticker windows used for the fetch; if they change, the fetch is redone.
        key = "|".join(f"{w.ticker}:{w.valid_from}:{w.valid_to}" for w in windows.itertuples(index=False))
        name = comp.loc[cik, "name"] if cik in comp.index else ""
        record = {"cik": cik, "name": name, "tickers": " ".join(windows["ticker"])}
        if path.exists() and stamp.exists() and stamp.read_text() == key and not refresh:
            record["status"] = "already_fetched"
            rows.append(record)
            continue
        frames = []
        try:
            for w in windows.itertuples(index=False):
                px = fetch_tiingo(w.ticker, token, session)
                if pd.notna(w.valid_from):
                    px = px[px["date"] >= w.valid_from]
                if pd.notna(w.valid_to):
                    px = px[px["date"] <= w.valid_to]
                frames.append(px)
                time.sleep(pause)
        except TiingoLimitError:
            record["status"] = "limit_reached_rerun_later"
            rows.append(record)
            log.warning("Tiingo limit reached. Run podbook fetch-delisted again in an hour; finished names are kept.")
            break
        prices = pd.concat(frames, ignore_index=True).drop_duplicates("date").sort_values("date") if frames else pd.DataFrame(columns=PRICE_COLUMNS)
        if cik in comp.index:
            c = comp.loc[cik]
            exp_from = max(pd.Timestamp(c["first_periodic"]), pd.Timestamp(analysis_start))
            exp_to = pd.Timestamp(c["last_periodic"])
            record.update(summarise_prices(prices, exp_from, exp_to))
        else:
            record["status"] = "not_a_candidate"
        if record["status"] in ("complete", "partial"):
            prices.to_csv(path, index=False)
            stamp.write_text(key)
        rows.append(record)
    out = pd.DataFrame(rows)
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(log_path, index=False)
    return out
