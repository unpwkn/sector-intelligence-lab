# Phase 6B private IBKR daily ETF history

## Data semantics and readiness

This adapter uses the official TWS Python socket API on `127.0.0.1` through a locally
authenticated Trader Workstation or IB Gateway session. It resolves the 11 sector
SPDR ETFs and SPY to USD SMART contracts with primary exchange ARCA, then requests
one-day regular-hours bars. It sends contract details and historical bar requests.
It uses no account, portfolio, streaming quote or trading requests.

| Request | Documented price basis | Use in this project |
| --- | --- | --- |
| `TRADES` | Split-adjusted trade OHLC. Cash distributions are outside this adjustment. | Validate split treatment against independent as-traded closes and split events. |
| `ADJUSTED_LAST` | Split- and dividend-adjusted trade OHLC, where available. | Use the adjusted close as a retrospective price proxy only after reconciliation. |

Both IBKR OHLC series are adjusted. Phase 6A requires as-traded OHLC, so neither
can directly populate those columns. The export uses independent as-traded OHLCV,
reconciled action terms and only IBKR's `ADJUSTED_LAST` for the adjusted close.
IBKR historical volume can use shares or round lots depending on settings,
and aggregation can differ across sources. The archive preserves returned
volume, WAP and bar count. The export takes integral share volume from the
independent source. [Historical bars and adjustment definitions](https://www.interactivebrokers.com/docs/tws-api/doc/md/historical-bars),
[IBKR volume notes](https://www.interactivebrokers.com/support/faq/content/102546341).

Daily timestamps are exchange session dates. The archive records the contract
identifiers, contract time zones, request parameters, callback bounds, client
SDK/server versions and local UTC request/retrieval times. Modern adjustments
and subsequent corrections can change older values. Even a reconciled export is
`RETROSPECTIVE_ADJUSTED_PRICE_PROXY`. Historical data availability and exact
distribution reinvestment remain unverified.

## Set up a local preflight

1. On your private workstation, use the [official TWS Python SDK](https://www.interactivebrokers.com/campus/trading-lessons/accessing-the-tws-python-api-source-code/).
   Install the project and the Python client from IBKR's distribution into the same
   environment. An SDK install is unnecessary for offline verification or CI.

   ```bash
   python -m pip install -e .
   python -m pip install /path/to/IBJts/source/pythonclient
   ```

2. Log in locally. Enable socket clients, keep **Read-Only API** enabled and allow
   loopback only. Use the port shown in your TWS or Gateway settings and an unused
   positive client ID. Confirm historical US equity/ETF market data is entitled for
   that user and the Market Data API Acknowledgement is in place. A chart visible
   on-platform does not establish off-platform API entitlement. Confirm applicable
   live/paper and professional/nonprofessional subscription requirements with IBKR.
   [Local socket settings](https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api/),
   [historical entitlement](https://www.interactivebrokers.com/support/faq/content/obtain-historical-data-via-api-28236420),
   [API acknowledgement](https://www.interactivebrokers.com/support/faq/content/market-data-api-acknowledgement-794571755).

3. Capture a completed-session interval into a new directory outside the checkout.
   For a depth probe choose a start more than five years before the end. The
   example uses Gateway's common paper port, which may differ in your settings.

   ```bash
   export IBKR_PRIVATE=/absolute/path/to/private/ibkr-research
   python scripts/ibkr_history.py capture --port 4002 --client-id 106 \
     --start 2018-06-18 --end 2025-12-31 --output "$IBKR_PRIVATE/capture"
   python scripts/ibkr_history.py verify --archive "$IBKR_PRIVATE/capture"
   ```

The adapter uses blank `endDateTime` for both series because `ADJUSTED_LAST`
requires it. It selects a year-based duration from the date of retrieval to reach
the requested start, with a boundary buffer. The requested length does not
establish vendor coverage. Responses can contain later bars, including a partial
latest session. They stay in the private capture and are excluded from the work
window. Both series must cover every expected listed XNYS session within that
window. The preflight reports requested-window and response first/last dates,
counts bars in the window more than five years before retrieval, and records
missing, duplicate and unexpected sessions, pre-listing observations, price consistency, entitlement or
pacing failures, and reconciliation status per ETF. Use a completed EOD window
with publication time to spare.

Only one request is in flight, and historical requests start at least two seconds
apart. The adapter does not automatically retry or substitute another feed.
Reusing archives avoids repeated requests. Broker soft throttling, primary
exchange changes, gaps, or an unavailable `ADJUSTED_LAST` can prevent coverage
even when the request length is allowed. Inspect `report.json` before restarting
or shortening a request. [Blank adjusted-series end time](https://www.interactivebrokers.com/docs/tws-api/sync-doc/historical-market-data),
[duration limits](https://www.interactivebrokers.com/docs/tws-api/doc/md/historical-bars),
[pacing and unavailable data](https://www.interactivebrokers.com/docs/tws-api/doc/md/market-data-limitations).

## Reconcile with independent evidence

The daily bar API does not deliver a per-event dividends or splits ledger. The
IBKR dividends tick gives trailing and forecast aggregates and the next dividend,
which cannot reconstruct the required history. Use the licensed IBKR action export
you obtained separately and retain original artifacts and records of their
provenance. Account-specific activity statements can contain only events that
affected that account and are insufficient as an all-universe action ledger.
[IBKR dividend tick](https://www.interactivebrokers.com/docs/tws-api/doc/md/streaming-data).

Prepare private `references.json` with these three top-level objects.

| Object | Required rows |
| --- | --- |
| `broker_actions` | `date,ticker,dividend_amount,split_coefficient` from the IBKR historical action source. Use `"source": "IBKR"`. |
| `independent_actions` | Same fields from an independent issuer, exchange or licensed source. |
| `as_traded` | `date,ticker,open,high,low,close,volume` for every expected listed session from an independent source. Declare `"price_basis": "as_traded"`. Use USD regular-session trade bars on a close convention comparable to IBKR SMART regular-hours bars. |

Each object has `source`, `source_reference`, `license_reference`, timezone-aware
`retrieved_at`, `start`, `end`, `universe`, and `rows`. The window must match
the capture and the source must cover all 12 ETFs. Use this universe order.

```json
["XLK", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLRE", "XLU", "SPY"]
```

The requested bounds in each action declaration attest to a complete event
history for the listed symbols and window. An absence of rows means the source
confirmed no event. Event rows use ex-date USD cash per contemporaneous
post-split share and effective-session new/old split ratio. Use 0 for no cash
and 1 for no split in an event row. Combine same-date cash items on a common
share basis and supply one row per ticker and date. Dates use `YYYY-MM-DD`.
Retain original exports and transformation recipes at the private lineage
references. Use source or agreement identifiers that contain no secrets.

Replay the original capture with the reference bundle. This can also be done
at capture time with `--references`.

```bash
python scripts/ibkr_history.py reconcile --archive "$IBKR_PRIVATE/capture" \
  --references "$IBKR_PRIVATE/references.json" --output "$IBKR_PRIVATE/reconciled"
python scripts/ibkr_history.py verify --archive "$IBKR_PRIVATE/reconciled"
```

The preflight matches the independent and IBKR event dates and values. It
checks every subsequent daily close ratio. For a split factor `s`, it compares
today's `TRADES` close ratio with the independent close divided by the prior
independent close on the post-split share basis. For cash `d`, it checks the
prior day's `ADJUSTED_LAST / TRADES` ratio divided by today's ratio against the
conventional backward cash factor `1 - d / (prior raw close / s)`. The daily ratio tolerance is five
basis points. On a cash event it is capped at half the implied cash adjustment
so unchanged ratios cannot pass. This checks one explicit convention and can
fail for differing close definitions, rounding, special distributions or
unsupported actions. Move the start back if an event falls on a ticker's first
covered session and a prior trading observation exists. Otherwise that event
requires external evidence beyond this validator. Investigate discrepancies
instead of broadening the tolerance simply to pass a sample.

An archive is saved for inspection if coverage or references block the export.
The CLI exits with status 0 when an export passes, 1 when the archive is valid
but is ineligible for export, and 2 for command or archive integrity errors.
The archive includes `capture.json`, `report.json`, `manifest.json` and, when
supplied, exact reference bytes in `references.json`. The manifest records
SHA-256 checksums and numerical/calendar versions. Replay checks the hashes
and regenerates the report and any export. Retain the manifest hash separately
and record the code revision and dependency versions. Local evidence and
declarations do not independently prove source identity or licensing rights.

## Use a reconciled export privately

After all 12 instruments pass, the archive includes `prices.csv` and
`provenance.json`, suitable for the existing Phase 6A importer. Keep its
snapshot and any report outside the checkout as well. Use the same bounds
as the verified archive.

```bash
python - <<'PY'
import os
from pathlib import Path

from sectorlab.market_data import import_csv

root = Path(os.environ["IBKR_PRIVATE"])
source = root / "reconciled"
import_csv(source / "prices.csv", source / "provenance.json",
           "2018-06-18", "2025-12-31", root / "phase6a-snapshot")
PY
python scripts/run_backtest.py --snapshot "$IBKR_PRIVATE/phase6a-snapshot" \
  --output "$IBKR_PRIVATE/phase5-report"
```

Phase 2 and Phase 5 rules are unchanged. The user-reported five-year delivery is
separate from this adapter's validation. The greater-than-five-year request,
split/dividend reconciliation, Phase 6A import and Phase 5 path were exercised
with fictional responses. Live depth, actual event reconciliation and empirical
returns remain unverified in this change.

## Licensing and storage

Keep broker logins, session files, IBKR market data, independent source files,
archives, snapshots, and derived outputs on authorized private storage. The
CLI rejects input and output paths inside this checkout. Run live capture only
on your private machine. Automated CI uses fictional data and needs neither
credentials nor the proprietary SDK. Review relevant IBKR/GFIS and exchange
agreements for off-platform API use, organizational or non-display research,
retention and backup, access by other users, and publication of raw observations
or derived metrics, charts and results. Rights may differ by data source and
subscription. Until confirmed, keep broker-derived research private.
[IBKR market data agreements](https://www.interactivebrokers.com/en/accounts/forms-and-disclosures-market-data.php),
[IBKR subscriber agreement](https://www.interactivebrokers.com/Universal/servlet/Registration_v2.formSampleView?file=IBISAgreement.html).
