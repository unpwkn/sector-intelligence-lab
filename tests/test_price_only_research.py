"""Deterministic fictional checks for the separate Phase 6C research entry point."""

import hashlib
import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from test_ibkr_history import FictionalBroker

from sectorlab.backtest import run_backtest
from sectorlab.backtest.fixture import _FixtureProvider, make_fixture_snapshot
from sectorlab.market_data import UNIVERSE, ValidationError, load_snapshot
from sectorlab.market_data.ibkr import _json_bytes, capture_history, publish_archive
from sectorlab.market_data.price_only import (
    ADJUSTMENT,
    ASSURANCE,
    import_ibkr_price_only,
    import_price_only,
    load_price_only,
)
from sectorlab.price_momentum import price_momentum_history
from sectorlab.price_research.report import SECTIONS, write_report
from sectorlab.price_research.study import _ic_inference, run_price_study

START, END = "2018-06-18", "2020-05-01"
SHORT_START, SHORT_END = "2024-07-01", "2024-07-05"


def _fixture_rows(start, end):
    fixture = _FixtureProvider()
    return pd.concat(
        [fixture.fetch(ticker, start, end).records for ticker in UNIVERSE],
        ignore_index=True,
    )[["date", "ticker", "adjusted_price_proxy"]].rename(
        columns={"adjusted_price_proxy": "adjusted_close"}
    )


def _inputs(root, rows, **declaration):
    root.mkdir()
    observations = root / "observations.csv"
    rows.to_csv(observations, index=False)
    provenance = root / "provenance.json"
    provenance.write_text(
        json.dumps(
            {
                "provider": "fictional",
                "source_id": "test generator",
                "source_reference": "software validation only",
                "license_reference": "generated fixture",
                "retrieved_at": datetime.now(UTC).isoformat(),
                "adjustment_basis": ADJUSTMENT,
                "data_kind": "synthetic",
                "raw_sha256": hashlib.sha256(observations.read_bytes()).hexdigest(),
            }
            | declaration
        ),
        encoding="utf-8",
    )
    return observations, provenance


@pytest.fixture(scope="module")
def study_fixture(tmp_path_factory):
    root = tmp_path_factory.mktemp("price-study")
    rows = _fixture_rows(START, END)
    inputs = _inputs(root / "inputs", rows)
    path = root / "snapshot"
    snapshot = import_price_only(*inputs, START, END, path)
    return path, snapshot, rows, run_price_study(path)


def test_price_only_replays_and_stays_separate(study_fixture, tmp_path):
    path, snapshot, rows, _ = study_fixture
    replay = load_price_only(path)
    assert replay.manifest_sha256 == snapshot.manifest_sha256
    assert replay.manifest["data_assurance"] == ASSURANCE
    assert replay.manifest["point_in_time_history"] is False
    assert replay.manifest["total_return_verified"] is False
    assert replay.manifest["empirical_claim_status"] == "NONE"
    assert replay.prices.columns.tolist() == ["date", "ticker", "adjusted_price_proxy"]
    assert replay.prices.query("ticker == 'XLC'").date.min() == pd.Timestamp("2018-06-19")
    pd.testing.assert_frame_equal(replay.prices, snapshot.prices)
    reimported = import_price_only(
        path / "observations.csv",
        path / "provenance.json",
        START,
        END,
        tmp_path / "reimported",
    )
    assert reimported.manifest["sha256"] == snapshot.manifest["sha256"]
    assert len(reimported.prices) == len(rows)
    with pytest.raises(ValidationError, match="schema 1.0 or 1.1"):
        run_backtest(path)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("missing", "SPY 1 missing"),
        ("absent", "XLC 4 missing"),
        ("duplicate", "Duplicate"),
        ("zero", "positive and finite"),
        ("negative", "positive and finite"),
        ("infinite", "positive and finite"),
        ("holiday", "non-XNYS"),
        ("vintage", "must follow"),
        ("basis", "split-and-distribution"),
        ("ibkr_alias", "original Phase 6B"),
        ("checksum", "checksum"),
    ],
)
def test_fail_closed_on_bad_price_only_inputs(tmp_path, mutation, error):
    rows = _fixture_rows(SHORT_START, SHORT_END)
    options = {}
    if mutation == "missing":
        rows = rows.query("ticker != 'SPY' or date != '2024-07-01'")
    elif mutation == "absent":
        rows = rows.query("ticker != 'XLC'")
    elif mutation == "duplicate":
        rows = pd.concat([rows, rows.iloc[:1]])
    elif mutation in {"zero", "negative", "infinite"}:
        rows.loc[0, "adjusted_close"] = {"zero": 0, "negative": -1, "infinite": np.inf}[mutation]
    elif mutation == "holiday":
        rows = pd.concat([rows, rows.iloc[:1].assign(date="2024-07-04")])
    elif mutation == "vintage":
        options["retrieved_at"] = "2024-07-05T19:59:00+00:00"
    elif mutation == "basis":
        options["adjustment_basis"] = "split_only"
    elif mutation == "ibkr_alias":
        options["provider"] = "Interactive Brokers"
    else:
        options["raw_sha256"] = "0" * 64
    inputs = _inputs(tmp_path / "inputs", rows, **options)
    output = tmp_path / "snapshot"
    with pytest.raises(ValidationError, match=error):
        import_price_only(*inputs, SHORT_START, SHORT_END, output)
    assert not output.exists()


