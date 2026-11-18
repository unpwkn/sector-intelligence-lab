"""Calendar-aware close-to-close simulation using the unchanged Phase 2 score."""

from __future__ import annotations

import importlib.metadata
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from exchange_calendars import ExchangeCalendar

from sectorlab.config import BENCHMARK, FIRST_SESSION, PRICE_MOMENTUM_WEIGHTS, SECTORS
from sectorlab.market_data import PriceSnapshot, ValidationError, load_snapshot
from sectorlab.market_data.pipeline import UNIVERSE, _calendar
from sectorlab.market_data.snapshot import PRICE_BASIS
from sectorlab.price_momentum import WINDOWS, price_momentum_history

from .metrics import performance

RETROSPECTIVE = "RETROSPECTIVE_ADJUSTED_PRICE_PROXY"
SYNTHETIC = "SYNTHETIC_OFFLINE_FIXTURE"
FIXTURE_PROVIDER = "synthetic_phase5_fixture"
STRATEGIES = ("spy_buy_hold", "equal_weight_sectors", "top_3_momentum", "top_5_momentum")


@dataclass(frozen=True)
class CostAssumptions:
    """Basis points per traded dollar. Spread is the full quoted bid-ask width."""

    commission_bps: float = 0.0
    full_spread_bps: float = 0.0
    slippage_bps: float = 0.0

    def __post_init__(self) -> None:
        if any(not np.isfinite(v) or v < 0 for v in asdict(self).values()):
            raise ValueError("Transaction cost inputs must be finite and nonnegative")
        if self.one_way_rate >= 1:
            raise ValueError("Combined one-way transaction cost must be below 10,000 bps")

    @property
    def one_way_rate(self) -> float:
        return (self.commission_bps + self.full_spread_bps / 2 + self.slippage_bps) / 10_000


ZERO_COSTS = CostAssumptions()


@dataclass(frozen=True)
class BacktestResult:
    metadata: dict
    decisions: pd.DataFrame
    trades: pd.DataFrame
    daily: pd.DataFrame
    metrics: pd.DataFrame
    monthly: pd.DataFrame
    rolling_12m: pd.DataFrame


def _check_coverage(snapshot: PriceSnapshot) -> None:
    # Phase 1 permits explicitly recorded gaps. A performance path cannot bridge them.
    gaps = [
        f"{ticker}: {len(dates)} missing (first {dates[0]})"
        for ticker, coverage in snapshot.manifest["coverage"].items()
        if (dates := coverage["missing_sessions"])
    ]
    if gaps:
        raise ValidationError(
            "Backtest requires complete listed-session coverage. " + "; ".join(gaps)
        )


def _month_ends(sessions: pd.DatetimeIndex) -> tuple[ExchangeCalendar, pd.DatetimeIndex]:
    last_month_end = sessions[-1] + pd.offsets.MonthEnd(0)
    calendar = _calendar(sessions[0], last_month_end)
    full_schedule = calendar.sessions_in_range(sessions[0], last_month_end)
    periods = full_schedule.to_period("M")
    month_ends = full_schedule[~periods.duplicated(keep="last")].intersection(sessions)
    return calendar, month_ends


def _decision_log(
    snapshot: PriceSnapshot,
    month_ends: pd.DatetimeIndex,
    calendar: ExchangeCalendar,
    universe: str,
) -> pd.DataFrame:
    history = price_momentum_history(snapshot, asof=month_ends[-1].strftime("%Y-%m-%d"))
    decisions = history.loc[history["signal_date"].isin(month_ends)].copy()
    decisions["decision_close_utc"] = decisions["signal_date"].map(calendar.session_close)
    next_sessions = pd.Series(snapshot.sessions[1:], index=snapshot.sessions[:-1])
    decisions["execution_session"] = decisions["signal_date"].map(next_sessions)
    decisions["execution_close_utc"] = pd.to_datetime(
        decisions["execution_session"].dropna().map(calendar.session_close), utc=True
    )
    decisions["eligible"] = decisions["price_momentum_score"].notna()
    required = len(SECTORS) if universe == "common" else 5
    has_universe = decisions["peer_count"] >= required
    can_execute = decisions["execution_session"].notna()
    decisions["decision_status"] = np.select(
        [~has_universe, ~can_execute],
        ["insufficient_universe", "no_execution_session"],
        default="scheduled",
    )
    return decisions


