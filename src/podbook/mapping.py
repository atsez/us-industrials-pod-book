"""CIK to ticker mapping with validity windows.

Sources, highest priority first:
1. reference/ticker_overrides.csv: hand-entered history with a source note
   (e.g. an old ticker read from a 10-K cover page).
2. SEC company_tickers_exchange.json: tickers trading today.
3. The submissions record of a delisted company: its last known ticker.

Two traps this module handles and tests:
* Ticker reuse. A delisted company's ticker can later be given to another
  company. Windows must not overlap, or a price fetch returns the wrong stock.
* Stale last-known tickers that collide with a ticker trading today under a
  different CIK. These are dropped and logged rather than guessed.
"""

from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

MAP_COLUMNS = ["cik", "ticker", "exchange", "valid_from", "valid_to", "source", "is_primary"]
MAJOR_EXCHANGES = {"NYSE", "NASDAQ", "NYSE AMERICAN", "NYSE MKT", "NYSE ARCA", "CBOE"}
SOURCE_RANK = {"manual": 0, "sec_current": 1, "sec_submissions": 2}


class MappingError(ValueError):
    pass


def normalise_cik(value: object) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit() or len(text) > 10:
        raise MappingError(f"Not a CIK: {value!r}")
    return text.zfill(10)


def normalise_ticker(value: object) -> str:
    return str(value).strip().upper()


def to_yahoo(ticker: str) -> str:
    """SEC 'BRK.B' or 'BRK-B' becomes Yahoo 'BRK-B'."""
    return normalise_ticker(ticker).replace(".", "-").replace("/", "-")


def to_stooq(ticker: str) -> str:
    return to_yahoo(ticker).lower() + ".us"


def choose_primary(tickers: list[str], exchanges: list[str] | None = None) -> str | None:
    """Pick the common-stock ticker from a company's list.

    Preference: no '-' suffix (preferreds, warrants, units usually carry one),
    then a major exchange, then SEC's own order.
    """
    if not tickers:
        return None
    exchanges = list(exchanges or [])
    exchanges += [""] * (len(tickers) - len(exchanges))

    def score(i: int) -> tuple[int, int, int]:
        t, ex = normalise_ticker(tickers[i]), str(exchanges[i] or "").upper()
        return ("-" in t or "." in t, ex not in MAJOR_EXCHANGES, i)

    best = min(range(len(tickers)), key=score)
    return normalise_ticker(tickers[best])


def parse_tickers_exchange(js: dict) -> pd.DataFrame:
    """SEC company_tickers_exchange.json -> cik, name, ticker, exchange."""
    df = pd.DataFrame(js["data"], columns=js["fields"])
    df["cik"] = df["cik"].map(normalise_cik)
    df["ticker"] = df["ticker"].map(normalise_ticker)
    df["exchange"] = df["exchange"].fillna("").astype(str)
    return df[["cik", "name", "ticker", "exchange"]]


def _rows_for(cik: str, tickers: list[str], exchanges: list[str], source: str, valid_to=pd.NaT) -> list[dict]:
    primary = choose_primary(tickers, exchanges)
    exchanges = list(exchanges) + [""] * (len(tickers) - len(exchanges))
    return [
        {
            "cik": cik,
            "ticker": normalise_ticker(t),
            "exchange": ex or "",
            "valid_from": pd.NaT,
            "valid_to": valid_to,
            "source": source,
            "is_primary": normalise_ticker(t) == primary,
        }
        for t, ex in zip(tickers, exchanges)
    ]


