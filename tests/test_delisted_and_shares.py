"""Share counts from several tags, and Tiingo prices for delisted companies."""

import pandas as pd
import pytest
from conftest import companyfacts, fact

from podbook.edgar.facts import parse_companyfacts, share_count
from podbook.mapping import build_mapping, parse_tickers_exchange
from podbook.prices import TiingoLimitError, fetch_delisted, fetch_prices_for_universe, fetch_tiingo


def _shares_facts(dei=None, balance=None, weighted=None):
    gaap = {}
    if balance is not None:
        gaap["CommonStockSharesOutstanding"] = {"units": {"shares": [fact(balance, "2015-03-31", "2015-05-01")]}}
    if weighted is not None:
        gaap["WeightedAverageNumberOfSharesOutstandingBasic"] = {
            "units": {"shares": [fact(weighted, "2015-03-31", "2015-05-01", start="2015-01-01")]}
        }
    shares = [] if dei is None else [fact(dei, "2015-04-25", "2015-05-01")]
    return parse_companyfacts(companyfacts(1000, shares, gaap=gaap))


def test_mis_scaled_share_tag_is_outvoted():
    f = _shares_facts(dei=100e9, balance=100e6, weighted=99e6)  # cover page off by 1,000
    got = share_count(f, "2015-06-30", 400)
    assert got["shares"].item() == pytest.approx(100e6)
    assert got["n_share_tags"].item() == 3


def test_two_class_company_is_sized_without_cover_page_count():
    f = _shares_facts(weighted=80e6)
    assert share_count(f, "2015-06-30", 400)["shares"].item() == pytest.approx(80e6)


def test_share_count_respects_filing_date():
    f = _shares_facts(dei=100e6, balance=100e6, weighted=100e6)
    assert share_count(f, "2015-04-30", 400).empty


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeTiingo:
    def __init__(self, series, limit_after=None):
        self.series = series
        self.calls = []
        self.limit_after = limit_after

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if self.limit_after is not None and len(self.calls) > self.limit_after:
            return FakeResp(429)
        ticker = url.split("/daily/")[1].split("/")[0].upper()
        if ticker not in self.series:
            return FakeResp(404)
        return FakeResp(200, self.series[ticker])


def _tiingo_rows(start, end, close=50.0):
    return [{"date": d.strftime("%Y-%m-%dT00:00:00.000Z"), "close": close, "adjClose": close * 0.9,
             "volume": 100, "splitFactor": 1.0} for d in pd.bdate_range(start, end)]


def test_fetch_tiingo_reads_raw_and_adjusted_close():
    px = fetch_tiingo("RTN", "token", FakeTiingo({"RTN": _tiingo_rows("2015-01-01", "2015-01-10")}))
    assert px["source"].eq("tiingo").all()
    assert px["close_raw"].iloc[0] == 50.0
    assert px["adj_close"].iloc[0] == pytest.approx(45.0)
    assert px["date"].dt.tz is None
    assert fetch_tiingo("NONE", "token", FakeTiingo({})).empty


def _world():
    companies = pd.DataFrame(
        [
            {"cik": "0000002000", "name": "Old Co", "first_periodic": pd.Timestamp("2009-05-01"),
             "last_periodic": pd.Timestamp("2014-11-01"), "tickers": [], "exchanges": []},
            {"cik": "0000003000", "name": "Renamed Co", "first_periodic": pd.Timestamp("2009-05-01"),
             "last_periodic": pd.Timestamp("2018-05-01"), "tickers": [], "exchanges": []},
            {"cik": "0000005000", "name": "New Co", "first_periodic": pd.Timestamp("2015-05-01"),
             "last_periodic": pd.Timestamp("2026-08-01"), "tickers": ["OLD"], "exchanges": ["NYSE"]},
        ]
    )
    current = parse_tickers_exchange({"fields": ["cik", "name", "ticker", "exchange"], "data": [[5000, "New Co", "OLD", "NYSE"]]})
    overrides = pd.DataFrame(
        [
            {"cik": "2000", "ticker": "OLD", "valid_from": None, "valid_to": "2014-12-31", "note": ""},
            {"cik": "3000", "ticker": "AAA", "valid_from": None, "valid_to": "2015-02-09", "note": ""},
            {"cik": "3000", "ticker": "BBB", "valid_from": "2015-02-10", "valid_to": "2018-08-31", "note": ""},
        ]
    )
    mapping, _ = build_mapping(current, companies, overrides)
    return companies, mapping


def test_reused_ticker_gives_only_the_old_company_its_window(tmp_path):
    companies, mapping = _world()
    # Tiingo's OLD series runs straight through the handover to the new company.
    tiingo = FakeTiingo({"OLD": _tiingo_rows("2009-01-01", "2020-12-31"),
                         "AAA": _tiingo_rows("2009-01-01", "2015-02-09"),
                         "BBB": _tiingo_rows("2015-02-10", "2018-06-30")})
    log = fetch_delisted(companies, mapping, tmp_path, tmp_path / "log.csv", "token", "2009-06-30", tiingo, pause=0)
    old = pd.read_csv(tmp_path / "0000002000.csv", parse_dates=["date"])
    assert old["date"].max() <= pd.Timestamp("2014-12-31")
    renamed = pd.read_csv(tmp_path / "0000003000.csv", parse_dates=["date"])
    assert renamed["date"].min() < pd.Timestamp("2015-02-10") < renamed["date"].max()
    assert not renamed["date"].duplicated().any()
    assert "0000005000" not in set(log["cik"])  # current owner is left to Yahoo


