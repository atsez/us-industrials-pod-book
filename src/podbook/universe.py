"""Point-in-time universe membership, completeness reporting and the coverage list.

At each month end a company is in the universe if
* its SIC code is in an included range (podbook.sic),
* it was listed on that date, and
* shares outstanding (latest filing on or before the date) times the traded
  close on the date is at least MCAP_MIN_USD.

Listing is judged from prices where we have them and from the filing record
where we do not, so companies that were later acquired or delisted stay in the
universe for the months they were listed. Where a listed company has no price
or no share count, its row is kept and classed as a gap. The public float from
its 10-K (reported once a year) tells us whether that gap was a large company,
so we can say what share of the true universe has complete data.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from podbook.tables import asof_merge

from podbook.config import (
    FLOAT_MAX_ASSET_MULTIPLE,
    FLOAT_STALENESS_DAYS,
    MCAP_MAX_ASSET_MULTIPLE,
    LISTING_GRACE_DAYS,
    MCAP_MIN_USD,
    PRICE_STALENESS_DAYS,
    SHARES_STALENESS_DAYS,
)
from podbook.edgar.facts import PUBLIC_FLOAT_TAGS, SHARE_COUNT_TAGS, latest_known, share_count
from podbook.mapping import ticker_for

PANEL_COLUMNS = [
    "date", "cik", "name", "sic", "subsector", "close_raw", "price_date", "shares", "shares_filed",
    "mcap", "public_float", "status", "gap_reason", "in_universe", "has_price_gap",
]


def rebalance_dates(start: object, end: object) -> pd.DatetimeIndex:
    """Calendar month ends from start to end inclusive."""
    return pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="ME")


def _listing_windows(companies: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """listed_from / listed_to per company.

    With prices: from the first price to the later of the last price and the
    last 10-K/10-Q (a series that stops while the company is still filing is a
    data gap, not a delisting). Without prices: the filing window plus a grace
    period, since we cannot see when trading stopped.
    """
    span = prices.groupby("cik")["date"].agg(first_price="min", last_price="max").reset_index()
    w = companies[["cik", "first_periodic", "last_periodic"]].merge(span, on="cik", how="left")
    for col in ("first_price", "last_price"):
        w[col] = pd.to_datetime(w[col])
    grace = pd.Timedelta(days=LISTING_GRACE_DAYS)
    priced = w["first_price"].notna()
    w["listed_from"] = w["first_price"].where(priced, w["first_periodic"])
    # The staleness allowance covers month ends that fall on a weekend or holiday.
    last_trade = w["last_price"] + pd.Timedelta(days=PRICE_STALENESS_DAYS)
    w["listed_to"] = pd.concat([last_trade, w["last_periodic"]], axis=1).max(axis=1).where(
        priced, w["last_periodic"] + grace
    )
    return w[["cik", "listed_from", "listed_to"]]


def build_universe(
    companies: pd.DataFrame,
    facts: pd.DataFrame,
    prices: pd.DataFrame,
    dates: pd.DatetimeIndex,
    price_gaps: pd.DataFrame | None = None,
    mcap_min: float = MCAP_MIN_USD,
    extra_ciks: set[str] | None = None,
) -> pd.DataFrame:
    """Membership panel: one row per (rebalance date, listed company in an included SIC range).

    extra_ciks adds named companies regardless of SIC. It is used only to size
    coverage-list additions today, never for the backtest universe.
    """
    eligible = companies["in_universe_sic"].astype(bool)
    if extra_ciks:
        eligible |= companies["cik"].isin(extra_ciks)
    comp = companies[eligible][["cik", "name", "sic", "subsector", "first_periodic", "last_periodic"]]
    prices = prices[prices["cik"].isin(comp["cik"])]
    windows = _listing_windows(comp, prices)

    grid = comp.merge(pd.DataFrame({"date": pd.DatetimeIndex(dates)}), how="cross").merge(windows, on="cik")
    grid = grid[(grid["date"] >= grid["listed_from"]) & (grid["date"] <= grid["listed_to"])]
    if grid.empty:
        return pd.DataFrame(columns=PANEL_COLUMNS)

    # Traded close on or just before the date, never after it.
    px = prices.dropna(subset=["close_raw"])[["cik", "date", "close_raw"]].rename(columns={"date": "price_date"})
    grid = asof_merge(
        grid.sort_values("date"),
        px.sort_values("price_date"),
        left_on="date",
        right_on="price_date",
        by="cik",
        direction="backward",
        tolerance=pd.Timedelta(days=PRICE_STALENESS_DAYS),
    )

    share_facts = facts[facts["tag"].isin(SHARE_COUNT_TAGS) & (facts["unit"] == "shares")]
    float_facts = facts[facts["tag"].isin(PUBLIC_FLOAT_TAGS) & (facts["unit"] == "USD")]
    asset_facts = facts[(facts["tag"] == "Assets") & (facts["unit"] == "USD")]
    pit = []
    for d in grid["date"].drop_duplicates():
        s = share_count(share_facts, d, SHARES_STALENESS_DAYS)[["cik", "shares", "shares_filed"]]
        f = latest_known(float_facts, d, PUBLIC_FLOAT_TAGS, FLOAT_STALENESS_DAYS).rename(
            columns={"val": "public_float"}
        )[["cik", "public_float"]]
        a = latest_known(asset_facts, d, ("Assets",), FLOAT_STALENESS_DAYS).rename(
            columns={"val": "_assets"}
        )[["cik", "_assets"]]
        f = a.merge(f, on="cik", how="left")
        # A float with no balance sheet to check it against, or far above total
        # assets, is not trusted (XBRL scaling errors are common in this tag).
        bad = f["_assets"].isna() | (f["public_float"] > FLOAT_MAX_ASSET_MULTIPLE * f["_assets"])
        f.loc[bad, "public_float"] = float("nan")
        pit.append(s.merge(f, on="cik", how="outer").assign(date=d))
    pit_df = pd.concat(pit, ignore_index=True) if pit else pd.DataFrame(
        columns=["cik", "shares", "shares_filed", "public_float", "_assets", "date"]
    )
    grid = grid.merge(pit_df, on=["cik", "date"], how="left")
    # Months with no facts at all produce empty frames, which turn these columns
    # into generic objects when concatenated. Force them back to numbers.
    for col in ("close_raw", "shares", "public_float", "_assets"):
        grid[col] = pd.to_numeric(grid[col], errors="coerce").astype(float)

    grid["mcap"] = grid["shares"] * grid["close_raw"]
    implausible = grid["mcap"] > MCAP_MAX_ASSET_MULTIPLE * grid["_assets"]
    grid.loc[implausible, "mcap"] = float("nan")
    grid["bad_share_count"] = implausible
    has_mcap = grid["mcap"].notna()
    large_float = grid["public_float"] >= mcap_min
    grid["status"] = "gap_unknown"
    grid.loc[has_mcap & (grid["mcap"] >= mcap_min), "status"] = "member"
    grid.loc[has_mcap & (grid["mcap"] < mcap_min), "status"] = "small"
    grid.loc[~has_mcap & large_float, "status"] = "gap_large"
    grid["gap_reason"] = ""
    grid.loc[~has_mcap & grid["close_raw"].isna(), "gap_reason"] = "no_price"
    grid.loc[~has_mcap & grid["close_raw"].notna(), "gap_reason"] = "no_shares"
    grid.loc[grid["bad_share_count"], "gap_reason"] = "bad_share_count"
    grid["in_universe"] = grid["status"].eq("member")

    gap_ciks: set[str] = set()
    if price_gaps is not None and not price_gaps.empty:
        gap_ciks = set(price_gaps.loc[price_gaps["status"] != "complete", "cik"])
    grid["has_price_gap"] = grid["cik"].isin(gap_ciks)

    return grid[PANEL_COLUMNS].sort_values(["date", "cik"]).reset_index(drop=True)


def completeness_report(panel: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Share of the (estimated) true universe that has complete data, by date and overall.

    Denominator: members plus listed companies we could not size but whose
    public float alone was above the threshold. Gaps whose float is unknown or
    small are reported separately; we cannot say whether they belonged.
    """
    g = panel.groupby("date")
    by_date = pd.DataFrame(
        {
            "members": g["status"].apply(lambda s: (s == "member").sum()),
            "gap_large": g["status"].apply(lambda s: (s == "gap_large").sum()),
            "gap_unknown": g["status"].apply(lambda s: (s == "gap_unknown").sum()),
            "members_with_price_gap": g.apply(
                lambda x: (x["in_universe"] & x["has_price_gap"]).sum(), include_groups=False
            ),
        }
    )
    denom = by_date["members"] + by_date["gap_large"]
    by_date["share_complete"] = by_date["members"] / denom.where(denom > 0)
    totals = {
        "member_months": int(by_date["members"].sum()),
        "gap_large_months": int(by_date["gap_large"].sum()),
        "gap_unknown_months": int(by_date["gap_unknown"].sum()),
        "share_complete": float(by_date["members"].sum() / max(1, denom.sum())),
        "share_members_with_price_gap": float(
            by_date["members_with_price_gap"].sum() / max(1, by_date["members"].sum())
        ),
    }
    member_ciks = set(panel.loc[panel["in_universe"], "cik"])
    last_seen = panel.groupby("cik")["date"].max()
    gone = set(last_seen[last_seen < panel["date"].max()].index)
    totals["distinct_members"] = len(member_ciks)
    totals["distinct_members_no_longer_listed"] = len(member_ciks & gone)
    return by_date.reset_index(), totals


