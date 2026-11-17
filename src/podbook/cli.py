"""Command line entry point.

    podbook fetch-edgar        EDGAR tickers, bulk submissions, companyfacts, CIK map
    podbook fetch-prices       Yahoo prices (Stooq fallback) and the price gap log
    podbook fetch-delisted     Tiingo prices for delisted companies in reference/ticker_overrides.csv
    podbook build-universe     month-end membership panel and completeness report
    podbook review-universe    SIC review report, read before freezing the rules
    podbook freeze-coverage    the 40 largest names today, written once
    podbook log-consensus      weekly Yahoo consensus snapshot for the coverage list
    podbook pipeline           fetch-edgar, fetch-prices, build-universe, review-universe
    podbook build-events       Stage 1: SUE, announcement dates, drift replication
    podbook build-factors      Stage 2: monthly point-in-time factor measures and scores
    podbook backtest           Stage 2: in-sample by default; --period out_of_sample runs once
    podbook research           build-events, build-factors and the in-sample backtest in one go
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import pandas as pd

from podbook import store
from podbook.config import COVERAGE_SIZE, UNIVERSE_START, Paths, sec_user_agent

log = logging.getLogger("podbook")


def _today(args: argparse.Namespace) -> pd.Timestamp:
    return pd.Timestamp(args.asof) if getattr(args, "asof", None) else pd.Timestamp.today().normalize()


def _coverage_adjustments(paths: Paths, current: pd.DataFrame | None = None) -> tuple[set[str], set[str]]:
    """Tickers to add to and exclude from the coverage list, resolved to CIKs where possible.

    These change the coverage list only. The backtest universe stays on the SIC rules.
    """
    if not paths.coverage_adjustments_csv.exists():
        return set(), set()
    adj = pd.read_csv(paths.coverage_adjustments_csv)
    add = set(adj.loc[adj["action"] == "add", "ticker"].str.upper())
    exclude = set(adj.loc[adj["action"] == "exclude", "ticker"].str.upper())
    if current is None:
        return add, exclude
    found = current[current["ticker"].isin(add)]
    missing = add - set(found["ticker"])
    if missing:
        log.warning("Coverage additions not in the SEC ticker list: %s", sorted(missing))
    return set(found["cik"]), exclude


def cmd_fetch_edgar(args: argparse.Namespace, paths: Paths) -> None:
    from podbook.edgar.client import (
        BULK_SUBMISSIONS_URL,
        COMPANYFACTS_URL,
        TICKERS_EXCHANGE_URL,
        EdgarClient,
    )
    from podbook.edgar.discover import scan_bulk_submissions
    from podbook.edgar.facts import parse_companyfacts
    from podbook.mapping import build_mapping, parse_tickers_exchange

    client = EdgarClient(sec_user_agent())
    tickers_js = client.cached_json(TICKERS_EXCHANGE_URL, paths.tickers_json, max_age_days=args.refresh_days)

    if args.refresh_bulk or not paths.bulk_submissions_zip.exists():
        log.info("Downloading bulk submissions zip (large, a few minutes)")
        client.download(BULK_SUBMISSIONS_URL, paths.bulk_submissions_zip)

    extra_ciks, _ = _coverage_adjustments(paths, parse_tickers_exchange(tickers_js))
    companies, filings = scan_bulk_submissions(paths.bulk_submissions_zip, extra_ciks=extra_ciks)
    store.save_companies(companies, paths)
    store.save_filings(filings, paths)
    log.info("%d candidates (%d in included SIC codes)", len(companies), int(companies["in_universe_sic"].sum()))

    frames = []
    for i, cik in enumerate(companies["cik"], 1):
        js = client.cached_json(
            COMPANYFACTS_URL.format(cik=cik), paths.companyfacts_dir / f"CIK{cik}.json", max_age_days=args.refresh_days
        )
        if js is not None:
            frames.append(parse_companyfacts(js))
        if i % 100 == 0:
            log.info("companyfacts %d / %d", i, len(companies))
    facts = pd.concat(frames, ignore_index=True)
    store.save_facts(facts, paths)
    log.info("%d facts for %d companies (%d had no XBRL facts)",
             len(facts), facts["cik"].nunique(), len(companies) - len(frames))

    overrides = pd.read_csv(paths.ticker_overrides_csv, dtype={"cik": str}) if paths.ticker_overrides_csv.exists() else None
    mapping, conflicts = build_mapping(parse_tickers_exchange(tickers_js), companies, overrides)
    store.save_mapping(mapping, paths)
    paths.logs.mkdir(parents=True, exist_ok=True)
    conflicts.to_csv(paths.logs / "mapping_conflicts.csv", index=False)
    no_ticker = companies.loc[~companies["cik"].isin(mapping["cik"]), "cik"].nunique()
    log.info("Mapping: %d rows, %d conflicts dropped, %d candidates with no ticker", len(mapping), len(conflicts), no_ticker)


def cmd_fetch_prices(args: argparse.Namespace, paths: Paths) -> None:
    import os

    from podbook.prices import fetch_prices_for_universe

    companies = store.load_companies(paths)
    companies = companies[companies["in_universe_sic"] | companies["coverage_addition"]]
    gaps = fetch_prices_for_universe(
        companies, store.load_mapping(paths), paths.prices_dir, paths.logs / "price_gaps.csv",
        analysis_start=args.start, today=_today(args), max_age_days=args.refresh_days,
        tiingo_token=os.environ.get("TIINGO_API_KEY") or None,
    )
    log.info("Price status counts:\n%s", gaps["status"].value_counts().to_string())


def cmd_fetch_delisted(args: argparse.Namespace, paths: Paths) -> None:
    import os

    from podbook.prices import fetch_delisted

    token = os.environ.get("TIINGO_API_KEY", "").strip()
    if not token:
        raise RuntimeError("Set TIINGO_API_KEY (see README).")
    log_df = fetch_delisted(
        store.load_companies(paths), store.load_mapping(paths), paths.prices_dir,
        paths.logs / "delisted_prices.csv", token, analysis_start=UNIVERSE_START, refresh=args.refresh,
    )
    log.info("Delisted price status:\n%s", log_df["status"].value_counts().to_string() if len(log_df) else "none")


def cmd_build_universe(args: argparse.Namespace, paths: Paths) -> None:
    from podbook.prices import load_price_panel
    from podbook.universe import build_universe, completeness_report, gap_report, mcap_anomalies, rebalance_dates

    companies = store.load_companies(paths)
    gaps_path = paths.logs / "price_gaps.csv"
    gaps = pd.read_csv(gaps_path, dtype={"cik": str}) if gaps_path.exists() else None
    panel = build_universe(
        companies, store.load_facts(paths), load_price_panel(paths.prices_dir),
        rebalance_dates(args.start, _today(args)), price_gaps=gaps,
    )
    store.save_panel(panel, paths)
    by_date, totals = completeness_report(panel)
    paths.reports.mkdir(parents=True, exist_ok=True)
    by_date.to_csv(paths.reports / "universe_completeness_by_date.csv", index=False)
    (paths.reports / "universe_completeness.json").write_text(json.dumps(totals, indent=2))
    gap_report(panel).to_csv(paths.reports / "universe_gaps.csv", index=False)
    mcap_anomalies(panel).to_csv(paths.reports / "mcap_float_anomalies.csv", index=False)
    log.info("Universe totals: %s", json.dumps(totals, indent=2))


def cmd_review_universe(args: argparse.Namespace, paths: Paths) -> None:
    from podbook.mapping import parse_tickers_exchange
    from podbook.review import ANCHOR_TICKERS, anchor_table, code_table, read_sic_for, write_review

    asof = _today(args)
    companies = store.load_companies(paths)
    current = parse_tickers_exchange(json.loads(paths.tickers_json.read_text()))
    anchor_ciks = current.loc[current["ticker"].isin(ANCHOR_TICKERS), "cik"].unique().tolist()
    anchors = anchor_table(current, read_sic_for(paths.bulk_submissions_zip, anchor_ciks))
    codes = code_table(companies, store.load_facts(paths), asof)
    out = write_review(paths.reports / "universe_review.md", codes, anchors, asof)
    log.info("Wrote %s", out)


def cmd_freeze_coverage(args: argparse.Namespace, paths: Paths) -> None:
    from podbook.prices import load_price_panel
    from podbook.universe import build_universe, freeze_coverage, select_coverage

    asof = _today(args)
    companies = store.load_companies(paths)
    _, exclude = _coverage_adjustments(paths)
    panel = build_universe(
        companies, store.load_facts(paths), load_price_panel(paths.prices_dir),
        pd.DatetimeIndex([asof]), extra_ciks=set(companies.loc[companies["coverage_addition"], "cik"]),
    )
    coverage = select_coverage(panel, store.load_mapping(paths), n=args.n, exclude_tickers=exclude)
    path = freeze_coverage(coverage, paths.coverage_csv, frozen_on=asof, force=args.force)
    log.info("Froze %d names to %s:\n%s", len(coverage), path, coverage.to_string(index=False))


def cmd_log_consensus(args: argparse.Namespace, paths: Paths) -> None:
    from podbook.consensus import log_consensus

    log_consensus(paths.coverage_csv, paths.yahoo_consensus, date=args.asof, force=args.force)


def cmd_build_events(args: argparse.Namespace, paths: Paths) -> None:
    from podbook.events import run_drift
    from podbook.prices import load_price_panel
    from podbook.tables import save_table

    out = run_drift(store.load_facts(paths), store.load_filings(paths), load_price_panel(paths.prices_dir), store.load_panel(paths))
    save_table(out["sue"], paths.interim / "sue")
    save_table(out["events"].assign(ann_q=out["events"]["ann_q"].astype(str)), paths.interim / "drift_events")
    paths.reports.mkdir(parents=True, exist_ok=True)
    out["by_group"].to_csv(paths.reports / "drift_by_group.csv", index=False)
    out["path"].to_csv(paths.reports / "drift_path.csv")
    summary = {"variants": out["summaries"], "announcement_date_source": out["ann_source_share"]}
    (paths.reports / "drift_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log.info("Drift summary: %s", json.dumps(summary, indent=2, default=str))


def cmd_build_factors(args: argparse.Namespace, paths: Paths) -> None:
    from podbook.factors import raw_measures, score
    from podbook.fundamentals import build_tables
    from podbook.tables import load_table, save_table

    sue = load_table(paths.interim / "sue", date_cols=("end", "available"))
    measures = raw_measures(store.load_panel(paths), build_tables(store.load_facts(paths)), sue)
    save_table(measures, paths.interim / "factor_measures")
    save_table(score(measures), paths.interim / "scores_pooled")
    save_table(score(measures, by="subsector"), paths.interim / "scores_within_subsector")
    paths.reports.mkdir(parents=True, exist_ok=True)
    coverage = measures.groupby("date")[[c for c in measures.columns if c not in ("date", "cik", "subsector", "mcap", "has_price_gap", "info_date")]].count()
    coverage.to_csv(paths.reports / "factor_coverage_by_date.csv")
    log.info("Factor measures for %d name-months", len(measures))


def cmd_backtest(args: argparse.Namespace, paths: Paths) -> None:
    from podbook.backtest import check_oos_lock, forward_returns, run_period
    from podbook.config import IN_SAMPLE, OUT_OF_SAMPLE
    from podbook.factors import FACTORS
    from podbook.prices import load_price_panel
    from podbook.tables import load_table

    start, end = IN_SAMPLE if args.period == "in_sample" else OUT_OF_SAMPLE
    columns = (*FACTORS, "combined")
    if args.period == "out_of_sample":
        check_oos_lock(paths.data / "locks" / "out_of_sample.json",
                       {"start": start, "end": end, "columns": list(columns)}, force_reason=args.reason)
    pooled = load_table(paths.interim / "scores_pooled", date_cols=("date",))
    within = load_table(paths.interim / "scores_within_subsector", date_cols=("date",))
    dates = pd.DatetimeIndex(sorted(store.load_panel(paths)["date"].unique()))
    fwd = forward_returns(load_price_panel(paths.prices_dir), dates)
    summary = run_period(pooled, within, fwd, start, end, columns, paths.reports / f"backtest_{args.period}")
    log.info("Backtest %s %s to %s:\n%s", args.period, start, end, json.dumps(summary["pooled"], indent=2, default=str))


def cmd_research(args: argparse.Namespace, paths: Paths) -> None:
    """Stage 1 and the in-sample Stage 2 run. The locked period stays a separate, deliberate command."""
    args.period, args.reason = "in_sample", None
    for step in (cmd_build_events, cmd_build_factors, cmd_backtest):
        log.info("=== %s ===", step.__name__.removeprefix("cmd_"))
        step(args, paths)


def cmd_pipeline(args: argparse.Namespace, paths: Paths) -> None:
    import os

    steps = [cmd_fetch_edgar]
    if os.environ.get("TIINGO_API_KEY"):
        steps.append(cmd_fetch_delisted)
    else:
        log.warning("TIINGO_API_KEY not set: delisted companies will have no prices")
    steps += [cmd_fetch_prices, cmd_build_universe, cmd_review_universe]
    args.refresh = getattr(args, "refresh", False)
    for step in steps:
        log.info("=== %s ===", step.__name__.removeprefix("cmd_"))
        step(args, paths)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="podbook", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, *, start=False, refresh=False, asof=False, force=False):
        sp = sub.add_parser(name)
        sp.set_defaults(func=func)
        if start:
            sp.add_argument("--start", default=UNIVERSE_START, help="first rebalance date")
        if refresh:
            sp.add_argument("--refresh-days", type=float, default=7, help="refetch cached files older than this")
        if asof:
            sp.add_argument("--asof", default=None, help="treat this date as today (YYYY-MM-DD)")
        if force:
            sp.add_argument("--force", action="store_true")
        return sp

    fe = add("fetch-edgar", cmd_fetch_edgar, refresh=True)
    fe.add_argument("--refresh-bulk", action="store_true", help="re-download the bulk submissions zip")
    add("fetch-prices", cmd_fetch_prices, start=True, refresh=True, asof=True)
    fd = add("fetch-delisted", cmd_fetch_delisted)
    fd.add_argument("--refresh", action="store_true", help="refetch names already fetched")
    add("build-universe", cmd_build_universe, start=True, asof=True)
    add("review-universe", cmd_review_universe, asof=True)
    fc = add("freeze-coverage", cmd_freeze_coverage, asof=True, force=True)
    fc.add_argument("--n", type=int, default=COVERAGE_SIZE)
    add("log-consensus", cmd_log_consensus, asof=True, force=True)
    add("build-events", cmd_build_events)
    add("build-factors", cmd_build_factors)
    add("research", cmd_research)
    bt = add("backtest", cmd_backtest)
    bt.add_argument("--period", choices=["in_sample", "out_of_sample"], default="in_sample")
    bt.add_argument("--reason", default=None, help="required to rerun the locked out-of-sample period")
    pl = add("pipeline", cmd_pipeline, start=True, refresh=True, asof=True)
    pl.add_argument("--refresh-bulk", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args.func(args, Paths.from_env())
    return 0


if __name__ == "__main__":
    sys.exit(main())
