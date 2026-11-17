"""Parse EDGAR submissions records: company metadata and filing history."""

from __future__ import annotations

import pandas as pd

from podbook.config import EARNINGS_8K_ITEM, PERIODIC_FORMS
from podbook.mapping import normalise_cik
from podbook.sic import parse_sic

FILING_FIELDS = {
    "accessionNumber": "accession",
    "form": "form",
    "filingDate": "filing_date",
    "reportDate": "report_date",
    "acceptanceDateTime": "acceptance_datetime",
    "items": "items",
}
FILING_COLUMNS = ["cik", *FILING_FIELDS.values()]


def company_meta(sub: dict) -> dict:
    """Top-level fields from a submissions record."""
    return {
        "cik": normalise_cik(sub["cik"]),
        "name": sub.get("name"),
        "sic": parse_sic(sub.get("sic")),
        "sic_description": sub.get("sicDescription"),
        "tickers": list(sub.get("tickers") or []),
        "exchanges": list(sub.get("exchanges") or []),
        "entity_type": sub.get("entityType"),
    }


def filings_frame(columnar: dict, cik: str) -> pd.DataFrame:
    """Turn EDGAR's column-oriented filing arrays into a DataFrame.

    Works for both filings.recent and the overflow files
    (CIK##########-submissions-001.json), which share the same layout.
    """
    n = len(columnar.get("accessionNumber") or [])
    data = {new: list(columnar.get(old) or [None] * n) for old, new in FILING_FIELDS.items()}
    df = pd.DataFrame(data)
    df.insert(0, "cik", normalise_cik(cik))
    for col in ("filing_date", "report_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df["items"] = df["items"].fillna("").astype(str)
    return df[FILING_COLUMNS]


def all_filings(sub: dict, overflow: list[dict] | None = None) -> pd.DataFrame:
    """Recent filings plus any overflow pages, de-duplicated on accession number."""
    cik = normalise_cik(sub["cik"])
    frames = [filings_frame(sub.get("filings", {}).get("recent", {}), cik)]
    frames += [filings_frame(page, cik) for page in (overflow or [])]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=FILING_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates("accession").sort_values("filing_date").reset_index(drop=True)


def keep_relevant_filings(filings: pd.DataFrame) -> pd.DataFrame:
    """10-K/10-Q filings plus 8-Ks carrying an earnings release (Item 2.02).

    The 8-K Item 2.02 date is the true earnings announcement date, usually days
    or weeks before the 10-Q. Kept for the drift replication in Stage 1.
    """
    periodic = filings["form"].isin(PERIODIC_FORMS)
    earnings_8k = filings["form"].eq("8-K") & filings["items"].str.contains(EARNINGS_8K_ITEM, regex=False)
    return filings[periodic | earnings_8k].reset_index(drop=True)


def periodic_summary(filings: pd.DataFrame) -> dict:
    """First and last 10-K/10-Q filing dates and a count."""
    periodic = filings[filings["form"].isin(PERIODIC_FORMS)]
    if periodic.empty:
        return {"first_periodic": pd.NaT, "last_periodic": pd.NaT, "n_periodic": 0}
    return {
        "first_periodic": periodic["filing_date"].min(),
        "last_periodic": periodic["filing_date"].max(),
        "n_periodic": int(len(periodic)),
    }
