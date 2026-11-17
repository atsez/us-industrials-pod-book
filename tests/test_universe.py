"""Mandatory survivorship tests plus market-cap and gap handling."""

import numpy as np
import pandas as pd
import pytest
from conftest import companyfacts, fact

from podbook.edgar.facts import parse_companyfacts
from podbook.universe import (
    build_universe,
    completeness_report,
    freeze_coverage,
    rebalance_dates,
    select_coverage,
)


def _company(cik, sic, subsector, first, last, name=None):
    return {
        "cik": cik, "name": name or cik, "sic": sic, "subsector": subsector, "in_universe_sic": subsector is not None,
        "first_periodic": pd.Timestamp(first), "last_periodic": pd.Timestamp(last), "tickers": [], "exchanges": [],
    }


def _prices(cik, start, end, close):
    d = pd.bdate_range(start, end)
    return pd.DataFrame({"cik": cik, "date": d, "close_raw": close, "close": close, "adj_close": close})


def _shares(cik_int, value, quarters):
    """One cover-page share count per quarter, filed 35 days after quarter end."""
    obs = [fact(value, (q - pd.Timedelta(days=5)).date().isoformat(), (q + pd.Timedelta(days=35)).date().isoformat())
           for q in quarters]
    return obs


@pytest.fixture
def world():
    q = pd.date_range("2009-03-31", "2026-09-30", freq="QE")
    companies = pd.DataFrame(
        [
            _company("0000001000", 3728, "aerospace_defence", "2008-03-01", "2026-08-01", "Alive Aero"),
            # Acquired in mid-2015: filings stop in May, trading stops in July.
            _company("0000002000", 3561, "machinery", "2009-05-01", "2015-05-01", "Gone Machinery"),
            # Delisted with no prices anywhere: only filings and a public float.
            _company("0000006000", 3612, "electrical_equipment", "2009-05-01", "2013-11-01", "Ghost Electric"),
            # Small company
            _company("0000007000", 3531, "machinery", "2009-05-01", "2026-08-01", "Tiny Tractors"),
            # Out of the SIC rules: never in the panel even though it is large.
            _company("0000008000", 3559, None, "2009-05-01", "2026-08-01", "Chip Tools"),
        ]
    )
    facts = pd.concat(
        [
            parse_companyfacts(companyfacts(1000, _shares(1000, 100e6, q))),
            parse_companyfacts(companyfacts(2000, _shares(2000, 50e6, q[q <= "2015-03-31"]))),
            parse_companyfacts(
                companyfacts(6000, _shares(6000, 80e6, q[q <= "2013-09-30"]),
                             public_float=[fact(4e9, "2012-06-30", "2013-02-20", form="10-K")],
                             gaap={"Assets": {"units": {"USD": [fact(3e9, "2012-12-31", "2013-02-20", form="10-K")]}}})
            ),
            parse_companyfacts(companyfacts(7000, _shares(7000, 10e6, q))),
            parse_companyfacts(companyfacts(8000, _shares(8000, 500e6, q))),
        ],
        ignore_index=True,
    )
    prices = pd.concat(
        [
            _prices("0000001000", "2009-01-01", "2026-11-13", 50.0),  # 5bn
            _prices("0000002000", "2009-01-01", "2015-07-15", 40.0),  # 2bn
            _prices("0000007000", "2009-01-01", "2026-11-13", 20.0),  # 0.2bn
            _prices("0000008000", "2009-01-01", "2026-11-13", 30.0),
        ],
        ignore_index=True,
    )
    return companies, facts, prices


def test_delisted_name_stays_in_universe_while_listed(world):
    companies, facts, prices = world
    panel = build_universe(companies, facts, prices, rebalance_dates("2010-01-31", "2026-10-31"))
    gone = panel[panel["cik"] == "0000002000"]
    assert gone.loc[gone["date"] == "2014-12-31", "in_universe"].item()
    assert gone.loc[gone["date"] == "2015-06-30", "in_universe"].item()
    # Trading stopped mid-July 2015: no row after that.
    assert gone["date"].max() == pd.Timestamp("2015-06-30")


