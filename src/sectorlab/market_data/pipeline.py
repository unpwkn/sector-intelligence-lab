"""Validate exchange-session data and publish immutable, auditable local snapshots."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from sectorlab.config import BENCHMARK, SECTORS

from .providers import FIELDS, MarketDataProvider

UNIVERSE = (*SECTORS, BENCHMARK)
# First trading sessions, not backfilled sector/index history.
FIRST_SESSION = dict.fromkeys(SECTORS, "1998-12-22") | {
    "SPY": "1993-01-29",
    "XLRE": "2015-10-08",
    "XLC": "2018-06-19",
}


class ValidationError(ValueError):
    """Input violates the local daily-price contract."""


def _date(value: str) -> pd.Timestamp:
    try:
        result = pd.Timestamp(value)
        if (
            not isinstance(value, str)
            or result.strftime("%Y-%m-%d") != value
            or result.tzinfo is not None
            or pd.isna(result)
            or result != result.normalize()
        ):
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ValidationError("Use timezone-free session dates YYYY-MM-DD") from None


def validate(
    records: pd.DataFrame, ticker: str, sessions: pd.DatetimeIndex, *, allow_missing: bool = False
) -> tuple[pd.DataFrame, dict]:
    """Reject bad rows; collapse exact duplicates, reject conflicting duplicates.

    Missing sessions are never filled. An explicit override allows an incomplete
    snapshot with a full list of gaps. Pre-launch dates are not expected sessions.
    """
    if not set(FIELDS).issubset(records.columns):
        raise ValidationError(f"Missing price columns for {ticker}")
    data = records[FIELDS].copy()
    data["date"] = data["date"].map(_date)
    if not data["ticker"].eq(ticker).all():
        raise ValidationError(f"Unexpected symbol in {ticker}")
    expected = sessions[sessions >= pd.Timestamp(FIRST_SESSION[ticker])]
    if not data["date"].isin(expected).all():
        raise ValidationError(f"Non-session, pre-launch or out-of-range date for {ticker}")
    for col in FIELDS[2:]:
        try:
            data[col] = pd.to_numeric(data[col], errors="raise")
        except (ValueError, TypeError):
            raise ValidationError(f"Invalid numeric {col} for {ticker}") from None
    if not np.isfinite(data[FIELDS[2:]].to_numpy(dtype=float)).all():
        raise ValidationError(f"Non-finite values for {ticker}")
    if (
        data[["open", "high", "low", "close", "adjusted_price_proxy", "split_coefficient"]]
        .le(0)
        .any()
        .any()
        or data[["volume", "dividend_amount"]].lt(0).any().any()
        or data["volume"].mod(1).ne(0).any()
    ):
        raise ValidationError(f"Invalid price, action or volume for {ticker}")
    if (
        data["low"].gt(data[["open", "close", "high"]].min(axis=1)).any()
        or data["high"].lt(data[["open", "close", "low"]].max(axis=1)).any()
    ):
        raise ValidationError(f"Inconsistent OHLC for {ticker}")
    before = len(data)
    data = data.drop_duplicates()
    if data["date"].duplicated().any():
        raise ValidationError(f"Conflicting duplicate session for {ticker}")
    missing = expected.difference(pd.DatetimeIndex(data["date"]))
    if len(missing) and not allow_missing:
        raise ValidationError(
            f"{ticker}: {len(missing)} missing sessions; first {missing[0].date()}"
        )
    # An absent active symbol is an error even under the missing-session override.
    if len(expected) and data.empty:
        raise ValidationError(f"No observations for active symbol {ticker}")
    return data.sort_values("date"), {
        "expected_sessions": len(expected),
        "observations": len(data),
        "exact_duplicates_removed": before - len(data),
        "missing_sessions": [d.strftime("%Y-%m-%d") for d in missing],
        "first_trading_session": FIRST_SESSION[ticker],
    }


def ingest(
    provider: MarketDataProvider,
    start: str,
    end: str,
    output: Path,
    *,
    allow_missing: bool = False,
    now: datetime | None = None,
) -> Path:
    """Fetch all 12 instruments; publish only after every response validates.

    output is a new snapshot directory. Existing paths are never overwritten.
    The NYSE calendar is the US equity session proxy for these NYSE Arca ETFs.
    """
    start_date, end_date = _date(start), _date(end)
    if start_date > end_date:
        raise ValidationError("start must be <= end")
    now = now or datetime.now(UTC)
    now_ts = pd.Timestamp(now)
    if now_ts.tzinfo is None:
        raise ValidationError("now must be timezone-aware")
    if provider.price_basis != "adjusted_price_proxy":
        raise ValidationError("Phase 1 requires explicit adjusted_price_proxy semantics")
    calendar = xcals.get_calendar(
        "XNYS", start=start_date - pd.Timedelta(days=10), end=end_date + pd.Timedelta(days=10)
    )
    sessions = calendar.sessions_in_range(start_date, end_date)
    if sessions.empty:
        raise ValidationError("Requested interval has no trading sessions")
    if any(calendar.session_close(s) > now_ts for s in sessions):
        raise ValidationError("Requested interval includes future or unclosed sessions")
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ingest-", dir=output.parent))
    frames, coverage, retrievals = [], {}, {}
    try:
        (stage / "raw").mkdir()
        for ticker in UNIVERSE:
            response = provider.fetch(ticker, start, end)
            retrieved = pd.Timestamp(response.retrieved_at)
            if retrieved.tzinfo is None or pd.isna(retrieved):
                raise ValidationError("Provider retrieval timestamp must be timezone-aware")
            frame, coverage[ticker] = validate(
                response.records, ticker, sessions, allow_missing=allow_missing
            )
            frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
            retrievals[ticker] = retrieved.isoformat()
            frame["retrieved_at"] = retrieved.isoformat()
            frame["provider"] = provider.name
            frame["currency"] = "USD"
            frame["price_basis"] = "adjusted_price_proxy"
            frame["total_return_verified"] = False
            frames.append(frame)
            (stage / "raw" / f"{ticker}.json").write_text(
                json.dumps(response.raw, sort_keys=True, allow_nan=False) + "\n"
            )
        combined = pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"])
        if combined.empty:
            raise ValidationError("No observations in requested universe/interval")
        combined.to_csv(stage / "prices.csv", index=False)
        files = sorted(p for p in stage.rglob("*") if p.is_file())
        manifest = {
            "schema_version": "1.0",
            "provider": provider.name,
            "endpoint": "TIME_SERIES_DAILY_ADJUSTED" if provider.name == "alpha_vantage" else None,
            "start": str(start_date.date()),
            "end": str(end_date.date()),
            "universe": list(UNIVERSE),
            "calendar": "XNYS (US equity session proxy)",
            "price_basis": "adjusted_price_proxy",
            "total_return_verified": False,
            "point_in_time_history": False,
            "retrievals": retrievals,
            "allow_missing": allow_missing,
            "coverage": coverage,
            "created_at": datetime.now(UTC).isoformat(),
            "versions": {
                p: importlib.metadata.version(p) for p in ("pandas", "numpy", "exchange-calendars")
            },
            "sha256": {
                str(p.relative_to(stage)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
            },
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        # rename within the same filesystem; no partially populated snapshot exposed.
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return output
