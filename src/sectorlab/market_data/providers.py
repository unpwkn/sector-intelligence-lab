"""Provider boundary and documented Alpha Vantage daily-adjusted adapter."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd


class ProviderError(RuntimeError):
    """Unavailable or malformed vendor data; never substitute invented values."""


@dataclass(frozen=True)
class PriceResponse:
    records: pd.DataFrame
    raw: dict
    retrieved_at: datetime


class MarketDataProvider(Protocol):
    """Return daily records with FIELDS below, plus raw evidence and retrieval time.

    Dates are exchange-local session labels; price basis must match the adapter's
    documented semantics. Historical retrieval time is not historical availability.
    """

    name: str
    price_basis: str

    def fetch(self, ticker: str, start: str, end: str) -> PriceResponse: ...


FIELDS = [
    "date",
    "ticker",
    "open",
    "high",
    "low",
    "close",
    "adjusted_price_proxy",
    "volume",
    "dividend_amount",
    "split_coefficient",
]


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key in provider response")
        result[key] = value
    return result


def _download(url: str) -> dict:
    with urlopen(url, timeout=30) as response:
        return json.load(response, object_pairs_hook=_unique_object)


class AlphaVantageProvider:
    """TIME_SERIES_DAILY_ADJUSTED, full history (premium entitlement required).

    Transport is injectable for offline tests. No retries: rate-limit and entitlement
    failures are surfaced to the caller, without logging credential-bearing URLs.
    """

    name = "alpha_vantage"
    price_basis = "adjusted_price_proxy"

    def __init__(self, api_key: str | None = None, *, transport: Callable[[str], dict] = _download):
        self._api_key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY")
        if not self._api_key:
            raise ProviderError("Set ALPHAVANTAGE_API_KEY in your environment")
        self._transport = transport

    def fetch(self, ticker: str, start: str, end: str) -> PriceResponse:
        query = urlencode(
            {
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": ticker,
                "outputsize": "full",
                "datatype": "json",
                "apikey": self._api_key,
            }
        )
        try:
            raw = self._transport("https://www.alphavantage.co/query?" + query)
        except Exception:  # noqa: BLE001 - transport errors must not expose credential URLs
            # Vendor/HTTP exception strings can contain the API key or full URL.
            raise ProviderError(f"Alpha Vantage request failed for {ticker}") from None
        if not isinstance(raw, dict) or any(
            k in raw for k in ("Error Message", "Note", "Information")
        ):
            raise ProviderError(f"Alpha Vantage rejected {ticker}: check entitlement/rate limits")
        series = raw.get("Time Series (Daily)")
        meta = raw.get("Meta Data", {})
        if (
            not isinstance(series, dict)
            or not series
            or not isinstance(meta, dict)
            or meta.get("2. Symbol") != ticker
        ):
            raise ProviderError(f"Missing history or mismatched symbol for {ticker}")
        columns = [
            "1. open",
            "2. high",
            "3. low",
            "4. close",
            "5. adjusted close",
            "6. volume",
            "7. dividend amount",
            "8. split coefficient",
        ]
        try:
            rows = [
                {
                    "date": day,
                    "ticker": ticker,
                    **dict(zip(FIELDS[2:], [values[c] for c in columns])),
                }
                for day, values in series.items()
                if start <= day <= end
            ]
        except (KeyError, TypeError):
            raise ProviderError(f"Malformed daily fields for {ticker}") from None
        return PriceResponse(pd.DataFrame(rows, columns=FIELDS), raw, datetime.now(UTC))
