"""Universe review report: where each SIC code's companies land, and where the
well-known industrials sit. Run this before freezing the SIC rules.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd

from podbook.edgar.facts import PUBLIC_FLOAT_TAGS, latest_known
from podbook.sic import REVIEW_ONLY, SIC_SUBSECTOR, SIC_TITLES, classify_sic, parse_sic

# Diagnostic only. These names do not affect membership; the report just shows
# which SIC code each one carries, so gaps in the rules are visible.
ANCHOR_TICKERS = (
    "BA", "LMT", "RTX", "NOC", "GD", "LHX", "HII", "TDG", "HWM", "HEI", "TXT", "AXON", "GE", "GEV",
    "HON", "MMM", "CAT", "DE", "ETN", "EMR", "ITW", "PH", "PCAR", "CMI", "ROK", "AME", "DOV", "XYL",
    "OTIS", "CARR", "TT", "WAB", "IR", "FTV", "JCI", "SWK", "SNA", "GNRC", "HUBB", "VRT", "OSK", "TER",
)


def read_sic_for(zip_path: Path, ciks: list[str]) -> pd.DataFrame:
    """SIC and name for specific CIKs straight from the bulk zip."""
    wanted = {f"CIK{c}.json": c for c in ciks}
    rows = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            base = Path(name).name
            if base in wanted:
                sub = json.loads(zf.read(name))
                rows.append({"cik": wanted[base], "name": sub.get("name"), "sic": parse_sic(sub.get("sic"))})
    return pd.DataFrame(rows, columns=["cik", "name", "sic"])


def anchor_table(current_tickers: pd.DataFrame, sic_lookup: pd.DataFrame) -> pd.DataFrame:
    anchors = pd.DataFrame({"ticker": ANCHOR_TICKERS})
    out = anchors.merge(current_tickers[["ticker", "cik"]], on="ticker", how="left")
    out = out.merge(sic_lookup, on="cik", how="left")
    out["subsector"] = out["sic"].map(classify_sic)
    out["in_universe_rules"] = out["subsector"].notna()
    out["sic_title"] = out["sic"].map(lambda s: SIC_TITLES.get(int(s), "") if pd.notna(s) else "")
    return out[["ticker", "cik", "name", "sic", "sic_title", "subsector", "in_universe_rules"]]


def code_table(companies: pd.DataFrame, facts: pd.DataFrame, asof: object, top: int = 6) -> pd.DataFrame:
    """One row per candidate SIC code with counts and its largest names by last public float."""
    asof = pd.Timestamp(asof)
    flt = latest_known(facts[facts["unit"] == "USD"], asof, PUBLIC_FLOAT_TAGS, max_age_days=100_000)
    c = companies.merge(flt[["cik", "val"]].rename(columns={"val": "last_float"}), on="cik", how="left")
    c["alive"] = c["last_periodic"] >= asof - pd.Timedelta(days=365)
    rows = []
    for sic, grp in c.groupby("sic"):
        biggest = grp.sort_values("last_float", ascending=False).head(top)
        names = ", ".join(
            f"{r.name} ({r.last_float / 1e9:.1f}bn{'' if r.alive else ', gone'})"
            for r in biggest.itertuples() if pd.notna(r.last_float)
        )
        rows.append(
            {
                "sic": int(sic),
                "title": SIC_TITLES.get(int(sic), ""),
                "included": int(sic) in SIC_SUBSECTOR,
                "subsector_or_reason": SIC_SUBSECTOR.get(int(sic)) or REVIEW_ONLY.get(int(sic), ""),
                "companies": len(grp),
                "still_filing": int(grp["alive"].sum()),
                "float_above_1bn_ever": int((grp["last_float"] >= 1e9).sum()),
                "largest_by_last_public_float": names,
            }
        )
    return pd.DataFrame(rows).sort_values(["included", "sic"], ascending=[False, True])


def write_review(path: Path, codes: pd.DataFrame, anchors: pd.DataFrame, asof: object) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Universe review ({pd.Timestamp(asof).date()})",
        "",
        "Public float is from each company's latest 10-K. 'gone' means no 10-K or 10-Q in the last year.",
        "",
        "## SIC codes",
        "",
        _markdown(codes),
        "",
        "## Where the well-known names sit",
        "",
        _markdown(anchors),
        "",
    ]
    path.write_text("\n".join(lines))
    return path


def _markdown(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        out.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |")
    return "\n".join(out)
