"""Stage 1: earnings surprise (SUE) and post-earnings-announcement drift.

Follows Bernard and Thomas (1989) in outline:
* Expected EPS is the same quarter last year (seasonal random walk).
* Surprise = actual minus expected, scaled by the standard deviation of the
  company's previous surprises.
* Events are sorted into groups using breakpoints from the previous calendar
  quarter's surprises, so no event's group depends on later events.
* Drift = buy-and-hold return over the following trading days minus the
  equal-weighted universe over the same days.

Per-share history is put on today's share basis using the split record from
the price files, because a first-reported EPS from before a split is not
comparable with one after it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from podbook.tables import asof_merge

from podbook.config import DRIFT_DAYS, EARNINGS_8K_ITEM, N_GROUPS, SUE_HISTORY_QUARTERS, SUE_MIN_HISTORY
from podbook.fundamentals import YEAR_GAP, first_reported, quarterly


def split_factors(prices: pd.DataFrame) -> pd.DataFrame:
    """Split events per company: cik, ex_date, ratio. Yahoo and Tiingo series carry splits; Stooq does not."""
    s = prices[prices["source"].isin(["yahoo", "tiingo"]) & (prices["split_ratio"] > 0) & (prices["split_ratio"] != 1)]
    return s[["cik", "date", "split_ratio"]].rename(columns={"date": "ex_date", "split_ratio": "ratio"})


def eps_on_current_basis(eps_q: pd.DataFrame, splits: pd.DataFrame) -> pd.DataFrame:
    """Divide each EPS by the product of splits after it was filed.

    This rescales a company's whole EPS history by a common factor for splits
    after any given event, which leaves that event's SUE unchanged, so no
    future information enters the surprise.
    """
    out = eps_q.copy()
    factor = np.ones(len(out))
    if not splits.empty:
        merged = out[["cik", "available"]].reset_index().merge(splits, on="cik")
        later = merged[merged["ex_date"] > merged["available"]]
        prod = later.groupby("index")["ratio"].prod()
        factor[prod.index.to_numpy()] = prod.to_numpy()
    out["eps"] = out["val"] / factor
    out["split_adjusted"] = factor != 1
    return out


def compute_sue(
    facts: pd.DataFrame,
    splits: pd.DataFrame,
    history: int = SUE_HISTORY_QUARTERS,
    min_history: int = SUE_MIN_HISTORY,
    with_drift: bool = False,
) -> pd.DataFrame:
    """One row per company quarter: eps, unexpected earnings and SUE.

    `available` is the 10-Q or 10-K filing date that first reported the quarter.
    """
    q = quarterly(first_reported(facts, "eps"))
    q = eps_on_current_basis(q, splits).sort_values(["cik", "end"]).reset_index(drop=True)

    lag = q[["cik", "end", "eps"]].rename(columns={"end": "end_lag4", "eps": "eps_lag4"})
    left = q.assign(_target=q["end"] - pd.Timedelta(days=365)).sort_values("_target")
    q = asof_merge(
        left, lag.sort_values("end_lag4"), left_on="_target", right_on="end_lag4",
        by="cik", direction="nearest", tolerance=pd.Timedelta(days=20),
    ).drop(columns="_target")
    ok = (q["end"] - q["end_lag4"]).dt.days.between(*YEAR_GAP)
    q["ue"] = (q["eps"] - q["eps_lag4"]).where(ok)

    q = q.sort_values(["cik", "end"]).reset_index(drop=True)
    past = q.groupby("cik")["ue"].shift(1)
    roll = past.groupby(q["cik"]).rolling(history, min_periods=min_history)
    q["ue_sd"] = roll.std().reset_index(level=0, drop=True)
    q["ue_mean"] = roll.mean().reset_index(level=0, drop=True)
    q["n_hist"] = roll.count().reset_index(level=0, drop=True)
    centre = q["ue_mean"] if with_drift else 0.0
    q["sue"] = ((q["ue"] - centre) / q["ue_sd"]).where(q["ue_sd"] > 0)
    return q[["cik", "end", "available", "source", "eps", "eps_lag4", "ue", "ue_sd", "n_hist", "sue", "split_adjusted"]]


def announcement_dates(sue: pd.DataFrame, filings: pd.DataFrame) -> pd.DataFrame:
    """Earliest 8-K Item 2.02 filed after the quarter ended and no later than the 10-Q/10-K.

    Falls back to the 10-Q/10-K filing date when there is no such 8-K.
    """
    eightk = filings[(filings["form"] == "8-K") & filings["items"].str.contains(EARNINGS_8K_ITEM, regex=False)]
    eightk = eightk[["cik", "filing_date"]]
    m = sue[["cik", "end", "available"]].merge(eightk, on="cik")
    m = m[(m["filing_date"] > m["end"]) & (m["filing_date"] <= m["available"])]
    first = m.groupby(["cik", "end"], as_index=False)["filing_date"].min().rename(columns={"filing_date": "ann_8k"})
    out = sue.merge(first, on=["cik", "end"], how="left")
    out["ann_date"] = out["ann_8k"].fillna(out["available"])
    out["ann_source"] = np.where(out["ann_8k"].notna(), "8-K 2.02", "10-Q/10-K")
    return out.drop(columns="ann_8k")


def restrict_to_universe(events: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """Keep events whose company was a universe member at the month end before the announcement."""
    members = panel.loc[panel["in_universe"], ["date", "cik", "subsector", "has_price_gap"]]
    ev = events.assign(_me=events["ann_date"].dt.to_period("M").dt.to_timestamp() - pd.Timedelta(days=1))
    out = ev.merge(members, left_on=["_me", "cik"], right_on=["date", "cik"])
    return out.drop(columns=["_me", "date"])


def assign_groups(events: pd.DataFrame, n: int = N_GROUPS, col: str = "sue") -> pd.DataFrame:
    """Group 1 (most negative) to n, using the previous calendar quarter's breakpoints."""
    ev = events.dropna(subset=[col]).copy()
    ev["ann_q"] = ev["ann_date"].dt.to_period("Q")
    probs = np.linspace(0, 1, n + 1)[1:-1]
    bps = ev.groupby("ann_q")[col].quantile(probs).unstack()
    bps.index = bps.index + 1  # breakpoints from quarter t are used in quarter t+1
    ev = ev[ev["ann_q"].isin(bps.index)]
    groups = np.empty(len(ev), dtype=int)
    for i, (q, v) in enumerate(zip(ev["ann_q"], ev[col])):
        groups[i] = int(np.searchsorted(bps.loc[q].to_numpy(), v, side="right")) + 1
    ev["group"] = groups
    return ev.reset_index(drop=True)


