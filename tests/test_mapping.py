import pandas as pd
import pytest

from podbook.mapping import (
    MappingError,
    build_mapping,
    choose_primary,
    normalise_cik,
    parse_tickers_exchange,
    ticker_for,
    to_stooq,
    to_yahoo,
    validate_mapping,
)


def _companies(rows):
    return pd.DataFrame(rows, columns=["cik", "tickers", "exchanges", "last_periodic"])


def _current(rows):
    js = {"fields": ["cik", "name", "ticker", "exchange"], "data": rows}
    return parse_tickers_exchange(js)


def test_cik_normalisation():
    assert normalise_cik(320193) == "0000320193"
    assert normalise_cik("0000320193") == "0000320193"
    assert normalise_cik("320193.0") == "0000320193"
    with pytest.raises(MappingError):
        normalise_cik("ABC")
    with pytest.raises(MappingError):
        normalise_cik("12345678901")


def test_symbol_conversion():
    assert to_yahoo("brk.b") == "BRK-B"
    assert to_yahoo("BRK-B") == "BRK-B"
    assert to_stooq("BRK.B") == "brk-b.us"


def test_primary_skips_preferred_and_warrants():
    assert choose_primary(["BA-PA", "BA"], ["NYSE", "NYSE"]) == "BA"
    assert choose_primary(["XYZW", "XYZ"], ["OTC", "NYSE"]) == "XYZ"
    assert choose_primary([], []) is None


def test_current_and_delisted_names_are_both_mapped():
    current = _current([[1000, "Alive", "ALIV", "NYSE"], [1000, "Alive", "ALIV-PA", "NYSE"]])
    companies = _companies(
        [["0000001000", ["ALIV"], ["NYSE"], pd.Timestamp("2026-08-01")],
         ["0000002000", ["GONE"], ["NYSE"], pd.Timestamp("2015-05-01")]]
    )
    m, conflicts = build_mapping(current, companies)
    assert ticker_for(m, "1000") == "ALIV"
    assert ticker_for(m, "2000", "2014-06-30") == "GONE"
    # Past its last filing plus grace the delisted ticker no longer maps.
    assert ticker_for(m, "2000", "2016-06-30") is None
    assert conflicts.empty


def test_ticker_reuse_gets_non_overlapping_windows():
    """GONE belonged to company 2000 until 2015; company 5000 trades as GONE today."""
    current = _current([[5000, "New Co", "GONE", "NASDAQ"]])
    companies = _companies(
        [["0000002000", [], [], pd.Timestamp("2015-05-01")],
         ["0000005000", ["GONE"], ["NASDAQ"], pd.Timestamp("2026-08-01")]]
    )
    overrides = pd.DataFrame(
        [{"cik": "2000", "ticker": "GONE", "valid_from": "2005-01-01", "valid_to": "2015-06-30", "note": "10-K cover"}]
    )
    m, _ = build_mapping(current, companies, overrides)
    assert ticker_for(m, "2000", "2012-01-31") == "GONE"
    assert ticker_for(m, "5000", "2012-01-31") is None
    assert ticker_for(m, "5000", "2020-01-31") == "GONE"


def test_stale_last_known_ticker_colliding_with_current_is_dropped():
    current = _current([[5000, "New Co", "GONE", "NASDAQ"]])
    companies = _companies(
        [["0000002000", ["GONE"], ["NYSE"], pd.Timestamp("2015-05-01")],
         ["0000005000", ["GONE"], ["NASDAQ"], pd.Timestamp("2026-08-01")]]
    )
    m, conflicts = build_mapping(current, companies)
    assert ticker_for(m, "2000") is None
    assert list(conflicts["cik"]) == ["0000002000"]


def test_validate_rejects_overlapping_windows():
    bad = pd.DataFrame(
        [
            {"cik": "0000000001", "ticker": "AAA", "exchange": "", "valid_from": pd.NaT, "valid_to": pd.NaT, "source": "sec_current", "is_primary": True},
            {"cik": "0000000002", "ticker": "AAA", "exchange": "", "valid_from": pd.Timestamp("2010-01-01"), "valid_to": pd.Timestamp("2012-01-01"), "source": "manual", "is_primary": True},
        ]
    )
    with pytest.raises(MappingError):
        validate_mapping(bad)
