"""Save and load the pipeline's intermediate tables in one consistent way."""

from __future__ import annotations

import json

import pandas as pd

from podbook.config import Paths
from podbook.edgar.facts import FACT_DATE_COLUMNS
from podbook.tables import load_table, save_table

_LIST_COLS = ("tickers", "exchanges")


def save_companies(df: pd.DataFrame, paths: Paths) -> None:
    out = df.copy()
    for col in _LIST_COLS:
        out[col] = out[col].map(json.dumps)
    save_table(out, paths.interim / "companies")


def load_companies(paths: Paths) -> pd.DataFrame:
    df = load_table(paths.interim / "companies", date_cols=("first_periodic", "last_periodic"))
    for col in _LIST_COLS:
        df[col] = df[col].map(lambda v: json.loads(v) if isinstance(v, str) else list(v))
    df["in_universe_sic"] = df["in_universe_sic"].astype(bool)
    if "coverage_addition" not in df.columns:
        df["coverage_addition"] = False
    df["coverage_addition"] = df["coverage_addition"].fillna(False).astype(bool)
    return df


def save_filings(df: pd.DataFrame, paths: Paths) -> None:
    save_table(df, paths.interim / "filings")


def load_filings(paths: Paths) -> pd.DataFrame:
    return load_table(paths.interim / "filings", date_cols=("filing_date", "report_date"))


def save_facts(df: pd.DataFrame, paths: Paths) -> None:
    save_table(df, paths.interim / "facts")


def load_facts(paths: Paths) -> pd.DataFrame:
    return load_table(paths.interim / "facts", date_cols=FACT_DATE_COLUMNS)


def save_mapping(df: pd.DataFrame, paths: Paths) -> None:
    # Plain CSV so it can be read and checked by eye.
    paths.interim.mkdir(parents=True, exist_ok=True)
    df.to_csv(paths.interim / "cik_ticker_map.csv", index=False)


def load_mapping(paths: Paths) -> pd.DataFrame:
    df = pd.read_csv(paths.interim / "cik_ticker_map.csv", dtype={"cik": str}, parse_dates=["valid_from", "valid_to"])
    df["is_primary"] = df["is_primary"].astype(bool)
    return df


def save_panel(df: pd.DataFrame, paths: Paths) -> None:
    save_table(df, paths.interim / "universe_panel")


def load_panel(paths: Paths) -> pd.DataFrame:
    return load_table(paths.interim / "universe_panel", date_cols=("date", "price_date", "shares_filed"))