def select_coverage(
    panel: pd.DataFrame, mapping: pd.DataFrame, n: int, exclude_tickers: set[str] | None = None
) -> pd.DataFrame:
    """The n largest members on the panel's latest date, with current tickers."""
    latest = panel[(panel["date"] == panel["date"].max()) & panel["in_universe"]]
    latest = latest.sort_values("mcap", ascending=False).copy()
    latest["ticker"] = [ticker_for(mapping, c) for c in latest["cik"]]
    latest = latest[latest["ticker"].notna() & ~latest["ticker"].isin(exclude_tickers or set())].head(n)
    out = latest[["cik", "ticker", "name", "sic", "subsector"]].reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out


def freeze_coverage(coverage: pd.DataFrame, path: Path, frozen_on: object, force: bool = False) -> Path:
    """Write the coverage list once. It is fixed at the start of the project."""
    path = Path(path)
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists. The coverage list is frozen; pass force=True to rebuild it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    coverage.assign(frozen_on=pd.Timestamp(frozen_on).date().isoformat()).to_csv(path, index=False)
    return path


def gap_report(panel: pd.DataFrame) -> pd.DataFrame:
    """Every company that was listed and probably large but could not be sized.

    One row per company, largest first. This is the list of names the backtest
    is missing, which drives the survivorship discussion in the note.
    """
    gaps = panel[panel["status"] == "gap_large"]
    if gaps.empty:
        return pd.DataFrame(columns=["cik", "name", "sic", "subsector", "gap_months", "first_gap", "last_gap",
                                     "main_reason", "max_public_float", "listed_at_panel_end"])
    last_date = panel["date"].max()
    last_seen = panel.groupby("cik")["date"].max()
    out = gaps.groupby("cik").agg(
        name=("name", "first"), sic=("sic", "first"), subsector=("subsector", "first"),
        gap_months=("date", "size"), first_gap=("date", "min"), last_gap=("date", "max"),
        main_reason=("gap_reason", lambda r: r.value_counts().index[0]),
        max_public_float=("public_float", "max"),
    ).reset_index()
    out["listed_at_panel_end"] = out["cik"].map(last_seen).eq(last_date)
    return out.sort_values("max_public_float", ascending=False).reset_index(drop=True)


def mcap_anomalies(panel: pd.DataFrame, low: float = 0.2, high: float = 20.0) -> pd.DataFrame:
    """Members whose market cap is implausible against their own public float.

    Float is normally a little below market cap. It is measured once a year, so
    a price fall since then can push the ratio well under 1, and a controlled
    company can sit well above it. Far outside [low, high] usually means a
    mis-scaled share count or a wrong price series.
    """
    m = panel[panel["in_universe"]].copy()
    m["public_float"] = pd.to_numeric(m["public_float"], errors="coerce").astype(float)
    m = m[m["public_float"] > 0]
    m["mcap_to_float"] = m["mcap"].astype(float) / m["public_float"]
    odd = m[(m["mcap_to_float"] < low) | (m["mcap_to_float"] > high)]
    return (
        odd.groupby("cik")
        .agg(name=("name", "first"), months=("date", "size"), median_ratio=("mcap_to_float", "median"),
             max_mcap=("mcap", "max"))
        .reset_index()
        .sort_values("months", ascending=False)
    )
