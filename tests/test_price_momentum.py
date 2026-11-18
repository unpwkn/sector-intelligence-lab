"""Fictional Phase 1 snapshots exercise the research path entirely offline."""

import hashlib
import json
from datetime import UTC, datetime

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest

from sectorlab.config import BENCHMARK, SECTORS
from sectorlab.market_data import PriceResponse, ValidationError, ingest, load_snapshot
from sectorlab.market_data.pipeline import FIRST_SESSION
from sectorlab.market_data.providers import FIELDS
from sectorlab.price_momentum import price_momentum_history

RETRIEVED = datetime(2025, 1, 10, tzinfo=UTC)
RATES = {ticker: (i - 5) * .001 for i, ticker in enumerate(SECTORS)} | {BENCHMARK: .002}


class FictionalProvider:
    name = "offline_fixture"
    price_basis = "adjusted_price_proxy"

    def __init__(self, *, missing=None, after=None, rates=None):
        self.missing = missing or {}
        self.after = after
        self.rates = RATES if rates is None else rates

    def fetch(self, ticker, start, end):
        days = xcals.get_calendar("XNYS").sessions_in_range(start, end)
        rows = []
        for i, day in enumerate(days):
            if day < pd.Timestamp(FIRST_SESSION[ticker]) or day in self.missing.get(ticker, ()):
                continue
            # A large, deliberate change strictly after the historical cutoff.
            offset = 2 if self.after is not None and day > self.after and ticker == "XLK" else 0
            price = 100 * np.exp(self.rates[ticker] * i + offset)
            rows.append([day.strftime("%Y-%m-%d"), ticker, 10, 12, 9, 11, price, 1000, 0, 1])
        return PriceResponse(
            pd.DataFrame(rows, columns=FIELDS), {"source": self.name, "ticker": ticker}, RETRIEVED
        )


def save(tmp_path, provider=None, *, start="2022-01-03", end="2024-01-08", name="snapshot"):
    provider = provider or FictionalProvider()
    return ingest(
        provider, start, end, tmp_path / name,
        allow_missing=bool(provider.missing), now=RETRIEVED,
    )


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    return save(tmp_path_factory.mktemp("price_momentum"))


def latest(history, ticker="XLI"):
    return history.loc[history["ticker"] == ticker].iloc[-1]


def test_relative_log_arithmetic_and_percentile_score(baseline):
    snapshot = load_snapshot(baseline)
    history = price_momentum_history(snapshot)
    row = latest(history)
    relative_rate = RATES["XLI"] - RATES["SPY"]
    assert row.rs_6_1 == pytest.approx(relative_rate * 105)
    assert row.rs_12_1 == pytest.approx(relative_rate * 231)
    assert row.rs_3m == pytest.approx(relative_rate * 63)
    assert row.relative_performance_3m == pytest.approx(np.exp(relative_rate * 63) - 1)
    assert row.price_momentum_score == pytest.approx(70)
    assert row.price_momentum_rank == 4
    assert row.peer_count == len(SECTORS)
    assert latest(history, "XLK").price_momentum_score == 0
    assert latest(history, "XLU").price_momentum_score == 100
    assert BENCHMARK not in history["ticker"].unique()
    assert row.status == "complete"


def test_weighting_and_average_ties(tmp_path):
    rates = dict.fromkeys(SECTORS, .001) | {"XLU": .006, "SPY": .002}
    history = price_momentum_history(load_snapshot(save(tmp_path, FictionalProvider(rates=rates))))
    assert latest(history, "XLI").rs_6_1_percentile == 45
    assert latest(history, "XLI").price_momentum_score == 45

    # Known change after t-21 affects only the 3M component.
    cutoff = history["signal_date"].max() - pd.Timedelta(days=20)
    modified = price_momentum_history(
        load_snapshot(save(tmp_path, FictionalProvider(after=cutoff, rates=rates), name="modified"))
    )
    row = latest(modified, "XLK")
    assert row.rs_6_1_percentile == row.rs_12_1_percentile == 45
    assert row.rs_3m_percentile == 100
    assert row.price_momentum_score == pytest.approx(.4 * 45 + .4 * 45 + .2 * 100)


def test_alignment_when_benchmark_and_sector_have_different_gaps(tmp_path):
    # July 4 is a holiday and a missing July 5 price must occupy its normal grid slot.
    provider = FictionalProvider(missing={
        "SPY": [pd.Timestamp("2023-07-05")],
        "XLK": [pd.Timestamp("2023-11-01")],
    })
    snapshot = load_snapshot(save(tmp_path, provider))
    history = price_momentum_history(snapshot)
    assert pd.Timestamp("2023-07-04") not in snapshot.sessions
    assert pd.Timestamp("2023-07-05") in snapshot.sessions
    assert latest(history).rs_3m == pytest.approx((RATES["XLI"] - RATES["SPY"]) * 63)
    assert latest(history).rs_12_1_status == "missing_observations"
    assert np.isnan(latest(history).rs_12_1)
    xlk = latest(history, "XLK")
    assert xlk.rs_3m_status == "missing_observations"
    assert np.isnan(xlk.rs_3m)
    assert xlk.status == "insufficient_data"
    assert np.isnan(xlk.price_momentum_score)
    assert xlk.peer_count == 0


