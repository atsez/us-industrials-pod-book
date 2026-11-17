"""Synthetic EDGAR and price data. No test touches the network."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest


def submissions_record(cik: int, name: str, sic: str, filings: list[tuple[str, str, str]], tickers=(), exchanges=()) -> dict:
    """filings: (form, filing_date, items)."""
    return {
        "cik": str(cik),
        "name": name,
        "sic": sic,
        "sicDescription": "",
        "entityType": "operating",
        "tickers": list(tickers),
        "exchanges": list(exchanges),
        "filings": {"recent": columnar(cik, filings), "files": []},
    }


def columnar(cik: int, filings: list[tuple[str, str, str]]) -> dict:
    return {
        "accessionNumber": [f"{cik:010d}-{f[1].replace('-', '')}-{f[0]}" for f in filings],
        "form": [f[0] for f in filings],
        "filingDate": [f[1] for f in filings],
        "reportDate": [f[1] for f in filings],
        "acceptanceDateTime": [f[1] + "T16:05:00.000Z" for f in filings],
        "items": [f[2] for f in filings],
    }


def quarterly_10q(start: str, end: str) -> list[tuple[str, str, str]]:
    return [("10-Q", d.date().isoformat(), "") for d in pd.date_range(start, end, freq="QE") + pd.Timedelta(days=35)]


def fact(val, end, filed, start=None, form="10-Q", fy=2014, fp="Q1", accn="a"):
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}


def companyfacts(cik: int, shares: list[dict], public_float: list[dict] | None = None, gaap: dict | None = None) -> dict:
    facts = {
        "dei": {
            "EntityCommonStockSharesOutstanding": {"units": {"shares": shares}},
            "EntityPublicFloat": {"units": {"USD": public_float or []}},
        },
        "us-gaap": gaap or {},
    }
    return {"cik": cik, "entityName": "x", "facts": facts}


@pytest.fixture
def bulk_zip(tmp_path: Path) -> Path:
    """Four companies:
    1000 Alive Aero      SIC 3728, files 2008 to 2026, ticker ALIV
    2000 Gone Machinery  SIC 3561, files 2009 to 2015 (acquired), last ticker GONE
    3000 Old Electrical  SIC 3621, stopped filing in 2005 (before the XBRL era)
    4000 Software Co     SIC 7372, out of range
    Company 1000 has an overflow page holding its oldest 10-K.
    """
    path = tmp_path / "submissions.zip"
    recs = {
        "CIK0000001000.json": submissions_record(
            1000, "Alive Aero", "3728", quarterly_10q("2010-01-01", "2026-09-30") + [("8-K", "2026-10-20", "2.02,9.01")],
            tickers=["ALIV", "ALIV-PA"], exchanges=["NYSE", "NYSE"],
        ),
        "CIK0000002000.json": submissions_record(2000, "Gone Machinery", "3561", quarterly_10q("2009-01-01", "2015-03-31"), tickers=["GONE"], exchanges=["NYSE"]),
        "CIK0000003000.json": submissions_record(3000, "Old Electrical", "3621", quarterly_10q("2001-01-01", "2005-06-30")),
        "CIK0000004000.json": submissions_record(4000, "Software Co", "7372", quarterly_10q("2010-01-01", "2026-06-30")),
    }
    overflow = {"CIK0000001000-submissions-001.json": columnar(1000, [("10-K", "2008-03-01", "")])}
    with zipfile.ZipFile(path, "w") as zf:
        for name, rec in {**recs, **overflow}.items():
            zf.writestr(name, json.dumps(rec))
    return path


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self._content = content

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=1):
        yield self._content

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None, stream=False):
        self.calls.append({"url": url, "headers": headers})
        return self.responses.pop(0)
