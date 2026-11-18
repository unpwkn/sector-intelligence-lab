# Phase 5 sector rotation backtesting

## Data readiness and interpretation

Phase 5 runs offline on verified market snapshots. Import a licensed
[local CSV export](HISTORICAL_DATA.md) or use the documented
[Alpha Vantage Daily Adjusted adapter](MARKET_DATA.md) with premium entitlement.
The legacy demo prices and Phases 3–4 fundamental CSVs use separate contracts.

The loader verifies the manifest and hashes of normalized prices and raw evidence.
For CSV imports it also replays the saved raw observations. Both routes validate
currency, price basis, retrieval timestamps, positive finite prices, and observed
versus expected sessions on the XNYS US equity calendar proxy. The run fails if
any requested listed session is missing, including warm-up. The requested bounds
determine sample coverage, and short windows can fail the momentum warm-up or
lack an execution session. The report preserves the bounds, input coverage,
file hashes, source retrievals, manifest hash, and runtime package versions.
Keep the original snapshot and record the repository revision to replay a run.

XLC was formed on 2018-06-18 and commenced operations on 2018-06-19. XLRE was
formed on 2015-10-07 and began trading on 2015-10-08. Phase 5 starts
eligibility from the trading sessions, using the Phase 1 calendar convention.
[XLC fund report](https://www.sec.gov/Archives/edgar/data/1064641/000119312518342039/d623019dncsr.htm)
[XLRE fund report](https://www.sec.gov/Archives/edgar/data/1064641/000119312516612173/d119366dncsrs.htm)
[NYSE Arca XLRE bulletin](https://www.nyse.com/publicdocs/nyse/markets/nyse-arca/rule-interpretations/2015/NYSEArca-RB-15-137.pdf)
[Phase 1 calendar convention](MARKET_DATA.md#validation-and-calendars)

The engine never uses pre-listing histories or substitutes a sector index.
Current fund definitions and the chosen ETF list still reflect the present-day
research design. Earlier coverage and sector exposures differ, particularly
around sector reclassifications.

Real-data runs carry `RETROSPECTIVE_ADJUSTED_PRICE_PROXY`. A single saved vintage
can include vendor corrections or later corporate-action adjustments. The decision
close is the modeled signal cutoff, and the session-date filter prevents using
later observations from that vintage. The source gives no historical publication
or correction timestamps, so historical availability at each close remains
unverified. Absolute price scale adjustments that cancel in a return ratio do
not establish that every revision cancels. Adjusted-close ratios are proxies for
investment returns and are not independently reconciled to dividends, splits, or
a total-return benchmark. No supported input can produce
`VALIDATED_PIT_BACKTEST`. That would require licensed archived vintages or
equivalent source evidence of original publication, corrections, and
corporate-action availability at each cutoff, with verified return construction.

The repository and this implementation were verified with fictional fixtures
only. No licensed snapshot was available for a historical study. The fixture
uses real calendar labels and simulated software-test prices. It is tagged
`SYNTHETIC_OFFLINE_FIXTURE` in every output table and carries no market evidence.

Provider entitlement and use rights are separate. Confirm commercial use,
storage, sharing, and derived-output rights under your agreement with the
vendor. The code license does not grant data rights. Keep vendor snapshots and
research outputs local in ignored directories and avoid redistributing them
without permission. [Alpha Vantage terms](https://www.alphavantage.co/terms_of_service/)

## Portfolio and execution rules

The primary comparison uses all 11 sector ETFs and SPY. All four portfolios
start at the same first executable month-end decision with a fully eligible
11-sector peer set. The Phase 2 score is reused with its 40% / 40% / 20% weights
and unchanged session offsets. Its longest offset requires 253 session slots
including the signal close. The four portfolios are SPY buy and hold, all sectors
equal weighted, the top three scores equal weighted, and the top five equal
weighted. SPY is held after its initial purchase. Sector portfolios reset
to target weights monthly and otherwise drift with price moves. Tied scores
are resolved alphabetically by ticker for the fixed top-N portfolios.

The optional `dynamic` analysis is a separate run with its own dates and
benchmark. After at least five sectors qualify, all its sector strategies use
that month's same eligible set. This permits a smaller universe before newer
funds have sufficient history. It mixes changing opportunity sets and can
have different market-regime exposure from the primary comparison.

The decision uses the last scheduled XNYS close of the calendar month within
the snapshot, including any holiday or early-close schedule. An end date
inside an unfinished month does not create a month-end decision. Positions
trade at the **next trading session's close**. The old holdings earn the
return up through that close. New holdings first earn a price return during
the following close-to-close interval. This is an execution convention under
daily data, with no verified fills or intraday price information. Decisions
without a next close in the snapshot are logged and skipped. An execution on
the last available close can incur costs with no subsequent price return.
All portfolios start with unit cash NAV at the first signal close. Cash earns
zero. No terminal liquidation is modeled.

## Costs and metrics

Commission and slippage inputs are one-way basis points per traded dollar.
The spread input is the full quoted width, with half charged per side.
Both purchases and sales generate costs. The net portfolio funds those
costs from pretrade NAV and reaches the specified target weights with the
remaining NAV. Reported trade notional is the sum of absolute ETF dollar
changes divided by pretrade NAV. Economic turnover is half the sum of
absolute changes from drifted to target weights, including cash. The initial
purchase counts as 100% turnover under the default `half_l1` convention.
Use `--turnover-convention etf_notional` to report total traded ETF notional
as turnover in the metrics instead. Both measures are in the trade ledger,
and the reporting convention leaves portfolio holdings and costs unchanged. Applying the
one-way rate to ETF notional accounts for both sides once. Gross and net use
identical selection and timing. CLI defaults are zero costs, and any cost
scenario should disclose its inputs. The model has no market-impact, tax or
position-size assumptions.

- **CAGR** uses elapsed calendar days and a 365.2425-day year. **Maximum
  drawdown** uses daily close NAV against its running peak, including starting cash.
- **Annualized volatility** and **tracking error** use daily sample standard
  deviations times the square root of 252. **Sharpe** uses mean daily return
  minus the effective daily equivalent of the configured annual risk-free rate
  (default zero), annualized by 252 and divided by annualized volatility.
  **Information ratio** uses the same annualization on mean daily active return
  versus SPY divided by tracking error. Each return basis uses the matching
  SPY basis on identical sessions. Ratios are blank when undefined.
- **Portfolio turnover** sums execution turnover and also reports it per
  elapsed calendar year. **Win rate** is the fraction of complete calendar
  months with a strictly positive return. The benchmark-relative win rate
  counts strictly positive performance versus SPY.
- **Downside capture** is the strategy's geometric mean monthly return during
  SPY's negative months divided by SPY's geometric mean in those same months.
  It is a ratio, where 1 represents SPY-equivalent capture. A sample with no
  negative SPY month has blank downside capture.
- **Rolling 12-month relative performance** uses the ratio of strategy to SPY
  wealth at matched month-end closes, divided by that ratio 12 month-ends
  earlier, minus one. The monthly statistics require paired month-end closes
  and exclude any incomplete final calendar month. Rolling values are blank
  until 12 complete monthly returns are available.

## Run and audit

From the repository root with the project's dependencies installed:

```bash
python scripts/run_backtest.py --fixture --output work/phase5-common \
  --commission-bps 1 --full-spread-bps 4 --slippage-bps 2
python scripts/run_backtest.py --snapshot work/phase5-common/fixture_snapshot \
  --universe dynamic --output work/phase5-dynamic \
  --commission-bps 1 --full-spread-bps 4 --slippage-bps 2
python scripts/run_backtest.py --snapshot data/market/your-verified-snapshot \
  --output work/phase5-research
pytest -q
```

Each run writes `metadata.json`, the month-end `decisions.csv` with Phase 2
status, scores, close times and scheduling decisions, and `trades.csv` with
actual modeled execution dates, selections, weights, turnover and cost fractions.
The report also includes daily gross/net NAV and returns, aligned monthly returns,
rolling relative returns, and one metrics row per strategy and return basis.
Python callers can use `sectorlab.backtest.run_backtest(path)` with the same
defaults. A fixture run saves its source snapshot alongside the results.
Replay that exact directory to retain its hashes. Generating a new fixture
creates new timestamped provenance.


The Streamlit **Sector Rotation Backtest** tab runs the same engine on snapshots
saved under `data/market/`. Select a snapshot and universe, enter the cost assumptions,
and run the comparison to view gross or net NAV, metrics, rolling relative returns,
trades, and snapshot provenance.
