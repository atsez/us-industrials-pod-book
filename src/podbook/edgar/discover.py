"""Find every candidate company, dead or alive, from the EDGAR bulk submissions zip.

Why the bulk file: company_tickers.json only lists companies that trade today.
Building the universe from it would drop every company that was acquired or
delisted, which is exactly the survivorship bias we must avoid. The bulk zip has
a submissions record for every CIK that has ever filed electronically.
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Callable

import pandas as pd

from podbook.config import XBRL_ERA_START
from podbook.edgar.submissions import (
    FILING_COLUMNS,
    all_filings,
    company_meta,
    keep_relevant_filings,
    periodic_summary,
)
from podbook.sic import classify_sic, is_candidate_sic, parse_sic

log = logging.getLogger(__name__)

_SIC_RE = re.compile(rb'"sic"\s*:\s*"(\d*)"')
_PRIMARY_RE = re.compile(r"^CIK(\d{10})\.json$")
_OVERFLOW_RE = re.compile(r"^CIK(\d{10})-submissions-\d+\.json$")


def _quick_sic(head: bytes) -> int | None | str:
    """Read the SIC field from the start of a record. 'unknown' if not found there."""
    match = _SIC_RE.search(head)
    if match is None:
        return "unknown"
    return parse_sic(match.group(1).decode())


def scan_bulk_submissions(
    zip_path: Path,
    keep_sic: Callable[[object], bool] = is_candidate_sic,
    since: str = XBRL_ERA_START,
    extra_ciks: set[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (companies, filings) for candidates in the bulk submissions zip.

    A company is kept if its SIC passes keep_sic and it filed at least one
    10-K or 10-Q on or after `since`. Delisted companies are kept.
    """
    since_ts = pd.Timestamp(since)
    companies: list[dict] = []
    filing_frames: list[pd.DataFrame] = []

    with zipfile.ZipFile(zip_path) as zf:
        primaries: list[str] = []
        overflow: dict[str, list[str]] = defaultdict(list)
        for name in zf.namelist():
            base = Path(name).name
            if m := _PRIMARY_RE.match(base):
                primaries.append((name, m.group(1)))
            elif m := _OVERFLOW_RE.match(base):
                overflow[m.group(1)].append(name)

        log.info("Scanning %d submissions records", len(primaries))
        extra = extra_ciks or set()
        for i, (name, file_cik) in enumerate(primaries, 1):
            if i % 100_000 == 0:
                log.info("scanned %d / %d", i, len(primaries))
            # Read only the head of each record to check the SIC code, so the
            # large records of out-of-range filers are never fully decompressed.
            with zf.open(name) as fh:
                head = fh.read(8000)
            is_extra = file_cik in extra
            quick = _quick_sic(head)
            if not is_extra and quick != "unknown" and not keep_sic(quick):
                continue
            sub = json.loads(zf.read(name))
            if not is_extra and not keep_sic(sub.get("sic")):
                continue
            meta = company_meta(sub)
            pages = [json.loads(zf.read(o)) for o in sorted(overflow.get(meta["cik"], []))]
            filings = all_filings(sub, pages)
            summary = periodic_summary(filings)
            if summary["n_periodic"] == 0 or summary["last_periodic"] < since_ts:
                continue
            meta.update(summary)
            meta["subsector"] = classify_sic(meta["sic"])
            meta["in_universe_sic"] = meta["subsector"] is not None
            meta["coverage_addition"] = is_extra and meta["subsector"] is None
            companies.append(meta)
            filing_frames.append(keep_relevant_filings(filings))

    companies_df = pd.DataFrame(companies)
    if companies_df.empty:
        companies_df = pd.DataFrame(
            columns=["cik", "name", "sic", "sic_description", "tickers", "exchanges", "entity_type",
                     "first_periodic", "last_periodic", "n_periodic", "subsector", "in_universe_sic", "coverage_addition"]
        )
    filings_df = (
        pd.concat(filing_frames, ignore_index=True) if filing_frames else pd.DataFrame(columns=FILING_COLUMNS)
    )
    log.info("Kept %d candidate companies", len(companies_df))
    return companies_df, filings_df
