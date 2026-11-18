"""Performance statistics on aligned strategy and SPY close-to-close paths."""

from __future__ import annotations

import numpy as np
import pandas as pd

SESSIONS_PER_YEAR = 252


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if np.isfinite(denominator) and denominator > 1e-14 else np.nan


def performance(
    daily: pd.DataFrame,
    executions: pd.DataFrame,
    month_ends: pd.DatetimeIndex,
    annual_risk_free: float,
    turnover_convention: str = "half_l1",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Use matched return bases and true calendar month-end observations."""
    monthly_rows, rolling_rows, metrics = [], [], []
    turnover_field = "turnover" if turnover_convention == "half_l1" else "etf_notional_fraction"
    benchmark = daily.loc[daily["strategy"].eq("spy_buy_hold")].set_index("session")
    risk_free_daily = np.expm1(np.log1p(annual_risk_free) / SESSIONS_PER_YEAR)

    for strategy, path in daily.groupby("strategy", sort=False):
        path = path.set_index("session")
        trades = executions.loc[executions["strategy"].eq(strategy)]
        months = month_ends.intersection(path.index)
        years = (path.index[-1] - path.index[0]).days / 365.2425
        for basis in ("gross", "net"):
            nav = path[f"{basis}_nav"]
            returns = path[f"{basis}_return"].iloc[1:]
            benchmark_nav = benchmark[f"{basis}_nav"]
            active = returns - benchmark[f"{basis}_return"].reindex(returns.index)
            vol = returns.std() * np.sqrt(SESSIONS_PER_YEAR)
            tracking = active.std() * np.sqrt(SESSIONS_PER_YEAR)
            monthly = nav.reindex(months).pct_change().iloc[1:]
            spy_monthly = benchmark_nav.reindex(months).pct_change().iloc[1:]
            down = spy_monthly.lt(0)
            downside_capture = np.nan
            if down.any():
                spy_down = np.expm1(np.log1p(spy_monthly[down]).mean())
                strategy_down = np.expm1(np.log1p(monthly[down]).mean())
                downside_capture = strategy_down / spy_down

            turnover = trades[f"{basis}_{turnover_field}"].sum()
            metrics.append(
                {
                    "strategy": strategy,
                    "basis": basis,
                    "cagr": (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1,
                    "annualized_volatility": vol,
                    "sharpe_ratio": _ratio(
                        (returns.mean() - risk_free_daily) * SESSIONS_PER_YEAR, vol
                    ),
                    "max_drawdown": (nav / nav.cummax() - 1).min(),
                    "information_ratio_vs_spy": _ratio(active.mean() * SESSIONS_PER_YEAR, tracking),
                    "tracking_error_vs_spy": tracking,
                    "turnover_convention": turnover_convention,
                    "portfolio_turnover": turnover,
                    "annualized_portfolio_turnover": turnover / years,
                    "win_rate": monthly.gt(0).mean(),
                    "win_rate_vs_spy": (monthly > spy_monthly).mean(),
                    "downside_capture_vs_spy": downside_capture,
                    "daily_return_count": len(returns),
                    "complete_month_count": len(monthly),
                    "spy_down_month_count": int(down.sum()),
                }
            )
            months_frame = pd.DataFrame(
                {
                    "strategy": strategy,
                    "basis": basis,
                    "strategy_return": monthly,
                    "spy_return": spy_monthly,
                }
            ).rename_axis("session")
            monthly_rows.append(months_frame.reset_index())
            relative_nav = nav.reindex(months) / benchmark_nav.reindex(months)
            rolling = relative_nav.div(relative_nav.shift(12)).sub(1)
            rolling_rows.append(
                rolling.rename("relative_return")
                .rename_axis("session")
                .reset_index()
                .assign(strategy=strategy, basis=basis)
            )
    return (
        pd.DataFrame(metrics),
        pd.concat(monthly_rows, ignore_index=True),
        pd.concat(rolling_rows, ignore_index=True),
    )