def test_snapshot_checks_normalized_and_raw_bytes(tmp_path):
    inputs = _inputs(tmp_path / "inputs", _fixture_rows(SHORT_START, SHORT_END))
    path = tmp_path / "snapshot"
    import_price_only(*inputs, SHORT_START, SHORT_END, path)
    with (path / "observations.csv").open("ab") as file:
        file.write(b"\n")
    with pytest.raises(ValidationError, match="Checksum mismatch"):
        load_price_only(path)
    (path / "observations.csv").write_bytes(inputs[0].read_bytes())
    normalized = path / "prices.csv"
    normalized.write_bytes(normalized.read_bytes() + b"\n")
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["sha256"]["prices.csv"] = hashlib.sha256(normalized.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValidationError, match="raw evidence replay"):
        load_price_only(path)


def test_scoring_and_execution_match_strict_path_without_future_leakage(study_fixture, tmp_path):
    _, snapshot, rows, study = study_fixture
    strict_path = make_fixture_snapshot(tmp_path / "strict", start=START, end=END)
    with pytest.raises(ValidationError, match="Phase 6C"):
        load_price_only(strict_path)
    strict = run_backtest(strict_path)
    strict_rank = price_momentum_history(load_snapshot(strict_path))
    research_rank = price_momentum_history(snapshot)
    cols = ["signal_date", "ticker", "price_momentum_score", "price_momentum_rank"]
    pd.testing.assert_frame_equal(strict_rank[cols], research_rank[cols])
    cols = ["session", "strategy", "gross_nav", "gross_return"]
    pd.testing.assert_frame_equal(strict.daily[cols], study.primary.daily[cols])
    cols = ["strategy", "signal_date", "execution_session", "targets"]
    pd.testing.assert_frame_equal(strict.trades[cols], study.primary.trades[cols])
    enter = study.primary.trades.execution_session.min()
    executed = study.primary.daily.loc[study.primary.daily.session.eq(enter)]
    assert len(executed) == 4 and executed.gross_return.eq(0).all()
    assert set(study.metrics.one_way_bps) == {0, 5, 10}
    assert set(study.metrics.universe_policy) == {"common", "dynamic"}
    assert study.sensitivity.query("strategy == 'spy_buy_hold'").relative_vs_spy.eq(0).all()

    cutoff = "2019-12-31"
    changed = rows.copy()
    changed.loc[changed.date.gt(cutoff), "adjusted_close"] *= 3
    inputs = _inputs(tmp_path / "shock", changed)
    shocked = import_price_only(*inputs, START, END, tmp_path / "shocked")
    cols = ["signal_date", "ticker", "price_momentum_score"]
    pd.testing.assert_frame_equal(
        price_momentum_history(snapshot, asof=cutoff)[cols],
        price_momentum_history(shocked, asof=cutoff)[cols],
    )
    replay = run_price_study(tmp_path / "shocked")
    daily_columns = ["session", "strategy", "gross_nav", "gross_return"]
    pd.testing.assert_frame_equal(
        study.primary.daily.loc[study.primary.daily.session.le(cutoff), daily_columns],
        replay.primary.daily.loc[replay.primary.daily.session.le(cutoff), daily_columns],
    )
    assert set(study.rolling_12m.one_way_bps) == {0, 5, 10}
    assert study.forward.exit_session.le(pd.Timestamp(END)).all()
    assert not study.forward.empty
    panel = snapshot.prices.pivot(index="date", columns="ticker", values="adjusted_price_proxy")
    row = study.forward.iloc[0]
    sector = panel.loc[row.exit_session, row.ticker] / panel.loc[row.entry_session, row.ticker]
    spy = panel.loc[row.exit_session, "SPY"] / panel.loc[row.entry_session, "SPY"]
    assert row.relative_return == pytest.approx(sector / spy - 1)


