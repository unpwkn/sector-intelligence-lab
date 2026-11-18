"""Exercise the source-to-snapshot path with fictional price observations."""

import hashlib
import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from sectorlab.backtest import run_backtest
from sectorlab.backtest.engine import RETROSPECTIVE, SYNTHETIC
from sectorlab.backtest.fixture import _FixtureProvider
from sectorlab.market_data import UNIVERSE, ValidationError, import_csv, load_snapshot
from sectorlab.market_data.providers import FIELDS

START, END = "2018-06-18", "2018-06-20"
RETRIEVED = datetime(2021, 1, 5, tzinfo=UTC)


def observations(start=START, end=END):
    provider = _FixtureProvider()
    frames = [provider.fetch(ticker, start, end).records for ticker in UNIVERSE]
    return pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)


def save_inputs(path, frame, **overrides):
    path.mkdir()
    csv_path = path / "data.csv"
    csv_path.write_bytes(frame.to_csv(index=False, lineterminator="\r\n").encode("utf-8-sig"))
    declaration = {
        "source": "fictional-source",
        "source_reference": "Phase 5 modeled observations for software tests",
        "license_reference": "Project-generated fixture",
        "retrieved_at": RETRIEVED.isoformat(),
        "data_kind": "synthetic",
        "ohlc_basis": "as_traded",
        "adjusted_close_basis": "split_and_distribution_adjusted",
        "corporate_action_method": "Fictional ex-date cash distributions and new/old share ratios",
    }
    provenance = path / "provenance.json"
    provenance.write_text(json.dumps(declaration | overrides), encoding="utf-8")
    return csv_path, provenance


def test_preserve_evidence_actions_launch_and_reproduce(tmp_path):
    rows = (
        observations().sort_values(["date", "ticker"], ascending=False)
        .astype({"dividend_amount": float})
    )
    xlc = rows.query("ticker == 'XLC'").index[-1]
    rows.loc[xlc, ["adjusted_price_proxy", "dividend_amount", "split_coefficient"]] = [
        73.1234567890123, 0.375, 2,
    ]
    rows = pd.concat([rows, rows.loc[[xlc]]], ignore_index=True)
    csv_path, provenance = save_inputs(tmp_path / "inputs", rows)
    snapshot = import_csv(csv_path, provenance, START, END, tmp_path / "snapshot")
    loaded = load_snapshot(snapshot)
    manifest = loaded.manifest
    assert (snapshot / "raw/observations.csv").read_bytes() == csv_path.read_bytes()
    assert (snapshot / "raw/provenance.json").read_bytes() == provenance.read_bytes()
    assert manifest["provenance"] == json.loads(provenance.read_bytes())
    assert manifest["fund_inception"]["XLC"] == "2018-06-18"
    coverage = manifest["coverage"]["XLC"]
    assert coverage["first_trading_session"] == "2018-06-19"
    assert coverage["observations"] == 2 and coverage["exact_duplicates_removed"] == 1
    assert coverage["missing_sessions"] == []
    first = loaded.prices.query("ticker == 'XLC'").iloc[0]
    assert first.date == pd.Timestamp("2018-06-19")
    assert first.adjusted_price_proxy == pytest.approx(73.1234567890123)
    assert first.close == pytest.approx(rows.loc[rows.ticker.eq("XLC"), "close"].iloc[-1])
    assert (first.dividend_amount, first.split_coefficient) == (0.375, 2)
    assert manifest["research_classification"] == loaded.research_classification == SYNTHETIC
    assert manifest["point_in_time_history"] is False
    assert manifest["total_return_verified"] is False
    assert "available_at" not in loaded.prices
    rerun = import_csv(
        snapshot / "raw/observations.csv", snapshot / "raw/provenance.json",
        START, END, tmp_path / "rerun",
    )
    replay = load_snapshot(rerun)
    assert replay.manifest["sha256"] == manifest["sha256"]
    pd.testing.assert_frame_equal(replay.prices, loaded.prices)
    with pytest.raises(FileExistsError):
        import_csv(csv_path, provenance, START, END, snapshot)


@pytest.mark.parametrize(
    ("ticker", "field", "value", "message"),
    [
        ("XLC", "date", "2018-06-18", "pre-launch"),
        ("SPY", "date", "2018-06-17", "Non-session"),
        ("SPY", "date", "2018-06-21", "out-of-range"),
        ("SPY", "ticker", "FAKE", "unexpected ticker"),
        ("SPY", "close", 0, "Invalid price"),
        ("SPY", "high", 1, "Inconsistent OHLC"),
        ("SPY", "dividend_amount", "", "Non-finite"),
        ("SPY", "split_coefficient", 0, "Invalid price"),
    ],
)
def test_reject_invalid_rows(tmp_path, ticker, field, value, message):
    rows = observations().astype(object)
    index = rows.index[rows.ticker == ticker][0]
    rows.loc[index, field] = value
    inputs = save_inputs(tmp_path / "inputs", rows)
    output = tmp_path / "snapshot"
    with pytest.raises(ValidationError, match=message):
        import_csv(*inputs, START, END, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".ingest-*"))