def test_fetch_delisted_resumes_after_hitting_the_limit(tmp_path):
    companies, mapping = _world()
    series = {"OLD": _tiingo_rows("2009-01-01", "2014-12-31"), "AAA": _tiingo_rows("2009-01-01", "2015-02-09"),
              "BBB": _tiingo_rows("2015-02-10", "2018-06-30")}
    first = fetch_delisted(companies, mapping, tmp_path, tmp_path / "log.csv", "t", "2009-06-30", FakeTiingo(series, limit_after=1), pause=0)
    assert "limit_reached_rerun_later" in set(first["status"])
    second_session = FakeTiingo(series)
    second = fetch_delisted(companies, mapping, tmp_path, tmp_path / "log.csv", "t", "2009-06-30", second_session, pause=0)
    assert "already_fetched" in set(second["status"])
    assert len(second_session.calls) == 2  # only the unfinished company is requested


def test_yahoo_fetch_leaves_hand_entered_tickers_alone(tmp_path):
    companies, mapping = _world()

    class NoYahoo:
        def Ticker(self, symbol):
            raise AssertionError(f"Yahoo should not be asked for {symbol}")

    gaps = fetch_prices_for_universe(
        companies[companies["cik"] != "0000005000"], mapping, tmp_path, tmp_path / "gaps.csv", "2009-06-30",
        today=pd.Timestamp("2026-11-16"), yf_module=NoYahoo(), pause=0,
    )
    assert set(gaps["status"]) == {"awaiting_fetch_delisted"}


def test_manual_history_replaces_last_known_ticker():
    companies = pd.DataFrame([{"cik": "0000002000", "name": "Old Co", "first_periodic": pd.Timestamp("2009-05-01"),
                               "last_periodic": pd.Timestamp("2014-11-01"), "tickers": ["STALE"], "exchanges": ["NYSE"]}])
    current = parse_tickers_exchange({"fields": ["cik", "name", "ticker", "exchange"], "data": []})
    overrides = pd.DataFrame([{"cik": "2000", "ticker": "OLD", "valid_from": None, "valid_to": "2014-12-31", "note": ""}])
    mapping, _ = build_mapping(current, companies, overrides)
    assert mapping["ticker"].tolist() == ["OLD"]


def test_blank_end_date_runs_to_last_filing_plus_grace():
    companies = pd.DataFrame([{"cik": "0000002000", "name": "Old Co", "first_periodic": pd.Timestamp("2009-05-01"),
                               "last_periodic": pd.Timestamp("2016-11-01"), "tickers": [], "exchanges": []}])
    current = parse_tickers_exchange({"fields": ["cik", "name", "ticker", "exchange"], "data": []})
    overrides = pd.DataFrame([{"cik": "2000", "ticker": "OLD", "valid_from": None, "valid_to": None, "note": ""}])
    mapping, _ = build_mapping(current, companies, overrides)
    assert mapping["valid_to"].item() == pd.Timestamp("2016-11-01") + pd.Timedelta(days=120)


def test_takeover_after_last_filing_counts_as_complete(tmp_path):
    from podbook.prices import summarise_prices

    # Last 10-Q in May, deal closes in July: prices run past the last filing.
    px = pd.DataFrame({"date": pd.bdate_range("2012-01-02", "2016-07-15")})
    assert summarise_prices(px, pd.Timestamp("2012-01-01"), pd.Timestamp("2016-05-01"))["status"] == "complete"


def test_changed_ticker_window_triggers_a_refetch(tmp_path):
    companies, mapping = _world()
    series = {"OLD": _tiingo_rows("2009-01-01", "2014-12-31"), "AAA": _tiingo_rows("2009-01-01", "2015-02-09"),
              "BBB": _tiingo_rows("2015-02-10", "2018-06-30")}
    fetch_delisted(companies, mapping, tmp_path, tmp_path / "log.csv", "t", "2009-06-30", FakeTiingo(series), pause=0)
    mapping.loc[mapping["ticker"] == "OLD", "valid_to"] = pd.Timestamp("2014-06-30")
    again = FakeTiingo(series)
    fetch_delisted(companies, mapping, tmp_path, tmp_path / "log.csv", "t", "2009-06-30", again, pause=0)
    assert len(again.calls) == 1  # only the company whose window changed


def test_tiingo_fills_in_when_yahoo_returns_almost_nothing(tmp_path):
    companies = pd.DataFrame([{"cik": "0000007000", "name": "Live Co", "first_periodic": pd.Timestamp("2016-08-01"),
                               "last_periodic": pd.Timestamp("2026-08-01"), "tickers": ["LIVE"], "exchanges": ["NYSE"]}])
    current = parse_tickers_exchange({"fields": ["cik", "name", "ticker", "exchange"], "data": [[7000, "Live Co", "LIVE", "NYSE"]]})
    mapping, _ = build_mapping(current, companies)

    class OneDayYahoo:
        def Ticker(self, symbol):
            class T:
                def history(self, **kw):
                    idx = pd.DatetimeIndex(["2026-10-16"])
                    return pd.DataFrame({"Close": [100.0], "Adj Close": [100.0], "Volume": [1], "Stock Splits": [0.0]}, index=idx)
            return T()

    tiingo = FakeTiingo({"LIVE": _tiingo_rows("2016-08-01", "2026-11-13")})
    gaps = fetch_prices_for_universe(companies, mapping, tmp_path, tmp_path / "g.csv", "2009-06-30",
                                     today=pd.Timestamp("2026-11-16"), yf_module=OneDayYahoo(), pause=0,
                                     tiingo_token="t", tiingo_session=tiingo)
    assert gaps["source"].item() == "tiingo"
    assert gaps["status"].item() == "complete"


def test_research_command_is_registered():
    from podbook.cli import build_parser

    assert build_parser().parse_args(["research"]).command == "research"
