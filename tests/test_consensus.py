import json

import pandas as pd
import pytest

from podbook.consensus import log_consensus


class FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    @property
    def eps_trend(self):
        if self.symbol == "BAD":
            raise KeyError("no data")
        return pd.DataFrame(
            {"current": [1.1], "7daysAgo": [1.0], "30daysAgo": [1.0], "60daysAgo": [0.9], "90daysAgo": [0.9]},
            index=pd.Index(["0q"], name="period"),
        )

    eps_revisions = property(lambda self: pd.DataFrame())
    earnings_estimate = property(lambda self: pd.DataFrame({"avg": [1.1]}, index=["0q"]))
    revenue_estimate = property(lambda self: None)
    growth_estimates = property(lambda self: None)
    analyst_price_targets = property(lambda self: {"mean": 120.0, "high": 150.0})


class FakeYF:
    __version__ = "test"

    @staticmethod
    def Ticker(symbol):
        return FakeTicker(symbol)


def test_logger_writes_dated_snapshot_and_records_failures(tmp_path):
    cov = tmp_path / "coverage.csv"
    pd.DataFrame({"rank": [1, 2], "ticker": ["ALIV", "BAD"]}).to_csv(cov, index=False)
    out = log_consensus(cov, tmp_path / "consensus", date="2026-11-21", yf_module=FakeYF, pause=0)
    assert out.name == "2026-11-21"
    trend = pd.read_csv(out / "eps_trend.csv")
    assert trend["ticker"].tolist() == ["ALIV"]
    assert {"period", "current", "90daysAgo", "snapshot_date"} <= set(trend.columns)
    manifest = json.loads((out / "manifest.json").read_text())
    assert "eps_trend" in manifest["failures"]["BAD"]
    with pytest.raises(FileExistsError):
        log_consensus(cov, tmp_path / "consensus", date="2026-11-21", yf_module=FakeYF, pause=0)
