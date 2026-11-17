"""Companyfacts parsing and point-in-time lookups.

Every XBRL fact carries the date it was filed. A value is usable at date d only
if it was filed on or before d. All lookups in this project go through
`latest_known`, which enforces that rule, so look-ahead is tested in one place.
"""

from __future__ import annotations

import pandas as pd

from podbook.mapping import normalise_cik

# Raw JSON is cached per company, so this list can grow later without refetching.
KEEP_TAGS: dict[str, frozenset[str]] = {
    "dei": frozenset({"EntityCommonStockSharesOutstanding", "EntityPublicFloat"}),
    "us-gaap": frozenset(
        {
            # Earnings
            "EarningsPerShareBasic",
            "EarningsPerShareDiluted",
            "EarningsPerShareBasicAndDiluted",
            "NetIncomeLoss",
            "ProfitLoss",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeTaxExpenseBenefit",
            "OperatingIncomeLoss",
            "InterestExpense",
            "InterestExpenseNonoperating",
            "InterestExpenseDebt",
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            # Sales
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
            # Cash flow
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            # Balance sheet
            "Assets",
            "AssetsCurrent",
            "Liabilities",
            "LiabilitiesCurrent",
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "MinorityInterest",
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "LongTermDebt",
            "LongTermDebtNoncurrent",
            "LongTermDebtCurrent",
            "DebtCurrent",
            "ShortTermBorrowings",
            "CommercialPaper",
            "OperatingLeaseLiability",
            # Shares
            "CommonStockSharesOutstanding",
            "WeightedAverageNumberOfSharesOutstandingBasic",
            "WeightedAverageNumberOfDilutedSharesOutstanding",
        }
    ),
}

FACT_COLUMNS = ["cik", "taxonomy", "tag", "unit", "start", "end", "val", "accn", "fy", "fp", "form", "filed", "frame"]
FACT_DATE_COLUMNS = ("start", "end", "filed")

SHARES_TAGS = ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding")
# For market cap the share count is the median of every count a company reports.
# The cover-page figure is missing for most companies with two share classes,
# and any one tag is sometimes mis-scaled by 1,000, so no single tag is trusted.
SHARE_COUNT_TAGS = (
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
)
PUBLIC_FLOAT_TAGS = ("EntityPublicFloat",)


def parse_companyfacts(js: dict, keep: dict[str, frozenset[str]] = KEEP_TAGS) -> pd.DataFrame:
    """Flatten a companyfacts JSON into one row per reported fact."""
    cik = normalise_cik(js["cik"])
    rows = []
    for taxonomy, tags in (js.get("facts") or {}).items():
        wanted = keep.get(taxonomy)
        if not wanted:
            continue
        for tag, body in tags.items():
            if tag not in wanted:
                continue
            for unit, observations in (body.get("units") or {}).items():
                for o in observations:
                    rows.append(
                        (cik, taxonomy, tag, unit, o.get("start"), o.get("end"), o.get("val"), o.get("accn"),
                         o.get("fy"), o.get("fp"), o.get("form"), o.get("filed"), o.get("frame"))
                    )
    df = pd.DataFrame(rows, columns=FACT_COLUMNS)
    for col in FACT_DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df["val"] = pd.to_numeric(df["val"], errors="coerce")
    df["fy"] = pd.to_numeric(df["fy"], errors="coerce").astype("Int64")
    return df


def latest_known(
    facts: pd.DataFrame,
    asof: object,
    tags: tuple[str, ...],
    max_age_days: int,
    unit: str | None = None,
) -> pd.DataFrame:
    """Most recent value per company that was public on `asof`.

    Rules, in order:
    1. Only facts with filed <= asof are visible (no look-ahead).
    2. Facts whose period ended more than max_age_days before asof are stale.
    3. Tags are tried in priority order; the first tag with a visible,
       non-stale value wins for that company.
    4. Within a tag, the latest period end wins, and for the same period end the
       latest filing wins (a restatement known by asof replaces the original).

    Returns columns cik, tag, end, filed, val.
    """
    asof = pd.Timestamp(asof)
    f = facts[facts["tag"].isin(tags)]
    if unit is not None:
        f = f[f["unit"] == unit]
    f = f[(f["filed"] <= asof) & (f["end"] <= asof) & (f["end"] >= asof - pd.Timedelta(days=max_age_days))]
    f = f.dropna(subset=["val"])
    if f.empty:
        return pd.DataFrame(
            {"cik": pd.Series(dtype=str), "tag": pd.Series(dtype=str), "end": pd.Series(dtype="datetime64[ns]"),
             "filed": pd.Series(dtype="datetime64[ns]"), "val": pd.Series(dtype=float)}
        )
    priority = {t: i for i, t in enumerate(tags)}
    f = f.assign(_priority=f["tag"].map(priority))
    f = f.sort_values(["cik", "_priority", "end", "filed"], ascending=[True, True, False, False])
    out = f.drop_duplicates("cik", keep="first")
    return out[["cik", "tag", "end", "filed", "val"]].reset_index(drop=True)


def share_count(facts: pd.DataFrame, asof: object, max_age_days: int) -> pd.DataFrame:
    """Median of the latest visible value of each share-count tag, per company.

    Returns cik, shares, shares_filed (latest filing used) and n_share_tags.
    """
    parts = []
    for tag in SHARE_COUNT_TAGS:
        got = latest_known(facts, asof, (tag,), max_age_days, unit="shares")
        if not got.empty:
            parts.append(got)
    if not parts:
        return pd.DataFrame({"cik": pd.Series(dtype=str), "shares": pd.Series(dtype=float),
                             "shares_filed": pd.Series(dtype="datetime64[ns]"), "n_share_tags": pd.Series(dtype=int)})
    allv = pd.concat(parts, ignore_index=True)
    allv = allv[allv["val"] > 0]
    g = allv.groupby("cik")
    return pd.DataFrame(
        {"shares": g["val"].median(), "shares_filed": g["filed"].max(), "n_share_tags": g["val"].size()}
    ).reset_index()
