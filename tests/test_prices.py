import pandas as pd
import pytest

from podbook.prices import (
    fetch_prices_for_universe,
    normalise_yahoo_history,
    summarise_prices,
    unadjust_for_splits,
)


def test_unadjust_two_for_one_split():
    idx = pd.date_range("2020-01-01", periods=4, freq="D")
    close = pd.Series([50.0, 50.0, 50.0, 50.0], index=idx)
    splits = pd.Series([0, 0, 2.0, 0], index=idx)
    raw = unadjust_for_splits(close, splits)
    assert list(raw) == [100.0, 100.0, 50.0, 50.0]


def test_unadjust_reverse_split_and_multiple_splits():
    idx = pd.date_range("2020-01-01", periods=5, freq="D")
    close = pd.Series([10.0] * 5, index=idx)
    splits = pd.Series([0, 3.0, 0, 0.5, 0], index=idx)
    raw = unadjust_for_splits(close, splits)
    assert list(raw) == [15.0, 5.0, 5.0, 10.0, 10.0]


def test_normalise_yahoo_history_drops_timezone():
    idx = pd.DatetimeIndex(["2020-01-02 00:00", "2020-01-03 00:00"]).tz_localize("America/New_York")
    hist = pd.DataFrame(
        {"Close": [10.0, 20.0], "Adj Close": [9.0, 19.0], "Volume": [1, 2], "Stock Splits": [0.0, 2.0]}, index=idx
    )
    out = normalise_yahoo_history(hist)
    assert out["date"].dt.tz is None
    assert list(out["close_raw"]) == [20.0, 20.0]
    assert out["source"].eq("yahoo").all()


def test_gap_summary_statuses():
    d = pd.Series(pd.bdate_range("2015-01-01", "2015-12-31"))
    full = pd.DataFrame({"date": d})
    assert summarise_prices(full, pd.Timestamp("2015-01-01"), pd.Timestamp("2015-12-31"))["status"] == "complete"
    holey = full[(full["date"] < "2015-05-01") | (full["date"] > "2015-06-30")]
    assert summarise_prices(holey, pd.Timestamp("2015-01-01"), pd.Timestamp("2015-12-31"))["status"] == "partial"
    assert summarise_prices(full.iloc[0:0], pd.Timestamp("2015-01-01"), pd.Timestamp("2015-12-31"))["status"] == "no_data"
    # A reused ticker returning a different company's recent history
    assert summarise_prices(full, pd.Timestamp("2005-01-01"), pd.Timestamp("2009-12-31"))["status"] == "ticker_mismatch"


class FakeYF:
    def __init__(self, frames):
        self.frames = frames

    def Ticker(self, symbol):
        frames = self.frames

        class T:
            def history(self, **kw):
                return frames.get(symbol, pd.DataFrame())

        return T()


class DeadStooq:
    def get(self, url, timeout=None):
        class R:
            text = "No data"

            def raise_for_status(self):
                pass

        return R()


def test_fetch_writes_log_and_rejects_mismatched_ticker(tmp_path):
    idx = pd.bdate_range("2020-01-01", "2026-10-30")
    hist = pd.DataFrame({"Close": 10.0, "Adj Close": 10.0, "Volume": 1, "Stock Splits": 0.0}, index=idx)
    yf = FakeYF({"ALIV": hist, "GONE": hist})
    companies = pd.DataFrame(
        [
            {"cik": "0000001000", "name": "Alive", "first_periodic": pd.Timestamp("2008-03-01"), "last_periodic": pd.Timestamp("2026-08-01")},
            {"cik": "0000002000", "name": "Gone", "first_periodic": pd.Timestamp("2009-05-01"), "last_periodic": pd.Timestamp("2015-05-01")},
            {"cik": "0000003000", "name": "NoTicker", "first_periodic": pd.Timestamp("2009-05-01"), "last_periodic": pd.Timestamp("2014-05-01")},
        ]
    )
    mapping = pd.DataFrame(
        [
            {"cik": "0000001000", "ticker": "ALIV", "exchange": "NYSE", "valid_from": pd.NaT, "valid_to": pd.NaT, "source": "sec_current", "is_primary": True},
            {"cik": "0000002000", "ticker": "GONE", "exchange": "NYSE", "valid_from": pd.NaT, "valid_to": pd.Timestamp("2020-06-30"), "source": "sec_submissions", "is_primary": True},
        ]
    )
    gaps = fetch_prices_for_universe(
        companies, mapping, tmp_path / "prices", tmp_path / "gaps.csv", analysis_start="2009-06-30",
        today=pd.Timestamp("2026-11-16"), yf_module=yf, stooq_session=DeadStooq(), pause=0,
    ).set_index("cik")
    assert gaps.loc["0000001000", "status"] == "partial"  # prices start in 2020, filings in 2008
    assert gaps.loc["0000002000", "status"] == "ticker_mismatch"
    assert not (tmp_path / "prices" / "0000002000.csv").exists()
    assert gaps.loc["0000003000", "status"] == "no_ticker"
    assert (tmp_path / "gaps.csv").exists()
