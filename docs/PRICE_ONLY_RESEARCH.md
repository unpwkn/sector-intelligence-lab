# Phase 6C price-only exploratory sector research

## Data contract and current readiness

Phase 6C reads a separate snapshot schema for historical USD adjusted closes.
Phases 6A and 6B retain their full-data and corporate-action validation contracts.
Phase 6C supports XLK, XLC, XLY, XLP, XLE, XLF, XLV, XLI, XLB, XLRE, XLU and SPY
on one XNYS session grid. Sessions before a ticker's first trading date do not
require a row. Every listed session in the requested window requires a positive
finite close. Duplicate rows, gaps, invalid session labels, mixed source bases
and snapshots ending before all 12 ETFs were listed block research. No prices are
filled. The common backtest also requires enough history for the Phase 2's
252-session offset, an eligible month-end and the next execution session.

Phase 6C market results carry `RETROSPECTIVE_ADJUSTED_PRICE_PROXY`,
`PRICE_ONLY_UNRECONCILED` and `EXPLORATORY`. Both `point_in_time_history` and
`total_return_verified` are false. Retrieval timestamps identify the original
source batch and never stand in for when a price was available historically.
The panel may incorporate later actions and data revisions. The loader validates
the supplied evidence and declarations. Source identity, external data quality
and license rights require separate confirmation.

This delivery had no authorized, verified real adjusted-close panel on which to
run Report #001. The workflow and algorithms were tested with fictional data.
The [checked-in template](../reports/report-001/README.md) contains the methods
and evidence still needed. For live IBKR data, the remaining requirement is a
local verified Phase 6B archive with completed `ADJUSTED_LAST` responses for all
12 instruments and appropriate rights for the intended use.

## Import one private source vintage

Install the project on a private workstation with Python 3.11 or later.
Use `.[research]` for empirical chart generation and add `app` for Streamlit.

```bash
python -m pip install -e '.[research,app]'
export PRICE_RESEARCH_PRIVATE=/absolute/path/to/private/price-research
```

### Non-IBKR vendor CSV

The observations file is UTF-8 with these three columns in order.

```csv
date,ticker,adjusted_close
```

Use exact `YYYY-MM-DD` session labels and only observations inside the stated
inclusive interval. A single provenance JSON applies to the entire file. Use a
consistent vendor dataset, currency, close convention, adjustment basis,
and original batch timestamp for every ticker. Do not stitch together vintages
with potentially inconsistent adjustment factors. Retain original source
artifacts privately if canonicalizing a supplier export.

```json
{
  "provider": "Licensed vendor name",
  "source_id": "Vendor dataset/export identifier",
  "source_reference": "Private export lineage identifier or credential-free source URL",
  "license_reference": "Applicable terms and permission record identifier",
  "retrieved_at": "2025-01-03T21:30:00Z",
  "adjustment_basis": "split_and_distribution_adjusted",
  "data_kind": "market",
  "raw_sha256": "Replace with the exact lowercase SHA-256 of observations.csv"
}
```

Use `synthetic` for fictional fixtures. Use `market` only for real observations
whose use is authorized. Confirm the basis from the vendor, including dividends
and splits. Publish no credentials or sensitive license terms in the declaration.
Use `sha256sum` (or `shasum -a 256` on macOS) for the file hash. The original
retrieval must follow the final covered session close.

```bash
python scripts/price_only_research.py import-csv \
  --observations "$PRICE_RESEARCH_PRIVATE/observations.csv" \
  --provenance "$PRICE_RESEARCH_PRIVATE/provenance.json" \
  --start 2018-06-18 --end 2024-12-31 \
  --output "$PRICE_RESEARCH_PRIVATE/snapshots/vendor-v1"
```

### IBKR TWS archives

