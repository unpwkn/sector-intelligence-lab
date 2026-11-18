# Phase 3 valuation CSV

Run Streamlit locally with `streamlit run app.py` and open **Sector Valuation Research**. Import a UTF-8 CSV or select **Synthetic demonstration** to exercise the same contract with reproducible fictional inputs. Keep source files in ignored `data/valuation/` or outside the repository. The [feasibility report](DATA_FEASIBILITY.md) documents the input gap.

## Version 1 contract

Use exactly these header names. Column order is flexible. Each row is one observation vintage for an already aggregated fund metric.

```csv
ticker,metric,observation_date,observed_at,value,denominator,fiscal_period,metric_definition,vendor,source,provenance,published_at,available_at,history_type,data_license
XLK,forward_pe,2025-12-31,2025-12-31T16:00:00-05:00,18.2,100,FY1,synthetic:forward_pe:v1,synthetic,fictional example,fictional:XLK:2025-12,2026-01-01T16:00:00-05:00,2026-01-01T17:00:00-05:00,synthetic,synthetic
```

| Field | Meaning |
|---|---|
| `ticker`, `metric` | One of the 11 configured sector/metric pairs, or SPY with `forward_pe` or `dividend_yield`. ETF observations must be on or after first trading. |
| `observation_date`, `observed_at` | New York calendar date (`YYYY-MM-DD`) and exact instant of the underlying valuation snapshot. Use the same cutoff convention across months. |
| `value` | Pre-aggregated ratio in multiples or yield as a decimal (e.g. 0.04 for 4%). Use a blank for an undefined value. |
| `denominator` | Actual economically relevant denominator in the source's consistent units, or a documented sign-preserving equivalent supported by source methodology. For P/E it must represent forward earnings. Record a blank when denominator validity cannot be established. The engine gates on positivity and never derives denominator validity from the submitted ratio alone. |
| `fiscal_period` | Stable fiscal basis or horizon label, e.g. `FY1`, `NTM`, `TTM`, or `midcycle_v2`. Use the recipe to describe calendarization and preserve underlying constituent fiscal dates in source lineage. |
| `metric_definition` | Stable versioned recipe identifier. Record units and currency, fiscal basis, price/NAV convention, weighting, aggregation, minimum constituent coverage, exclusions, denominator construction, accounting adjustments and any cycle assumptions in the corresponding definition. The importer compares the identifier literally. |
| `vendor`, `source` | Provider and stable product or feed identity. These identify a comparable series across vintages. |
| `provenance` | Row-specific source locator, e.g. archived document and page, filing accession plus table, or vendor snapshot ID. Preserve the original unmodified evidence outside the repo. |
| `published_at`, `available_at` | Original release instant and earliest documented access instant for this vintage, including any vendor or acquisition delay. Use ISO-8601 with seconds and a timezone offset for all three instants. Observation ≤ publication ≤ availability. If an exact release time is unknown, use a justified conservative bound and document it in lineage. Corrections have their own release and availability timestamps. |
| `history_type` | One of `point_in_time`, `reconstructed`, `synthetic`, uniform per file. Use `point_in_time` for original archived vintages with documented availability. For a modern reconstruction, record actual reconstruction publication and availability and use `reconstructed`. It can be charted after that availability as raw history. |
| `data_license` | `private` for restricted or unverified redistribution rights, `public_redistributable` only with documented permission, and `synthetic` for fictional files. Rights and timing remain importer assertions. |

Rows sharing ticker, metric, observation date and availability instant are rejected as ambiguous. Keep an earlier vintage as a separate row when adding a correction. The importer rejects malformed structure, missing metadata and inconsistent timings. Blank or non-finite numeric fields and invalid denominator signs stay visible as missing-score reasons. A positive denominator is required for all metrics. Multiples must be positive, dividend yield must be nonnegative, and signed FCF yields and implied cap rates are allowed.

## Reproduce offline

```python
from sectorlab.valuation import evaluate_sector, metric_history
from sectorlab.valuation_data import load_valuation_csv

dataset = load_valuation_csv("data/valuation/monthly.csv")
cutoff = "2026-01-04T00:00:00Z"
result = evaluate_sector(dataset, "XLK", asof=cutoff)
print(dataset.sha256, result.historical_score, result.relative_score, result.blended_score)
history = metric_history(dataset, "XLK", "forward_pe", asof=cutoff)
```

For a fully offline synthetic example, replace the CSV load with
`parse_valuation_csv(synthetic_valuation_csv())` using
`parse_valuation_csv` from `sectorlab.valuation_data` and
`synthetic_valuation_csv` and `DEMO_ASOF` from `sectorlab.valuation_demo`.
Record the exact CSV SHA-256, cutoff, package versions and source definitions when sharing authorized outputs. Changing input byte order changes the fingerprint even if selected vintages and results agree. An import validates the file's structure and declared timing. Source rights, historical holdings, denominator calculations and coverage must be verified against original evidence before use.

Run `pytest -q` and `ruff check .` after installing `.[dev,app]`. All unit tests work offline. No vendor credentials are needed.
