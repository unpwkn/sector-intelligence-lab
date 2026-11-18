"""Prespecified sensitivity and forward diagnostics for Report #001."""

from __future__ import annotations

from dataclasses import dataclass
from math import erfc, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from sectorlab.backtest.engine import (
    BacktestResult,
    CostAssumptions,
    _month_ends,
    _run_validated_backtest,
)
from sectorlab.config import BENCHMARK, SECTORS
from sectorlab.market_data.price_only import PriceOnlySnapshot, load_price_only
from sectorlab.market_data.snapshot import PRICE_BASIS

ONE_WAY_BPS = (0, 5, 10)
HORIZONS = (1, 3, 6)
MIN_INFERENCE_MONTHS = 24


@dataclass(frozen=True)
class PriceStudy:
    snapshot: PriceOnlySnapshot
    primary: BacktestResult
    metrics: pd.DataFrame
    rolling_12m: pd.DataFrame
    sensitivity: pd.DataFrame
    forward: pd.DataFrame
    monthly_ic: pd.DataFrame
    inference: pd.DataFrame
    forward_summary: pd.DataFrame
    regimes: pd.DataFrame


def _forward_diagnostics(
    snapshot: PriceOnlySnapshot,
    primary: BacktestResult,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sessions = snapshot.sessions
    _, month_ends = _month_ends(sessions)
    decisions = primary.decisions.query("decision_status == 'scheduled'")
    scores = decisions.pivot(index="signal_date", columns="ticker", values="price_momentum_score")
    prices = snapshot.prices.pivot(index="date", columns="ticker", values=PRICE_BASIS)
    prices = prices.reindex(index=sessions)
    next_session = pd.Series(sessions[1:], index=sessions[:-1])
    regimes = prices[BENCHMARK].div(prices[BENCHMARK].shift(126)).sub(1)
    forwards, ics = [], []
    for horizon in HORIZONS:
        exit_lookup = pd.Series(month_ends[horizon:], index=month_ends[:-horizon]).map(next_session)
        for signal, row in scores.iterrows():
            entry, exit_date = next_session[signal], exit_lookup.get(signal)
            if pd.isna(exit_date):
                continue
            wealth = prices.loc[exit_date] / prices.loc[entry]
            relative = wealth[list(SECTORS)].div(wealth[BENCHMARK]).sub(1)
            regime = (
                "SPY trailing 126 sessions >= 0"
                if regimes[signal] >= 0
                else "SPY trailing 126 sessions < 0"
            )
            correlation = (
                row.rank().corr(relative.rank())
                if row.nunique() > 1 and relative.nunique() > 1
                else np.nan
            )
            info = {
                "signal_date": signal,
                "entry_session": entry,
                "exit_session": exit_date,
                "horizon_months": horizon,
                "regime": regime,
            }
            ranked = row.sort_index().sort_values(ascending=False, kind="stable").index
            equal_weight = relative.mean()
            ics.append(
                info
                | {
                    "rank_ic": correlation,
                    "sector_count": len(relative),
                    "equal_sector_relative_return": equal_weight,
                    "top_3_minus_equal": relative.loc[ranked[:3]].mean() - equal_weight,
                    "top_5_minus_equal": relative.loc[ranked[:5]].mean() - equal_weight,
                }
            )
            forwards.extend(
                info | {"ticker": ticker, "momentum_score": row[ticker], "relative_return": value}
                for ticker, value in relative.items()
            )
    info_columns = ["signal_date", "entry_session", "exit_session", "horizon_months", "regime"]
    return (
        pd.DataFrame(
            forwards, columns=[*info_columns, "ticker", "momentum_score", "relative_return"]
        ),
        pd.DataFrame(
            ics,
            columns=[
                *info_columns,
                "rank_ic",
                "sector_count",
                "equal_sector_relative_return",
                "top_3_minus_equal",
                "top_5_minus_equal",
            ],
        ),
    )


def _ic_inference(monthly: pd.DataFrame) -> pd.DataFrame:
    """Newey–West Bartlett HAC on the equally weighted time series of monthly ICs."""
    rows = []
    for horizon in HORIZONS:
        values = monthly.loc[monthly.horizon_months.eq(horizon)].set_index("signal_date").rank_ic
        if not values.empty:
            values.index = values.index.to_period("M")
            values = values.reindex(pd.period_range(values.index[0], values.index[-1], freq="M"))
        count = int(values.notna().sum())
        average = values.mean()
        # Include at least as many lags as the horizon to address overlap and
        # the extra lag induced by adjacent execution-close boundaries.
        lags = min(max(0, len(values) - 1), max(horizon, int(4 * (len(values) / 100) ** (2 / 9))))
        se = low = high = p_value = np.nan
        if count >= max(MIN_INFERENCE_MONTHS, 2 * lags + 1):
            residual = values.sub(average).fillna(0).to_numpy()
            variance = float(residual @ residual)
            for lag in range(1, lags + 1):
                variance += 2 * (1 - lag / (lags + 1)) * (residual[lag:] @ residual[:-lag])
            if variance > 0:
                se = sqrt(variance) / count
                low, high = average - 1.96 * se, average + 1.96 * se
                p_value = erfc(abs(average / se) / sqrt(2))
        rows.append(
            {
                "horizon_months": horizon,
                "observed_months": count,
                "mean_rank_ic": average,
                "hac_lags": lags,
                "hac_se": se,
                "ci_95_low": low,
                "ci_95_high": high,
                "p_value_two_sided_unadjusted": p_value,
            }
        )
    return pd.DataFrame(rows)


def run_price_study(path: str | Path) -> PriceStudy:
    """Run the same rules for common and dynamic universes at each fixed cost."""
    snapshot = load_price_only(path)
    cases = {}
    for universe in ("common", "dynamic"):
        for bps in ONE_WAY_BPS:
            cases[universe, bps] = _run_validated_backtest(
                snapshot,
                universe=universe,
                costs=CostAssumptions(commission_bps=bps),
            )
    primary = cases["common", 0]
    tables, rolling, sensitivity = [], [], []
    for (universe, bps), result in cases.items():
        basis = "gross" if bps == 0 else "net"
        table = result.metrics.loc[result.metrics.basis.eq(basis)].copy()
        table = table.assign(
            one_way_bps=bps,
            baseline_session=result.metadata["baseline_session"],
            last_session=result.metadata["last_session"],
        )
        tables.append(table)
        rolling.append(
            result.rolling_12m.loc[result.rolling_12m.basis.eq(basis)].assign(one_way_bps=bps)
        )
        paths = result.daily.pivot(index="session", columns="strategy", values=f"{basis}_nav")
        # Rebase for a same-window descriptive comparison. Dynamic holdings are
        # carried in from their earlier inception, where applicable.
        aligned = paths.loc[primary.metadata["baseline_session"] :]
        wealth = aligned.iloc[-1] / aligned.iloc[0]
        for strategy, growth in wealth.items():
            sensitivity.append(
                {
                    "universe_policy": universe,
                    "one_way_bps": bps,
                    "strategy": strategy,
                    "common_window_start": primary.metadata["baseline_session"],
                    "relative_vs_spy": growth / wealth["spy_buy_hold"] - 1,
                    "relative_vs_equal_weight": growth / wealth["equal_weight_sectors"] - 1,
                }
            )
    forward, monthly = _forward_diagnostics(snapshot, primary)
    forward_summary = monthly.groupby("horizon_months", as_index=False).agg(
        complete_months=("signal_date", "size"),
        mean_equal_sector_relative_return=("equal_sector_relative_return", "mean"),
        mean_top_3_minus_equal=("top_3_minus_equal", "mean"),
        mean_top_5_minus_equal=("top_5_minus_equal", "mean"),
    )
    regimes = monthly.groupby(["horizon_months", "regime"], as_index=False).agg(
        signal_months=("rank_ic", "size"),
        observed_ics=("rank_ic", "count"),
        mean_rank_ic=("rank_ic", "mean"),
    )
    return PriceStudy(
        snapshot,
        primary,
        pd.concat(tables, ignore_index=True),
        pd.concat(rolling, ignore_index=True),
        pd.DataFrame(sensitivity),
        forward,
        monthly,
        _ic_inference(monthly),
        forward_summary,
        regimes,
    )
