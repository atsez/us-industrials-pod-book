"""Paths, constants and environment settings.

Every threshold used by the universe build lives here so it can be quoted
in HYPOTHESES.md and changed in one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Universe rules
MCAP_MIN_USD = 1_000_000_000
COVERAGE_SIZE = 40
# Companyfacts only covers the XBRL era. A candidate must have filed a 10-K or
# 10-Q on or after this date to be worth fetching.
XBRL_ERA_START = "2009-01-01"
UNIVERSE_START = "2009-06-30"

# Point-in-time tolerances
PRICE_STALENESS_DAYS = 7  # last close must be within this many days of the rebalance date
SHARES_STALENESS_DAYS = 400  # shares outstanding older than this are ignored
# Public float is measured at the end of Q2 and reported in the following 10-K,
# so the latest one can be up to about 20 months old. 550 days was too short and
# left a hole every January before 10-K season.
FLOAT_STALENESS_DAYS = 700
# Public float in XBRL is sometimes mis-scaled by a factor of 1,000 or more. A
# float above this multiple of total assets is treated as an error.
FLOAT_MAX_ASSET_MULTIPLE = 20
# A market cap above this multiple of total assets means a mis-scaled share count
# (the third run found Woodward, Terex and Rockwell Collins sized in the trillions).
MCAP_MAX_ASSET_MULTIPLE = 50
LISTING_GRACE_DAYS = 120  # a filer counts as listed this long after its last 10-K/10-Q

# EDGAR fair-access rules allow 10 requests a second. Stay under it.
SEC_MAX_REQUESTS_PER_SECOND = 8

PERIODIC_FORMS = frozenset(
    {"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A", "10-QT", "10-QT/A", "10-K405"}
)
EARNINGS_8K_ITEM = "2.02"  # Results of Operations and Financial Condition


@dataclass(frozen=True)
class Paths:
    root: Path

    @classmethod
    def from_env(cls) -> "Paths":
        return cls(Path(os.environ.get("PODBOOK_ROOT", ".")).resolve())

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def raw_edgar(self) -> Path:
        return self.data / "raw" / "edgar"

    @property
    def bulk_submissions_zip(self) -> Path:
        return self.raw_edgar / "submissions.zip"

    @property
    def tickers_json(self) -> Path:
        return self.raw_edgar / "company_tickers_exchange.json"

    @property
    def companyfacts_dir(self) -> Path:
        return self.raw_edgar / "companyfacts"

    @property
    def prices_dir(self) -> Path:
        return self.data / "raw" / "prices"

    @property
    def interim(self) -> Path:
        return self.data / "interim"

    @property
    def yahoo_consensus(self) -> Path:
        return self.data / "yahoo" / "consensus"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    @property
    def reports(self) -> Path:
        return self.data / "reports"

    # Committed reference files (no Yahoo-derived numbers in here)
    @property
    def reference(self) -> Path:
        return self.root / "reference"

    @property
    def coverage_csv(self) -> Path:
        return self.reference / "coverage_list.csv"

    @property
    def coverage_adjustments_csv(self) -> Path:
        return self.reference / "coverage_adjustments.csv"

    @property
    def ticker_overrides_csv(self) -> Path:
        return self.reference / "ticker_overrides.csv"


def sec_user_agent() -> str:
    """EDGAR requires a User-Agent naming you and giving a contact email."""
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua or "@" not in ua:
        raise RuntimeError(
            "Set SEC_USER_AGENT to your name and email, for example\n"
            "  export SEC_USER_AGENT='Jane Smith jane@example.com'"
        )
    return ua


# ---- Stage 1 and 2 settings. Every value here is quoted in HYPOTHESES.md. ----

# Filing data becomes usable this many calendar days after its filing date.
REPORTING_LAG_DAYS = 1
# Fundamentals older than this (period end before the rebalance date) are stale.
FUNDAMENTAL_MAX_AGE_DAYS = 200

# SUE (seasonal random walk on quarterly diluted EPS)
SUE_HISTORY_QUARTERS = 8
SUE_MIN_HISTORY = 6
SUE_MAX_AGE_DAYS = 120  # a SUE counts as a monthly factor for this long after the filing

# Drift replication
DRIFT_DAYS = 60
N_GROUPS = 5

# Screen
MIN_FACTORS = 3  # a name needs at least this many of the five factors to get a combined score
IN_SAMPLE = ("2012-01-31", "2019-12-31")
# Formation month ends. The last out-of-sample return month is October 2026,
# fixed here so the result does not depend on the day the backtest is run.
OUT_OF_SAMPLE = ("2020-01-31", "2026-09-30")
