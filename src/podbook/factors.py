"""Stage 2: monthly factor measures and scores, all point in time.

At each month end d, only filings made on or before d minus REPORTING_LAG_DAYS
are visible. Every merged table leaves a *_available column, and the panel
records info_date, the latest filing date it used, so the look-ahead rule can
be checked row by row.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from podbook.config import FUNDAMENTAL_MAX_AGE_DAYS, MIN_FACTORS, REPORTING_LAG_DAYS, SUE_MAX_AGE_DAYS
from podbook.fundamentals import pit_merge

# measure -> (factor, direction). Direction +1 means higher is better.
MEASURES: dict[str, tuple[str, int]] = {
    "sue": ("surprise", +1),
    "d_op_margin": ("fund_momentum", +1),
    "d_roic": ("fund_momentum", +1),
    "sales_growth": ("fund_momentum", +1),
    "roic": ("quality", +1),
    "leverage": ("quality", -1),
    "interest_cover": ("quality", +1),
    "accruals": ("earnings_quality", -1),
    "ebit_yield": ("valuation", +1),
    "fcf_yield": ("valuation", +1),
}
FACTORS = ("surprise", "fund_momentum", "quality", "earnings_quality", "valuation")


def _safe_div(a: pd.Series, b: pd.Series, positive_denominator: bool = True) -> pd.Series:
    ok = b > 0 if positive_denominator else b != 0
    return (a / b).where(ok)


def _debt(df: pd.DataFrame, suffix: str = "") -> pd.Series:
    """Total debt: long-term debt (with current portion) plus short-term borrowings and commercial paper.

    Uses LongTermDebt where reported, otherwise non-current plus current long-term debt.
    Missing when no debt line at all is reported.
    """
    lt_total = df[f"debt_lt_total{suffix}"]
    lt_parts = df[f"debt_lt_noncurrent{suffix}"].add(df[f"debt_lt_current{suffix}"], fill_value=0)
    lt = lt_total.fillna(lt_parts)
    extra = df[f"debt_short{suffix}"].fillna(0) + df[f"commercial_paper{suffix}"].fillna(0)
    any_line = pd.concat(
        [df[f"{c}{suffix}"] for c in ("debt_lt_total", "debt_lt_noncurrent", "debt_lt_current", "debt_short", "commercial_paper")],
        axis=1,
    ).notna().any(axis=1)
    return (lt.fillna(0) + extra).where(any_line)


def raw_measures(
    panel: pd.DataFrame,
    tables: dict[str, pd.DataFrame],
    sue_events: pd.DataFrame,
    lag_days: int = REPORTING_LAG_DAYS,
    max_age_days: int = FUNDAMENTAL_MAX_AGE_DAYS,
) -> pd.DataFrame:
    """One row per (month end, universe member) with every raw measure."""
    grid = panel.loc[panel["in_universe"], ["date", "cik", "subsector", "mcap", "has_price_gap"]].copy()
    grid["cutoff"] = grid["date"] - pd.Timedelta(days=lag_days)
    for name, table in tables.items():
        grid = pit_merge(grid, table, name, max_age_days)

    sue = sue_events[["cik", "end", "available", "sue"]].dropna(subset=["sue"])
    grid = pit_merge(grid, sue, "sue", SUE_MAX_AGE_DAYS + 100)
    too_old = grid["sue_available"] < grid["date"] - pd.Timedelta(days=SUE_MAX_AGE_DAYS)
    grid.loc[too_old, ["sue", "sue_available"]] = [np.nan, pd.NaT]

    for y in ("", "_y1", "_y2"):
        grid[f"debt{y}"] = _debt(grid, y)
        grid[f"ic{y}"] = grid[f"debt{y}"].fillna(0) + grid[f"equity{y}"] - grid[f"cash{y}"].fillna(0)

    g = grid
    g["op_margin"] = _safe_div(g["ebit_ttm"], g["sales_ttm"])
    g["d_op_margin"] = g["op_margin"] - _safe_div(g["ebit_ttm_prev"], g["sales_ttm_prev"])
    g["sales_growth"] = _safe_div(g["sales_ttm"], g["sales_ttm_prev"]) - 1
    g["roic"] = _safe_div(g["ebit_ttm"], (g["ic"] + g["ic_y1"]) / 2)
    g["d_roic"] = g["roic"] - _safe_div(g["ebit_ttm_prev"], (g["ic_y1"] + g["ic_y2"]) / 2)
    g["leverage"] = _safe_div(g["debt"], g["assets"])
    g["interest_cover"] = _safe_div(g["ebit_ttm"], g["interest_ttm"]).clip(upper=100)
    g["accruals"] = _safe_div(g["net_income_ttm"] - g["cfo_ttm"], (g["assets"] + g["assets_y1"]) / 2)
    ev = g["mcap"] + g["debt"].fillna(0) + g["nci"].fillna(0) - g["cash"].fillna(0)
    g["ebit_yield"] = _safe_div(g["ebit_ttm"], ev)
    g["fcf_yield"] = _safe_div(g["cfo_ttm"] - g["capex_ttm"].fillna(0), g["mcap"])

    avail_cols = [c for c in g.columns if c.endswith("_available")]
    g["info_date"] = g[avail_cols].max(axis=1)
    keep = ["date", "cik", "subsector", "mcap", "has_price_gap", "info_date", *MEASURES]
    return g[keep].sort_values(["date", "cik"]).reset_index(drop=True)


def score(measures: pd.DataFrame, by: str | None = None, min_factors: int = MIN_FACTORS) -> pd.DataFrame:
    """Percentile scores per factor and combined, ranked by date (and by `by` if given).

    Each measure is ranked 0 to 1 with its direction; a factor is the mean of
    its measures' ranks, re-ranked so each factor has the same spread; the
    combined score is the equal-weighted mean of the factor ranks, required
    for names with at least min_factors factors.
    """
    keys = ["date"] + ([by] if by else [])
    out = measures[["date", "cik", "subsector", "has_price_gap"]].copy()
    ranks = pd.DataFrame(index=measures.index)
    for m, (_, sign) in MEASURES.items():
        ranks[m] = (measures[m] * sign).groupby([measures[k] for k in keys]).rank(pct=True)
    for f in FACTORS:
        cols = [m for m, (fac, _) in MEASURES.items() if fac == f]
        raw = ranks[cols].mean(axis=1)
        out[f] = raw.groupby([measures[k] for k in keys]).rank(pct=True)
    count = out[list(FACTORS)].notna().sum(axis=1)
    out["combined"] = out[list(FACTORS)].mean(axis=1).where(count >= min_factors)
    out["n_factors"] = count
    return out
