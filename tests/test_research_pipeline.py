"""End to end on a synthetic world with a planted drift.

Thirty companies report quarterly EPS. After each earnings 8-K, a stock's
daily return gets a small push in the direction of its surprise for 60 days.
The pipeline must find that drift, keep the out-of-sample lock, and write
every report. This checks the plumbing and that the engine can see a real
effect; it says nothing about whether the effect exists in real data.
"""

import json

import numpy as np
import pandas as pd
import pytest
from factsutil import frame, instant_rows, row

from podbook import store
from podbook.cli import main
from podbook.config import Paths

N = 30
QUARTERS = pd.date_range("2009-03-31", "2021-06-30", freq="QE")
DAYS = pd.bdate_range("2009-01-02", "2021-12-31")


def _world(seed=1):
    rng = np.random.default_rng(seed)
    facts, filings, companies, prices = [], [], [], {}
    for i in range(N):
        cik = f"{1000 + i:010d}"
        u = rng.normal(0, 0.1, len(QUARTERS))
        eps = list(1.0 + rng.normal(0, 0.05, 4))
        for k in range(4, len(QUARTERS)):
            eps.append(eps[k - 4] + u[k])
        drift = np.zeros(len(DAYS))
        for k, e in enumerate(QUARTERS):
            start = (e - pd.offsets.QuarterBegin(startingMonth=1)).normalize()
            filed = e + pd.Timedelta(days=35)
            ann = e + pd.Timedelta(days=25)
            s, en, f = start.date().isoformat(), e.date().isoformat(), filed.date().isoformat()
            facts.append(row(cik, "EarningsPerShareDiluted", eps[k], en, f, start=s, unit="USD/shares"))
            facts.append(row(cik, "Revenues", 1e6 * (1000 + 10 * k + rng.normal(0, 20)), en, f, start=s))
            facts.append(row(cik, "OperatingIncomeLoss", 1e6 * (100 + rng.normal(0, 10)), en, f, start=s))
            facts.append(row(cik, "NetIncomeLoss", 1e6 * (70 + rng.normal(0, 10)), en, f, start=s))
            facts.append(row(cik, "NetCashProvidedByUsedInOperatingActivities", 1e6 * (80 + rng.normal(0, 10)), en, f, start=s))
            facts.append(row(cik, "PaymentsToAcquirePropertyPlantAndEquipment", 2e7, en, f, start=s))
            facts.append(row(cik, "InterestExpense", 5e6, en, f, start=s))
            facts.append(row(cik, "EntityCommonStockSharesOutstanding", 1e8, en, f, unit="shares"))
            filings.append({"cik": cik, "accession": f"{cik}-{k}-q", "form": "10-Q", "filing_date": filed,
                            "report_date": e, "acceptance_datetime": None, "items": ""})
            filings.append({"cik": cik, "accession": f"{cik}-{k}-8k", "form": "8-K", "filing_date": ann,
                            "report_date": ann, "acceptance_datetime": None, "items": "2.02,9.01"})
            if k >= 4:
                d0 = int(np.searchsorted(DAYS, ann))
                drift[d0 + 1 : d0 + 61] += 0.003 * np.clip(u[k] / 0.1, -2, 2)
        # Balance sheet in dollars at a realistic scale for a 5bn company, so the
        # market-cap-to-assets sanity check passes.
        for tag, vals in (("Assets", 5e9), ("StockholdersEquity", 2.5e9), ("LongTermDebt", 1e9),
                          ("CashAndCashEquivalentsAtCarryingValue", 2e8)):
            facts += instant_rows(cik, tag, [vals] * len(QUARTERS), first_q_end="2009-03-31")
        r = rng.normal(0, 0.01, len(DAYS)) + drift
        px = 50 * np.cumprod(1 + r)
        prices[cik] = pd.DataFrame({"date": DAYS, "close_raw": px, "close": px, "adj_close": px, "volume": 1.0,
                                    "split_ratio": 0.0, "source": "yahoo"})
        companies.append({"cik": cik, "name": f"Co {i}", "sic": 3561, "sic_description": "", "tickers": [f"T{i}"],
                          "exchanges": ["NYSE"], "entity_type": "operating", "first_periodic": pd.Timestamp("2009-05-05"),
                          "last_periodic": pd.Timestamp("2021-08-04"), "n_periodic": len(QUARTERS),
                          "subsector": "machinery" if i % 2 else "aerospace_defence", "in_universe_sic": True})
    return frame(facts), pd.DataFrame(filings), pd.DataFrame(companies), prices


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    root = tmp_path_factory.mktemp("world")
    paths = Paths(root)
    facts, filings, companies, prices = _world()
    store.save_companies(companies, paths)
    store.save_filings(filings, paths)
    store.save_facts(facts, paths)
    paths.prices_dir.mkdir(parents=True)
    for cik, df in prices.items():
        df.to_csv(paths.prices_dir / f"{cik}.csv", index=False)
    mp = pytest.MonkeyPatch()
    mp.setenv("PODBOOK_ROOT", str(root))
    assert main(["build-universe", "--start", "2009-06-30", "--asof", "2021-12-31"]) == 0
    assert main(["build-events"]) == 0
    assert main(["build-factors"]) == 0
    assert main(["backtest"]) == 0
    yield paths, mp
    mp.undo()


def test_drift_replication_finds_planted_drift(ran):
    paths, _ = ran
    summary = json.loads((paths.reports / "drift_summary.json").read_text())
    primary = summary["variants"]["primary"]
    assert primary["events"] > 500
    assert primary["spread_mean"] > 0.05
    assert primary["spread_t"] > 3
    assert summary["announcement_date_source"] == {"8-K 2.02": 1.0}
    by_group = pd.read_csv(paths.reports / "drift_by_group.csv")
    assert by_group.sort_values("group")["mean"].is_monotonic_increasing


def test_every_robustness_variant_is_reported(ran):
    paths, _ = ran
    variants = json.loads((paths.reports / "drift_summary.json").read_text())["variants"]
    assert set(variants) == {
        "primary", "window_days_2_to_61", "day0_is_10q_filing_date", "sue_with_drift_term",
        "excluding_recent_splits_and_no_split_data", "excluding_names_with_price_gaps", "deciles",
    }


def test_in_sample_backtest_sees_the_surprise_factor(ran):
    paths, _ = ran
    summary = json.loads((paths.reports / "backtest_in_sample" / "summary.json").read_text())
    assert set(summary) == {"pooled", "within_subsector", "pooled_excluding_price_gap_names"}
    surprise = summary["pooled"]["surprise"]
    assert surprise["spread"]["mean_monthly"] > 0
    assert surprise["ic_mean"] > 0
    assert summary["pooled"]["combined"]["spread"]["months"] > 50


def test_factor_panel_never_uses_future_filings(ran):
    paths, _ = ran
    from podbook.tables import load_table

    m = load_table(paths.interim / "factor_measures", date_cols=("date", "info_date"))
    used = m.dropna(subset=["info_date"])
    assert (used["info_date"] <= used["date"] - pd.Timedelta(days=1)).all()


def test_out_of_sample_runs_once(ran):
    paths, _ = ran
    assert main(["backtest", "--period", "out_of_sample"]) == 0
    from podbook.backtest import LockedPeriodError

    with pytest.raises(LockedPeriodError):
        main(["backtest", "--period", "out_of_sample"])
    assert main(["backtest", "--period", "out_of_sample", "--reason", "test rerun"]) == 0