def test_unpriced_delisted_name_is_a_large_gap_not_silently_dropped(world):
    companies, facts, prices = world
    panel = build_universe(companies, facts, prices, rebalance_dates("2013-03-31", "2013-12-31"))
    ghost = panel[panel["cik"] == "0000006000"].set_index("date")
    assert (ghost["status"] == "gap_large").all()
    assert (ghost["gap_reason"] == "no_price").all()
    assert not ghost["in_universe"].any()


def test_market_cap_threshold_and_sic_rules(world):
    companies, facts, prices = world
    panel = build_universe(companies, facts, prices, rebalance_dates("2020-01-31", "2020-03-31"))
    by_cik = panel.groupby("cik")["status"].first()
    assert by_cik["0000001000"] == "member"
    assert by_cik["0000007000"] == "small"
    assert "0000008000" not in by_cik.index


def test_shares_filed_after_rebalance_date_are_not_used(world):
    companies, facts, prices = world
    # A huge share count for company 7000, filed one day after the rebalance date.
    late = parse_companyfacts(companyfacts(7000, [fact(10e9, "2020-01-25", "2020-02-01")]))
    panel = build_universe(companies, pd.concat([facts, late]), prices, pd.DatetimeIndex(["2020-01-31"]))
    tiny = panel[panel["cik"] == "0000007000"].iloc[0]
    assert tiny["status"] == "small"
    assert tiny["shares_filed"] <= pd.Timestamp("2020-01-31")


def test_price_after_rebalance_date_is_not_used(world):
    companies, facts, prices = world
    # Company 1000 has no price in the 10 days before 2020-01-31, only after it.
    pruned = prices[~((prices["cik"] == "0000001000") & prices["date"].between("2020-01-15", "2020-01-31"))]
    panel = build_universe(companies, facts, pruned, pd.DatetimeIndex(["2020-01-31"]))
    alive = panel[panel["cik"] == "0000001000"].iloc[0]
    assert pd.isna(alive["close_raw"])
    assert alive["status"] != "member"


def test_completeness_counts_large_gaps(world):
    companies, facts, prices = world
    panel = build_universe(companies, facts, prices, rebalance_dates("2013-03-31", "2013-06-30"))
    by_date, totals = completeness_report(panel)
    # Members: 1000 and 2000. Large gap: 6000. Small: 7000 (not counted).
    assert by_date["members"].tolist() == [2, 2, 2, 2]
    assert by_date["gap_large"].tolist() == [1, 1, 1, 1]
    assert totals["share_complete"] == pytest.approx(2 / 3)


def test_coverage_uses_latest_date_and_freezes_once(world, tmp_path):
    companies, facts, prices = world
    panel = build_universe(companies, facts, prices, pd.DatetimeIndex(["2026-11-16"]))
    mapping = pd.DataFrame(
        [{"cik": "0000001000", "ticker": "ALIV", "exchange": "NYSE", "valid_from": pd.NaT, "valid_to": pd.NaT,
          "source": "sec_current", "is_primary": True}]
    )
    cov = select_coverage(panel, mapping, n=40)
    assert cov["ticker"].tolist() == ["ALIV"]
    path = tmp_path / "coverage_list.csv"
    freeze_coverage(cov, path, "2026-11-16")
    with pytest.raises(FileExistsError):
        freeze_coverage(cov, path, "2026-11-17")
    assert "mcap" not in pd.read_csv(path).columns  # no Yahoo-derived numbers in the committed file


def test_weekend_month_end_keeps_a_name_that_traded_on_the_friday(world):
    companies, facts, prices = world
    # Gone Machinery's last trade is Friday 29 May 2015; 31 May is a Sunday.
    cut = prices[~((prices["cik"] == "0000002000") & (prices["date"] > "2015-05-29"))]
    panel = build_universe(companies, facts, cut, rebalance_dates("2015-04-30", "2015-07-31"))
    gone = panel[panel["cik"] == "0000002000"].set_index("date")
    assert gone.loc["2015-05-31", "in_universe"]
    assert pd.Timestamp("2015-06-30") not in gone.index