def _targets(eligible: pd.DataFrame) -> dict[str, list[str]]:
    # The Phase 2 score uses average ranks. Break equal scores by ticker for fixed top N.
    ranked = eligible.sort_values(["price_momentum_score", "ticker"], ascending=[False, True])[
        "ticker"
    ].tolist()
    return dict(zip(STRATEGIES, ([BENCHMARK], sorted(ranked), ranked[:3], ranked[:5])))


def _rebalance(
    holdings: np.ndarray,
    target: np.ndarray,
    cost_rate: float,
) -> tuple[np.ndarray, float, float, float]:
    """Allocate exact target weights after paying costs from strategy NAV.

    Positions include cash in the final slot. Solve for the fraction of pretrade
    NAV remaining after cost, where ETF buys and sells both contribute to cost.
    """
    nav = holdings.sum()
    before = holdings / nav
    scale = 1.0
    if cost_rate:
        low, high = 0.0, 1.0
        for _ in range(52):
            candidate = (low + high) / 2
            traded = np.abs(target[:-1] * candidate - before[:-1]).sum()
            if candidate + cost_rate * traded > 1:
                high = candidate
            else:
                low = candidate
        scale = (low + high) / 2

    traded = np.abs(target[:-1] * scale - before[:-1]).sum()
    cost_fraction = cost_rate * traded
    turnover = np.abs(target - before).sum() / 2
    return target * (nav * (1 - cost_fraction)), turnover, traded, cost_fraction