def test_missing_sessions_never_filled_or_backtested(tmp_path):
    rows = observations().query("ticker != 'SPY' or date != '2018-06-19'")
    inputs = save_inputs(tmp_path / "inputs", rows)
    with pytest.raises(ValidationError, match="SPY: 1 missing"):
        import_csv(*inputs, START, END, tmp_path / "strict")
    output = import_csv(*inputs, START, END, tmp_path / "gapped", allow_missing=True)
    loaded = load_snapshot(output)
    assert loaded.manifest["coverage"]["SPY"]["missing_sessions"] == ["2018-06-19"]
    assert len(loaded.prices) == len(rows)
    with pytest.raises(ValidationError, match="SPY: 1 missing"):
        run_backtest(output)


def test_invalid_declarations_and_conflicting_duplicates(tmp_path):
    rows = observations()
    duplicate = rows.iloc[[0]].copy()
    duplicate["adjusted_price_proxy"] *= 2
    inputs = save_inputs(tmp_path / "duplicate", pd.concat([rows, duplicate]))
    with pytest.raises(ValidationError, match="Conflicting duplicate"):
        import_csv(*inputs, START, END, tmp_path / "snapshot")

    inputs = save_inputs(tmp_path / "bad-basis", rows, adjusted_close_basis="split_only")
    with pytest.raises(ValidationError, match="split_and_distribution_adjusted"):
        import_csv(*inputs, START, END, tmp_path / "snapshot")
    inputs = save_inputs(tmp_path / "early", rows, retrieved_at="2018-06-20T19:59:59Z")
    with pytest.raises(ValidationError, match="between last session close and now"):
        import_csv(*inputs, START, END, tmp_path / "snapshot")
    inputs = save_inputs(tmp_path / "future", rows)
    with pytest.raises(ValidationError, match="future or unclosed"):
        import_csv(*inputs, START, END, tmp_path / "snapshot", now=datetime(2018, 6, 20, tzinfo=UTC))
    csv_path, _ = inputs
    rows.drop(columns=FIELDS[-1]).to_csv(csv_path, index=False)
    with pytest.raises(ValidationError, match="CSV header"):
        import_csv(*inputs, START, END, tmp_path / "snapshot")


def test_raw_hash_and_replay_detect_divergence(tmp_path):
    inputs = save_inputs(tmp_path / "inputs", observations())
    snapshot = import_csv(*inputs, START, END, tmp_path / "snapshot")
    raw = snapshot / "raw/observations.csv"
    raw.write_bytes(raw.read_bytes() + b"\n")
    with pytest.raises(ValidationError, match="Checksum mismatch for raw/observations.csv"):
        load_snapshot(snapshot)
    raw.write_bytes(inputs[0].read_bytes())

    normalized = snapshot / "prices.csv"
    rows = pd.read_csv(normalized, float_precision="round_trip")
    rows.loc[0, "adjusted_price_proxy"] *= 2
    rows.to_csv(normalized, index=False)
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["sha256"]["prices.csv"] = hashlib.sha256(normalized.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValidationError, match="Normalized prices disagree with raw CSV"):
        load_snapshot(snapshot)


def test_csv_backtest_ignores_later_price_shocks(tmp_path):
    end = "2020-04-01"
    cutoff = "2019-12-31"
    base = observations(START, end)
    changed = base.copy()
    changed.loc[changed.date.gt(cutoff), "adjusted_price_proxy"] *= 10
    results = []
    for name, rows in (("baseline", base), ("shocked", changed)):
        inputs = save_inputs(tmp_path / name, rows)
        snapshot = import_csv(*inputs, START, end, tmp_path / f"{name}-snapshot")
        results.append(run_backtest(snapshot))
    first, second = results
    assert first.metadata["research_classification"] == SYNTHETIC
    assert first.metadata["snapshot_manifest_sha256"] != second.metadata["snapshot_manifest_sha256"]
    for table, date_field in (("decisions", "signal_date"), ("daily", "session")):
        earlier = []
        for result in results:
            frame = getattr(result, table)
            prefix = frame.loc[frame[date_field].le(pd.Timestamp(cutoff))]
            earlier.append(prefix.drop(columns="snapshot_manifest_sha256").reset_index(drop=True))
        pd.testing.assert_frame_equal(*earlier)
    assert not first.daily.gross_nav.equals(second.daily.gross_nav)

    # Exercise the market classification branch with an explicitly mocked declaration.
    inputs = save_inputs(tmp_path / "mock-market", base, data_kind="market")
    market = import_csv(*inputs, START, end, tmp_path / "mock-market-snapshot")
    metadata = run_backtest(market).metadata
    assert metadata["research_classification"] == RETROSPECTIVE
    assert metadata["point_in_time_history"] is False
    assert metadata["total_return_verified"] is False
