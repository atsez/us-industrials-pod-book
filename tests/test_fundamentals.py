import numpy as np
import pandas as pd
import pytest
from factsutil import frame, instant_rows, quarter_rows, row

from podbook.fundamentals import first_reported, instant_table, pit_merge, quarterly, ttm_table

CIK = "0000001000"


def test_quarters_recovered_from_year_to_date_figures():
    f = frame(
        [
            row(CIK, "NetCashProvidedByUsedInOperatingActivities", 10, "2015-03-31", "2015-05-01", start="2015-01-01"),
            row(CIK, "NetCashProvidedByUsedInOperatingActivities", 25, "2015-06-30", "2015-08-01", start="2015-01-01"),
            row(CIK, "NetCashProvidedByUsedInOperatingActivities", 45, "2015-09-30", "2015-11-01", start="2015-01-01"),
            row(CIK, "NetCashProvidedByUsedInOperatingActivities", 70, "2015-12-31", "2016-02-20", start="2015-01-01"),
        ]
    )
    q = quarterly(first_reported(f, "cfo")).set_index("end")
    assert q["val"].tolist() == [10, 15, 20, 25]
    assert q.loc["2015-12-31", "available"] == pd.Timestamp("2016-02-20")
    assert q.loc["2015-06-30", "source"] == "derived"


def test_derived_quarter_waits_for_both_inputs():
    # The 6-month figure is filed before the 3-month one was (odd, but possible).
    f = frame(
        [
            row(CIK, "OperatingIncomeLoss", 10, "2015-03-31", "2015-09-01", start="2015-01-01"),
            row(CIK, "OperatingIncomeLoss", 25, "2015-06-30", "2015-08-01", start="2015-01-01"),
        ]
    )
    q = quarterly(first_reported(f, "ebit")).set_index("end")
    assert q.loc["2015-06-30", "available"] == pd.Timestamp("2015-09-01")


def test_first_reported_ignores_later_restatement_and_follows_tag_priority():
    f = frame(
        [
            row(CIK, "Revenues", 100, "2015-03-31", "2015-05-01", start="2015-01-01"),
            row(CIK, "Revenues", 90, "2015-03-31", "2016-05-01", start="2015-01-01"),  # restated a year later
            row(CIK, "SalesRevenueNet", 80, "2015-03-31", "2015-05-01", start="2015-01-01"),  # lower priority
        ]
    )
    fr = first_reported(f, "sales")
    assert len(fr) == 1
    assert fr["val"].item() == 100
    assert fr["available"].item() == pd.Timestamp("2015-05-01")


def test_ttm_and_prior_year_ttm():
    f = frame(quarter_rows(CIK, "Revenues", [1, 2, 3, 4, 5, 6, 7, 8]))
    t = ttm_table(quarterly(first_reported(f, "sales")), "sales").set_index("end")
    last = t.loc["2011-12-31"]
    assert last["sales_ttm"] == 26
    assert last["sales_ttm_prev"] == 10


def test_ttm_needs_four_consecutive_quarters():
    rows = quarter_rows(CIK, "Revenues", [1, 2, 3, 4, 5, 6])
    del rows[2]  # gap in the third quarter
    t = ttm_table(quarterly(first_reported(frame(rows), "sales")), "sales")
    assert pd.Timestamp("2010-12-31") not in set(t["end"])


def test_instant_year_ago_values():
    t = instant_table(first_reported(frame(instant_rows(CIK, "Assets", list(range(100, 109)))), "assets"), "assets")
    last = t.set_index("end").loc["2012-03-31"]
    assert last["assets"] == 108
    assert last["assets_y1"] == 104
    assert last["assets_y2"] == 100


def test_pit_merge_uses_only_rows_available_by_the_cutoff():
    t = ttm_table(quarterly(first_reported(frame(quarter_rows(CIK, "Revenues", [1, 2, 3, 4, 5, 6, 7, 8])), "sales")), "sales")
    # Q4 2011 is filed 2012-02-04. On 2012-02-03 only Q3 2011 TTM is known.
    grid = pd.DataFrame({"cik": [CIK, CIK], "date": pd.to_datetime(["2012-02-04", "2012-02-05"])})
    grid["cutoff"] = grid["date"] - pd.Timedelta(days=1)
    m = pit_merge(grid, t, "sales", 200).sort_values("date")
    assert m["sales_ttm"].tolist() == [22, 26]
    assert (m["sales_available"] <= m["cutoff"]).all()


def test_pit_merge_marks_stale_values_missing():
    t = ttm_table(quarterly(first_reported(frame(quarter_rows(CIK, "Revenues", [1, 2, 3, 4])), "sales")), "sales")
    grid = pd.DataFrame({"cik": [CIK], "date": pd.to_datetime(["2012-06-30"]), "cutoff": pd.to_datetime(["2012-06-29"])})
    m = pit_merge(grid, t, "sales", 200)
    assert np.isnan(m["sales_ttm"].item())