def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Wide table (trading day x cik) of total returns from adjusted closes."""
    px = prices.dropna(subset=["adj_close"]).pivot_table(index="date", columns="cik", values="adj_close")
    return px.sort_index().pct_change(fill_method=None)


def benchmark_returns(rets: pd.DataFrame, panel: pd.DataFrame) -> pd.Series:
    """Equal-weighted daily return of last month end's universe members."""
    members = panel[panel["in_universe"]].assign(v=1.0).pivot_table(index="date", columns="cik", values="v")
    members.index = members.index + pd.Timedelta(days=1)  # membership at month end applies from the next day
    mask = members.reindex(rets.index, method="ffill").reindex(columns=rets.columns)
    return rets.where(mask.notna()).mean(axis=1)


def event_bhar(
    events: pd.DataFrame,
    rets: pd.DataFrame,
    bench: pd.Series,
    first_day: int = 1,
    last_day: int = DRIFT_DAYS,
) -> tuple[pd.Series, pd.Series, np.ndarray]:
    """Buy-and-hold abnormal return from trading day first_day to last_day after day 0.

    Day 0 is the first trading day on or after the announcement date. If the
    stock stops trading inside the window, the window is cut at its last
    return and the benchmark is cut at the same point.

    Returns (bhar, days_used, path) where path[i, k] is the cumulative abnormal
    return of event i through window day k.
    """
    days = rets.index
    R = rets.to_numpy()
    B = bench.reindex(days).to_numpy()
    col = {c: i for i, c in enumerate(rets.columns)}
    width = last_day - first_day + 1
    bhar = np.full(len(events), np.nan)
    used = np.zeros(len(events), dtype=int)
    path = np.full((len(events), width), np.nan)
    for i, (cik, ann) in enumerate(zip(events["cik"], events["ann_date"])):
        j = col.get(cik)
        if j is None:
            continue
        d0 = int(np.searchsorted(days.to_numpy(), np.datetime64(ann), side="left"))
        lo, hi = d0 + first_day, min(d0 + last_day + 1, len(days))
        if lo >= hi or lo < 0:
            continue
        r, b = R[lo:hi, j], B[lo:hi]
        valid = ~np.isnan(r)
        if not valid.any():
            continue
        stop = len(r) if valid.all() else int(np.argmin(valid))  # cut at first missing return
        if stop == 0:
            continue
        r, b = r[:stop], np.nan_to_num(b[:stop])
        cum = np.cumprod(1 + r) - np.cumprod(1 + b)
        path[i, :stop] = cum
        bhar[i] = cum[-1]
        used[i] = stop
    return pd.Series(bhar, index=events.index), pd.Series(used, index=events.index), path


