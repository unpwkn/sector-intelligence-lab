# Methodology v0.1 (research hypothesis, not a validated signal)

## Scope and hypotheses

Universe: 11 Sector SPDR proxies; comparator SPY. Monthly decisions are the intended research unit, with execution no earlier than the next tradable session after data becomes available. An *as-of* snapshot includes only items whose `available_at` is no later than the timestamp of the signal. The full scoring pipeline calculates one end-of-month snapshot from synthetic data. Phase 2 also calculates retrospective price-only diagnostics from a single retrieved vendor snapshot.

## Sector Valuation Research (Phase 3)

The dedicated engine consumes already aggregated, versioned ETF observations under the
[CSV contract](VALUATION_DATA.md). The Phase 1 and 2 price workflows and the legacy synthetic
opportunity pipeline keep their existing inputs and outputs.

| Sector | Metric weights and preferred direction | ETF aggregation limits |
|---|---|---|
| XLK Technology | Forward P/E 50% ↓, FCF Yield 30% ↑, EV/EBIT 20% ↓ | Stock-based compensation, capitalized costs and concentrated weights affect cash flow and EBIT. |
| XLC Communication Services | Forward P/E 50% ↓, FCF Yield 30% ↑, EV/EBITDA 20% ↓ | Advertising platforms, content producers and telecom differ in depreciation, leases and capital intensity. |
| XLY Consumer Discretionary | Forward P/E 50% ↓, FCF Yield 30% ↑, EV/EBITDA 20% ↓ | Retail leases, auto captive finance and platform cash-flow profiles require consistent adjustments. |
| XLP Consumer Staples | Forward P/E 50% ↓, FCF Yield 30% ↑, Dividend Yield 20% ↑ | Working-capital timing and payout policies influence yields. |
| XLE Energy | Mid-cycle FCF Yield 50% ↑, EV/EBITDA 30% ↓, EV/DACF 20% ↓ | Upstream, refining and services have different cycles. Use commodity, utilization and capex assumptions known at the time and define debt-adjusted cash flow consistently. |
| XLF Financials | P/TBV 50% ↓, Forward P/E 30% ↓, Dividend Yield 20% ↑ | Tangible common book is most useful for banks and depends on intangibles, marks and equity treatment. Insurers, brokers and fee businesses may need separate aggregation or coverage flags. |
| XLV Health Care | Forward P/E 50% ↓, EV/EBITDA 30% ↓, FCF Yield 20% ↑ | Loss-making biotech and R&D accounting can shift which constituents enter a multiple. |
| XLI Industrials | Mid-cycle EV/EBITDA 50% ↓, Forward P/E 30% ↓, FCF Yield 20% ↑ | Cycle, lease and balance-sheet adjustments need a stable recipe across diverse subindustries. |
| XLB Materials | Mid-cycle EV/EBITDA 50% ↓, Mid-cycle FCF Yield 30% ↑, Forward P/E 20% ↓ | Margins and reinvestment vary by commodity, capacity and subindustry. Fix contemporaneous mid-cycle assumptions. |
| XLRE Real Estate | P/AFFO 50% ↓, Implied Cap Rate 30% ↑, Dividend Yield 20% ↑ | REIT AFFO varies in recurring capex and lease adjustments. Cap rates require comparable property NOI and enterprise or property values. Financing and property mix matter. |
| XLU Utilities | Forward P/E 50% ↓, Dividend Yield 30% ↑, EV/EBITDA 20% ↓ | Regulated and merchant power have different leverage, capex, depreciation and payout capacity. |

ETF ratios can differ materially across weighted harmonic, ratio-of-sums and other aggregation
methods. Concentration, loss exclusions, missing estimates and changes in historical holdings can
drive the result. Preserve dated constituents and weights upstream and document numerator,
denominator, constituent coverage and exclusions. Mid-cycle inputs require a specified rule and
contemporaneous assumptions. The engine performs no constituent aggregation, denominator
reconstruction or metric substitution.

### Reference window and cutoff

