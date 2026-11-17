"""Read and write tables. Parquet when pyarrow is installed, gzipped CSV otherwise."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def _has_pyarrow() -> bool:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return True


def save_table(df: pd.DataFrame, stem: Path) -> Path:
    """Write df to stem.parquet or stem.csv.gz and return the path written."""
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    if _has_pyarrow():
        path = stem.with_suffix(".parquet")
        df.to_parquet(path, index=False)
    else:
        path = stem.with_suffix(".csv.gz")
        df.to_csv(path, index=False)
    return path


def load_table(stem: Path, date_cols: tuple[str, ...] = (), str_cols: tuple[str, ...] = ("cik",)) -> pd.DataFrame:
    stem = Path(stem)
    parquet = stem.with_suffix(".parquet")
    csv = stem.with_suffix(".csv.gz")
    if parquet.exists():
        df = pd.read_parquet(parquet)
    elif csv.exists():
        dtypes = {c: str for c in str_cols}
        df = pd.read_csv(csv, dtype=dtypes)
    else:
        raise FileNotFoundError(f"No table at {parquet} or {csv}")
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def asof_merge(left: pd.DataFrame, right: pd.DataFrame, left_on: str, right_on: str, by: str = "cik", **kw) -> pd.DataFrame:
    """pd.merge_asof with keys coerced to matching types.

    pandas infers microsecond or nanosecond datetimes depending on the source
    (CSV, parquet, date_range), and merge_asof refuses to mix them.
    """
    left = left.copy()
    right = right.copy()
    left[left_on] = pd.to_datetime(left[left_on]).astype("datetime64[ns]")
    right[right_on] = pd.to_datetime(right[right_on]).astype("datetime64[ns]")
    left[by] = left[by].astype(str)
    right[by] = right[by].astype(str)
    return pd.merge_asof(
        left.sort_values(left_on), right.sort_values(right_on), left_on=left_on, right_on=right_on, by=by, **kw
    )