def _simulate(
    snapshot: PriceSnapshot,
    scheduled: pd.DataFrame,
    costs: CostAssumptions,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    baseline = scheduled["signal_date"].min()
    sessions = snapshot.sessions[snapshot.sessions >= baseline]
    prices = snapshot.prices.pivot(index="date", columns="ticker", values=PRICE_BASIS)
    prices = prices.reindex(index=sessions, columns=UNIVERSE).astype(float)
    # After the strict coverage gate, undefined ratios are confined to baseline and
    # pre-listing/first-listed sessions. No position can be held on those intervals.
    market_moves = prices.div(prices.shift()).fillna(1.0)
    if not np.isfinite(market_moves.to_numpy()).all() or market_moves.le(0).any().any():
        raise ValidationError("Adjusted-price session ratios must be finite and positive")
    market_moves["cash"] = 1.0

    schedule = {}
    for execution, decision in scheduled.groupby("execution_session", sort=True):
        schedule[execution] = (decision.iloc[0], _targets(decision.loc[decision["eligible"]]))

    first_execution = min(schedule)
    daily, trades = [], []
    instruments = list(market_moves.columns)
    for strategy in STRATEGIES:
        gross = np.zeros(len(instruments))
        gross[-1] = 1.0
        net = gross.copy()
        previous_gross = previous_net = 1.0
        for session, moves in zip(sessions, market_moves.to_numpy()):
            # First earn the interval ending at this close using previous holdings.
            gross = gross * moves
            net = net * moves
            # Then trade at the modeled close. New positions participate from next session.
            if session in schedule and (strategy != STRATEGIES[0] or session == first_execution):
                decision, targets = schedule[session]
                selected = targets[strategy]
                target = np.array(
                    [1 / len(selected) if ticker in selected else 0.0 for ticker in instruments]
                )
                gross, gross_turnover, gross_traded, _ = _rebalance(gross, target, 0.0)
                net, net_turnover, net_traded, cost = _rebalance(net, target, costs.one_way_rate)
                trades.append(
                    {
                        "strategy": strategy,
                        "signal_date": decision["signal_date"],
                        "decision_close_utc": decision["decision_close_utc"],
                        "execution_session": session,
                        "execution_close_utc": decision["execution_close_utc"],
                        "targets": " ".join(selected),
                        "target_weight_per_etf": 1 / len(selected),
                        "gross_turnover": gross_turnover,
                        "net_turnover": net_turnover,
                        "gross_etf_notional_fraction": gross_traded,
                        "net_etf_notional_fraction": net_traded,
                        "cost_fraction_of_pretrade_nav": cost,
                    }
                )
            gross_nav, net_nav = gross.sum(), net.sum()
            daily.append(
                {
                    "session": session,
                    "strategy": strategy,
                    "gross_nav": gross_nav,
                    "net_nav": net_nav,
                    "gross_return": gross_nav / previous_gross - 1
                    if session != baseline
                    else np.nan,
                    "net_return": net_nav / previous_net - 1 if session != baseline else np.nan,
                }
            )
            previous_gross, previous_net = gross_nav, net_nav
    return pd.DataFrame(daily), pd.DataFrame(trades)


def run_backtest(
    snapshot_path: str | Path,
    *,
    universe: str = "common",
    costs: CostAssumptions = ZERO_COSTS,
    annual_risk_free: float = 0.0,
    turnover_convention: str = "half_l1",
) -> BacktestResult:
    """Run one universe policy, using only the verified saved snapshot as input."""
    if universe not in {"common", "dynamic"}:
        raise ValueError("Universe must be common or dynamic")
    if turnover_convention not in {"half_l1", "etf_notional"}:
        raise ValueError("Turnover convention must be half_l1 or etf_notional")
    if not np.isfinite(annual_risk_free) or annual_risk_free <= -1:
        raise ValueError("Annual risk-free rate must be finite and greater than -1")

    snapshot = load_snapshot(snapshot_path)
    _check_coverage(snapshot)
    calendar, month_ends = _month_ends(snapshot.sessions)
    if month_ends.empty:
        raise ValidationError("Snapshot contains no completed month-end trading session")
    decisions = _decision_log(snapshot, month_ends, calendar, universe)
    scheduled = decisions.loc[decisions["decision_status"].eq("scheduled")]
    if scheduled.empty:
        raise ValidationError(
            f"No executable {universe} month-end signal. Supply sufficient contiguous "
            "Phase 2 lookback history and at least the next trading session."
        )
    daily, trades = _simulate(snapshot, scheduled, costs)
    metrics, monthly, rolling = performance(
        daily, trades, month_ends, annual_risk_free, turnover_convention
    )
    manifest = snapshot.manifest
    classification = SYNTHETIC if manifest["provider"] == FIXTURE_PROVIDER else RETROSPECTIVE
    metadata = {
        "schema_version": "1.0",
        "research_classification": classification,
        "point_in_time_history": False,
        "total_return_verified": False,
        "snapshot_manifest_sha256": snapshot.manifest_sha256,
        "snapshot_manifest": manifest,
        "runtime_versions": {
            name: importlib.metadata.version(name)
            for name in ("pandas", "numpy", "exchange-calendars")
        },
        "universe_policy": universe,
        "sector_universe": list(SECTORS),
        "benchmark": BENCHMARK,
        "first_trading_sessions": FIRST_SESSION.copy(),
        "momentum_weights": PRICE_MOMENTUM_WEIGHTS.copy(),
        "momentum_session_offsets": WINDOWS.copy(),
        "decision_convention": "Last XNYS session close of each calendar month",
        "execution_convention": "Next XNYS session close; new holdings effective afterward",
        "execution_lag_sessions": 1,
        "cost_assumptions": asdict(costs) | {"one_way_rate": costs.one_way_rate},
        "annual_risk_free": annual_risk_free,
        "turnover_convention": turnover_convention,
        "baseline_session": scheduled["signal_date"].min().strftime("%Y-%m-%d"),
        "last_session": snapshot.sessions[-1].strftime("%Y-%m-%d"),
    }
    for frame in (decisions, trades, daily, metrics, monthly, rolling):
        frame.insert(0, "research_classification", classification)
        frame.insert(1, "universe_policy", universe)
        if "snapshot_manifest_sha256" not in frame:
            frame.insert(2, "snapshot_manifest_sha256", snapshot.manifest_sha256)
    return BacktestResult(metadata, decisions, trades, daily, metrics, monthly, rolling)
