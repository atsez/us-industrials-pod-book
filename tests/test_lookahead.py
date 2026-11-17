"""Mandatory look-ahead tests: nothing filed after the as-of date may be used."""

import pandas as pd
from conftest import companyfacts, fact

from podbook.edgar.facts import SHARES_TAGS, latest_known, parse_companyfacts


def _facts():
    js = companyfacts(
        1000,
        shares=[
            fact(100e6, "2014-04-25", "2014-05-01"),
            fact(110e6, "2014-07-25", "2014-08-01"),
            # filed after the as-of dates used below
            fact(500e6, "2014-10-25", "2014-11-01"),
        ],
    )
    return parse_companyfacts(js)


def test_value_filed_after_asof_is_invisible():
    f = _facts()
    got = latest_known(f, "2014-10-31", SHARES_TAGS, max_age_days=400)
    assert got["val"].iloc[0] == 110e6
    assert (got["filed"] <= pd.Timestamp("2014-10-31")).all()


def test_value_visible_on_its_filing_date():
    f = _facts()
    assert latest_known(f, "2014-11-01", SHARES_TAGS, 400)["val"].iloc[0] == 500e6


def test_nothing_known_before_first_filing():
    f = _facts()
    assert latest_known(f, "2014-04-30", SHARES_TAGS, 400).empty


def test_restatement_only_replaces_original_after_it_is_filed():
    js = companyfacts(
        1000,
        shares=[],
        gaap={
            "CommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        fact(200e6, "2014-12-31", "2015-02-20", form="10-K"),
                        fact(210e6, "2014-12-31", "2015-06-01", form="10-K/A"),
                    ]
                }
            }
        },
    )
    f = parse_companyfacts(js)
    assert latest_known(f, "2015-03-31", SHARES_TAGS, 400)["val"].iloc[0] == 200e6
    assert latest_known(f, "2015-06-30", SHARES_TAGS, 400)["val"].iloc[0] == 210e6


def test_stale_values_are_dropped():
    f = _facts()
    assert latest_known(f, "2016-06-30", SHARES_TAGS, max_age_days=400).empty


def test_tag_priority_falls_back():
    js = companyfacts(
        1000,
        shares=[],
        gaap={"CommonStockSharesOutstanding": {"units": {"shares": [fact(300e6, "2014-06-30", "2014-08-01")]}}},
    )
    got = latest_known(parse_companyfacts(js), "2014-09-30", SHARES_TAGS, 400)
    assert got["tag"].iloc[0] == "CommonStockSharesOutstanding"


def test_parse_keeps_filing_dates_and_drops_unlisted_tags():
    js = companyfacts(1000, shares=[fact(1, "2014-04-25", "2014-05-01")], gaap={"SomeOtherTag": {"units": {"USD": [fact(1, "2014-03-31", "2014-05-01")]}}})
    f = parse_companyfacts(js)
    assert set(f["tag"]) == {"EntityCommonStockSharesOutstanding"}
    assert f["filed"].iloc[0] == pd.Timestamp("2014-05-01")
    assert f["cik"].iloc[0] == "0000001000"