Use the [local Phase 6B capture workflow](IBKR_HISTORY.md). A bars-only archive
may be valid for Phase 6C even when Phase 6B cannot issue its reconciled OHLCV
export. Phase 6C verifies the original capture and request envelope, matches the
contract identifiers, checks callback completion and reads only
`ADJUSTED_LAST.close`. It retains the original capture bytes privately and
records the per-ticker retrieval times. Auxiliary price fields and the action
ledgers are outside this research contract. Connected-plugin `Last`,
split-only `TRADES`, unadjusted closes and manually invented adjustments
are ineligible. The generic CSV import cannot establish an IBKR request type.
Request accounts, subscriptions and retention rights remain subject to IBKR
and exchange terms. [TWS historical data definitions](https://www.interactivebrokers.com/docs/tws-api/doc/md/historical-bars),
[IBKR market data agreements](https://www.interactivebrokers.com/en/accounts/forms-and-disclosures-market-data.php).

```bash
python scripts/price_only_research.py import-ibkr \
  --archive "$PRICE_RESEARCH_PRIVATE/ibkr-capture" \
  --source-reference "Private TWS archive identifier" \
  --license-reference "Private entitlement and permissions record" \
  --output "$PRICE_RESEARCH_PRIVATE/snapshots/ibkr-v1"
```

Each snapshot contains original observations, provenance, the normalized CSV,
and a manifest with hashes, coverage and dependency versions. Opening it replays
the normalization from the original evidence and verifies exact file and manifest
bytes. Save the manifest hash separately, preserve the source, record
`git rev-parse HEAD` and `python -m pip freeze`, and replay with the same
dependencies. The hashes provide integrity for the saved vintage.

## Report #001 and dashboard

Generate the evidence-pending template offline with base dependencies.
The generator emits the same template for a snapshot labeled synthetic.

```bash
python scripts/price_only_research.py report --template --output work/report-001-template
```

Generate the private research report only after obtaining an eligible panel.

```bash
python scripts/price_only_research.py report \
  --snapshot "$PRICE_RESEARCH_PRIVATE/snapshots/ibkr-v1" \
  --output "$PRICE_RESEARCH_PRIVATE/report-001"
SECTORLAB_PRICE_ONLY_DIR="$PRICE_RESEARCH_PRIVATE/snapshots" \
  streamlit run app.py --server.address 127.0.0.1
```

Use a new output directory for each run. The research export contains a Markdown
report, readable PNGs, coverage and scenario metadata, and machine-readable
performance, trade, decision, rolling-relative and forward-diagnostic tables.
The dashboard mode **Price-only Exploratory Research** displays the comparable
coverage, as-of rankings and strategy scenarios.

Keep licensed data, archives, snapshots, dashboards and derived outputs private.
Confirm applicable research use, off-platform/API rights, retention, backup,
and any publication of raw or derived output with the relevant vendor and
exchanges before sharing results. The public template contains no observations,
performance numbers or figures.

## Statistical conventions

The existing Phase 2 weights and Phase 5 portfolios, benchmarking, cost funding
and execution timing drive the research. Gross and net share the same signal
and execution rules. Transaction costs are all-in scenarios of 0, 5 and 10 bps
per traded dollar, applied to purchases and sales. The report uses the Phase 5
default 0% risk-free rate and half-L1 annualized turnover. Strategy volatility,
Sharpe, IR and tracking error use the existing 252-session convention. Monthly
hit rate and rolling relative wealth use completed matched month-ends.

Each forward window starts at the close after the signal month-end and ends at
the close after the horizon's month-end. Relative performance divides sector
wealth growth by SPY's and subtracts one. On each completed horizon, Spearman rank
IC compares the 11 scores with those subsequent relative returns, using average
ranks for ties. Constant cross-sections have undefined IC. The mean IC weights
signal months equally. Because forward windows overlap, the test of zero mean IC
uses Bartlett Newey–West HAC on the monthly series. The lag count is at least
the horizon when sample length permits. Missing IC slots preserve calendar lags.
Inference is withheld below 24 valid months or twice the lag count plus one,
whichever is larger. Reported intervals and p-values are asymptotic, exploratory
and unadjusted across horizons. Forward spreads and regime summaries are
descriptive. [Newey and West, NBER working paper](https://www.nber.org/papers/t0055).

Dynamic-universe paths may start earlier and use the eligible-sector equal-weight
benchmark. The common-window sensitivity rebases both universe policies to the
primary baseline and carries in earlier dynamic holdings. Regimes use the sign
of SPY's trailing 126-session return at the signal close. The study does not
establish independently verified total returns, point-in-time results or
investable alpha.
