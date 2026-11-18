"""Offline validation of Phase 5's timing, accounting, and research labels."""

import numpy as np
import pandas as pd
import pytest

from sectorlab.backtest import CostAssumptions, run_backtest
from sectorlab.backtest.engine import RETROSPECTIVE, SYNTHETIC, _rebalance
from sectorlab.backtest.fixture import _FixtureProvider, make_fixture_snapshot
from sectorlab.backtest.metrics import performance
from sectorlab.config import SECTORS
from sectorlab.market_data import PriceResponse, ValidationError, ingest, load_snapshot
from sectorlab.price_momentum import price_momentum_history


@pytest.fixture(scope="module")
def snapshot_path(tmp_path_factory):
    return make_fixture_snapshot(tmp_path_factory.mktemp("phase5") / "snapshot")


@pytest.fixture(scope="module")
def common(snapshot_path):
    return run_backtest(snapshot_path, costs=CostAssumptions(1, 4, 2))


def test_common_vs_dynamic_and_inception(snapshot_path, common, tmp_path):
    dynamic = run_backtest(snapshot_path, universe="dynamic")
    equal_common = common.trades.query("strategy == 'equal_weight_sectors'")
    equal_dynamic = dynamic.trades.query("strategy == 'equal_weight_sectors'")
    assert common.metadata["baseline_session"] == "2019-06-28"
    assert dynamic.metadata["baseline_session"] < common.metadata["baseline_session"]
    assert all(set(target.split()) == set(SECTORS) for target in equal_common.targets)
    assert "XLC" not in equal_dynamic.iloc[0].targets.split()
    assert "XLC" in equal_dynamic.iloc[-1].targets.split()

    snapshot = load_snapshot(snapshot_path)
    assert snapshot.prices.query("ticker == 'XLC'").date.min() == pd.Timestamp("2018-06-19")
    assert common.metadata["snapshot_manifest_sha256"] == snapshot.manifest_sha256
    assert common.metadata["snapshot_manifest"]["sha256"] == snapshot.manifest["sha256"]
    assert set(common.daily.research_classification) == {SYNTHETIC}

    launch_path = make_fixture_snapshot(tmp_path / "launch", start="2015-10-01", end="2015-11-02")
    launch = load_snapshot(launch_path)
    assert launch.prices.query("ticker == 'XLRE'").date.min() == pd.Timestamp("2015-10-08")
    assert launch.manifest["coverage"]["XLRE"]["missing_sessions"] == []
    with pytest.raises(ValidationError, match="No executable common"):
        run_backtest(launch_path)


def test_signal_reuses_phase2_cutoff_and_trades_next_close(snapshot_path, common):
    snapshot = load_snapshot(snapshot_path)
    prices = snapshot.prices.pivot(index="date", columns="ticker", values="adjusted_price_proxy")
    daily = common.daily.query("strategy == 'top_3_momentum'").set_index("session")
    trades = common.trades.query("strategy == 'top_3_momentum'").reset_index(drop=True)
    entry, rebalance = trades.iloc[0], trades.iloc[1]
    signal = entry.signal_date
    phase2 = price_momentum_history(snapshot, asof=signal.strftime("%Y-%m-%d")).query(
        "signal_date == @signal"
    )
    scores = common.decisions.query("signal_date == @signal")
    pd.testing.assert_series_equal(
        scores.set_index("ticker").price_momentum_score,
        phase2.set_index("ticker").price_momentum_score,
    )
    assert (
        entry.targets.split()
        == phase2.sort_values(["price_momentum_score", "ticker"], ascending=[False, True])
        .ticker.head(3)
        .tolist()
    )
    assert entry.execution_session == pd.Timestamp("2019-07-01")
    assert daily.loc[entry.execution_session, "gross_return"] == 0
    rate = common.metadata["cost_assumptions"]["one_way_rate"]
    assert daily.loc[entry.execution_session, "net_nav"] == pytest.approx(1 / (1 + rate))

    for trade, holding in [(entry, entry), (rebalance, entry)]:
        ix = prices.index.get_loc(trade.execution_session)
        if trade is rebalance:
            cols = holding.targets.split()
            # The old basket earns the return through the rebalance close.
            wealth = prices[cols].div(prices.loc[holding.execution_session, cols]).mean(axis=1)
            assert daily.loc[trade.execution_session, "gross_return"] == pytest.approx(
                wealth.iloc[ix] / wealth.iloc[ix - 1] - 1
            )
        cols = trade.targets.split()
        assert daily.loc[prices.index[ix + 1], "gross_return"] == pytest.approx(
            (prices[cols].iloc[ix + 1] / prices[cols].iloc[ix]).mean() - 1
        )
    assert len(common.trades.query("strategy == 'spy_buy_hold'")) == 1
    assert common.decisions.signal_date.max() == pd.Timestamp("2020-12-31")
    early_close = trades.loc[trades.signal_date.eq(pd.Timestamp("2019-11-29"))].iloc[0]
    assert early_close.decision_close_utc == pd.Timestamp("2019-11-29 18:00:00Z")
    assert early_close.execution_session == pd.Timestamp("2019-12-02")


