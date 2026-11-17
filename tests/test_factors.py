"""Mandatory look-ahead tests for the monthly factors, plus scoring rules."""

import numpy as np
import pandas as pd
import pytest
from factsutil import frame, instant_rows, quarter_rows

from podbook.factors import FACTORS, raw_measures, score
from podbook.fundamentals import build_tables

A, B = "0000001000", "0000002000"


def _facts():
    rows = []
    for cik, scale in ((A, 1.0), (B, 2.0)):
        rows += quarter_rows(cik, "Revenues", [scale * (100 + i) for i in range(24)])
        rows += quarter_rows(cik, "OperatingIncomeLoss", [scale * (10 + i) for i in range(24)])
        rows += quarter_rows(cik, "NetIncomeLoss", [scale * (7 + i) for i in range(24)])
        rows += quarter_rows(cik, "NetCashProvidedByUsedInOperatingActivities", [scale * (9 + i) for i in range(24)])
        rows += quarter_rows(cik, "PaymentsToAcquirePropertyPlantAndEquipment", [scale * 2.0] * 24)
        rows += quarter_rows(cik, "InterestExpense", [scale * 1.0] * 24)
        rows += instant_rows(cik, "Assets", [scale * (1000 + 10 * i) for i in range(24)])
        rows += instant_rows(cik, "StockholdersEquity", [scale * (500 + 5 * i) for i in range(24)])
        rows += instant_rows(cik, "LongTermDebt", [scale * 300.0] * 24)
        rows += instant_rows(cik, "CashAndCashEquivalentsAtCarryingValue", [scale * 50.0] * 24)
    return frame(rows)


def _panel(dates):
    rows = []
    for d in pd.to_datetime(dates):
        for cik, sub in ((A, "machinery"), (B, "aerospace_defence")):
            rows.append({"date": d, "cik": cik, "subsector": sub, "mcap": 5e3, "has_price_gap": False, "in_universe": True})
    return pd.DataFrame(rows)


NO_SUE = pd.DataFrame({"cik": pd.Series(dtype=str), "end": pd.Series(dtype="datetime64[ns]"),
                       "available": pd.Series(dtype="datetime64[ns]"), "sue": pd.Series(dtype=float)})


def test_no_measure_uses_a_filing_dated_after_the_rebalance_date():
    m = raw_measures(_panel(pd.date_range("2011-01-31", "2015-12-31", freq="ME")), build_tables(_facts()), NO_SUE)
    used = m.dropna(subset=["info_date"])
    assert len(used) > 0
    assert (used["info_date"] <= used["date"] - pd.Timedelta(days=1)).all()


def test_filing_on_the_rebalance_date_is_not_used():
    facts = _facts()
    # Q1 2015 is filed on 2015-05-05 (35 days after quarter end).
    tables = build_tables(facts)
    on_day = raw_measures(_panel(["2015-05-05"]), tables, NO_SUE).set_index("cik")
    day_after = raw_measures(_panel(["2015-05-06"]), tables, NO_SUE).set_index("cik")
    # On the filing day the TTM still ends in Q4 2014; a day later it includes Q1 2015.
    assert on_day.loc[A, "info_date"] < pd.Timestamp("2015-05-05")
    assert day_after.loc[A, "info_date"] == pd.Timestamp("2015-05-05")
    assert on_day.loc[A, "sales_growth"] != day_after.loc[A, "sales_growth"]


def test_sue_factor_respects_filing_date_and_age():
    sue = pd.DataFrame({"cik": [A], "end": pd.to_datetime(["2015-03-31"]),
                        "available": pd.to_datetime(["2015-05-05"]), "sue": [2.5]})
    m = raw_measures(_panel(["2015-04-30", "2015-05-31", "2015-08-31", "2015-09-30"]), build_tables(_facts()), sue)
    a = m[m["cik"] == A].set_index("date")["sue"]
    assert np.isnan(a.loc["2015-04-30"])  # not yet filed
    assert a.loc["2015-05-31"] == 2.5
    assert a.loc["2015-08-31"] == 2.5  # 118 days old
    assert np.isnan(a.loc["2015-09-30"])  # older than 120 days


def test_measures_are_computed_as_defined():
    m = raw_measures(_panel(["2015-05-31"]), build_tables(_facts()), NO_SUE).set_index("cik")
    a = m.loc[A]
    # Q2 2014 to Q1 2015 are quarters 17 to 20 (0-based), so TTM sales = 117+118+119+120.
    assert a["sales_growth"] == pytest.approx((117 + 118 + 119 + 120) / (113 + 114 + 115 + 116) - 1)
    assert a["leverage"] == pytest.approx(300 / (1000 + 10 * 20))
    accruals = ((17 + 18 + 19 + 20) * 1.0 - (19 + 20 + 21 + 22)) / ((1200 + 1160) / 2)
    assert a["accruals"] == pytest.approx(accruals)
    ev = 5e3 + 300 - 50
    assert a["ebit_yield"] == pytest.approx((27 + 28 + 29 + 30) / ev)


def test_scores_respect_direction_and_sub_sector_ranking():
    measures = pd.DataFrame(
        {
            "date": pd.Timestamp("2015-05-31"),
            "cik": ["a", "b", "c", "d"],
            "subsector": ["x", "x", "y", "y"],
            "has_price_gap": False,
            "leverage": [0.1, 0.5, 0.2, 0.9],
        }
    )
    for col in ("sue", "d_op_margin", "d_roic", "sales_growth", "roic", "interest_cover", "accruals", "ebit_yield", "fcf_yield"):
        measures[col] = np.nan
    pooled = score(measures, min_factors=1).set_index("cik")
    assert pooled.loc["a", "quality"] > pooled.loc["c", "quality"] > pooled.loc["b", "quality"]
    within = score(measures, by="subsector", min_factors=1).set_index("cik")
    assert within.loc["a", "quality"] == within.loc["c", "quality"] == 1.0
    assert set(FACTORS) <= set(pooled.columns)


def test_combined_score_needs_minimum_factors():
    measures = pd.DataFrame({"date": pd.Timestamp("2015-05-31"), "cik": ["a", "b"], "subsector": "x",
                             "has_price_gap": False, "leverage": [0.1, 0.2], "sue": [1.0, 2.0]})
    for col in ("d_op_margin", "d_roic", "sales_growth", "roic", "interest_cover", "accruals", "ebit_yield", "fcf_yield"):
        measures[col] = np.nan
    s = score(measures, min_factors=3)
    assert s["combined"].isna().all()
    assert (s["n_factors"] == 2).all()
