"""Build companyfacts-style rows directly, for the Stage 1 and 2 tests."""

import pandas as pd

from podbook.edgar.facts import FACT_COLUMNS, FACT_DATE_COLUMNS


def row(cik, tag, val, end, filed, start=None, unit="USD"):
    return {"cik": cik, "taxonomy": "us-gaap", "tag": tag, "unit": unit, "start": start, "end": end, "val": val,
            "accn": f"{cik}-{filed}", "fy": None, "fp": None, "form": "10-Q", "filed": filed, "frame": None}


def frame(rows):
    df = pd.DataFrame(rows, columns=FACT_COLUMNS)
    for c in FACT_DATE_COLUMNS:
        df[c] = pd.to_datetime(df[c])
    df["val"] = df["val"].astype(float)
    return df


def quarter_rows(cik, tag, values, first_q_end="2010-03-31", unit="USD", lag_days=35):
    """One 3-month fact per quarter, each filed lag_days after quarter end."""
    ends = pd.date_range(first_q_end, periods=len(values), freq="QE")
    out = []
    for e, v in zip(ends, values):
        s = (e - pd.offsets.QuarterBegin(startingMonth=1)).normalize()
        out.append(row(cik, tag, v, e.date().isoformat(), (e + pd.Timedelta(days=lag_days)).date().isoformat(),
                       start=s.date().isoformat(), unit=unit))
    return out


def instant_rows(cik, tag, values, first_q_end="2010-03-31", lag_days=35):
    ends = pd.date_range(first_q_end, periods=len(values), freq="QE")
    return [row(cik, tag, v, e.date().isoformat(), (e + pd.Timedelta(days=lag_days)).date().isoformat()) for e, v in zip(ends, values)]