def test_future_price_rows_do_not_change_earlier_results(common, tmp_path):
    prefix = make_fixture_snapshot(tmp_path / "prefix", end="2019-12-31")
    partial = run_backtest(prefix, costs=CostAssumptions(1, 4, 2))
    cutoff = pd.Timestamp("2019-12-31")
    common_prefix = common.daily.loc[common.daily.session.le(cutoff)].reset_index(drop=True)
    # Each run retains its own source identity even when its simulated prefix is identical.
    hash_column = "snapshot_manifest_sha256"
    assert common_prefix[hash_column].iat[0] != partial.daily[hash_column].iat[0]
    pd.testing.assert_frame_equal(
        common_prefix.drop(columns=hash_column),
        partial.daily.drop(columns=hash_column).reset_index(drop=True),
    )
    final_month = partial.decisions.loc[
        partial.decisions.signal_date.eq(pd.Timestamp("2019-12-31"))
    ]
    assert set(final_month.decision_status) == {"no_execution_session"}
    assert final_month.execution_close_utc.isna().all()
    assert "2019-12-31" not in partial.trades.signal_date.dt.strftime("%Y-%m-%d").tolist()
    assert partial.rolling_12m.relative_return.isna().all()


def test_coverage_gate_and_retro_label(tmp_path):
    class ExportLikeProvider(_FixtureProvider):
        name = "mock_external_provider"

        def fetch(self, ticker, start, end):
            response = super().fetch(ticker, start, end)
            if ticker == "SPY":
                rows = response.records.query("date != '2020-02-03'")
                return PriceResponse(rows, response.raw, response.retrieved_at)
            return response

    snapshot = ingest(
        ExportLikeProvider(),
        "2019-01-02",
        "2020-03-02",
        tmp_path / "gapped",
        allow_missing=True,
    )
    with pytest.raises(ValidationError, match="SPY: 1 missing"):
        run_backtest(snapshot, universe="dynamic")

    complete = ingest(ExportLikeProvider(), "2018-06-19", "2019-08-01", tmp_path / "complete")
    result = run_backtest(complete)
    assert result.metadata["research_classification"] == RETROSPECTIVE
    assert result.metadata["point_in_time_history"] is False
    (complete / "raw/SPY.json").write_text("{}")
    with pytest.raises(ValidationError, match="Checksum mismatch"):
        run_backtest(complete)


def test_cost_funding_and_turnover():
    costs = CostAssumptions(1, 4, 2)
    assert costs.one_way_rate == pytest.approx(0.0005)
    holdings, turnover, notional, cost = _rebalance(
        np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]), costs.one_way_rate
    )
    expected_nav = (1 - costs.one_way_rate) / (1 + costs.one_way_rate)
    assert holdings.tolist() == pytest.approx([0.0, expected_nav, 0.0])
    assert turnover == 1
    assert notional == pytest.approx(1 + expected_nav)
    assert cost == pytest.approx(1 - expected_nav)
    with pytest.raises(ValueError, match="finite and nonnegative"):
        CostAssumptions(slippage_bps=-1)


def test_metrics_align_gross_and_net_and_use_twelve_full_months():
    dates = pd.date_range("2020-01-31", periods=13, freq="ME")
    spy = np.tile([-0.02, 0.04], 6)
    sector = np.tile([-0.01, 0.05], 6)
    rows = []
    for name, monthly in [("spy_buy_hold", spy), ("test", sector)]:
        net_monthly = monthly - 0.001
        rows.append(
            pd.DataFrame(
                {
                    "session": dates,
                    "strategy": name,
                    "gross_nav": np.r_[1.0, np.cumprod(1 + monthly)],
                    "net_nav": np.r_[1.0, np.cumprod(1 + net_monthly)],
                    "gross_return": np.r_[np.nan, monthly],
                    "net_return": np.r_[np.nan, net_monthly],
                }
            )
        )
    trades = pd.DataFrame(
        {
            "strategy": ["spy_buy_hold", "test"],
            "gross_turnover": [1.0, 2.0],
            "net_turnover": [1.0, 2.0],
            "gross_etf_notional_fraction": [1.0, 3.0],
            "net_etf_notional_fraction": [0.99, 2.98],
        }
    )
    metrics, monthly, rolling = performance(pd.concat(rows), trades, dates, 0.0)
    gross = metrics.query("strategy == 'test' and basis == 'gross'").iloc[0]
    net = metrics.query("strategy == 'test' and basis == 'net'").iloc[0]
    assert gross.portfolio_turnover == 2
    assert gross.max_drawdown == pytest.approx(-0.01)
    assert gross.tracking_error_vs_spy == pytest.approx(0)
    assert np.isnan(gross.information_ratio_vs_spy)
    assert gross.annualized_volatility == pytest.approx(np.std(sector, ddof=1) * np.sqrt(252))
    assert gross.sharpe_ratio == pytest.approx(sector.mean() * 252 / gross.annualized_volatility)
    assert gross.downside_capture_vs_spy == pytest.approx(0.5)
    assert net.downside_capture_vs_spy == pytest.approx(0.011 / 0.021)
    assert gross.win_rate == 0.5 and gross.win_rate_vs_spy == 1
    assert len(monthly.query("strategy == 'test' and basis == 'net'")) == 12
    relative = rolling.query("strategy == 'test' and basis == 'gross'").relative_return
    assert relative.iloc[:-1].isna().all()
    assert relative.iloc[-1] == pytest.approx(np.prod(1 + sector) / np.prod(1 + spy) - 1)

    notional_metrics, _, _ = performance(pd.concat(rows), trades, dates, 0.0, "etf_notional")
    assert notional_metrics.query("strategy == 'test'").portfolio_turnover.tolist() == [3.0, 2.98]
