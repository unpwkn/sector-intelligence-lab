# Phase 1: real ETF price ingestion

Phase 1 is an independent price-data path. It does not change the factor engine,
synthetic demo, dashboard, valuation, estimates, quality or breadth inputs. Downloaded
prices alone cannot produce a complete real-data opportunity/rotation score.

## Source and access

The adapter uses Alpha Vantage's documented
[`TIME_SERIES_DAILY_ADJUSTED`](https://www.alphavantage.co/documentation/#dailyadj)
JSON endpoint at `https://www.alphavantage.co/query`. It requests `outputsize=full`
for each of the 11 tickers in `sectorlab.config.SECTORS` and benchmark `SPY`, then
selects the inclusive requested date range. The API supplies as-traded OHLC and
volume, adjusted close, dividend amounts, and split coefficients. No scraping or
undocumented Yahoo endpoints are used.

**An API key with premium Daily Adjusted entitlement is required.** A free/demo
key must not be assumed to provide this endpoint or full history. Check current
[plans and limits](https://www.alphavantage.co/premium/) before subscribing. Full
history is vendor-dependent (advertised as 20+ years), not a promise of coverage
back to ETF inception. Strict coverage validation catches truncated histories.
All 12 instruments are requested, but upstream entitlements, symbol coverage and
outages can prevent a download. There is no synthetic fallback.

Review [Alpha Vantage terms](https://www.alphavantage.co/terms_of_service/) for your
use. An API subscription is not blanket redistribution or commercial-use
permission; confirm applicable rights with the vendor. The repository's MIT
license covers code, not third-party data. Raw and normalized downloads are local
and Git-ignored. Do not publish them without appropriate permission.

The service can throttle, change schema, revise history or become unavailable.
Each request has a 30-second timeout; HTTP, JSON, quota, entitlement and schema
errors fail the run. There are no automatic retries or undocumented fallbacks.
After resolving the error or waiting for your quota window, rerun the command.
A run makes 12 sequential full-history calls: ensure your plan permits this request
rate. Do not schedule concurrent runs against a shared quota without coordination.
No key or source URL containing a key is written to snapshots or normal errors.

## Reproducible execution

From the repository root, with Python 3.11+:

```bash
python -m venv .venv
# Activate .venv using your operating system's command.
pip install -e ".[dev]"
# Supply ALPHAVANTAGE_API_KEY through your shell or secret manager.
# Do not put the real key in a script, command committed to Git, or a PR.
python scripts/ingest_market_data.py \
  --start 2024-01-02 --end 2024-12-31 --snapshot av-2024-v1
pytest -q
```

The output is `data/market/av-2024-v1/`. `--snapshot` accepts a single directory
name only. Existing snapshots cannot be overwritten: use a new name for a new
retrieval. `--start` and `--end` are inclusive `YYYY-MM-DD` session-date bounds.
Keep the manifest, raw files and normalized CSV together. Record your environment
with `pip freeze > data/market/av-2024-v1/environment.txt` if an exact dependency
lock is needed; core numerical/calendar versions are already in the manifest.
A fresh API call may produce different values after vendor corrections. The saved
raw response plus hashes is the evidence for a specific retrieval; matching dates
alone does not make later downloads identical.

A real download requires outbound HTTPS access to `www.alphavantage.co` and your
key's entitlement. Offline CI requires neither. This implementation was validated
with fictional mocked payloads, not a credentialed live-service smoke test.

## Validation and calendars

- Universe: XLK, XLC, XLY, XLP, XLE, XLF, XLV, XLI, XLB, XLRE, XLU, SPY.
- `exchange-calendars` XNYS defines expected US equity sessions, holidays,
  exceptional closures and close times (including early closes and DST).
  This is a session-calendar proxy for NYSE Arca listings, not an Arca trading
  halt/intraday calendar. Calendar rule corrections can change results; versions
  are recorded. Session labels remain exchange-local dates, not midnight UTC bars.
- Only completed sessions are accepted. An unclosed/future expected session
  fails the request before provider calls. Provider publication can lag the close;
  a just-closed but unpublished session will fail the coverage check. Use an end
  date with a publication buffer, usually the previous completed trading day.
- Expected coverage begins at the first trading session: SPY 1993-01-29; original
  nine sector SPDRs 1998-12-22; XLRE 2015-10-08; XLC 2018-06-19. These are ETF
  trading histories, not backfilled sector indices. Dates before launch do not
  count as missing observations. See the manager's [sector fund directory](https://www.sectorspdrs.com/)
  and [SPY information](https://www.ssga.com/us/en/individual/etfs/spdr-sp-500-etf-trust-spy)
  for fund context. Vendor history may start later; it is not silently accepted.
- Required columns, symbols, dates, finite positive prices and split coefficients,
  nonnegative dividends, integer nonnegative volume and OHLC bounds are checked.
  A missing action field is an error, not an assumed zero dividend/unit split.
- Exact normalized duplicate records collapse with a count in the manifest.
  Conflicting duplicate sessions fail. Data are sorted by date/ticker.
- A missing expected session fails by default. `--allow-missing` explicitly saves
  an incomplete snapshot with all gaps listed per ticker. It still rejects an
  entirely absent active instrument. There is no forward fill, interpolation,
  weekend row or invented price. Review gaps before any downstream analysis.
- All symbols must validate before the staged snapshot is renamed into place.
  Any failure removes staging files. No successful partial universe is published.

## Adjustment and corporate-action semantics

`adjusted_price_proxy` is the vendor's adjusted close. Dividend and split fields
are retained as reported in native USD/share and new/old share ratio (1 means no
split), respectively. OHLC are left as supplied; they must not be mixed with
adjusted close as if they share a price basis. The adapter does not apply splits
or dividends again. It does not independently certify completeness or accuracy
of the action ledger, historical volume adjustment, or vendor methodology.

**This is not a verified dividend-reinvested total-return series.** Reinvestment
timing, distribution treatment, taxes, rounding and vendor corrections can cause
differences. Phase 1 does not reconstruct total returns or label adjusted-price
changes as verified total returns. Production return research requires separate
corporate-action reconciliation and comparison with an independently validated
series. Mechanically valid data can still contain economically incorrect vendor
values; no cross-vendor reconciliation is claimed.

Modern adjusted history can incorporate later actions and corrections. Each row
has `retrieved_at`, deliberately **no historical `available_at`**. Retrieval does
not prove historical investment availability. Do not copy session dates into
`available_at` or treat these files as a point-in-time backtest input. The existing
synthetic pipeline's price contract is separate; Phase 1 does not feed real prices
into it or fabricate the missing valuation, revision, quality or breadth pillars.

## Local schema v1.0

Each new snapshot directory contains:

```text
prices.csv
manifest.json
raw/XLK.json ... raw/SPY.json
```

`prices.csv` has one observed `(date,ticker)` row, with these columns in order:

| Field | Type / meaning |
|---|---|
| date | ISO exchange session date, YYYY-MM-DD |
| ticker | One of the 12 universe symbols |
| open, high, low, close | Positive finite vendor OHLC, USD/share |
| adjusted_price_proxy | Positive finite vendor adjusted close, USD/share |
| volume | Nonnegative integral vendor share volume |
| dividend_amount | Nonnegative vendor distribution, USD/share |
| split_coefficient | Positive vendor split ratio, 1 when none |
| retrieved_at | Timezone-aware ISO retrieval timestamp; normally UTC |
| provider | `alpha_vantage` for the supplied adapter |
| currency | `USD` for this US ETF universe |
| price_basis | Always `adjusted_price_proxy` |
| total_return_verified | Always `False` |

`manifest.json` includes schema version, provider/endpoint, requested bounds,
universe, calendar, per-symbol retrieval timestamps and coverage (expected count,
observed count, exact duplicates removed, all missing session dates and first
trading session), missing-data override, creation timestamp, numerical/calendar
package versions, and SHA-256 hashes of `prices.csv` and every raw response.
`point_in_time_history` and `total_return_verified` are explicitly false. Raw
responses retain the full vendor history, not only the normalized requested range;
this can consume disk space and remains subject to vendor licensing.

## Extension and tests

`MarketDataProvider` is a structural protocol: `name`, `price_basis`, and
`fetch(ticker, start, end) -> PriceResponse`. A response carries a DataFrame in
`providers.FIELDS`, raw JSON evidence and an aware retrieval timestamp. Implement
another adapter with equivalent price/action semantics and document its rights
and methodology before use. USD and adjustment semantics are explicit Phase 1
constraints; an adapter for another basis/currency needs a schema extension.

Tests inject a transport or a mock provider. All numerical fixtures are fictional
and only verify software behavior. Tests exercise all 12 symbols, field mapping,
actions, holidays, early closes, launch dates, gaps, duplicates, invalid values,
provider failures, secret-safe errors, failure cleanup and saved-file hashes.