At each timezone-aware cutoff, first filter to vintages with `available_at <= cutoff`. Timestamp
order is checked at import. Within each New York calendar month, take the latest visible observation
date and, within that date, the latest available vintage. The latest available month supplies the
current value. If it is over 45 New York calendar days old or invalid, its signal is missing.
Earlier observations in the same month cannot replace an invalid latest print.

For current observation month M, the reference is M−84 through M−1 inclusive. Count at most one
valid observation per month, excluding all of M. Keep only the uninterrupted definition regime
matching the selected current observation's vendor, source, fiscal basis and recipe. A change
resets eligibility for earlier observations, including when a previous definition returns.
Sparse months stay missing. Require 48 valid months for each normalized component.

Historical charts replay each past New York calendar month end, then evaluate the exact final
cutoff. A newly released correction can enter decisions at or after its own availability and
cannot rewrite an earlier decision. A latest observation may be reused across cutoffs while fresh.
Reconstructed exports can show original observation dates as raw history once the modern file is
available. They are excluded from point-in-time scoring.

### Robust Z, attractiveness and percentile

```text
median = median(valid prior values)
MAD = median(abs(prior values - median))
Z = (current - median) / (1.4826 × MAD)
attractiveness_Z = clip(direction × Z, -3, 3)
score = 50 + (50 / 3) × attractiveness_Z
percentile = 100 × (count strictly less attractive + 0.5 × count tied) / valid prior months
```

Direction is +1 for higher-is-cheaper yields and −1 for lower-is-cheaper multiples. The multiplier
scales MAD to match the standard deviation under a normal reference distribution. The raw Z is
reported independently of the clipped score. The percentile uses empirical midranks in the
attractiveness direction and makes no normality assumption. MAD at or below `1e-12` in submitted
units, invalid arithmetic, missing or invalid denominators and inadequate history produce missing
scores. All multiples require positive values and positive denominators, including positive
forward earnings for P/E. Dividend yield may be zero. Signed FCF yield and implied cap rate
can be measured with valid positive denominators. Upstream coverage and loss rules still apply.
A score of 50 corresponds to the historical median. Statistical significance, market mispricing
and return predictability require separate empirical tests.

### Relative to SPY and composite

Forward P/E and dividend yield are eligible only with identical vendor, product, fiscal basis and
metric-definition identifiers, the same date and observation instant, and valid inputs available
at the cutoff. Historical pairs use the intersection of the two comparable monthly histories.
For forward P/E use `sector / SPY - 1` (fractional premium, lower is more attractive). For
dividend yield use `sector - SPY` (decimal yield spread, higher is more attractive). Normalize
each relative series against its own historical matched pairs with the same rolling rules.
Sector-specific, capital-structure-sensitive and mid-cycle metrics are outside the benchmark
comparison set. XLE has no eligible relative metric under this policy.

The historical sector score requires all three configured metric scores and a common observation
month. The relative sector score requires every predesignated comparable metric for the sector and
a common observation month. It uses the original metric weights normalized over that fixed eligible
set. Coverage gaps never change the required set. If both components exist, the blend is
`0.70 × historical + 0.30 × relative`. Otherwise the blend is missing and any complete component
is reported separately. The weights are research hypotheses. Published metrics with identical
labels still require a source methodology review before relative use.

### Legacy synthetic valuation V

The illustrative full-factor pipeline accepts its original simple synthetic CSV. It keeps the
84-prior-row window, 48 observation minimum, 45-day freshness convention and original
`V = sum(weight × (50 + 15 × attractiveness_Z))` scoring scale (5–95). The research tab uses the
calendar-window and provenance-aware engine above. Both reuse the robust transformation.

## Momentum M (0-100)

For daily adjusted-price *proxies* (production must validate total return), compute:

```
RS_6_1 = ln(P_sector[t-21]/P_sector[t-126]) - ln(P_SPY[t-21]/P_SPY[t-126])
RS_12_1 = ln(P_sector[t-21]/P_sector[t-252]) - ln(P_SPY[t-21]/P_SPY[t-252])
RS_3M = ln(P_sector[t]/P_sector[t-63]) - ln(P_SPY[t]/P_SPY[t-63])
```

