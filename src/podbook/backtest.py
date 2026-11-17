"""Stage 2 backtest: monthly quintile spreads, information coefficients, turnover.

Scores formed at month end d are paired with the total return from d to the
next month end. A name that stops trading during the month earns its return up
to its last close, and is flagged. Missing delisting returns are a known bias
of free data and are stated in the note.

The out-of-sample period is locked: the first run writes a record to
data/locks and any later run refuses unless forced with a written reason,
which is appended to the record. That is the audit trail for "run once".
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from podbook.tables import asof_merge

from podbook.config import N_GROUPS, PRICE_STALENESS_DAYS


def forward_returns(prices: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Total return from each month end to the next, per company."""
    px = prices.dropna(subset=["adj_close"])[["cik", "date", "adj_close"]].sort_values("date")
    dates = pd.DatetimeIndex(dates).sort_values()
    grid = pd.DataFrame({"date": dates[:-1], "next_date": dates[1:]})
    grid = grid.merge(pd.DataFrame({"cik": px["cik"].unique()}), how="cross")

    start = asof_merge(
        grid.sort_values("date"), px.rename(columns={"date": "p0_date", "adj_close": "p0"}),
        left_on="date", right_on="p0_date", by="cik", direction="backward",
        tolerance=pd.Timedelta(days=PRICE_STALENESS_DAYS),
    )
    end = asof_merge(
        start.sort_values("next_date"), px.rename(columns={"date": "p1_date", "adj_close": "p1"}),
        left_on="next_date", right_on="p1_date", by="cik", direction="backward",
    )
    end = end[end["p0"].notna() & end["p1"].notna() & (end["p1_date"] > end["date"])]
    end["fwd_ret"] = end["p1"] / end["p0"] - 1
    end["stopped_trading"] = end["p1_date"] < end["next_date"] - pd.Timedelta(days=PRICE_STALENESS_DAYS)
    return end[["date", "cik", "fwd_ret", "stopped_trading"]].reset_index(drop=True)


def assign_quantiles(scores: pd.DataFrame, col: str, n: int = N_GROUPS, by: str | None = None) -> pd.Series:
    """Group 1 (lowest score) to n (highest) within each date (and `by` group)."""
    keys = ["date"] + ([by] if by else [])
    pct = scores.groupby(keys)[col].rank(pct=True, method="first")
    return np.ceil(pct * n).clip(1, n).astype(int)


def quantile_returns(df: pd.DataFrame, col: str, n: int = N_GROUPS, by: str | None = None) -> tuple[pd.DataFrame, pd.Series]:
    """Equal-weighted return of each group per month, and the top-minus-bottom spread.

    With `by`, groups are formed within each sub-sector and the spread is the
    equal-weighted average of the sub-sector spreads, so it carries no
    sub-sector bet.
    """
    d = df.dropna(subset=[col, "fwd_ret"]).copy()
    if d.empty:
        return pd.DataFrame(), pd.Series(dtype=float)
    d["q"] = assign_quantiles(d, col, n, by)
    if by is None:
        port = d.groupby(["date", "q"])["fwd_ret"].mean().unstack()
        spread = port.get(n, pd.Series(dtype=float)) - port.get(1, pd.Series(dtype=float))
    else:
        port = d.groupby(["date", by, "q"])["fwd_ret"].mean().unstack()
        spread = (port.get(n, pd.Series(dtype=float)) - port.get(1, pd.Series(dtype=float))).groupby(level="date").mean()
        port = d.groupby(["date", "q"])["fwd_ret"].mean().unstack()
    return port, spread.dropna()


def information_coefficient(df: pd.DataFrame, col: str) -> pd.Series:
    """Spearman rank correlation between score and next-month return, per month."""
    d = df.dropna(subset=[col, "fwd_ret"])

    def ic(x: pd.DataFrame) -> float:
        if len(x) < 10:
            return np.nan
        return x[col].rank().corr(x["fwd_ret"].rank())

    return d.groupby("date")[[col, "fwd_ret"]].apply(ic).dropna()


def turnover(df: pd.DataFrame, col: str, n: int = N_GROUPS) -> pd.Series:
    """Share of the top and bottom groups replaced each month (average of the two)."""
    d = df.dropna(subset=[col]).copy()
    d["q"] = assign_quantiles(d, col, n)
    out = {}
    dates = sorted(d["date"].unique())
    for prev, cur in zip(dates[:-1], dates[1:]):
        shares = []
        for g in (1, n):
            a = set(d.loc[(d["date"] == prev) & (d["q"] == g), "cik"])
            b = set(d.loc[(d["date"] == cur) & (d["q"] == g), "cik"])
            if b:
                shares.append(len(b - a) / len(b))
        if shares:
            out[cur] = float(np.mean(shares))
    return pd.Series(out, dtype=float)


