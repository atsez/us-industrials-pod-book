"""Runs the offline CLI steps end to end on synthetic data stored the way fetch-edgar stores it."""

import json

import pandas as pd
from conftest import companyfacts, fact

from podbook import store
from podbook.cli import main
from podbook.config import Paths
from podbook.edgar.discover import scan_bulk_submissions
from podbook.edgar.facts import parse_companyfacts
from podbook.mapping import build_mapping, parse_tickers_exchange


def test_offline_steps(tmp_path, bulk_zip, monkeypatch):
    paths = Paths(tmp_path)
    monkeypatch.setenv("PODBOOK_ROOT", str(tmp_path))
    paths.raw_edgar.mkdir(parents=True)
    bulk_zip.rename(paths.bulk_submissions_zip)

    tickers_js = {"fields": ["cik", "name", "ticker", "exchange"], "data": [[1000, "Alive Aero", "ALIV", "NYSE"]]}
    paths.tickers_json.write_text(json.dumps(tickers_js))

    companies, filings = scan_bulk_submissions(paths.bulk_submissions_zip)
    store.save_companies(companies, paths)
    store.save_filings(filings, paths)

    q = pd.date_range("2009-03-31", "2026-09-30", freq="QE")
    shares = [fact(100e6, (d - pd.Timedelta(days=5)).date().isoformat(), (d + pd.Timedelta(days=35)).date().isoformat()) for d in q]
    facts = pd.concat(
        [
            parse_companyfacts(companyfacts(1000, shares, public_float=[fact(4e9, "2025-06-30", "2026-02-10", form="10-K")])),
            parse_companyfacts(companyfacts(2000, shares[:24], public_float=[fact(3e9, "2014-06-30", "2015-02-10", form="10-K")],
                                            gaap={"Assets": {"units": {"USD": [fact(2e9, "2014-12-31", "2015-02-10", form="10-K")]}}})),
        ]
    )
    store.save_facts(facts, paths)
    mapping, _ = build_mapping(parse_tickers_exchange(tickers_js), companies)
    store.save_mapping(mapping, paths)

    paths.prices_dir.mkdir(parents=True)
    d = pd.bdate_range("2009-01-02", "2026-11-13")
    pd.DataFrame({"date": d, "close_raw": 50.0, "close": 50.0, "adj_close": 50.0, "volume": 1.0,
                  "split_ratio": 0.0, "source": "yahoo"}).to_csv(paths.prices_dir / "0000001000.csv", index=False)

    assert main(["build-universe", "--start", "2012-01-31", "--asof", "2026-11-16"]) == 0
    totals = json.loads((paths.reports / "universe_completeness.json").read_text())
    assert totals["distinct_members"] == 1
    assert totals["gap_large_months"] > 0  # Gone Machinery has no prices but a 3bn float
    assert store.load_panel(paths)["cik"].str.len().eq(10).all()

    assert main(["review-universe", "--asof", "2026-11-16"]) == 0
    report = (paths.reports / "universe_review.md").read_text()
    assert "Gone Machinery (3.0bn, gone)" in report
    assert "| BA |" in report  # anchors not in the ticker file are still listed

    assert main(["freeze-coverage", "--asof", "2026-11-16"]) == 0
    cov = pd.read_csv(paths.coverage_csv)
    assert cov["ticker"].tolist() == ["ALIV"]
