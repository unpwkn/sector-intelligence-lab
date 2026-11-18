"""Validate exchange-session data and publish immutable, auditable local snapshots."""

from __future__ import annotations

import csv
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

from sectorlab.config import BENCHMARK, ETF_INCEPTION, FIRST_SESSION, SECTORS

from .local_csv import CSV_ENDPOINT, LocalCsv
from .providers import FIELDS, MarketDataProvider

UNIVERSE = (*SECTORS, BENCHMARK)
CALENDAR_LABEL = "XNYS (US equity session proxy)"
RETROSPECTIVE = "RETROSPECTIVE_ADJUSTED_PRICE_PROXY"
SYNTHETIC = "SYNTHETIC_OFFLINE_FIXTURE"
FIXTURE_PROVIDER = "synthetic_phase5_fixture"


def _research_classification(manifest: dict) -> str:
    if manifest["schema_version"] == "1.1":
        synthetic = manifest["provenance"]["data_kind"] == "synthetic"
    else:
        synthetic = manifest["provider"] == FIXTURE_PROVIDER
    return SYNTHETIC if synthetic else RETROSPECTIVE


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
    data["date"] = pd.to_datetime(data["date"].map(_date))
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


def _calendar(start: pd.Timestamp, end: pd.Timestamp) -> xcals.ExchangeCalendar:
    return xcals.get_calendar(
        "XNYS", start=start - pd.Timedelta(days=10), end=end + pd.Timedelta(days=10)
    )


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
    if provider.price_basis != "adjusted_price_proxy":
        raise ValidationError("Market data requires explicit adjusted_price_proxy semantics")
    return _publish(provider, start, end, output, allow_missing=allow_missing, now=now)


def import_csv(
    observations: Path,
    provenance: Path,
    start: str,
    end: str,
    output: Path,
    *,
    allow_missing: bool = False,
    now: datetime | None = None,
) -> Path:
    """Validate a local export and preserve the original CSV and provenance bytes."""
    try:
        source = LocalCsv.read(Path(observations), Path(provenance))
    except (csv.Error, ValueError, OverflowError) as exc:
        raise ValidationError(f"Invalid local CSV input: {exc}") from exc
    return _publish(source, start, end, output, allow_missing=allow_missing, now=now)


def _csv_groups(source: LocalCsv) -> dict[str, pd.DataFrame]:
    if not source.records["ticker"].isin(UNIVERSE).all():
        raise ValidationError("CSV contains an unexpected ticker")
    return dict(iter(source.records.groupby("ticker")))


def _publish(
    source: MarketDataProvider | LocalCsv,
    start: str,
    end: str,
    output: Path,
    *,
    allow_missing: bool,
    now: datetime | None,
) -> Path:
    start_date, end_date = _date(start), _date(end)
    if start_date > end_date:
        raise ValidationError("start must be <= end")
    now = now or datetime.now(UTC)
    now_ts = pd.Timestamp(now)
    if now_ts.tzinfo is None:
        raise ValidationError("now must be timezone-aware")
    calendar = _calendar(start_date, end_date)
    sessions = calendar.sessions_in_range(start_date, end_date)
    if sessions.empty:
        raise ValidationError("Requested interval has no trading sessions")
    last_close = calendar.session_close(sessions[-1])
    if last_close > now_ts:
        raise ValidationError("Requested interval includes future or unclosed sessions")
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    local = isinstance(source, LocalCsv)
    if local:
        groups = _csv_groups(source)
        csv_retrieved = pd.Timestamp(source.retrieved_at)
        if not last_close <= csv_retrieved <= now_ts:
            raise ValidationError("CSV retrieval must fall between last session close and now")
        provider = source.provenance["source"]
        endpoint = CSV_ENDPOINT
    else:
        provider = source.name
        endpoint = "TIME_SERIES_DAILY_ADJUSTED" if provider == "alpha_vantage" else None

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ingest-", dir=output.parent))
    frames, coverage, retrievals = [], {}, {}
    try:
        (stage / "raw").mkdir()
        if local:
            for name, content in source.evidence.items():
                (stage / name).write_bytes(content)
        for ticker in UNIVERSE:
            if local:
                records = groups.get(ticker, source.records.iloc[:0])
                retrieved = csv_retrieved
            else:
                response = source.fetch(ticker, start, end)
                records = response.records
                retrieved = pd.Timestamp(response.retrieved_at)
                if retrieved.tzinfo is None or pd.isna(retrieved):
                    raise ValidationError("Provider retrieval timestamp must be timezone-aware")
                (stage / "raw" / f"{ticker}.json").write_text(
                    json.dumps(response.raw, sort_keys=True, allow_nan=False) + "\n"
                )
            frame, coverage[ticker] = validate(
                records, ticker, sessions, allow_missing=allow_missing
            )
            frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
            retrievals[ticker] = retrieved.isoformat()
            frame["retrieved_at"] = retrieved.isoformat()
            frame["provider"] = provider
            frame["currency"] = "USD"
            frame["price_basis"] = "adjusted_price_proxy"
            frame["total_return_verified"] = False
            if not frame.empty:
                frames.append(frame)
        if not frames:
            raise ValidationError("No observations in requested universe/interval")
        combined = pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"])
        combined.to_csv(stage / "prices.csv", index=False)
        files = sorted(p for p in stage.rglob("*") if p.is_file())
        manifest = {
            "schema_version": "1.1" if local else "1.0",
            "provider": provider,
            "endpoint": endpoint,
            "start": str(start_date.date()),
            "end": str(end_date.date()),
            "universe": list(UNIVERSE),
            "calendar": CALENDAR_LABEL,
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
                p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in files
            },
        }
        if local:
            manifest["provenance"] = source.provenance
            manifest["fund_inception"] = ETF_INCEPTION
        manifest["research_classification"] = _research_classification(manifest)
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        # rename within the same filesystem; no partially populated snapshot exposed.
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return output
