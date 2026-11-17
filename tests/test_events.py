import numpy as np
import pandas as pd
import pytest
from factsutil import frame, quarter_rows

from podbook.events import (
    announcement_dates,
    assign_groups,
    benchmark_returns,
    compute_sue,
    eps_on_current_basis,
    event_bhar,
)

CIK = "0000001000"
NO_SPLITS = pd.DataFrame(columns=["cik", "ex_date", "ratio"])


def _eps_series(surprises):
    """EPS built so the seasonal surprises equal `surprises` from the fifth quarter on."""
    eps = [1.0, 1.0, 1.0, 1.0]
    for u in surprises:
        eps.append(eps[-4] + u)
    return eps


def test_sue_matches_hand_calculation():
    u = [0.1, -0.1, 0.2, -0.2, 0.1, -0.1, 0.2, -0.2, 0.5]
    f = frame(quarter_rows(CIK, "EarningsPerShareDiluted", _eps_series(u), unit="USD/shares"))
    sue = compute_sue(f, NO_SPLITS).dropna(subset=["sue"])
    last = sue.iloc[-1]
    expected = 0.5 / np.std(u[:-1], ddof=1)
    assert last["ue"] == pytest.approx(0.5)
    assert last["sue"] == pytest.approx(expected)
    assert last["n_hist"] == 8


def test_sue_needs_minimum_history():
    u = [0.1, -0.1, 0.2, -0.2, 0.3]
    f = frame(quarter_rows(CIK, "EarningsPerShareDiluted", _eps_series(u), unit="USD/shares"))
    assert compute_sue(f, NO_SPLITS)["sue"].isna().all()


def test_split_puts_old_eps_on_current_basis():
    eps = pd.DataFrame({"cik": [CIK, CIK], "end": pd.to_datetime(["2014-03-31", "2015-03-31"]),
                        "available": pd.to_datetime(["2014-05-01", "2015-05-01"]), "val": [2.0, 1.1]})
    splits = pd.DataFrame({"cik": [CIK], "ex_date": pd.to_datetime(["2014-12-01"]), "ratio": [2.0]})
    out = eps_on_current_basis(eps, splits)
    assert out["eps"].tolist() == pytest.approx([1.0, 1.1])
    assert out["split_adjusted"].tolist() == [True, False]


def test_announcement_date_prefers_earnings_8k_inside_the_window():
    sue = pd.DataFrame({"cik": [CIK, CIK], "end": pd.to_datetime(["2015-03-31", "2015-06-30"]),
                        "available": pd.to_datetime(["2015-05-05", "2015-08-05"])})
    filings = pd.DataFrame(
        {
            "cik": [CIK, CIK, CIK],
            "form": ["8-K", "8-K", "8-K"],
            "filing_date": pd.to_datetime(["2015-03-20", "2015-04-24", "2015-07-15"]),
            "items": ["2.02", "2.02,9.01", "5.02"],  # last one is not an earnings release
        }
    )
    out = announcement_dates(sue, filings).set_index("end")
    assert out.loc["2015-03-31", "ann_date"] == pd.Timestamp("2015-04-24")
    assert out.loc["2015-03-31", "ann_source"] == "8-K 2.02"
    assert out.loc["2015-06-30", "ann_date"] == pd.Timestamp("2015-08-05")
    assert out.loc["2015-06-30", "ann_source"] == "10-Q/10-K"


def test_groups_use_previous_quarter_breakpoints():
    q1 = pd.DataFrame({"cik": [f"{i:010d}" for i in range(10)], "ann_date": pd.Timestamp("2015-02-15"),
                       "sue": np.arange(1.0, 11.0)})
    q2 = pd.DataFrame({"cik": [f"{i:010d}" for i in range(10)], "ann_date": pd.Timestamp("2015-05-15"),
                       "sue": np.arange(100.0, 110.0)})
    g = assign_groups(pd.concat([q1, q2], ignore_index=True), n=5)
    # Q1 has no earlier breakpoints so it is dropped; every Q2 value beats Q1's top breakpoint.
    assert set(g["ann_q"].astype(str)) == {"2015Q2"}
    assert (g["group"] == 5).all()


def test_bhar_window_starts_after_day_zero_and_stops_at_delisting():
    days = pd.bdate_range("2015-01-01", periods=120)
    rets = pd.DataFrame({CIK: 0.01, "0000002000": 0.01}, index=days)
    rets.loc[days[15]:, "0000002000"] = np.nan  # stops trading
    bench = pd.Series(0.0, index=days)
    ann = days[4] + pd.Timedelta(days=1) if days[4].day_of_week == 4 else days[4]
    events = pd.DataFrame({"cik": [CIK, "0000002000"], "ann_date": [ann, ann]})
    bhar, used, path = event_bhar(events, rets, bench, first_day=1, last_day=60)
    assert bhar.iloc[0] == pytest.approx(1.01**60 - 1)
    assert used.iloc[0] == 60
    assert used.iloc[1] == 10  # days 5..14 after day 0 at position 4
    assert path.shape == (2, 60)


def test_bhar_day_zero_is_next_trading_day_after_weekend_announcement():
    days = pd.bdate_range("2015-01-05", periods=80)  # starts on a Monday
    rets = pd.DataFrame({CIK: 0.0}, index=days)
    rets.iloc[1, 0] = 0.10  # Tuesday
    bench = pd.Series(0.0, index=days)
    saturday = pd.Timestamp("2015-01-03")
    bhar, _, _ = event_bhar(pd.DataFrame({"cik": [CIK], "ann_date": [saturday]}), rets, bench, 1, 1)
    assert bhar.iloc[0] == pytest.approx(0.10)


def test_benchmark_uses_last_month_end_membership():
    days = pd.bdate_range("2015-01-26", "2015-02-06")
    rets = pd.DataFrame({"A": 0.01, "B": 0.03}, index=days)
    panel = pd.DataFrame({"date": pd.to_datetime(["2015-01-31", "2015-01-31", "2014-12-31"]),
                          "cik": ["A", "B", "A"], "in_universe": [True, True, True]})
    bench = benchmark_returns(rets, panel)
    assert bench.loc["2015-01-28"] == pytest.approx(0.01)  # only A was a member at end-December
    assert bench.loc["2015-02-03"] == pytest.approx(0.02)
