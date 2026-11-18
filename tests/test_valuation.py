import csv
import hashlib
import io

import numpy as np
import pandas as pd
import pytest

from sectorlab.config import VALUATION_METRICS
from sectorlab.valuation import (
    evaluate_metric,
    evaluate_sector,
    metric_history,
    normalize_valuation,
)
from sectorlab.valuation_data import CSV_COLUMNS, ValuationDataError, parse_valuation_csv
from sectorlab.valuation_demo import DEMO_ASOF, synthetic_valuation_csv

CUTOFF = "2025-02-03T00:00:00Z"
CURRENT = "2025-01-31"


def row(date=CURRENT, value=20, **overrides):
    return {
        "ticker": "XLK", "metric": "forward_pe", "observation_date": date,
        "observed_at": f"{date}T22:00:00Z", "published_at": f"{date}T22:10:00Z",
        "available_at": f"{date}T22:15:00Z", "value": value, "denominator": 100,
        "fiscal_period": "FY1", "metric_definition": "FY1 aggregate PE v1",
        "vendor": "Fixture vendor", "source": "Fixture product", "provenance": f"fixture/{date}",
        "history_type": "point_in_time", "data_license": "private",
    } | overrides


def csv_bytes(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def evaluate(rows, *, metric="forward_pe", asof=CUTOFF):
    return evaluate_metric(parse_valuation_csv(csv_bytes(rows)), "XLK", metric, asof=asof)


def baseline(count=48, *, gap=0, **overrides):
    months = pd.period_range(end=pd.Period("2024-12", freq="M") - gap, periods=count, freq="M")
    return [row(str(m.end_time.date()), i + 1, **overrides) for i, m in enumerate(months)]


def test_robust_median_mad_z_and_percentile():
    observed = normalize_valuation(30, list(range(1, 49)), higher_is_cheap=False)
    z = (30 - 24.5) / (1.4826 * 12)
    assert observed.status == "complete"
    assert (observed.median, observed.mad, observed.n_history) == (24.5, 12, 48)
    assert observed.z == pytest.approx(z)
    assert observed.attractiveness_z == pytest.approx(-z)
    assert observed.score == pytest.approx(50 - (50 / 3) * z)
    assert observed.percentile == pytest.approx(100 * (18 + 0.5) / 48)


@pytest.mark.parametrize("higher_is_cheap,expected", [(True, 100), (False, 0)])
def test_clipping_keeps_raw_z_and_inverts_multiple(higher_is_cheap, expected):
    observed = normalize_valuation(10_000, list(range(1, 49)), higher_is_cheap=higher_is_cheap)
    assert observed.score == expected
    assert abs(observed.attractiveness_z) == 3 < observed.z
    assert observed.percentile == expected


def test_zero_mad_and_insufficient_history():
    assert normalize_valuation(10, [5] * 48, higher_is_cheap=False).status == "zero_mad"
    assert evaluate([*baseline(47), row()]).absolute.status == "insufficient_history"
    assert evaluate([*baseline(48), row()]).absolute.status == "complete"


@pytest.mark.parametrize("denominator", [0, -3, "", "nan", "inf"])
def test_unusable_pe_denominator(denominator):
    data = [*baseline(), row(denominator=denominator)]
    assert evaluate(data).absolute.status == "invalid_denominator"
    # A current invalid observation does not fall back to a prior print.
    historical = [*baseline()[:-1], row("2024-12-31", denominator=denominator), row()]
    result = evaluate(historical).absolute
    assert result.status == "insufficient_history"
    assert result.n_history == 47


@pytest.mark.parametrize("value", [0, -20, "", "nan", "inf"])
def test_invalid_multiple_with_positive_denominator(value):
    assert evaluate([*baseline(), row(value=value)]).absolute.status == "invalid_value"


def test_negative_fcf_yield_is_measurable():
    prior = [r | {"value": r["value"] / 1000} for r in baseline(metric="fcf_yield")]
    result = evaluate([*prior, row(value=-0.1, metric="fcf_yield")], metric="fcf_yield")
    assert result.absolute.status == "complete"
    assert result.absolute.score == 0
    assert result.relative.status == "not_comparable"


def test_reference_exactly_84_prior_calendar_months_excludes_current_month():
    prior = baseline(count=85)
    prior[0]["value"] = 50_000
    earlier_current_month = row("2025-01-03", value=90_000)
    another_earlier_print = row("2024-12-11", value=80_000)
    result = evaluate([*prior, another_earlier_print, earlier_current_month, row()]).absolute
    assert result.n_history == 84
    assert result.median == 43.5
    sparse = evaluate([*baseline(48, gap=37), row()]).absolute
    assert sparse.status == "insufficient_history"
    assert sparse.n_history == 47


def test_point_in_time_availability_late_revision_and_replay():
    old = baseline()
    late = row("2024-12-31", 99, published_at="2025-02-04T22:00:00Z",
               available_at="2025-02-05T12:00:00Z", provenance="correction")
    delayed = row(value=31, published_at="2025-02-02T00:00:00Z",
                  available_at="2025-02-03T00:00:00Z")
    future = row("2025-02-28", value=1000)
    data = [*old, late, delayed, future]
    before = evaluate(data, asof="2025-02-02T23:59:59Z")
    assert before.current["observation_date"] == pd.Timestamp("2024-12-31")
    at_availability = evaluate(data)
    assert at_availability.current["value"] == 31
    assert at_availability.absolute == evaluate([*old, delayed]).absolute
    after = evaluate(data, asof="2025-02-05T12:00:00Z")
    assert after.current["value"] == 31
    revised_baseline = [*old[:-1], late, delayed]
    assert after.absolute == evaluate(
        revised_baseline, asof="2025-02-05T12:00:00Z",
    ).absolute

    sample = parse_valuation_csv(csv_bytes(data))
    replay = metric_history(sample, "XLK", "forward_pe", asof="2025-02-06T12:00:00Z")
    january = next(entry for entry in replay if entry.asof.date().isoformat() == "2025-02-01")
    assert january.current["observation_date"] == pd.Timestamp("2024-12-31")
    assert january.current["value"] == 48
    assert all(entry.current is None or entry.current["available_at"] <= entry.asof
               for entry in replay)


def test_future_unavailable_missing_and_stale_boundaries():
    future_only = evaluate([row()], asof="2025-01-31T22:14:59Z")
    assert future_only.current is None
    assert future_only.absolute.status == "missing_observation"

    data = [*baseline(), row()]
    assert evaluate(data, asof="2025-03-17T23:59:00-04:00").absolute.status == "complete"
    assert evaluate(data, asof="2025-03-18T00:00:00-04:00").absolute.status == "stale"


def test_definition_changes_reset_reference_history():
    old = baseline(60)
    old[-10]["metric_definition"] = "alternate methodology"
    changed_back = evaluate([*old, row()]).absolute
    assert changed_back.status == "insufficient_history"
    assert changed_back.n_history == 9


def test_relative_multiple_exact_date_alignment_and_arithmetic():
    prior = baseline()
    spy = [r | {"ticker": "SPY", "value": 20} for r in prior]
    data = [*prior, *spy, row(value=30), row(ticker="SPY", value=25)]
    result = evaluate(data)
    history = [i / 20 - 1 for i in range(1, 49)]
    expected = normalize_valuation(30 / 25 - 1, history, higher_is_cheap=False)
    assert result.relative_value == pytest.approx(0.20)
    assert result.relative.score == pytest.approx(expected.score)
    assert result.relative.n_history == 48
    assert result.benchmark["ticker"] == "SPY"
    assert evaluate(data[:-1]).relative.status == "missing_benchmark"
    assert evaluate(data[:-1] + [row("2025-01-30", ticker="SPY")]).relative.status == "missing_benchmark"
    assert evaluate(data[:-1] + [row(ticker="SPY", denominator=0)]).relative.status == "invalid_benchmark"
    mismatch = row(ticker="SPY", fiscal_period="NTM", value=25)
    assert evaluate(data[:-1] + [mismatch]).relative.status == "incompatible_benchmark"
    unaligned = row(ticker="SPY", observed_at=f"{CURRENT}T21:00:00Z")
    assert evaluate(data[:-1] + [unaligned]).relative.status == "unaligned_benchmark"
    gap = spy[1:] + [spy[0] | {"observed_at": f'{spy[0]["observation_date"]}T21:00:00Z'}]
    assert evaluate([*prior, *gap, *data[-2:]]).relative.n_history == 47


def test_relative_yield_spread_and_direction():
    rows = baseline(metric="dividend_yield", ticker="XLP")
    rows = [r | {"value": r["value"] / 1000} for r in rows]
    spy = [r | {"ticker": "SPY", "value": 0.02} for r in rows]
    xlp = row(ticker="XLP", metric="dividend_yield", value=0.04)
    benchmark = xlp | {"ticker": "SPY", "value": 0.025}
    dataset = parse_valuation_csv(csv_bytes([*rows, *spy, xlp, benchmark]))
    result = evaluate_metric(dataset, "XLP", "dividend_yield", asof=CUTOFF)
    assert result.relative_value == pytest.approx(0.015)
    assert result.relative.score > 50


@pytest.mark.parametrize("override", [
    {"published_at": "2025-01-31T21:59:59Z"},
    {"available_at": "2025-01-31T22:09:59Z"},
    {"available_at": "2025-01-31T22:15:00"},
    {"available_at": "2025-01-31T22:15:00+999:00"},
    {"observed_at": "2025-01-31T01:00:00Z"},
    {"source": ""},
    {"provenance": ""},
    {"ticker": "SPY", "metric": "p_tbv"},
    {"data_license": "synthetic"},
])
def test_contract_rejects_inconsistent_or_undocumented_rows(override):
    with pytest.raises(ValuationDataError, match="CSV line 2"):
        parse_valuation_csv(csv_bytes([row(**override)]))


def test_inception_and_duplicate_vintages():
    old = row("2017-12-31", ticker="XLC")
    with pytest.raises(ValuationDataError, match="inception"):
        parse_valuation_csv(csv_bytes([old]))
    with pytest.raises(ValuationDataError, match="Duplicate"):
        parse_valuation_csv(csv_bytes([row(), row(value=40)]))


def test_reconstructed_raw_history_cannot_score_or_mix_with_point_in_time():
    with pytest.raises(ValuationDataError, match="histories separate"):
        parse_valuation_csv(csv_bytes([row(), row("2024-12-31", history_type="reconstructed")]))
    downloaded = [r | {"history_type": "reconstructed", "published_at": CUTOFF,
                        "available_at": CUTOFF} for r in [*baseline(), row()]]
    reconstructed = parse_valuation_csv(csv_bytes(downloaded))
    assert metric_history(reconstructed, "XLK", "forward_pe", asof="2025-02-02T00:00:00Z") == []
    history = metric_history(reconstructed, "XLK", "forward_pe", asof=CUTOFF)
    assert len(history) == 49
    assert all(point.absolute.status == "reconstructed_history" for point in history)
    assert all(np.isnan(point.absolute.score) for point in history)


def test_source_fingerprint_and_vintage_selection_are_reproducible():
    records = [*baseline(), row(available_at="2025-01-31T23:15:00+01:00"),
               row(value=30, available_at="2025-02-01T01:15:00+01:00", provenance="new vintage")]
    payload = csv_bytes(records)
    first = evaluate(records)
    second = evaluate(records[::-1])
    assert first.input_sha256 == hashlib.sha256(payload).hexdigest()
    assert first.input_sha256 != second.input_sha256
    assert first.absolute == second.absolute
    assert first.current["provenance"] == second.current["provenance"] == "new vintage"
    assert first.current["data_license"] == "private"


def test_synthetic_universe_and_strict_blend():
    payload = synthetic_valuation_csv()
    assert payload == synthetic_valuation_csv()
    dataset = parse_valuation_csv(payload)
    assert dataset.history_type == "synthetic"
    for ticker in VALUATION_METRICS:
        result = evaluate_sector(dataset, ticker, asof=DEMO_ASOF)
        assert result.metrics.keys() == VALUATION_METRICS[ticker].keys()
        assert 0 <= result.historical_score <= 100
        if ticker == "XLE":
            assert np.isnan(result.relative_score)
            assert np.isnan(result.blended_score)
        else:
            assert 0 <= result.relative_score <= 100
            assert result.blended_score == pytest.approx(
                0.70 * result.historical_score + 0.30 * result.relative_score,
            )


def test_absence_of_one_required_relative_metric_blocks_blend():
    payload = synthetic_valuation_csv().splitlines(keepends=True)
    dataset = parse_valuation_csv(b"".join(
        line for line in payload if not line.startswith(b"SPY,dividend_yield,")
    ))
    result = evaluate_sector(dataset, "XLP", asof=DEMO_ASOF)
    assert 0 <= result.historical_score <= 100
    assert result.metrics["forward_pe"].relative.status == "complete"
    assert result.metrics["dividend_yield"].relative.status == "missing_benchmark"
    assert np.isnan(result.relative_score)
    assert np.isnan(result.blended_score)
