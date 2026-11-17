"""Point-in-time fundamentals from companyfacts, as first reported.

Convention: every value is taken from the first filing that reported it, and it
becomes usable on that filing's date. Later restatements are ignored. This
loses a little accuracy but rules out look-ahead by construction, and it is
what an analyst actually saw at the time.

Quarterly flows (sales, EBIT, cash flow...) are rebuilt from whatever a
company reports. 10-Qs often give cash flow only year-to-date, and 10-Ks give
only the full year, so quarters are recovered by differencing year-to-date
figures that share a fiscal-year start (Q2 = 6M - Q1, Q4 = FY - 9M and so on).
A derived quarter is available only once both of its inputs have been filed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from podbook.tables import asof_merge


@dataclass(frozen=True)
class Concept:
    tags: tuple[str, ...]  # in priority order
    unit: str
    kind: str  # "flow" or "instant"


CONCEPTS: dict[str, Concept] = {
    "eps": Concept(("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"), "USD/shares", "flow"),
    "sales": Concept(("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet"), "USD", "flow"),
    "ebit": Concept(("OperatingIncomeLoss",), "USD", "flow"),
    "net_income": Concept(("NetIncomeLoss", "ProfitLoss"), "USD", "flow"),
    "cfo": Concept(("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"), "USD", "flow"),
    "capex": Concept(("PaymentsToAcquirePropertyPlantAndEquipment",), "USD", "flow"),
    "interest": Concept(("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"), "USD", "flow"),
    "assets": Concept(("Assets",), "USD", "instant"),
    "equity": Concept(("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"), "USD", "instant"),
    "cash": Concept(("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"), "USD", "instant"),
    "nci": Concept(("MinorityInterest",), "USD", "instant"),
    "debt_lt_total": Concept(("LongTermDebt",), "USD", "instant"),
    "debt_lt_noncurrent": Concept(("LongTermDebtNoncurrent",), "USD", "instant"),
    "debt_lt_current": Concept(("LongTermDebtCurrent",), "USD", "instant"),
    "debt_short": Concept(("ShortTermBorrowings",), "USD", "instant"),
    "commercial_paper": Concept(("CommercialPaper",), "USD", "instant"),
}

FLOW_CONCEPTS = tuple(k for k, c in CONCEPTS.items() if c.kind == "flow")
INSTANT_CONCEPTS = tuple(k for k, c in CONCEPTS.items() if c.kind == "instant")

# Day ranges for 1, 2, 3 and 4-quarter durations (covers 52/53-week years).
QUARTER_DAYS = {1: (80, 100), 2: (170, 195), 3: (260, 285), 4: (350, 380)}
QUARTER_GAP = (80, 100)
YEAR_GAP = (350, 380)


def _n_quarters(days: float) -> float:
    for k, (lo, hi) in QUARTER_DAYS.items():
        if lo <= days <= hi:
            return float(k)
    return np.nan


def first_reported(facts: pd.DataFrame, concept: str) -> pd.DataFrame:
    """One row per (company, period): the first-filed value of the highest-priority tag.

    Returns cik, start, end, val, available, tag.
    """
    c = CONCEPTS[concept]
    f = facts[facts["tag"].isin(c.tags) & (facts["unit"] == c.unit)].dropna(subset=["val", "end", "filed"])
    if c.kind == "instant":
        f = f[f["start"].isna()]
    else:
        f = f[f["start"].notna()]
    f = f.assign(_p=f["tag"].map({t: i for i, t in enumerate(c.tags)}))
    f = f.sort_values("filed", kind="stable").drop_duplicates(["cik", "tag", "start", "end"], keep="first")
    f = f.sort_values(["_p", "filed"], kind="stable").drop_duplicates(["cik", "start", "end"], keep="first")
    out = f.rename(columns={"filed": "available"})[["cik", "start", "end", "val", "available", "tag"]]
    return out.sort_values(["cik", "end"]).reset_index(drop=True)


def quarterly(obs: pd.DataFrame) -> pd.DataFrame:
    """Single-quarter values from reported quarters and year-to-date differences.

    Returns cik, end, val, available, source ('reported' or 'derived').
    """
    o = obs.copy()
    o["nq"] = (o["end"] - o["start"]).dt.days.map(_n_quarters)
    o = o.dropna(subset=["nq"])
    direct = o[o["nq"] == 1].assign(source="reported")[["cik", "end", "val", "available", "source"]]

    longer = o[o["nq"] >= 2]
    shorter = o[["cik", "start", "end", "val", "available", "nq"]].rename(
        columns={"end": "prev_end", "val": "prev_val", "available": "prev_available", "nq": "prev_nq"}
    )
    m = longer.merge(shorter, on=["cik", "start"])
    m = m[m["prev_nq"] == m["nq"] - 1]
    derived = pd.DataFrame(
        {
            "cik": m["cik"],
            "end": m["end"],
            "val": m["val"] - m["prev_val"],
            "available": m[["available", "prev_available"]].max(axis=1),
            "source": "derived",
        }
    )
    q = pd.concat([direct, derived], ignore_index=True)
    q["_rank"] = (q["source"] == "derived").astype(int)
    q = q.sort_values(["cik", "end", "_rank", "available"]).drop_duplicates(["cik", "end"], keep="first")
    return q.drop(columns="_rank").reset_index(drop=True)


def _drop_out_of_order(t: pd.DataFrame) -> pd.DataFrame:
    """Drop rows that became available after a later period was already known.

    A point-in-time lookup takes the most recently available row; a late report
    for an old period must not replace newer information.
    """
    t = t.sort_values(["cik", "available", "end"])
    running_max = t.groupby("cik")["end"].cummax()
    return t[t["end"] >= running_max]


def ttm_table(quarters: pd.DataFrame, name: str) -> pd.DataFrame:
    """Trailing twelve months, and the same a year earlier, keyed on availability date."""
    q = quarters.sort_values(["cik", "end"]).reset_index(drop=True)
    g = q.groupby("cik")
    ends = [q["end"]] + [g["end"].shift(k) for k in (1, 2, 3)]
    vals = [q["val"]] + [g["val"].shift(k) for k in (1, 2, 3)]
    avails = [q["available"]] + [g["available"].shift(k) for k in (1, 2, 3)]
    consecutive = pd.Series(True, index=q.index)
    for k in range(1, 4):
        gap = (ends[k - 1] - ends[k]).dt.days
        consecutive &= gap.between(*QUARTER_GAP)
    ttm = sum(vals).where(consecutive)
    available = pd.concat(avails, axis=1).max(axis=1)
    out = pd.DataFrame({"cik": q["cik"], "end": q["end"], "available": available, f"{name}_ttm": ttm})
    og = out.groupby("cik")
    prev_end = og["end"].shift(4)
    year_ok = (out["end"] - prev_end).dt.days.between(*YEAR_GAP)
    prev_available = og["available"].shift(4)
    # Strict: the year-earlier figure must itself have been public by then.
    out[f"{name}_ttm_prev"] = og[f"{name}_ttm"].shift(4).where(year_ok & (prev_available <= out["available"]))
    return _drop_out_of_order(out.dropna(subset=[f"{name}_ttm"]))


def instant_table(obs: pd.DataFrame, name: str) -> pd.DataFrame:
    """Balance-sheet value with the values one and two years earlier, keyed on availability."""
    o = obs.sort_values("end")[["cik", "end", "val", "available"]].reset_index(drop=True)
    out = o.rename(columns={"val": name})
    for years in (1, 2):
        right = o.rename(columns={"end": f"_end_y{years}", "val": f"{name}_y{years}", "available": "_avail_y"})
        left = out.assign(_target=out["end"] - pd.Timedelta(days=365 * years)).sort_values("_target")
        out = asof_merge(
            left, right.sort_values(f"_end_y{years}"), left_on="_target", right_on=f"_end_y{years}",
            by="cik", direction="nearest", tolerance=pd.Timedelta(days=45),
        )
        out.loc[out["_avail_y"] > out["available"], f"{name}_y{years}"] = np.nan
        out = out.drop(columns=["_target", f"_end_y{years}", "_avail_y"])
    return _drop_out_of_order(out)


def pit_merge(grid: pd.DataFrame, table: pd.DataFrame, prefix: str, max_age_days: int) -> pd.DataFrame:
    """Attach the latest row of `table` available on or before grid['cutoff'].

    Values whose period ended more than max_age_days before the rebalance date
    are set to missing. Adds `{prefix}_available` for the look-ahead audit.
    """
    t = table.rename(columns={"end": f"{prefix}_end", "available": f"{prefix}_available"})
    merged = asof_merge(
        grid.sort_values("cutoff"),
        t.sort_values(f"{prefix}_available"),
        left_on="cutoff",
        right_on=f"{prefix}_available",
        by="cik",
        direction="backward",
    )
    stale = merged[f"{prefix}_end"] < merged["date"] - pd.Timedelta(days=max_age_days)
    value_cols = [c for c in t.columns if c not in ("cik", f"{prefix}_end", f"{prefix}_available")]
    merged.loc[stale, value_cols] = np.nan
    merged.loc[stale, f"{prefix}_available"] = pd.NaT
    return merged


def build_tables(facts: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """All TTM and balance-sheet tables used by the factors."""
    tables: dict[str, pd.DataFrame] = {}
    for name in FLOW_CONCEPTS:
        if name == "eps":
            continue
        tables[name] = ttm_table(quarterly(first_reported(facts, name)), name)
    for name in INSTANT_CONCEPTS:
        tables[name] = instant_table(first_reported(facts, name), name)
    return tables