Code uses -22, -127, -253 and -1, -64 zero-based position indices, equivalent to the above when t is the last row. Each value is ranked within the 11-sector universe to a 0-100 cross-sectional percentile. `breadth` is a separately supplied, point-in-time constituent-level value between 0 and 1. M = 35% RS6-1 + 35% RS12-1 + 15% RS3M + 15% breadth score. No complete momentum score without breadth.

**Caveat:** Relative rank is a cross-section comparison, not a standardized historical z-score or an objective expected return. Three-month momentum includes the current month, while 6-1 and 12-1 omit the latest month intentionally. This is a testable candidate design, not a proven optimum.

## Price Momentum Score (Phase 2)

The price-only engine uses the snapshot's USD `adjusted_price_proxy` for
each ETF and SPY. For a signal session `t` the components are:

```
RS_6_1  = ln(P_sector[t-21] / P_sector[t-126]) - ln(P_SPY[t-21] / P_SPY[t-126])
RS_12_1 = ln(P_sector[t-21] / P_sector[t-252]) - ln(P_SPY[t-21] / P_SPY[t-252])
RS_3M   = ln(P_sector[t] / P_sector[t-63])     - ln(P_SPY[t] / P_SPY[t-63])
```

**Calendar and signal date.** The index is the complete expected XNYS session
grid within the snapshot's inclusive requested bounds, shared by all 12
instruments. `t` is the session labeled by `signal_date`, an exchange-local
YYYY-MM-DD date measured using that session's closing observation for 3M.
Offsets count exchange sessions backwards with `t-0 = t`. Holidays and weekends
have no slots. Zero-price-coverage sessions keep their slots. The two skip-month
measures end at `t-21` and span 105 and 231 session intervals, respectively.
The 3M measure spans 63 session intervals. With an inclusive `asof` cutoff,
the latest row is the last snapshot session on or before that date, even when
prices are missing. A cutoff before the snapshot yields no rows.

**Component coverage.** Both ETFs must have a valid observation on every
session of the component's inclusive interval. Missing endpoints and interior
gaps both yield `missing_observations`. If the start endpoint falls before the
snapshot or the sector's first ETF session, the component is marked
`insufficient_history`. The earliest 3M, 6–1 and 12–1 components need 64,
127 and 253 session slots, respectively, counting the signal session. The
engine retains the 11 sector rows and reports valid shorter windows even
when the composite is unavailable. Prices are never filled.

**Cross-sectional score.** At each date let `N` count the sectors with all
three components. The same `N` eligible peers enter each component rank.
SPY is used only as the benchmark. With ascending ranks and average ranks
for ties, convert each to a 0–100 percentile and combine:

```
percentile(component) = 100 * (rank(component) - 1) / (N - 1)
Price Momentum Score = 0.40 * percentile(RS_6_1)
                     + 0.40 * percentile(RS_12_1)
                     + 0.20 * percentile(RS_3M)
```

An ineligible sector or a cross-section with fewer than two eligible sectors
receives `insufficient_data` and a missing composite score and rank. Final
ranks descend by score after rounding to 12 decimal places, with the same best
rank assigned to ties. Because
eligible peers can change with inception and coverage, a sector's score can
move when the peer set changes. Weights are research hypotheses. The full
Momentum M above still uses breadth and its existing weighting.

**Relative performance.** The dashboard expresses the three log components in
log percentage points. Its 3M performance column uses
`100 * (exp(RS_3M) - 1)`, the percentage change in the sector-to-SPY adjusted
price ratio. This is the ratio of their price growth factors minus one.

**Historical scope.** Session labels later than `t` cannot affect that
session's signal or cross-sectional ranks. All observations still come from
the saved retrieval vintage. Later vendor corrections or adjustments can be
embedded in past prices. The retrieved data do not establish point-in-time
availability, verified total returns, executable signals, or investment
performance. See [Market Data](MARKET_DATA.md) for provenance and reproduction.

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
