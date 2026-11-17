# Hypotheses and test plan

Status: signed off on 17 November 2026, before any backtest, drift study or factor result was produced.

This file fixes every definition and choice before any result is seen. Once signed off it is committed, and the commit date is the record that the plan came first. Any later change goes in the change log at the bottom with a date and a reason, and the out-of-sample period is still run only once.

Every setting quoted here lives in `src/podbook/config.py` or the module named, so the code and this file cannot drift apart without it showing in the diff.

## Decisions at sign-off

1. SIC code lists and coverage adjustments as in section 2, settled after the universe reviews of 16 and 17 November 2026. Those reviews looked only at where companies sit and at data quality, never at returns.
2. In-sample formation months 2012 to 2019, out-of-sample January 2020 to September 2026 (section 4.6).
3. Equal factor weights (section 4.3).

## 1. Data and timing

* Fundamentals come from SEC EDGAR companyfacts (XBRL). Each value is taken from the first filing that reported it and becomes usable one calendar day after that filing's date (`REPORTING_LAG_DAYS = 1`). Later restatements are ignored. This rules out look-ahead by construction, at the cost of ignoring corrections.
* Quarterly flows are rebuilt from what each company reports. Where only year-to-date figures exist, quarters are recovered by difference (Q2 = six months less Q1, Q4 = full year less nine months). A derived quarter is usable only once both inputs are filed.
* Fundamentals whose period ended more than 200 days before the rebalance date are treated as missing (`FUNDAMENTAL_MAX_AGE_DAYS`).
* Prices come from Yahoo, with Stooq as a fallback. Delisted companies come from Tiingo, using old tickers entered by hand in `reference/ticker_overrides.csv`, each cut to the dates that company used the ticker. Returns use the dividend and split adjusted close. Market cap uses the close that actually traded that day (Yahoo's split adjustment is undone; Tiingo gives it directly) times the share count.
* The share count is the median of the latest cover-page count, balance-sheet count and weighted-average basic count filed by then. The first two runs showed that companies with two share classes often report no cover-page total, and that any one of these tags is sometimes mis-scaled by a factor of 1,000.
* A market cap above 50 times total assets is treated as a mis-scaled share count, and that company-month is a gap rather than a member. The third run found Woodward, Terex and Rockwell Collins sized in the trillions.
* XBRL coverage starts in 2009. With the surprise history needed (section 3.1), the first usable year is 2012.

## 2. Universe

* US-listed companies that file 10-K and 10-Q, with an EDGAR SIC code in the lists below, and a market cap of at least 1 billion dollars at the month end.
* Companies later acquired or delisted stay in for every month they traded. Candidates come from EDGAR's bulk submissions file, which covers every company that has ever filed.
* Listed companies with no price or no share count are kept as gaps and sized by the public float in their 10-K. The share of the estimated true universe with complete data is reported, with the list of missing names.
* Public float is measured at the end of Q2 and reported in the next 10-K, so it may be up to 700 days old. A float above 20 times total assets is treated as a data error and ignored, as is a float with no balance sheet to check it against. The first review found floats wrong by a factor of 1,000 or more for several small filers.

SIC lists.

* Aerospace and defence: 3480, 3720, 3721, 3724, 3728, 3730, 3760, 3812
* Machinery: 3420, 3490, 3510, 3523, 3524, 3530, 3531, 3532, 3537, 3540, 3541, 3550, 3555, 3560, 3561, 3562, 3564, 3567, 3569, 3580, 3585, 3590, 3743, 3822
* Electrical equipment: 3600, 3612, 3613, 3620, 3621, 3640, 3690
* Excluded with reasons, listed in `REVIEW_ONLY` in `src/podbook/sic.py`. These include 3533 (oilfield equipment), 3559 (mostly semiconductor equipment) and 3711 (car and truck makers together).

EDGAR gives only a company's current SIC code, so a company that changed business is classified by what it is today. SIC has no conglomerate code. 3822 was added after the review because its only large filer is Trane.

The review showed several large industrials sit in codes that are mostly other businesses (3M under medical instruments, Howmet under aluminium, PACCAR with the car makers, Rockwell Automation, Ametek and Fortive with scientific and test instruments, Hubbell and Vertiv with electronics). Taking whole codes would bring in Danaher, Thermo Fisher, Tesla and the like. So the backtest universe stays on the SIC rules alone, applied mechanically, and these names are added to the coverage list only, through `reference/coverage_adjustments.csv`. The same file removes two names the SIC rules bring in that are not industrial businesses (Garmin and Zebra). Hand-picking names with today's knowledge would bias a backtest towards survivors, which is why the adjustments never touch it.

### 2.1 Survivorship (first measurement, 16 November 2026)

Of 147 companies that were ever members, none later delisted. Companies acquired or delisted since 2009 (for example Raytheon, Rockwell Collins, Goodrich and Pall) have no free price history, so they appear only as gaps. Before the float fixes above, the estimated true universe was 86% complete over the whole period and 73% to 84% complete in 2012 and 2013 (leaving out the January months the float bug distorted). The large missing names seen so far are mainly companies that were taken over. Free data alone therefore gives a backtest of companies that survived, during the years they were in the universe.

Decided 16 November 2026: fill delisted prices from Tiingo's free service before any backtest, so the main result includes them. The second run listed 49 large companies the universe could not size. 37 had no price at all and were almost all takeover targets (Raytheon, Rockwell Collins, Goodrich, Joy Global, Pall, B/E Aerospace and others). The other 12 were trading but had no usable share count, mostly companies with two share classes, which the median share count above fixes. One (CBAK Energy) is a public float error that passed the asset check and is not filled. Names Tiingo cannot supply stay listed as gaps, and the share of the estimated universe with complete data is reported after the fill. The first fill (17 November 2026) found prices for 27 of 36 delisted names and raised completeness from 86% to 98.5%. Not found on the first attempt were Goodrich, Pall, Gardner Denver, Thomas & Betts, SPX Flow, Lufkin, Robbins & Myers, Circor and Velodyne.

## 3. Stage 1: replicating post-earnings drift

### 3.1 Earnings surprise (SUE)

* EPS is quarterly diluted EPS as first reported (`EarningsPerShareDiluted`, falling back to basic-and-diluted, then basic).
* Per-share history is put on today's share basis using Yahoo's split record. This rescales a company's whole history by a common factor for any split after a given event, so it cannot leak future information into that event's surprise.
* Unexpected earnings = this quarter's EPS less the same quarter a year earlier (seasonal random walk).
* SUE = unexpected earnings divided by the standard deviation of the previous eight quarters' unexpected earnings, with at least six required (`SUE_HISTORY_QUARTERS`, `SUE_MIN_HISTORY`).
* Bernard and Thomas use a longer estimation window. Eight quarters is the most the XBRL era allows without losing years of sample. The note will quote the paper's own model definition and window when comparing.

### 3.2 Announcement date

* Primary: the earliest 8-K with Item 2.02 (earnings release) filed after the quarter ended and no later than the 10-Q or 10-K. If there is none, the 10-Q or 10-K filing date.
* The surprise uses EPS from the 10-Q or 10-K, which can be filed after the 8-K. This assumes the GAAP diluted EPS in the release matches the filing, as the Compustat-based literature does. A robustness check uses the 10-Q or 10-K date as day 0 instead.

### 3.3 Groups, returns and test

* Events are restricted to companies in the universe at the month end before the announcement.
* Events are sorted into quintiles using breakpoints from the previous calendar quarter's surprises, so no event's group depends on later events. Quintile 5 is the most positive surprise.
* Day 0 is the first trading day on or after the announcement date. Drift is the buy-and-hold return from trading day 1 to 60, less the buy-and-hold return of the equal-weighted universe (members at the previous month end) over the same days. If a stock stops trading inside the window, the window ends at its last return.
* The announcement return (days -1 to +1) is reported separately.
* The test statistic is the top-minus-bottom quintile drift, averaged per announcement quarter, with a t-statistic across quarters.

### 3.4 Expectation

Positive drift for the top quintile and negative for the bottom, with the spread smaller than in the original paper. Reasons expected in advance are a different period, a sector-only universe, a sector benchmark rather than size-adjusted returns, and the decay of the anomaly since publication. The size and shape will be compared with the paper's published figures, quoted from the paper itself.

### 3.5 Robustness checks (all reported, whatever they show)

* Window days 2 to 61 instead of 1 to 60.
* Day 0 is the 10-Q or 10-K filing date.
* SUE with a drift term (the mean of the previous eight surprises subtracted).
* Excluding events with a stock split in the three years before, and names with no split record.
* Excluding names whose price history has gaps.
* Deciles instead of quintiles, for comparison with the paper.

## 4. Stage 2: the screen

### 4.1 Measures

All as of the rebalance date, from filings usable by then. TTM means the sum of the last four consecutive quarters.

| Factor | Measure | Definition | Better |
|---|---|---|---|
| Surprise | SUE | Latest SUE from section 3.1 whose filing is usable and at most 120 days old | Higher |
| Fundamental momentum | Change in operating margin | TTM EBIT / TTM sales, less the same a year earlier | Higher |
| | Change in ROIC | ROIC (below) less the same a year earlier | Higher |
| | Sales growth | TTM sales / TTM sales a year earlier, less 1 | Higher |
| Quality | ROIC | TTM EBIT / average invested capital (now and a year earlier). Invested capital = debt + equity (including minority interest where reported) - cash. Pre-tax, to avoid tax noise | Higher |
| | Leverage | Debt / total assets | Lower |
| | Interest cover | TTM EBIT / TTM interest expense, capped at 100 | Higher |
| Earnings quality | Accruals | (TTM net income - TTM operating cash flow) / average total assets | Lower |
| Valuation | EBIT yield | TTM EBIT / enterprise value. EV = market cap + debt + minority interest - cash. Yield rather than EV/EBIT so loss-makers rank sensibly | Higher |
| | FCF yield | (TTM operating cash flow - TTM capex) / market cap | Higher |

Debt is long-term debt including the current portion (`LongTermDebt`, otherwise non-current plus current), plus short-term borrowings and commercial paper. EBIT is `OperatingIncomeLoss`.

### 4.2 Missing data

A missing measure is left out of its factor rather than filled. A name needs at least three of the five factors for a combined score (`MIN_FACTORS`). Interest cover is missing where interest expense is not reported.

### 4.3 Combination

Each measure is ranked 0 to 1 across the universe each month, in its better direction. A factor is the mean of its measures' ranks, re-ranked 0 to 1. The combined score is the equal-weighted mean of the five factor ranks. Equal weights are fixed in advance and will not be tuned.

### 4.4 Portfolios and statistics

* Monthly rebalance at calendar month ends. Scores at month end d are paired with the total return from d to the next month end. A name that stops trading earns its return to its last close. Missing delisting returns are a known bias of free data and are stated in the note.
* Equal-weighted quintiles. The headline statistic is the top-minus-bottom monthly spread, with its mean, annualised mean and volatility, Sharpe ratio, t-statistic and hit rate.
* For each factor and the combined score, the note also reports the mean monthly information coefficient (Spearman) and its t-statistic, monthly turnover of the top and bottom quintiles, and results by calendar year.
* The smallest monthly spread the sample could reliably detect (80% power, 5% two-sided) is reported next to every result, so a weak result is read as a measurement of what this universe can show.

### 4.5 Checks that the result is not something else

* Within sub-sector. Ranks and quintiles are formed within aerospace and defence, machinery and electrical equipment separately, and the spread is the equal-weighted average of the three. If the pooled result disappears here, it was a sub-sector bet.
* Excluding names with price gaps. If the result depends on names with incomplete prices, it is not trusted.

### 4.6 Periods

* In-sample formation months from January 2012 to December 2019.
* Out-of-sample formation months from January 2020 to September 2026, the last return month being October 2026.
* The out-of-sample period is run once. The code writes a lock record on the first run and refuses to rerun without a written reason, which is logged.

### 4.7 What happens with a weak in-sample result

The out-of-sample run goes ahead unchanged and is reported. If the combined score's in-sample t-statistic is below 2, the note says the screen did not show reliable predictive power in this universe, and it is presented as a coverage tool for ranking names, not as evidence of a return premium.

## 5. Live overlay (not backtested)

Yahoo's 90-day consensus revision for the current fiscal year (current estimate divided by the estimate 90 days ago, less 1), from the weekly snapshots. It is shown as a separate column on the one-page screen and never enters the combined score or any backtest.

## 6. Choosing the long and the short

The coverage list is the 40 largest companies today from the SIC universe plus the additions in `reference/coverage_adjustments.csv`, less its exclusions. It is frozen once.

After the out-of-sample run, the latest combined score ranks the 40 coverage names. The long comes from the top eight and the short from the bottom eight. The choice within each group is a fundamental judgement, written down with its reasons before the pitch is drafted.

## 7. Planned extension: Bloomberg consensus (defined now, run later)

Defined here, before any free-data result, so adding it later is not a response to what the screen showed. Results are reported as a separate, labelled section.

* Consensus surprise = actual EPS less the mean consensus EPS for that quarter on the day before the announcement, divided by the share price at the end of the previous quarter. Actual and consensus on the same (adjusted) basis from Bloomberg.
* Revisions = change in the mean FY1 consensus EPS over the previous three months, divided by price, as of the rebalance date less one day.
* Drift is re-run with consensus surprise in place of SUE, with every rule in section 3 unchanged.
* The screen is re-run with revisions as a sixth factor at equal weight. The five-factor result stays the headline.
* The same in-sample and out-of-sample periods apply. The extension's out-of-sample run is also done once, with its own lock record.
* Bloomberg prices for delisted names are used to re-run the survivorship check, reported separately.
* No Bloomberg data is committed to the repo.

## Change log

| Date | Change | Reason |
|---|---|---|
| 2026-11-17 | Signed off | Universe and data checks complete; no results seen |