def _float_world(float_val, assets_val, float_end, float_filed):
    companies = pd.DataFrame([_company("0000009000", 3561, "machinery", "2009-05-01", "2026-08-01", "No Price Co")])
    gaap = {} if assets_val is None else {"Assets": {"units": {"USD": [fact(assets_val, float_end, float_filed, form="10-K")]}}}
    facts = parse_companyfacts(companyfacts(9000, [], public_float=[fact(float_val, float_end, float_filed, form="10-K")], gaap=gaap))
    return companies, facts, pd.DataFrame(columns=["cik", "date", "close_raw", "close", "adj_close"])


def test_public_float_still_counts_in_january_before_the_next_10k():
    # Float measured mid-2014, filed Feb 2015; the next one arrives Feb 2016.
    companies, facts, prices = _float_world(4e9, 3e9, "2014-06-30", "2015-02-20")
    panel = build_universe(companies, facts, prices, pd.DatetimeIndex(["2016-01-31"]))
    assert panel["status"].item() == "gap_large"


def test_mis_scaled_public_float_is_ignored():
    companies, facts, prices = _float_world(3.5e12, 1.5e9, "2014-06-30", "2015-02-20")
    panel = build_universe(companies, facts, prices, pd.DatetimeIndex(["2015-06-30"]))
    assert panel["status"].item() == "gap_unknown"
    assert np.isnan(panel["public_float"].item())


def test_coverage_additions_only_enter_when_named(world):
    companies, facts, prices = world
    dates = pd.DatetimeIndex(["2020-01-31"])
    assert "0000008000" not in set(build_universe(companies, facts, prices, dates)["cik"])
    with_extra = build_universe(companies, facts, prices, dates, extra_ciks={"0000008000"})
    assert with_extra.set_index("cik").loc["0000008000", "in_universe"]


def test_gap_report_lists_unpriced_large_names(world):
    from podbook.universe import gap_report

    companies, facts, prices = world
    panel = build_universe(companies, facts, prices, rebalance_dates("2013-03-31", "2014-06-30"))
    gaps = gap_report(panel)
    assert gaps["cik"].tolist() == ["0000006000"]
    assert gaps["main_reason"].item() == "no_price"
    assert not gaps["listed_at_panel_end"].item()


def test_coverage_exclusions_are_dropped(world):
    companies, facts, prices = world
    panel = build_universe(companies, facts, prices, pd.DatetimeIndex(["2026-11-16"]), extra_ciks={"0000008000"})
    mapping = pd.DataFrame(
        [{"cik": c, "ticker": t, "exchange": "NYSE", "valid_from": pd.NaT, "valid_to": pd.NaT, "source": "sec_current", "is_primary": True}
         for c, t in (("0000001000", "ALIV"), ("0000008000", "CHIP"))]
    )
    assert select_coverage(panel, mapping, n=40)["ticker"].tolist() == ["CHIP", "ALIV"]
    assert select_coverage(panel, mapping, n=40, exclude_tickers={"CHIP"})["ticker"].tolist() == ["ALIV"]


def test_zero_float_and_empty_early_months_do_not_break_reports(world):
    from podbook.universe import mcap_anomalies

    companies, facts, prices = world
    zero = parse_companyfacts(companyfacts(1000, [], public_float=[fact(0, "2014-06-30", "2015-02-20", form="10-K")],
                                           gaap={"Assets": {"units": {"USD": [fact(5e9, "2014-12-31", "2015-02-20", form="10-K")]}}}))
    # Start before any filing exists so the first months have no facts at all.
    panel = build_universe(companies, pd.concat([facts, zero]), prices, rebalance_dates("2009-01-31", "2015-06-30"))
    assert panel["public_float"].dtype == float
    mcap_anomalies(panel)  # must not raise


def test_market_cap_far_above_total_assets_is_rejected(world):
    companies, facts, prices = world
    # Company 1000 trades at 50 with 100m shares (5bn). Give it 10m of assets: 500 times.
    tiny_assets = parse_companyfacts(companyfacts(1000, [], gaap={"Assets": {"units": {"USD": [fact(1e7, "2019-12-31", "2020-01-10", form="10-K")]}}}))
    panel = build_universe(companies, pd.concat([facts, tiny_assets]), prices, pd.DatetimeIndex(["2020-01-31"]))
    row = panel.set_index("cik").loc["0000001000"]
    assert not row["in_universe"]
    assert row["gap_reason"] == "bad_share_count"
