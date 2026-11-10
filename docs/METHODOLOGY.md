# Methodology v0.1 (research hypothesis, not a validated signal)

## Scope and hypotheses

Universe: 11 Sector SPDR proxies; comparator SPY. Monthly decisions are the intended research unit, with execution no earlier than the next tradable session after data becomes available. An *as-of* snapshot includes only items whose `available_at` is no later than the timestamp of the signal. The current scaffold calculates one end-of-month snapshot from synthetic data; historical live evaluation is not implemented.

## Valuation V (0-100)

For each sector, use a specific set of economically sensible multiples or yields from `src/sectorlab/config.py` rather than a common sector-wide P/E rule. The present code consumes *already aggregated sector values* and is a prototype: it does not yet implement constituent/subindustry aggregation, cyclically normalized inputs or relative-to-SPY valuation.

For each metric m at t:

```
center = median(last 84 prior observations)
mad = median(abs(prior observations - center))
z = clip((current_value - center)/(1.4826 * mad), -3, +3)
if lower_is_cheaper: z = -z
metric_score = clip(50 + 15*z, 5, 95)
V = sum(metric_score * sector_metric_weight)
```

Require at least 48 prior observations and all required within-sector metrics. Zero MAD yields missing, not a made-up neutral value. Data older than 45 calendar days are stale. The target production design is 70% historical cheapness and 30% sector-appropriate relative valuation; v0.1 implements only historical cheapness.

## Momentum M (0-100)

For daily adjusted-price *proxies* (production must validate total return), compute:

```
RS_6_1 = ln(P_sector[t-21]/P_sector[t-126]) - ln(P_SPY[t-21]/P_SPY[t-126])
RS_12_1 = ln(P_sector[t-21]/P_sector[t-252]) - ln(P_SPY[t-21]/P_SPY[t-252])
RS_3M = ln(P_sector[t]/P_sector[t-63]) - ln(P_SPY[t]/P_SPY[t-63])
```

Code uses -22, -127, -253 and -1, -64 zero-based position indices, equivalent to the above when t is the last row. Each value is ranked within the 11-sector universe to a 0-100 cross-sectional percentile. `breadth` is a separately supplied, point-in-time constituent-level value between 0 and 1. M = 35% RS6-1 + 35% RS12-1 + 15% RS3M + 15% breadth score. No complete momentum score without breadth.

**Caveat:** Relative rank is a cross-section comparison, not a standardized historical z-score or an objective expected return. Three-month momentum includes the current month, while 6-1 and 12-1 omit the latest month intentionally. This is a testable candidate design, not a proven optimum.

## Earnings revisions E (0-100)

PIT, calendarized, denominator-controlled EPS revision inputs should be built upstream from historical analyst estimates. This scaffold accepts raw sector revision features and percentile-ranks them across the 11 sectors. E = 45% 3M + 30% 1M + 25% upward revision breadth. If the underlying forward EPS is negative or approaches zero, the upstream layer must use a guarded revision representation, not an unstable division. None of the included demo estimates are market data.

## Fundamental quality Q (0-100)

Expects already-normalized (0-100) profitability, financial strength and growth-durability metrics. Q = 40% profitability + 35% financial strength + 25% growth durability. Sector-specific accounting/normalization remains a roadmap item. In particular, banks/insurance need regulatory capital, ROE/ROTCE and reserve checks, and REITs need AFFO and refinancing checks.

## Composite and gating

```
O = 0.40V + 0.25M + 0.20E + 0.15Q
R = 0.55M + 0.45E
```

These scores remain `NaN` whenever a required pillar is missing. Classifications:

- `V>=65 & R>=60`: VALUATION_SUPPORTED_ROTATION
- `V>=65 & R<=40`: CHEAP_BUT_WEAK
- `V<=35 & R>=60`: EXPENSIVE_LEADERSHIP
- `V<=35 & R<=40`: EXPENSIVE_AND_WEAK
- otherwise: NEUTRAL_OR_MIXED

A complete input set is marked `INPUTS_COMPLETE_UNVALIDATED`: completeness must never be confused with measured validity or forecast performance.

## Planned validation protocol

1. Freeze the methodology and tested feature definitions before evaluating final out-of-sample performance.
2. Use security and sector constituents genuinely known on each historical date; honor provider timestamps, corporate actions and publication lag.
3. Walk-forward over chronological, nonoverlapping train/test splits. For overlapping 3M/6M/12M labels, use purged/embargoed evaluation or block-bootstrap inference.
4. Evaluate monotonic score deciles/ranks where feasible (11 sectors limit cross-sectional granularity), rank IC, 3M/6M/12M excess returns vs SPY, information ratio, drawdown, turnover and transaction costs.
5. Compare price-only, valuation-only, revision-only and composite ablations; test fixed weights vs data-driven weights under out-of-sample constraints.
6. Avoid optimized thresholds chosen solely because they maximize historical CAGR. Report sample-size and regime dependence and publish null or negative findings.

## Required technical improvements before a research claim

- Sector/subindustry-relevant PIT aggregation of valuation and quality.
- PIT dataset of analyst consensus changes, not revised modern snapshots.
- Fully specified data confidence tiers and reproducible raw-data hashes.
- Real-data ingestion with explicit license/compliance checks.
- Benchmark-relative valuation adjustment with suitable peer comparison.
- Calendar alignment across equity, estimates, cash flows and macro regimes.