def drift_summary(events: pd.DataFrame, n: int = N_GROUPS, col: str = "bhar") -> tuple[pd.DataFrame, dict]:
    """Mean drift by group and the top-minus-bottom spread with a t-stat across quarters."""
    ev = events.dropna(subset=[col])
    by_group = ev.groupby("group")[col].agg(["count", "mean", "median"]).reset_index()
    per_q = ev.groupby(["ann_q", "group"])[col].mean().unstack()
    spread = (per_q.get(n) - per_q.get(1)).dropna() if n in per_q and 1 in per_q else pd.Series(dtype=float)
    stats = {
        "events": int(len(ev)),
        "quarters": int(len(spread)),
        "spread_mean": float(spread.mean()) if len(spread) else None,
        "spread_t": float(spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread)))) if len(spread) > 2 else None,
        "spread_share_positive": float((spread > 0).mean()) if len(spread) else None,
    }
    return by_group, stats


def drift_path(events: pd.DataFrame, path: np.ndarray, first_day: int = 1) -> pd.DataFrame:
    """Mean cumulative abnormal return by group for each day in the window (for the chart)."""
    df = pd.DataFrame(path, index=events.index)
    df.columns = range(first_day, first_day + path.shape[1])
    df["group"] = events["group"].to_numpy()
    return df.groupby("group").mean().T.rename_axis("day")


def _with_returns(ev: pd.DataFrame, rets: pd.DataFrame, bench: pd.Series, first_day: int, last_day: int) -> tuple[pd.DataFrame, np.ndarray]:
    ev = ev.copy()
    ev["bhar"], ev["days_used"], path = event_bhar(ev, rets, bench, first_day, last_day)
    return ev, path


def run_drift(
    facts: pd.DataFrame,
    filings: pd.DataFrame,
    prices: pd.DataFrame,
    panel: pd.DataFrame,
    split_lookback_years: int = 3,
) -> dict:
    """The full Stage 1 run: primary result plus the pre-registered robustness checks.

    Returns a dict with the unrestricted SUE table (used by the monthly factor),
    the primary event table, the drift path, and a summary per variant.
    """
    splits = split_factors(prices)
    rets = daily_returns(prices)
    bench = benchmark_returns(rets, panel)
    yahoo_ciks = set(prices.loc[prices["source"].isin(["yahoo", "tiingo"]), "cik"])

    def prepare(sue_table: pd.DataFrame, use_filing_date: bool = False) -> pd.DataFrame:
        ev = announcement_dates(sue_table, filings)
        if use_filing_date:
            ev["ann_date"], ev["ann_source"] = ev["available"], "10-Q/10-K"
        ev = restrict_to_universe(ev, panel)
        ev["split_data"] = ev["cik"].isin(yahoo_ciks)
        m = ev[["cik", "ann_date"]].reset_index().merge(splits, on="cik")
        recent = m[(m["ex_date"] <= m["ann_date"]) & (m["ex_date"] > m["ann_date"] - pd.DateOffset(years=split_lookback_years))]
        ev["recent_split"] = ev.index.isin(recent["index"])
        return assign_groups(ev)

    sue = compute_sue(facts, splits)
    primary, path = _with_returns(prepare(sue), rets, bench, 1, DRIFT_DAYS)
    primary["bhar_announcement"] = event_bhar(primary, rets, bench, -1, 1)[0]

    variants = {
        "primary": primary,
        "window_days_2_to_61": _with_returns(primary.drop(columns=["bhar", "days_used"]), rets, bench, 2, DRIFT_DAYS + 1)[0],
        "day0_is_10q_filing_date": _with_returns(prepare(sue, use_filing_date=True), rets, bench, 1, DRIFT_DAYS)[0],
        "sue_with_drift_term": _with_returns(prepare(compute_sue(facts, splits, with_drift=True)), rets, bench, 1, DRIFT_DAYS)[0],
        "excluding_recent_splits_and_no_split_data": primary[~primary["recent_split"] & primary["split_data"]],
        "excluding_names_with_price_gaps": primary[~primary["has_price_gap"].astype(bool)],
    }
    summaries = {name: drift_summary(ev)[1] for name, ev in variants.items()}
    # Bernard and Thomas report deciles. Quintiles are the primary here because
    # a sector universe gives few events per decile; deciles are shown for comparison.
    deciles = assign_groups(primary.drop(columns=["group", "ann_q"]), n=10)
    summaries["deciles"] = drift_summary(deciles, n=10)[1]
    by_group, _ = drift_summary(primary)
    ann_by_group = primary.groupby("group")["bhar_announcement"].mean().rename("mean_announcement_return")
    return {
        "sue": sue,
        "events": primary,
        "by_group": by_group.merge(ann_by_group, on="group", how="left"),
        "path": drift_path(primary, path),
        "summaries": summaries,
        "ann_source_share": primary["ann_source"].value_counts(normalize=True).to_dict(),
    }