def summarise(spread: pd.Series) -> dict:
    s = spread.dropna()
    t = len(s)
    if t < 3:
        return {"months": t}
    sd = s.std(ddof=1)
    return {
        "months": t,
        "mean_monthly": float(s.mean()),
        "annualised_mean": float(s.mean() * 12),
        "annualised_vol": float(sd * np.sqrt(12)),
        "sharpe": float(s.mean() / sd * np.sqrt(12)) if sd > 0 else None,
        "t_stat": float(s.mean() / (sd / np.sqrt(t))) if sd > 0 else None,
        "hit_rate": float((s > 0).mean()),
        # Smallest true mean monthly spread this sample would detect 80% of the
        # time at 5% two-sided significance: (1.96 + 0.84) standard errors.
        "min_detectable_monthly": float(2.8 * sd / np.sqrt(t)),
    }


def by_year(spread: pd.Series) -> pd.DataFrame:
    s = spread.dropna()
    g = s.groupby(s.index.year)
    return pd.DataFrame(
        {"months": g.size(), "mean_monthly": g.mean(), "compounded": g.apply(lambda x: (1 + x).prod() - 1)}
    ).rename_axis("year").reset_index()


def run(
    scores: pd.DataFrame,
    fwd: pd.DataFrame,
    start: str,
    end: str,
    columns: tuple[str, ...],
    by: str | None = None,
    exclude_price_gaps: bool = False,
    n: int = N_GROUPS,
) -> dict:
    """Backtest every score column over formation dates start..end."""
    df = scores.merge(fwd, on=["date", "cik"], how="inner")
    df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
    if exclude_price_gaps:
        df = df[~df["has_price_gap"].astype(bool)]
    results = {}
    for col in columns:
        port, spread = quantile_returns(df, col, n, by)
        ic = information_coefficient(df, col)
        results[col] = {
            "spread": summarise(spread),
            "ic_mean": float(ic.mean()) if len(ic) else None,
            "ic_t": float(ic.mean() / (ic.std(ddof=1) / np.sqrt(len(ic)))) if len(ic) > 2 and ic.std(ddof=1) > 0 else None,
            "turnover": float(turnover(df, col, n).mean()) if by is None else None,
            "names_per_month": float(df.dropna(subset=[col]).groupby("date").size().mean()) if len(df) else 0.0,
            "by_year": by_year(spread),
            "spread_series": spread,
            "group_returns": port,
        }
    return results


class LockedPeriodError(RuntimeError):
    pass


def check_oos_lock(lock_path: Path, spec: dict, force_reason: str | None = None) -> None:
    """Record the first out-of-sample run; refuse later runs unless a reason is given."""
    lock_path = Path(lock_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if lock_path.exists():
        record = json.loads(lock_path.read_text())
        if not force_reason:
            raise LockedPeriodError(
                f"The out-of-sample period was already run on {record['runs'][0]['at']}. "
                "Rerunning it after seeing the result defeats the point. If you must "
                "(for example a bug fix), pass a reason; it is logged."
            )
        record["runs"].append({"at": now, "spec": spec, "reason": force_reason})
    else:
        record = {"runs": [{"at": now, "spec": spec, "reason": "first run"}]}
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(record, indent=2))


VARIANTS = {
    "pooled": {"by": None, "exclude_price_gaps": False},
    "within_subsector": {"by": "subsector", "exclude_price_gaps": False},
    "pooled_excluding_price_gap_names": {"by": None, "exclude_price_gaps": True},
}


def run_period(
    scores_pooled: pd.DataFrame,
    scores_within: pd.DataFrame,
    fwd: pd.DataFrame,
    start: str,
    end: str,
    columns: tuple[str, ...],
    out_dir: Path,
) -> dict:
    """Run every pre-registered variant for one period and write the outputs."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {}
    for name, spec in VARIANTS.items():
        scores = scores_within if spec["by"] else scores_pooled
        res = run(scores, fwd, start, end, columns, by=spec["by"], exclude_price_gaps=spec["exclude_price_gaps"])
        summary[name] = {}
        spreads = {}
        for col, r in res.items():
            summary[name][col] = {k: v for k, v in r.items() if k not in ("by_year", "spread_series", "group_returns")}
            r["by_year"].assign(score=col).to_csv(out_dir / f"{name}_{col}_by_year.csv", index=False)
            spreads[col] = r["spread_series"]
        pd.DataFrame(spreads).to_csv(out_dir / f"{name}_monthly_spreads.csv")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary
