# Data provenance, integrity and licensing

## Important: included data are simulated

The `make_demo_data.py` generator writes fictional price, valuation, breadth, quality and estimate-revision values. They may resemble market-like series, but they **are not observations from the securities named in the project**. The names, dates and tickers are for software testing only. No portfolio-performance charts, investor-facing results, fair-value conclusions or alpha claims may be drawn from them.

## Input CSV contracts

All records must include both the date they describe and the first time they were available for investment decisions (`available_at`). Only `available_at <= signal_date` may enter a signal. The current implementation uses date-level rather than intraday time-level gating and is not execution-ready.

- `valuations.csv`: `date,ticker,metric,value,available_at` (sector-aggregated values; one record per metric/month).
- `prices.csv`: `date,ticker,adjusted_price_proxy,available_at` (must include SPY and sector ETFs, common daily calendar).
- `breadth.csv`: `date,ticker,breadth,available_at` (PIT constituent-based 0-1 breadth value).
- `revisions.csv`: `date,ticker,revision_1m,revision_3m,revision_breadth,available_at` (guarded, calendarized EPS estimate features).
- `quality.csv`: `date,ticker,profitability,financial_strength,growth_durability,available_at` (sector-adjusted 0-100 features).

For production, extend each row with provider, observation timestamp/time zone, vintage or correction ID, metric definition and licensing classification. Do not assert that a downloaded modern CSV is point-in-time simply because a row has a past date. Verify how historical snapshots were recorded by the vendor.

## Phase 1 real-price snapshots

Optional real-price ingestion now uses Alpha Vantage's documented premium Daily
Adjusted API for the 11 sector ETFs and SPY. See [MARKET_DATA.md](MARKET_DATA.md)
for reproducible commands, licensing/access limitations, validation rules and the
v1.0 local schema. Snapshots live separately in ignored `data/market/` directories.
They retain reported corporate actions and mark adjusted close as a proxy;
`total_return_verified` and `point_in_time_history` are false. Retrieval timestamps
are not historical `available_at` timestamps. These snapshots do not implement
or replace the synthetic scoring-input contracts above.

## Suggested real-data path

- Market prices: properly licensed market-data vendor, cross-checking distributions and ETF splits; benchmark and currency consistent.
- ETF holdings: official manager/provider snapshots or licensed point-in-time index holdings (not current constituents projected backward).
- Fundamentals: company filings / SEC EDGAR, mapped to publication dates and historical changes; consistent GAAP/adjusted treatment.
- Analyst estimates: licensed PIT consensus or archived vendor snapshots with genuine observation vintages.
- Macro: FRED/ALFRED vintage data for revisions and release calendars where relevant.

Never commit API credentials, unlicensed proprietary extracts, client/portfolio information or private research to a public repository. If a data vendor does not permit redistribution, ship an adapter, example schema, synthetic test fixture and documentation instead of the data itself.

## Coverage gates

The roadmap calls for 48+ monthly valuation references, 80%+ constituent weight coverage, two or more economically relevant valuation metrics, stale-data checks and missingness flags. The v0.1 code implements a subset: 48 historical samples, three required valuation proxy metrics per sector, a 45-day staleness gate, and no imputation. It does not yet implement total coverage audits or a full standardized confidence score.

## Known source limitations

- Historical P/E estimates in an ETF factsheet may be latest-period calculations only.
- Industry groups have different accounting denominators; some P/E values are undefined.
- FCF Yield, EBITDA and AFFO formulas vary by vendor.
- Adjusted close may be an approximation to total-return indices, not exactly reinvested distributions.
- Past constituent weights and corporate actions are essential for unbiased historical analysis.