def test_hac_uses_calendar_lags_and_suppresses_thin_windows():
    dates = pd.date_range("2018-01-31", periods=48, freq="ME").delete(12)
    monthly = pd.DataFrame(
        {
            "signal_date": dates,
            "horizon_months": 6,
            "rank_ic": np.sin(np.arange(len(dates)) / 3) / 4,
        }
    )
    result = _ic_inference(monthly).set_index("horizon_months")
    assert result.loc[6, "hac_lags"] >= 6
    assert result.loc[6, "observed_months"] == 47
    assert result.loc[6, "hac_se"] > 0
    assert np.isnan(result.loc[1, "p_value_two_sided_unadjusted"])


def test_reports_without_market_evidence_only_emit_template(study_fixture, tmp_path):
    path, _, _, _ = study_fixture
    no_input = write_report(tmp_path / "empty")
    fixture = write_report(tmp_path / "fixture", path)
    assert no_input.read_bytes() == fixture.read_bytes()
    text = fixture.read_text()
    assert "EMPIRICAL EVIDENCE PENDING" in text
    assert "![" not in text
    assert all(f"### {i}. {section}" in text for i, section in enumerate(SECTIONS, 1))
    assert list(fixture.parent.iterdir()) == [fixture]


def test_verified_ibkr_archive_uses_only_adjusted_last_and_keeps_6b_gate(tmp_path):
    broker = FictionalBroker(SHORT_START, SHORT_END)
    capture = capture_history(broker, SHORT_START, SHORT_END)
    for bar in capture["instruments"]["XLK"]["histories"]["TRADES"]["rows"]:
        bar["close"] = "999"
    # The price-only reader ignores auxiliary price fields, even on ADJUSTED_LAST.
    capture["instruments"]["XLK"]["histories"]["ADJUSTED_LAST"]["rows"][0]["volume"] = "-1"
    archive = tmp_path / "ibkr"
    report = publish_archive(_json_bytes(capture), archive)
    assert report["phase6a_export_eligible"] is False
    local = import_ibkr_price_only(
        archive,
        tmp_path / "snapshot",
        source_reference="fictional TWS capture",
        license_reference="generated fixture",
    )
    assert local.manifest["provenance"]["source_id"] == "TWS ADJUSTED_LAST"
    assert local.prices.query("ticker == 'XLK'").adjusted_price_proxy.eq(100).all()
    assert load_price_only(tmp_path / "snapshot").manifest_sha256 == local.manifest_sha256
    assert (tmp_path / "snapshot/ibkr_tws_capture.json").read_bytes() == (
        archive / "capture.json"
    ).read_bytes()

    capture["instruments"]["XLK"]["histories"]["ADJUSTED_LAST"] = {"error": {"code": 162}}
    unavailable = tmp_path / "unavailable"
    publish_archive(_json_bytes(capture), unavailable)
    with pytest.raises(ValidationError, match="lacks confirmed TWS ADJUSTED_LAST"):
        import_ibkr_price_only(
            unavailable,
            tmp_path / "rejected",
            source_reference="fictional",
            license_reference="generated fixture",
        )


def test_market_declaration_carries_exploratory_labels_without_return_claim(tmp_path):
    inputs = _inputs(
        tmp_path / "inputs",
        _fixture_rows(SHORT_START, SHORT_END),
        data_kind="market",
    )
    # Exercise the market declaration branch using mocked inputs and no report.
    snapshot = import_price_only(*inputs, SHORT_START, SHORT_END, tmp_path / "snapshot")
    assert snapshot.manifest["empirical_claim_status"] == "EXPLORATORY"
    assert snapshot.research_classification == "RETROSPECTIVE_ADJUSTED_PRICE_PROXY"
    assert snapshot.manifest["point_in_time_history"] is False
    assert snapshot.manifest["total_return_verified"] is False
    with pytest.raises(ValidationError, match="No executable common|month-end trading session"):
        run_price_study(tmp_path / "snapshot")
