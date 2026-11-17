import pandas as pd

from podbook.edgar.discover import scan_bulk_submissions
from podbook.sic import MACHINERY


def test_scan_keeps_dead_companies_and_drops_pre_xbrl_and_out_of_range(bulk_zip):
    companies, filings = scan_bulk_submissions(bulk_zip)
    ciks = set(companies["cik"])
    # Gone Machinery stopped filing in 2015 but must be in the candidate set.
    assert "0000002000" in ciks
    assert "0000001000" in ciks
    # Stopped filing before the XBRL era: no companyfacts, so not a candidate.
    assert "0000003000" not in ciks
    # Software: SIC out of range.
    assert "0000004000" not in ciks

    gone = companies.set_index("cik").loc["0000002000"]
    assert gone["subsector"] == MACHINERY
    assert gone["tickers"] == ["GONE"]


def test_overflow_page_supplies_oldest_filing(bulk_zip):
    companies, _ = scan_bulk_submissions(bulk_zip)
    alive = companies.set_index("cik").loc["0000001000"]
    assert alive["first_periodic"] == pd.Timestamp("2008-03-01")


def test_earnings_8k_kept_for_announcement_dates(bulk_zip):
    _, filings = scan_bulk_submissions(bulk_zip)
    eights = filings[filings["form"] == "8-K"]
    assert len(eights) == 1
    assert "2.02" in eights.iloc[0]["items"]


def test_named_extra_company_is_kept_outside_the_sic_rules(bulk_zip):
    companies, _ = scan_bulk_submissions(bulk_zip, extra_ciks={"0000004000"})
    soft = companies.set_index("cik").loc["0000004000"]
    assert soft["coverage_addition"]
    assert not soft["in_universe_sic"]
