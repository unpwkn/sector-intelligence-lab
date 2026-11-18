"""Explicitly fictional, deterministic prices for offline Phase 5 verification."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from sectorlab.config import FIRST_SESSION
from sectorlab.market_data import UNIVERSE, PriceResponse, ingest
from sectorlab.market_data.pipeline import _calendar
from sectorlab.market_data.providers import FIELDS

from .engine import FIXTURE_PROVIDER


class _FixtureProvider:
    name = FIXTURE_PROVIDER
    price_basis = "adjusted_price_proxy"

    def fetch(self, ticker: str, start: str, end: str) -> PriceResponse:
        start_session, end_session = pd.Timestamp(start), pd.Timestamp(end)
        calendar = _calendar(start_session, end_session)
        sessions = calendar.sessions_in_range(start_session, end_session)
        position = UNIVERSE.index(ticker)
        index = np.arange(len(sessions))
        log_prices = 0.0001 * position * index + 0.08 * np.sin(index / 35 + position)
        prices = 100 * np.exp(log_prices)
        rows = [
            (session.strftime("%Y-%m-%d"), ticker, price, price, price, price, price, 0, 0, 1)
            for session, price in zip(sessions, prices)
            if session >= pd.Timestamp(FIRST_SESSION[ticker])
        ]
        raw = {"classification": "FICTIONAL SOFTWARE FIXTURE", "ticker": ticker, "rows": rows}
        return PriceResponse(pd.DataFrame(rows, columns=FIELDS), raw, datetime.now(UTC))


def make_fixture_snapshot(
    output: Path,
    *,
    start: str = "2017-01-03",
    end: str = "2021-01-15",
) -> Path:
    """Build a standalone Phase 1 snapshot from modeled prices with real session labels."""
    return ingest(_FixtureProvider(), start, end, output)
