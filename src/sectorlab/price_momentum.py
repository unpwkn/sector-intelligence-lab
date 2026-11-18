"""Retrospective price-only diagnostics on verified Phase 1 snapshot files."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import BENCHMARK, PRICE_MOMENTUM_WEIGHTS, SECTORS
from .market_data import PriceSnapshot
from .market_data.pipeline import FIRST_SESSION, UNIVERSE, _date
from .market_data.snapshot import PRICE_BASIS

# Offsets in XNYS sessions, inclusive endpoints.
WINDOWS = {"rs_6_1": (126, 21), "rs_12_1": (252, 21), "rs_3m": (63, 0)}


def price_momentum_history(
    snapshot: PriceSnapshot, *, asof: str | None = None
) -> pd.DataFrame:
    """Return one row per session/sector up to the inclusive session-date cutoff.

    Each component requires a complete pair of price windows on the shared
    calendar. Ranking uses the same complete-case peer set for all components.
    Dates label observations in one retrospective vendor vintage.
    """
    sessions = snapshot.sessions
    if asof is not None:
        sessions = sessions[sessions <= _date(asof)]

    # Keep calendar slots even when no instrument reported a price that day.
    prices = snapshot.prices.loc[snapshot.prices["date"].isin(sessions)]
    panel = prices.pivot(index="date", columns="ticker", values=PRICE_BASIS)
    panel = panel.reindex(index=sessions, columns=UNIVERSE).astype(float)
    log_prices = np.log(panel)
    relative_log_prices = log_prices[list(SECTORS)].sub(log_prices[BENCHMARK], axis=0)
    inception_history = pd.DataFrame(
        {ticker: sessions >= pd.Timestamp(FIRST_SESSION[ticker]) for ticker in SECTORS},
        index=sessions,
    )

    output = {}
    for component, (start, end) in WINDOWS.items():
        enough_history = inception_history.shift(start, fill_value=False)
        complete_window = (
            relative_log_prices.rolling(window=start - end + 1).count()
            .eq(start - end + 1).shift(end, fill_value=False)
        )
        output[component] = (
            relative_log_prices.shift(end) - relative_log_prices.shift(start)
        ).where(enough_history & complete_window)
        output[f"{component}_status"] = pd.DataFrame(
            np.select(
                [~enough_history.to_numpy(), ~complete_window.to_numpy()],
                ["insufficient_history", "missing_observations"],
                default="complete",
            ),
            index=sessions,
            columns=list(SECTORS),
        )

    eligible = output["rs_6_1"].notna() & output["rs_12_1"].notna() & output["rs_3m"].notna()
    peer_count = eligible.sum(axis=1)
    denominator = (peer_count - 1).where(peer_count >= 2)
    for component in PRICE_MOMENTUM_WEIGHTS:
        ranks = output[component].where(eligible).rank(axis=1, method="average")
        output[f"{component}_percentile"] = (ranks - 1).div(denominator, axis=0) * 100
    score = sum(
        output[f"{component}_percentile"] * weight
        for component, weight in PRICE_MOMENTUM_WEIGHTS.items()
    )
    # Tie equivalent weighted ranks despite floating-point summation noise.
    score = score.round(12)
    output["price_momentum_score"] = score
    output["price_momentum_rank"] = score.rank(axis=1, ascending=False, method="min")
    output["relative_performance_3m"] = np.expm1(output["rs_3m"])
    output["status"] = pd.DataFrame(
        np.where(score.notna(), "complete", "insufficient_data"),
        index=sessions,
        columns=list(SECTORS),
    )
    output["peer_count"] = pd.DataFrame(
        {ticker: peer_count for ticker in SECTORS}, index=sessions
    )

    index = pd.MultiIndex.from_product([sessions, SECTORS], names=["signal_date", "ticker"])
    history = pd.DataFrame(
        {key: value.to_numpy().ravel() for key, value in output.items()}, index=index
    ).reset_index()
    history["provider"] = snapshot.manifest["provider"]
    history["price_basis"] = PRICE_BASIS
    history["snapshot_manifest_sha256"] = snapshot.manifest_sha256
    return history