def build_mapping(
    current: pd.DataFrame,
    companies: pd.DataFrame,
    overrides: pd.DataFrame | None = None,
    grace_days: int = 120,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the mapping for candidate companies. Returns (mapping, conflicts)."""
    ciks = set(companies["cik"])
    rows: list[dict] = []

    last_filing = companies.set_index("cik")["last_periodic"].to_dict()
    if overrides is not None and not overrides.empty:
        for r in overrides.itertuples(index=False):
            cik = normalise_cik(r.cik)
            valid_to = pd.to_datetime(r.valid_to)
            if pd.isna(valid_to) and pd.notna(last_filing.get(cik)):
                # No end date given: the ticker is taken to run until the company's
                # last 10-K or 10-Q plus the listing grace period.
                valid_to = pd.Timestamp(last_filing[cik]) + pd.Timedelta(days=grace_days)
            rows.append(
                {
                    "cik": cik,
                    "ticker": normalise_ticker(r.ticker),
                    "exchange": "",
                    "valid_from": pd.to_datetime(r.valid_from),
                    "valid_to": valid_to,
                    "source": "manual",
                    "is_primary": True,
                }
            )

    cur = current[current["cik"].isin(ciks)]
    for cik, grp in cur.groupby("cik", sort=False):
        rows += _rows_for(cik, list(grp["ticker"]), list(grp["exchange"]), "sec_current")

    current_ciks = set(cur["cik"])
    for c in companies.itertuples(index=False):
        if c.cik in current_ciks or not c.tickers:
            continue
        valid_to = pd.Timestamp(c.last_periodic) + pd.Timedelta(days=grace_days)
        rows += _rows_for(c.cik, list(c.tickers), list(c.exchanges), "sec_submissions", valid_to)

    mapping = pd.DataFrame(rows, columns=MAP_COLUMNS)
    mapping, conflicts = _resolve(mapping)
    validate_mapping(mapping)
    return mapping.sort_values(["cik", "valid_from"], na_position="first").reset_index(drop=True), conflicts


def _resolve(mapping: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    mapping = mapping.copy()
    conflicts = []

    # A stale last-known ticker that another CIK trades under today is dropped.
    current_owner = mapping[mapping["source"] == "sec_current"].set_index("ticker")["cik"].to_dict()
    stale = (mapping["source"] == "sec_submissions") & mapping["ticker"].map(
        lambda t: t in current_owner
    ) & (mapping["ticker"].map(current_owner) != mapping["cik"])
    for r in mapping[stale].itertuples(index=False):
        conflicts.append({"cik": r.cik, "ticker": r.ticker, "reason": f"ticker now used by CIK {current_owner[r.ticker]}"})
    mapping = mapping[~stale]

    # Hand-entered history replaces the last-known ticker for that company.
    manual_ciks = set(mapping.loc[mapping["source"] == "manual", "cik"])
    mapping = mapping[~((mapping["source"] == "sec_submissions") & mapping["cik"].isin(manual_ciks))]

    # Where a closed window exists (manual history), open-start rows for the same
    # ticker or the same company's primary line begin the day after it closes.
    closed = mapping[mapping["valid_to"].notna() & (mapping["source"] == "manual")]
    for idx, r in mapping.iterrows():
        if pd.notna(r["valid_from"]) or r["source"] == "manual":
            continue
        same_ticker = closed[(closed["ticker"] == r["ticker"]) & (closed["cik"] != r["cik"])]
        same_company = closed[(closed["cik"] == r["cik"]) & closed["is_primary"]] if r["is_primary"] else closed.iloc[0:0]
        ends = pd.concat([same_ticker["valid_to"], same_company["valid_to"]])
        if not ends.empty:
            mapping.at[idx, "valid_from"] = ends.max() + pd.Timedelta(days=1)
    return mapping.reset_index(drop=True), pd.DataFrame(conflicts, columns=["cik", "ticker", "reason"])


def _overlaps(a_from, a_to, b_from, b_to) -> bool:
    lo = pd.Timestamp.min
    hi = pd.Timestamp.max
    a_from = lo if pd.isna(a_from) else a_from
    b_from = lo if pd.isna(b_from) else b_from
    a_to = hi if pd.isna(a_to) else a_to
    b_to = hi if pd.isna(b_to) else b_to
    return a_from <= b_to and b_from <= a_to


def validate_mapping(mapping: pd.DataFrame) -> None:
    """Raise MappingError if the mapping is ambiguous at any date."""
    for cik in mapping["cik"]:
        normalise_cik(cik)
        if len(str(cik)) != 10:
            raise MappingError(f"CIK not zero-padded: {cik}")

    def check(groups, label):
        for key, grp in groups:
            recs = list(grp.itertuples(index=False))
            for i in range(len(recs)):
                for j in range(i + 1, len(recs)):
                    a, b = recs[i], recs[j]
                    if label == "ticker" and a.cik == b.cik:
                        continue
                    if _overlaps(a.valid_from, a.valid_to, b.valid_from, b.valid_to):
                        raise MappingError(f"Overlapping {label} windows for {key}: {a} / {b}")

    check(mapping.groupby("ticker"), "ticker")
    check(mapping[mapping["is_primary"]].groupby("cik"), "primary")


def ticker_for(mapping: pd.DataFrame, cik: str, date: object | None = None) -> str | None:
    """Primary ticker for a company at a date, or its most recent one if date is None."""
    rows = mapping[(mapping["cik"] == normalise_cik(cik)) & mapping["is_primary"]]
    if rows.empty:
        return None
    if date is None:
        # Open-ended windows (still trading) come first, then the latest end date.
        rows = rows.assign(_open=rows["valid_to"].isna(), _rank=rows["source"].map(SOURCE_RANK))
        return rows.sort_values(["_open", "valid_to", "_rank"], ascending=[False, False, True]).iloc[0]["ticker"]
    d = pd.Timestamp(date)
    ok = rows[(rows["valid_from"].isna() | (rows["valid_from"] <= d)) & (rows["valid_to"].isna() | (rows["valid_to"] >= d))]
    return None if ok.empty else ok.iloc[0]["ticker"]
