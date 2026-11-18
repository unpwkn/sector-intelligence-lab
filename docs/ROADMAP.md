# Project roadmap

## Phase 0: public reproducibility scaffold (v0.1, delivered)

- [x] Eleven-sector universe and proxy metric definitions.
- [x] Past-only robust valuation z-score and incompleteness gates.
- [x] Relative momentum, breadth, revision rank and quality score logic.
- [x] Synthetic data generator, tests, CLI and Streamlit demonstration.
- [x] Source policy / methodology documentation.

## Phase 1 — ETF market data

- [x] Price provider adapter with date alignment, dividends and corporate actions.
- [ ] Daily automated fetch into immutable raw snapshots (no public redistribution restrictions violated).
- [ ] Research-grade market breadth from historical available constituent snapshots.
- [ ] Current ETF characteristics on dashboard clearly labeled as *not* backtestable PIT history.
- [ ] Screenshot and public GitHub README demo image.
- [ ] Publish practical research blog describing why cheapness differs from rotation.

## Phase 2 — Sector price momentum

- [x] Snapshot-backed price momentum and relative strength versus SPY.
- [x] Independent Momentum Research dashboard and coverage diagnostics.

## Phase 3 — Sector valuation and historical normalization

- [x] Valuation CSV contract with data vintages and provenance.
- [x] Historical sector valuation, comparable SPY premiums and spreads, and offline research tab.
- [x] [Source feasibility and licensing review](DATA_FEASIBILITY.md).
- [ ] Audit real exports and test constituent coverage, denominator construction and paired SPY data.

## Phase 4 — Earnings Revision and Fundamental Quality

- [x] [Historical source feasibility, timestamp evidence and licensing review](PHASE4_FEASIBILITY.md).
- [x] Independent vintage-aware revision and sector-specific quality research.
- [x] [Validated CSV contract](EARNINGS_QUALITY_DATA.md), deterministic fixtures and coverage views.
- [ ] Obtain and audit entitled detail-level EPS, recommendation and ETF holdings vintages.
- [ ] Audit as-filed financial operands and refine models for uncovered subindustries.
- [ ] Measure issuer and portfolio coverage on sourced vintages before empirical testing.

## Phase 5 — Sector ETF rotation

- [x] [Snapshot readiness gate and retrospective comparison](BACKTESTING.md) with the Phase 2 score.
- [x] Common and dynamic universe runs, next-session execution, costs and aligned metrics.
- [x] Offline validation fixture and trade-level audit exports.
- [ ] Audit licensed data and reconcile corporate actions and benchmark returns.
- [ ] Obtain historical price vintages and verify historical availability for PIT claims.

## Future institutional data research

- [ ] PIT holdings and analyst EPS estimate histories.
- [ ] Sector / subindustry financial denominator normalization.
- [ ] Production data lineage, correction timestamps and coverage dashboard.
- [ ] Quarterly revalidation of source definitions and sector mappings.

## Future empirical testing

- [ ] Pre-register hypotheses and compare simple baselines.
- [ ] 3/6/12M forward excess-return tests; rank IC and confidence intervals.
- [ ] Chronological walk-forward, no peeking and embargo for overlapping labels.
- [ ] Robustness to momentum horizons, valuation windows and regime shifts.
- [ ] Publish both positive and negative/insignificant findings.

## Public project milestones worth showing on a resume

1. Code quality: documented architecture, passing tests and reproducible commands.
2. Data quality: genuine vintage snapshots, source citations and leakage test coverage.
3. Research: clearly defined economic hypothesis, incremental factor contributions and null tests.
4. Presentation: hosted dashboard, annotated charts and a reproducibility/read-me-first story.
