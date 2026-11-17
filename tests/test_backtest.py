import json

import numpy as np
import pandas as pd
import pytest

from podbook.backtest import (
    LockedPeriodError,
    check_oos_lock,
    forward_returns,
    quantile_returns,
    run,
    turnover,
)


def _prices(n_names=20, months=24, seed=0):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2015-01-01", periods=months * 22)
    rows = []
    for i in range(n_names):
        r = rng.normal(0, 0.01, len(days))
        px = 100 * np.cumprod(1 + r)
        rows.append(pd.DataFrame({"cik": f"{i:010d}", "date": days, "adj_close": px}))
    return pd.concat(rows, ignore_index=True)


def test_forward_return_runs_from_this_month_end_to_the_next():
    days = pd.bdate_range("2015-01-01", "2015-03-31")
    px = pd.DataFrame({"cik": "x", "date": days, "adj_close": np.arange(len(days), dtype=float) + 100})
    fwd = forward_returns(px, pd.DatetimeIndex(["2015-01-31", "2015-02-28", "2015-03-31"]))
    jan = fwd.set_index("date").loc["2015-01-31"]
    p_jan = px.set_index("date").loc["2015-01-30", "adj_close"]  # last trading day of January
    p_feb = px.set_index("date").loc["2015-02-27", "adj_close"]
    assert jan["fwd_ret"] == pytest.approx(p_feb / p_jan - 1)
    assert not jan["stopped_trading"]


def test_name_that_stops_trading_earns_return_to_last_close():
    days = pd.bdate_range("2015-01-01", "2015-02-10")
    px = pd.DataFrame({"cik": "x", "date": days, "adj_close": 100.0})
    px.loc[px["date"] == days[-1], "adj_close"] = 80.0
    fwd = forward_returns(px, pd.DatetimeIndex(["2015-01-31", "2015-02-28"]))
    assert fwd["fwd_ret"].item() == pytest.approx(-0.2)
    assert fwd["stopped_trading"].item()


def _scored(prices, dates, perfect=True):
    fwd = forward_returns(prices, dates)
    s = fwd.copy()
    s["combined"] = s["fwd_ret"] if perfect else 0.5
    s["subsector"] = np.where(s["cik"].str[-1].astype(int) % 2 == 0, "a", "b")
    s["has_price_gap"] = False
    return s.drop(columns=["fwd_ret", "stopped_trading"]), fwd


def test_perfect_foresight_score_gives_positive_spread_and_ic_of_one():
    prices = _prices()
    dates = pd.date_range("2015-01-31", periods=20, freq="ME")
    scores, fwd = _scored(prices, dates)
    res = run(scores, fwd, "2015-01-31", "2016-12-31", ("combined",))["combined"]
    assert res["spread"]["mean_monthly"] > 0
    assert res["ic_mean"] == pytest.approx(1.0)
    assert res["spread"]["hit_rate"] == 1.0


def test_sector_neutral_spread_averages_within_sector_spreads():
    df = pd.DataFrame(
        {
            "date": pd.Timestamp("2015-01-31"),
            "cik": list("abcdefgh"),
            "subsector": ["x"] * 4 + ["y"] * 4,
            "combined": [1, 2, 3, 4, 1, 2, 3, 4],
            "fwd_ret": [0.0, 0.0, 0.0, 0.10, 0.0, 0.0, 0.0, 0.30],
        }
    )
    _, spread = quantile_returns(df, "combined", n=4, by="subsector")
    assert spread.item() == pytest.approx(0.20)


def test_static_scores_have_zero_turnover():
    dates = pd.date_range("2015-01-31", periods=4, freq="ME")
    df = pd.DataFrame([{"date": d, "cik": c, "combined": i} for d in dates for i, c in enumerate("abcdefghij")])
    assert (turnover(df, "combined") == 0).all()


def test_out_of_sample_lock_records_and_refuses(tmp_path):
    lock = tmp_path / "locks" / "oos.json"
    check_oos_lock(lock, {"columns": ["combined"]})
    with pytest.raises(LockedPeriodError):
        check_oos_lock(lock, {"columns": ["combined"]})
    check_oos_lock(lock, {"columns": ["combined"]}, force_reason="fixed a bug in forward returns")
    record = json.loads(lock.read_text())
    assert [r["reason"] for r in record["runs"]] == ["first run", "fixed a bug in forward returns"]
