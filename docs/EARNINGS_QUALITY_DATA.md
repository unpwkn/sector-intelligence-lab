# Earnings and quality CSV contract, version 1

A bundle is one UTF-8 CSV containing holdings and any combination of analyst EPS,
analyst recommendations and quality operands. Holdings alone produce coverage
diagnostics. An earnings-only or quality-only bundle is valid. Place private files
in ignored `data/earnings_quality/` and open **Earnings and Quality** in Streamlit.
Use a trusted local deployment for private files. Check rights for vendor extracts
and for publishing derived outputs. See the [source review](PHASE4_FEASIBILITY.md).

## Columns

The header must contain these exact column names in any order. Use empty fields
where a field does not apply.

| Fields | Contract |
| --- | --- |
| `kind` | `holding`, `eps`, `recommendation` or `quality`. |
| `issuer_id` | Stable issuer ID, shared across kinds. For holdings, sum listed share classes belonging to the same issuer and preserve the mapping in the audit evidence. Reserve `RESIDUAL` for cash and other unscored NAV. |
| `ticker`, `subindustry` | Holdings only. One supported sector ETF ticker and a classification from the [quality registry](../src/sectorlab/fundamental_research/quality_definitions.py) valid on the holding date. Use `unmapped` when evidence or an appropriate model is missing, and `residual` for `RESIDUAL`. |
| `analyst_id` | EPS and ratings only. Stable vendor contributor ID for the same analyst at both endpoints. Record mergers, identity changes, duplicate contributors and coverage withdrawals in the audit evidence. |
| `observation_date`, `observed_at` | Calendar date described by the observation in New York and original observation instant. For a restatement of a specific original observation, keep its observation date/instant and supply the restatement's own publication and availability. An estimate update for a new date is a new observation. |
| `published_at`, `available_at` | Original publication instant of this vintage and first instant available to the intended user/channel, including any vendor delay. For a derived observation use the latest of all required operands' release/availability and when the derivation itself was available. Require `observed_at <= published_at <= available_at <= import_cutoff`. All timestamps require seconds and an explicit timezone offset or `Z`. |
| `period_start`, `period_end` | Inclusive start and **exclusive** end, `YYYY-MM-DD`. EPS uses actual fiscal quarter boundaries, 77–105 days, and an observation before quarter end. Quality uses a completed trailing fiscal year anchor, 350–380 days, ending on or before the observation date. Both fields are empty for holdings and ratings. A source using inclusive period end must convert it explicitly. |
| `metric_definition`, `basis` | Versioned canonical definition and comparable data basis from the table below. |
| `value`, `denominator`, `status` | Finite numeric inputs for `valid` rows. Quality supplies numerator in `value` and a strictly positive `denominator`, using matched units and registered ratio bounds. Other kinds leave the denominator empty. Mark unusable records `invalid` or `withdrawn` with both numeric fields empty so the engine sees the explicit supersession. Holdings require `valid`. |
| `vendor`, `source`, `provenance`, `audit_ref` | Vendor name, source URI or original accession/feed identifier, original record/vintage identifier or derivation lineage, and a reference to audit evidence. The evidence should include native timestamps, item mappings and versions, all source accession or broker IDs and financial operands, classification and adjustments, and license review. This metadata is required and is an importer attestation. |
| `history_type`, `data_license` | `point_in_time` with `private` or `public_redistributable`; `synthetic` with `synthetic`. Use one history type per file. Reconstructed or backfilled histories with unproven timestamps are ineligible. |

| Kind | Definition | Basis and input |
| --- | --- | --- |
| Holdings | `etf_nav_weight_v1` | `ETF_NAV`. Fraction of the actual ETF's full NAV, with positive weights summing to 1 within one part per million. Supply an atomic snapshot for each ETF, observation date and availability timestamp. All rows share source, vendor, timestamp and audit metadata. Corrections supply a full replacement snapshot, including residual. Leveraged or net-short snapshots are outside this contract. |
| EPS | `quarterly_diluted_adjusted_eps_v1` | `USD/share:<corporate-action-basis-id>`. Analyst estimate of diluted adjusted EPS for the exact fiscal quarter, in nominal USD. Use one vendor and audit comparable adjustment policy. Two vintages must share the same point-in-time per-share basis ID. Record basis changes only when known and preserve the originals. |
| Recommendation | `analyst_rating_1_to_5_v1` | `ordinal`. Stable, documented vendor mapping of strong sell (1), sell (2), hold (3), buy (4) and strong buy (5). Keep one vendor. Coverage initiations, withdrawals and unauditable scale changes are not directional votes. |
| Quality | `<metric>_v1` | `ratio`. See the registry for numerator, denominator, economic bounds, pillar, and scoring anchors. Keep one vendor per issuer/metric series. Document any issuer-specific adjustments and the matching periods of every operand. |

Duplicate vintages are rejected. The key is kind, ticker, issuer, analyst, metric,
fiscal interval, original observation instant and availability. Historical queries exclude
later vintages before resolving amendments. Import validation checks shape, timestamp
ordering and formula identifiers. It cannot establish historical entitlement,
prove that a timestamp reflects first visibility, or validate the contents of an
external audit reference. Source confidence reports **declared PIT** or **synthetic**.

## Offline use

Run `streamlit run app.py`, open **Earnings and Quality**, and select **Synthetic fixture**.
The fixture is deterministic, covers all 11 ETFs and contains fictional issuer records.

```python
from sectorlab.fundamental_research.data import load_research_csv
from sectorlab.fundamental_research.earnings import earnings_research
from sectorlab.fundamental_research.quality import quality_research

bundle = load_research_csv("data/earnings_quality/private.csv")
asof = "2024-10-31T22:00:00Z"
earnings = earnings_research(bundle, asof=asof)
quality = quality_research(bundle, asof=asof)
print(earnings.sectors, quality.sectors)
```

Both APIs return sector and issuer diagnostics, source coverage, the query cutoff,
history type and input SHA-256. Source coverage lists visible candidate sources
alongside distinct usable component weights. Record the cutoff and code revision
with the bundle hash when reproducing a result.
