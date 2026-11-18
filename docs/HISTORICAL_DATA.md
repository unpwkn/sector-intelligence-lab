# Phase 6A historical ETF data

## Provider review

Access and terms reviewed on 2026-11-18. Coverage below describes published
offerings. ETF-by-ETF depth must be measured from a licensed export. Confirm
permissions for internal firm research, storage after cancellation and any
publication of source data or derived results under the applicable agreement.

| Provider | Access, depth and API limits | Adjustments and evidence | Commercial research and redistribution |
| --- | --- | --- | --- |
| State Street, ETF sponsor | Public fund pages, fact sheets, NAV history and distribution references. Historical depth depends on the product and field. Reviewed pages do not establish a complete inception-to-date daily OHLCV API for all 12 funds. | Primary source for inception, listing metadata and fund distributions. NAV, bid/ask midpoint and primary exchange close are distinct. Match field definitions before comparing prices. | Check website, portal and third-party data terms. Public fact sheets and downloadable workbooks do not grant a public price-data redistribution license. [Fund pages](https://www.ssga.com/us/en/individual/resources/doc-viewer), [XLK definitions](https://www.ssga.com/mainfund/XLK), [terms](https://www.ssga.com/us/en/footer/terms-and-conditions) |
| Stooq | Public historical portal and per-symbol CSV route. The workspace received HTTP 403 at the proxy for both. All-12 depth, current free/paid entitlements and automation limits are unverified. | As-traded and adjusted bases, adjustment factors and a matching action ledger could not be established from accessible primary documentation. | No commercial or redistribution permission was verified. [Historical portal](https://stooq.com/db/h/), [SPY history page](https://stooq.com/q/d/?s=spy.us) |
| Yahoo Finance | Free historical views for many listed securities. Yahoo Help lists historical CSV export under paid Gold. Actual earliest observations depend on the symbol, and no supported public Finance price API for this pipeline was identified. | Yahoo documents split/dividend adjusted close and displays events. Export price bases and action coverage need reconciliation, especially around special distributions. | Yahoo's service terms restrict automated collection and reuse. Gold access alone does not establish commercial archival or redistribution rights. [Historical CSV help](https://help.yahoo.com/kb/SLN2311.html), [adjusted close](https://au.help.yahoo.com/kb/finance-app-for-ios/adjusted-close-sln28256.html), [terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) |
| Alpha Vantage | Documented key-based API. Standard free service allows up to 25 requests/day across most endpoints. Full daily adjusted history is premium, with plan-specific limits. Advertised depth is 25+ years for this endpoint, subject to verification per ETF. | As-traded OHLCV, separate adjusted close, cash distributions and split coefficients in one response. The endpoint contract is documented and the existing adapter retains the response. Coverage and action accuracy require a live entitlement and reconciliation. | Standard license covers individual noncommercial use. Firm research requires a written commercial agreement. Confirm redistribution and retention rights separately. [API](https://www.alphavantage.co/documentation/#dailyadj), [limits](https://www.alphavantage.co/support/), [plans](https://www.alphavantage.co/premium/), [terms](https://www.alphavantage.co/terms_of_service/) |
| EODHD | Documented key-based EOD API. Free plan has one year of depth and 20 calls/day. Paid plans extend depth, with advertised US history up to 30+ years for some instruments. Confirm exact ETF start dates and limits. | Documents as-traded OHLC, split-adjusted volume, split/dividend adjusted close and separate action endpoints. Verify ex-date action units and split ratios when mapping the export. | Personal plans cover private individual backtesting. Commercial internal research and redistribution are licensed separately. Confirm storage and output rights for the use case. [API](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes), [plans](https://eodhd.com/pricing/), [personal use](https://eodhd.com/help/personal-use), [commercial use](https://eodhd.com/help/commercial) |
| Massive (formerly Polygon.io) | Documented US daily aggregates. Individual Basic offers two years free, with longer windows on paid plans. Listed US daily bars start September 2003 at the earliest, so full SPY and original sector fund history is unavailable. | Consolidated aggregates offer as-traded or split-adjusted bars. Dividend adjustment requires a separate action-based calculation and reconciliation. | Individual plans are nonprofessional. Professional use needs a business plan, and redistribution requires an applicable license. [Plans](https://massive.com/pricing?product=stocks), [stock data FAQs](https://massive.com/knowledge-base/categories/stocks), [adjustments](https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends), [redistribution](https://massive.com/knowledge-base/article/how-can-i-redistribute-massives-market-data) |

**Selected route** — license-compatible local CSV import. It works offline and
incurs no additional API cost when the researcher already holds a suitable
historical export. Alpha Vantage Daily Adjusted remains the selected direct
adapter because it delivers the required fields together and has a documented
interface. EODHD is an alternative export candidate with separate action files.
Use State Street for identity and action checks when permitted, and a licensed
independent source for like-for-like daily price checks.

**Real coverage verified in this phase** — zero price rows. No appropriately
licensed 12-ETF export or eligible vendor entitlement was available in the
workspace. Alpha Vantage requires premium access to the adjusted daily endpoint
and a written agreement for firm use. EODHD's free depth is insufficient for
Phase 2's 253-session warm-up plus a useful backtest window, and commercial access
was unavailable. Massive Basic is nonprofessional and supplies no
dividend-adjusted bars. Stooq access and rights remain unverified, and the sponsor
and Yahoo materials did not supply an authorized complete export. Numerical
checks, including prefix stability, used fictional prices. No independent price
reconciliation or empirical return calculation was performed.

## Fund inception and first eligible session

A fund can exist before it begins trading. Expected daily prices start at the
first exchange trading session, and there are no observations or missing-session
penalties before then. The calendar is XNYS as a proxy for US equity sessions.
See the [calendar and validation notes](MARKET_DATA.md#validation-and-calendars).

| Tickers | Sponsor inception | First trading session used |
| --- | --- | --- |
| XLK, XLY, XLP, XLE, XLF, XLV, XLI, XLB, XLU | 1998-12-16 | 1998-12-22 |
| XLRE | 2015-10-07 | 2015-10-08 |
| XLC | 2018-06-18 | 2018-06-19 |
| SPY | 1993-01-22 | 1993-01-29 |

Sources include State Street's [sector fund summary](https://www.ssga.com/library-content/pdfs/etf/us/target-a-lower-cost-of-ownership-with-sector-etfs.pdf),
[XLK listing](https://www.ssga.com/uk/en_gb/institutional/etfs/state-street-technology-select-sector-spdr-etf-xlk),
[XLRE](https://www.ssga.com/us/en/individual/etfs/state-street-real-estate-select-sector-spdr-etf-xlre),
[XLC](https://www.ssga.com/us/en/individual/etfs/state-street-communication-services-select-sector-spdr-etf-xlc),
[SPY inception and listing history](https://www.ssga.com/us/en/intermediary/insights/how-spy-reinvented-investing-story-of-first-us-etf),
and the [historical SPY first-session record](https://stockspricehistory.com/quotes/SPY/history/1993-01-29).

Phase 6B provides a [private IBKR capture and reconciliation workflow](IBKR_HISTORY.md)
that can generate this CSV contract from independently reconciled evidence.

## Import and replay

1. Save licensed source files, their checksums, the retrieval/export recipe, and
   the applicable terms in private storage. Place the canonical import files
   outside the checkout or in ignored `work/`. Use one consistent vendor,
   close convention and adjustment vintage for the requested panel. Record each
   original retrieval if the export was assembled from multiple files.
2. Produce a UTF-8 comma-separated file with these ten required column names.
   Column order is flexible. Include only rows in the requested inclusive range,
   for every listed ETF session. `date` is the exchange session in `YYYY-MM-DD`.
   USD OHLC are as traded and `adjusted_price_proxy` is the supplier's split-
   and distribution-adjusted close. `volume` is nonnegative integral share
   volume. Cash distributions use USD per share on ex-date and splits use the
   new/old share ratio on the effective session. Use 0 and 1 respectively
   only when the source confirms no action. A blank action, NAV, split-only price,
   or pre-listing sector index does not satisfy this contract.

   ```csv
   date,ticker,open,high,low,close,adjusted_price_proxy,volume,dividend_amount,split_coefficient
   ```

3. Add this JSON declaration alongside the CSV. Replace the example values
   with actual evidence. For a multi-file export, use the time the final
   constituent source file was retrieved and reference the private per-file
   evidence. The timestamp must be timezone-aware and at or after the last
   requested session close. Keep credentials and sensitive agreement details
   out of the declaration. Use `synthetic` for fictional inputs.

   ```json
   {
     "source": "Name of licensed data source",
     "source_reference": "Credential-free export or internal lineage reference",
     "license_reference": "Applicable terms or nonsecret agreement identifier",
     "retrieved_at": "2024-12-31T23:00:00Z",
     "data_kind": "market",
     "ohlc_basis": "as_traded",
     "adjusted_close_basis": "split_and_distribution_adjusted",
     "corporate_action_method": "Document source price adjustment, action field mapping, and any distribution or volume conventions"
   }
   ```

4. Import the complete file and run Phase 5. The date window below is an example
   with momentum warm-up. It does not indicate that this dataset was obtained.

   ```bash
   python scripts/ingest_market_data.py --csv work/prices.csv \
     --provenance work/provenance.json --start 2023-01-03 --end 2024-12-31 \
     --snapshot historical-2024-v1
   python scripts/run_backtest.py --snapshot data/market/historical-2024-v1 \
     --output work/historical-2024-backtest
   python -m pip freeze > work/historical-2024-environment.txt
   git rev-parse HEAD > work/historical-2024-revision.txt
   ```

A v1.1 CSV snapshot stores normalized `prices.csv`, `manifest.json`, and the
exact bytes of the canonical input and declaration in `raw/observations.csv`
and `raw/provenance.json`. Preserve the original vendor artifacts separately
when canonicalization changed their format. The loader checks file hashes,
revalidates the raw CSV against the session grid, and compares the observations
and duplicate counts with the normalized snapshot. Source-declared methodology
and rights still require external evidence. The original manifest hash identifies
the precise snapshot. Reimporting identical inputs with the same package versions
reproduces the price file and raw hashes while recording a new creation timestamp.

An EOD refresh uses a fresh, complete export through the newest published
completed session and a new snapshot name. If publication is delayed, stop at
the last session with full coverage. Avoid appending newer adjusted values to an
older vintage because a split, dividend or vendor revision can change historical
levels. An incomplete snapshot can be retained with `--allow-missing` for
diagnostics. Phase 5 refuses recorded gaps in the sample or warm-up. Neither
route forward-fills. Keep vendor files, snapshots and derived reports private.

## Evidence needed before a performance claim

- Verify observed first and last sessions, row counts and gaps by ticker against
  the exchange calendar and archived source. Retain retrieval metadata,
  version info, manifest hash, code revision, universe and execution parameters.
- For each ticker, select comparable official or licensed independent observations
  across first/last coverage, ordinary dates and action windows. Compare same-
  basis closes within a rounding tolerance documented in advance. Check
  ex-dates, cash amounts and split ratios against the issuer or independent
  action ledger and compare normalized adjusted returns around events. Include
  special distributions and reclassifications. Save discrepancy evidence
  privately and resolve material breaks before relying on results.
- Reconcile adjusted-price changes with an independently validated distribution-
  reinvested return series before treating them as total returns. Review
  realistic execution costs, investable universe definitions and robustness
  out of sample before inferring investable performance from the simulation.
- For point-in-time claims, use archived vintages or verified timestamped
  observations and revisions as they were available at each modeled signal.
  Phase 5 rejects later session observations at an earlier signal cutoff, while
  a modern export can embed historical corrections and future actions in older
  values. Market CSV simulations carry `RETROSPECTIVE_ADJUSTED_PRICE_PROXY`.

See [backtesting methodology](BACKTESTING.md) for execution and metric details
and [data policy](DATA_POLICY.md) for storage and publication.
