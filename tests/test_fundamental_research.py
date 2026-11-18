import csv
import io
import itertools

import pandas as pd
import pytest

from sectorlab.config import QUALITY_WEIGHTS, REVISION_WEIGHTS, SECTORS
from sectorlab.fundamental_research.data import (
    CSV_COLUMNS,
    ResearchDataError,
    parse_research_csv,
)
from sectorlab.fundamental_research.demo import DEMO_ASOF, demo_csv
from sectorlab.fundamental_research.earnings import (
    calendarized_eps,
    earnings_research,
    guarded_revision,
)
from sectorlab.fundamental_research.quality import quality_research

CUTOFF = "2025-12-31T23:59:59Z"


@pytest.fixture
def rows():
    return list(csv.DictReader(io.StringIO(demo_csv().decode())))


def load(rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return parse_research_csv(buffer.getvalue().encode(), import_cutoff=CUTOFF)


def select(rows, kind, issuer="FICTIONAL_XLK"):
    return [row for row in rows if row["kind"] == kind and row["issuer_id"] == issuer]


def test_fixed_window_calendarization():
    start, end = pd.Timestamp("2024-02-15"), pd.Timestamp("2025-02-15")
    boundaries = pd.to_datetime(
        ["2023-12-01", "2024-03-01", "2024-06-01", "2024-09-01", "2024-12-01", "2025-03-01"]
    )
    quarters = dict(
        zip(
            itertools.pairwise(boundaries),
            (1.0, 2.0, 3.0, 4.0, 5.0),
            strict=True,
        )
    )
    assert calendarized_eps(quarters, start, end) == pytest.approx(15 / 91 + 9 + 5 * 76 / 90)
    assert calendarized_eps(dict(list(quarters.items())[1:]), start, end) is None
    quarters[(pd.Timestamp("2024-06-02"), pd.Timestamp("2024-09-02"))] = 3
    assert calendarized_eps(quarters, start, end) is None


@pytest.mark.parametrize(
    ("now", "then", "expected"),
    [
        (0, 0, 0),
        (0.001, 0, 0.004),
        (-1, -2, 0.5),
        (-2, -1, -0.5),
        (1, -1, 2),
        (-1, 1, -2),
        (1e308, -1e308, 2),
    ],
)
def test_guarded_revision(now, then, expected):
    assert guarded_revision(now, then) == pytest.approx(expected)


def test_raw_revisions_breadth_and_score_weights(rows):
    dataset = load(rows)
    result = earnings_research(dataset, asof=DEMO_ASOF)
    sector = result.sectors.loc["XLK"]
    latest_start = pd.Timestamp("2024-11-01")
    quarter_starts = pd.date_range("2024-10-01", periods=6, freq="QS")
    weights = [
        (min(right, latest_start + pd.DateOffset(years=1)) - max(left, latest_start)).days
        / (right - left).days
        for left, right in itertools.pairwise(quarter_starts)
    ]
    old_eps = sum(weight * (1.23 + j * 0.10) for j, weight in enumerate(weights))
    change_1m = -0.06 * sum(weights)
    assert sector["revision_1m"] == pytest.approx(change_1m / (old_eps + change_1m))
    assert sector["revision_3m"] == pytest.approx(2 * change_1m / old_eps)
    assert sector["revision_breadth"] == -1
    assert sector["earnings_revision_score"] == pytest.approx(
        sum(sector[f"{key}_score"] * weight for key, weight in REVISION_WEIGHTS.items())
    )
    assert (
        result.issuers.set_index("issuer_id").loc["FICTIONAL_XLK", "revision_1m_matched_analysts"]
        == 4
    )


def test_historical_weights_and_exact_coverage_gate(rows):
    xlk = select(rows, "holding")[0]
    xlk["value"] = ".60"
    rows += [
        xlk | {"issuer_id": "SECOND", "value": ".20"},
        xlk | {"issuer_id": "UNSCORED", "value": ".14", "subindustry": "unmapped"},
    ]
    expected = {}
    for item in select(rows, "quality"):
        rows.append(item | {"issuer_id": "SECOND", "value": "0"})
    result = quality_research(load(rows), asof=DEMO_ASOF)
    sector = result.sectors.loc["XLK"]
    assert sector["joint_covered_weight"] == pytest.approx(0.80)
    detail = result.issuers.query("ticker == 'XLK'").set_index("issuer_id")
    for pillar in QUALITY_WEIGHTS:
        expected[pillar] = (
            0.60 * detail.loc["FICTIONAL_XLK", pillar] + 0.20 * detail.loc["SECOND", pillar]
        ) / 0.80
        assert sector[pillar] == pytest.approx(expected[pillar])
    assert sector["quality_score"] == pytest.approx(
        sum(expected[k] * weight for k, weight in QUALITY_WEIGHTS.items())
    )

    newer = [
        row
        | {
            "observation_date": "2024-10-31",
            "observed_at": "2024-10-31T18:00:00Z",
            "published_at": "2024-10-31T23:00:00Z",
            "available_at": "2024-11-01T00:00:00Z",
        }
        for row in rows
        if row["kind"] == "holding" and row["ticker"] == "XLK"
    ]
    next(row for row in newer if row["issuer_id"] == "FICTIONAL_XLK")["value"] = ".59"
    next(row for row in newer if row["issuer_id"] == "UNSCORED")["value"] = ".15"
    dataset = load(rows + newer)
    assert quality_research(dataset, asof=DEMO_ASOF).sectors.loc["XLK", "status"] == "complete"
    later = quality_research(dataset, asof="2024-11-01T00:00:00Z").sectors.loc["XLK"]
    assert later["joint_covered_weight"] == pytest.approx(0.79)
    assert later["status"] == "insufficient_joint_weight"
    assert pd.isna(later["quality_score"])


def test_late_estimate_and_rating_withdrawals_are_point_in_time(rows):
    new = []
    for kind in ("eps", "recommendation"):
        for item in select(rows, kind):
            if item["observation_date"] == "2024-10-30" and item["analyst_id"] in {"A0", "A1"}:
                new.append(
                    item
                    | {
                        "status": "withdrawn",
                        "value": "",
                        "published_at": "2024-10-31T23:00:00Z",
                        "available_at": "2024-11-01T00:00:00Z",
                    }
                )
    bundle = load(rows + new)
    assert earnings_research(bundle, asof=DEMO_ASOF).sectors.loc["XLK", "status"] == "complete"
    later = earnings_research(bundle, asof="2024-11-01T00:00:00Z")
    sector = later.sectors.loc["XLK"]
    assert sector["status"] == "insufficient_joint_weight"
    assert sector["revision_3m_covered_weight"] == 0
    assert sector["revision_breadth_covered_weight"] == 0
    assert pd.isna(sector["earnings_revision_score"])


def test_incompatible_eps_basis_and_no_unmatched_analyst_substitution(rows):
    for row in select(rows, "eps"):
        if row["observation_date"] == "2024-09-27":
            if row["analyst_id"] in {"A0", "A1"}:
                row["basis"] = "USD/share:other-split-basis"
            else:
                row["analyst_id"] += "_NEW"
            # Add fresh records that override the prior observations for departed IDs.
            if row["analyst_id"].endswith("_NEW"):
                rows.append(
                    row
                    | {
                        "analyst_id": row["analyst_id"].removesuffix("_NEW"),
                        "status": "withdrawn",
                        "value": "",
                    }
                )
    result = earnings_research(load(rows), asof=DEMO_ASOF)
    assert result.sectors.loc["XLK", "revision_1m_status"] == "insufficient_weight"
    assert result.sectors.loc["XLK", "revision_3m_status"] == "available"


def test_missing_and_stale_holdings_and_quality(rows):
    dataset = load(rows)
    for fn in (earnings_research, quality_research):
        for instant in ("2024-10-30T18:59:59Z", "2025-02-01T20:00:00Z"):
            result = fn(dataset, asof=instant)
            assert result.sectors["status"].eq("missing_or_stale_holdings").all()
    for row in select(rows, "quality"):
        row.update(period_start="2023-01-01", period_end="2024-01-01")
    result = quality_research(load(rows), asof=DEMO_ASOF)
    assert result.sectors.loc["XLK", "profitability_status"] == "insufficient_weight"
    assert (
        "stale"
        in result.issuers.set_index("issuer_id").loc["FICTIONAL_XLK", "profitability_status"]
    )


def test_quality_latest_anchor_missing_and_sector_specific_dimensions(rows):
    for row in select(rows, "quality"):
        if row["metric_definition"] == "roic_v1":
            row.update(period_start="2024-01-01", period_end="2024-10-01")
            # Invalid period length is rejected even on a quality observation with metadata.
            with pytest.raises(ResearchDataError, match="trailing-year fiscal anchor"):
                load(rows)
            row.update(period_start="2023-07-01", period_end="2024-07-01")
    result = quality_research(load(rows), asof=DEMO_ASOF)
    issuers = result.issuers.set_index("issuer_id")
    assert "roic:missing_at_anchor" in issuers.loc["FICTIONAL_XLK", "profitability_status"]
    assert pd.isna(result.sectors.loc["XLK", "quality_score"])
    assert pd.notna(issuers.loc["FICTIONAL_XLF", "ratio_rotce"])
    assert pd.notna(issuers.loc["FICTIONAL_XLRE", "ratio_unfinanced_maturities"])
    assert pd.notna(issuers.loc["FICTIONAL_XLU", "ratio_regulated_earnings_share"])


@pytest.mark.parametrize(
    ("kind", "change", "error"),
    [
        ("holding", {"value": ".8"}, "sum to full ETF NAV"),
        ("holding", {"vendor": "OTHER"}, "share source and vintage"),
        ("eps", {"metric_definition": "annual_eps"}, "Unsupported metric definition"),
        ("eps", {"available_at": "2026-01-01T00:00:00Z"}, "import cutoff"),
        ("eps", {"available_at": "2024-07-29T16:00:00Z"}, "publication <= availability"),
        ("eps", {"observed_at": "2024-07-29T17:00:00"}, "UTC offset"),
        ("recommendation", {"value": "3.1"}, "integer from 1"),
        ("quality", {"denominator": "-7"}, "Invalid quality ratio"),
        ("quality", {"status": "invalid"}, "require empty"),
        ("quality", {"value": "nan"}, "finite numeric"),
        (
            "quality",
            {"history_type": "point_in_time", "data_license": "private"},
            "separate bundles",
        ),
    ],
)
def test_rejects_invalid_contract_rows(rows, kind, change, error):
    select(rows, kind)[0].update(change)
    with pytest.raises(ResearchDataError, match=error):
        load(rows)


def test_quality_bounded_ratios_and_duplicate_vintages(rows):
    bound = next(
        item for item in select(rows, "quality") if "retention" in item["metric_definition"]
    )
    with pytest.raises(ResearchDataError, match="Invalid quality ratio"):
        load([item | {"value": "1.01"} if item is bound else item for item in rows])
    with pytest.raises(ResearchDataError, match="Duplicate observation"):
        load(rows + [bound])


def test_reproducibility_and_engine_independence(rows):
    data = load(rows)
    reversed_data = load(list(reversed(rows)))
    assert data.sha256 != reversed_data.sha256
    assert demo_csv() == demo_csv()
    for runner, unrelated in (
        (earnings_research, {"quality"}),
        (quality_research, {"eps", "recommendation"}),
    ):
        baseline = runner(data, asof=DEMO_ASOF)
        assert list(baseline.sectors.index) == list(SECTORS)
        assert baseline.sectors["status"].eq("complete").all()
        assert "opportunity" not in baseline.sectors
        assert baseline.source_coverage["confidence"].eq("synthetic").all()
        for candidate in (
            reversed_data,
            load([row for row in rows if row["kind"] not in unrelated]),
        ):
            pd.testing.assert_frame_equal(
                baseline.sectors,
                runner(candidate, asof=DEMO_ASOF).sectors,
            )


def test_real_provenance_remains_declared_and_single_eligible_sector_has_no_rank(rows):
    rows = [
        row | {"history_type": "point_in_time", "data_license": "private", "vendor": "TEST"}
        for row in rows
        if row["ticker"] in {"", "XLK"}
    ]
    result = earnings_research(load(rows), asof=DEMO_ASOF)
    assert result.source_coverage.query("ticker == 'XLK'")["confidence"].eq("declared_pit").all()
    assert result.sectors.loc["XLK", "revision_3m_status"] == "available"
    assert result.sectors.loc["XLK", "status"] == "insufficient_sector_peers"
    assert pd.isna(result.sectors.loc["XLK", "earnings_revision_score"])


def test_joint_gate_holds_score_even_when_each_pillar_has_enough_weight(rows):
    holder = select(rows, "holding")[0]
    holder["value"] = ".70"
    for issuer, missing in (("NO_ROIC", "roic_v1"), ("NO_MARGIN", "margin_retention_v1")):
        rows.append(holder | {"issuer_id": issuer, "value": ".12"})
        rows.extend(
            item | {"issuer_id": issuer}
            for item in select(rows, "quality")
            if item["metric_definition"] != missing
        )
    sector = quality_research(load(rows), asof=DEMO_ASOF).sectors.loc["XLK"]
    assert all(sector[f"{pillar}_status"] == "available" for pillar in QUALITY_WEIGHTS)
    assert sector["joint_covered_weight"] == pytest.approx(0.70)
    assert sector["status"] == "insufficient_joint_weight"
    assert pd.isna(sector["quality_score"])


def test_quality_requires_full_and_integer_durability_window(rows):
    item = next(row for row in rows if row["metric_definition"] == "midcycle_fcf_years_v1")
    for change in ({"denominator": "4"}, {"value": "2.5"}):
        with pytest.raises(ResearchDataError, match="Invalid year count"):
            load([row | change if row is item else row for row in rows])


def test_stale_estimates_do_not_score_with_fresh_holdings(rows):
    for row in select(rows, "eps"):
        if row["observation_date"] >= "2024-09-27":
            stale = "2024-06-30" if row["observation_date"] == "2024-09-27" else "2024-07-01"
            row.update(
                observation_date=stale,
                observed_at=f"{stale}T17:00:00Z",
                published_at=f"{stale}T18:00:00Z",
                available_at=f"{stale}T19:00:00Z",
            )
        else:
            row.update(
                observation_date="2024-07-02",
                observed_at="2024-07-02T17:00:00Z",
                published_at="2024-07-02T18:00:00Z",
                available_at="2024-07-02T19:00:00Z",
            )
    result = earnings_research(load(rows), asof=DEMO_ASOF)
    sector = result.sectors.loc["XLK"]
    assert sector["revision_1m_covered_weight"] == 0
    assert sector["revision_3m_covered_weight"] == 0
    assert sector["revision_breadth_covered_weight"] == pytest.approx(0.94)


def test_holdings_corrections_cannot_relabel_original_observation_time(rows):
    corrected = []
    for row in (item for item in rows if item["kind"] == "holding" and item["ticker"] == "XLK"):
        corrected.append(
            row
            | {
                "observed_at": "2024-10-30T20:00:00Z",
                "published_at": "2024-10-31T19:00:00Z",
                "available_at": "2024-10-31T20:00:00Z",
            }
        )
    with pytest.raises(ResearchDataError, match="preserve the original observation instant"):
        load(rows + corrected)