def test_missing_signal_session_does_not_move_signal_date(tmp_path):
    end = pd.Timestamp("2024-01-08")
    missing = {ticker: [end] for ticker in (*SECTORS, BENCHMARK)}
    snapshot = load_snapshot(save(tmp_path, FictionalProvider(missing=missing)))
    row = latest(price_momentum_history(snapshot))
    assert row.signal_date == end
    assert row.rs_6_1_status == "complete"
    assert row.rs_12_1_status == "complete"
    assert row.rs_3m_status == "missing_observations"
    assert row.status == "insufficient_data"


def test_single_peer_is_not_scored(tmp_path):
    missing = {
        ticker: [pd.Timestamp("2024-01-08")] for ticker in SECTORS if ticker != "XLK"
    }
    snapshot = load_snapshot(save(tmp_path, FictionalProvider(missing=missing)))
    row = latest(price_momentum_history(snapshot), "XLK")
    assert row.peer_count == 1
    assert row.rs_3m_status == "complete"
    assert row.status == "insufficient_data"
    assert np.isnan(row.price_momentum_score)


@pytest.mark.parametrize(
    "start,end,ticker",
    [("2014-01-02", "2015-10-09", "XLRE"), ("2017-01-03", "2018-06-20", "XLC")],
)
def test_inception_limits_history_without_requiring_future_peer(tmp_path, start, end, ticker):
    snapshot = load_snapshot(save(tmp_path, start=start, end=end))
    history = price_momentum_history(snapshot)
    row = latest(history, ticker)
    assert row.rs_3m_status == "insufficient_history"
    assert row.rs_12_1_status == "insufficient_history"
    assert row.status == "insufficient_data"
    assert latest(history).status == "complete"


def test_exact_lookback_boundaries(baseline):
    snapshot = load_snapshot(baseline)
    for offset, component in ((63, "rs_3m"), (126, "rs_6_1"), (252, "rs_12_1")):
        in_range = snapshot.sessions[offset].strftime("%Y-%m-%d")
        history = price_momentum_history(snapshot, asof=in_range)
        sector = history[history["ticker"] == "XLI"]
        assert sector.iloc[-2][f"{component}_status"] == "insufficient_history"
        assert sector.iloc[-1][f"{component}_status"] == "complete"
    assert price_momentum_history(snapshot, asof="2021-12-31").empty


def test_cutoff_weekend_and_future_price_changes(baseline, tmp_path):
    cutoff = pd.Timestamp("2023-07-07")
    base = load_snapshot(baseline)
    revised = load_snapshot(save(tmp_path, FictionalProvider(after=cutoff)))
    original_history = price_momentum_history(base, asof="2023-07-09")
    revised_history = price_momentum_history(revised, asof="2023-07-09")
    assert original_history.signal_date.max() == cutoff
    pd.testing.assert_frame_equal(
        original_history.drop(columns="snapshot_manifest_sha256"),
        revised_history.drop(columns="snapshot_manifest_sha256"),
    )
    full = price_momentum_history(base)
    pd.testing.assert_frame_equal(
        original_history, full.loc[full["signal_date"] <= cutoff].reset_index(drop=True),
    )
    # The revised future data would affect a later signal.
    assert latest(full, "XLK").rs_6_1 != latest(price_momentum_history(revised), "XLK").rs_6_1


@pytest.mark.parametrize("filename", ["prices.csv", "raw/SPY.json"])
def test_rejects_tampered_snapshot(tmp_path, filename):
    path = save(tmp_path, start="2023-10-10", end="2023-10-12")
    file = path / filename
    file.write_bytes(file.read_bytes() + b"\n")
    with pytest.raises(ValidationError, match=f"Checksum mismatch for {filename}"):
        load_snapshot(path)


def test_rejects_manifest_coverage_mismatch(tmp_path):
    path = save(tmp_path, start="2023-10-10", end="2023-10-12")
    manifest = path / "manifest.json"
    metadata = json.loads(manifest.read_text())
    metadata["coverage"]["XLK"]["missing_sessions"] = ["2023-10-12"]
    manifest.write_text(json.dumps(metadata))
    with pytest.raises(ValidationError, match="coverage disagrees for XLK"):
        load_snapshot(path)


def test_synthetic_data_cannot_enter_snapshot_pipeline(tmp_path):
    fake = tmp_path / "prices.csv"
    fake.write_text("date,ticker,adjusted_price_proxy,available_at\n2024-01-08,SPY,1,2024-01-08\n")
    with pytest.raises(ValidationError, match="manifest.json"):
        load_snapshot(tmp_path)

    snapshot = save(tmp_path, start="2023-10-10", end="2023-10-12")
    (snapshot / "prices.csv").write_bytes(fake.read_bytes())
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sha256"]["prices.csv"] = hashlib.sha256(fake.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValidationError, match="prices.csv schema"):
        load_snapshot(snapshot)


def test_reproducible_outputs_and_explicit_provenance(baseline):
    snapshot = load_snapshot(baseline)
    first_run = price_momentum_history(snapshot)
    pd.testing.assert_frame_equal(first_run, price_momentum_history(load_snapshot(baseline)))
    assert not set(first_run).intersection({"available_at", "momentum", "rotation", "opportunity"})
    assert set(first_run["provider"]) == {"offline_fixture"}
    assert set(first_run["price_basis"]) == {"adjusted_price_proxy"}
    assert set(first_run["snapshot_manifest_sha256"]) == {
        hashlib.sha256((baseline / "manifest.json").read_bytes()).hexdigest()
    }
    assert snapshot.manifest["point_in_time_history"] is False
    assert snapshot.manifest["total_return_verified"] is False
