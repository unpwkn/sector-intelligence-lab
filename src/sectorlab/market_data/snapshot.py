"""Read a Phase 1 snapshot without requesting or reconstructing vendor data."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .pipeline import CALENDAR_LABEL, UNIVERSE, ValidationError, _calendar, _date, validate
from .providers import FIELDS, _unique_object

PRICE_BASIS = "adjusted_price_proxy"
METADATA_COLUMNS = ["retrieved_at", "provider", "currency", "price_basis", "total_return_verified"]


@dataclass(frozen=True)
class PriceSnapshot:
    """Validated normalized rows, expected session grid and original provenance."""

    prices: pd.DataFrame
    sessions: pd.DatetimeIndex
    manifest: dict
    manifest_sha256: str


def _aware_timestamp(value: object, label: str) -> pd.Timestamp:
    try:
        if isinstance(value, str):
            timestamp = pd.Timestamp(value)
            if pd.notna(timestamp) and timestamp.tzinfo is not None:
                return timestamp
    except (ValueError, OverflowError):
        pass
    raise ValidationError(f"Invalid timezone-aware {label}")


def _read_snapshot(path: Path) -> PriceSnapshot:
    manifest_bytes = (path / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes, object_pairs_hook=_unique_object)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0":
        raise ValidationError("Expected a Phase 1 snapshot manifest (schema 1.0)")
    if (
        manifest.get("universe") != list(UNIVERSE)
        or manifest.get("calendar") != CALENDAR_LABEL
        or manifest.get("price_basis") != PRICE_BASIS
        or manifest.get("total_return_verified") is not False
        or manifest.get("point_in_time_history") is not False
        or type(manifest.get("allow_missing")) is not bool
    ):
        raise ValidationError("Invalid snapshot universe, calendar, or price metadata")
    provider = manifest.get("provider")
    if not isinstance(provider, str) or not provider.strip() or "endpoint" not in manifest:
        raise ValidationError("Missing provider or endpoint metadata")
    if provider == "alpha_vantage" and manifest["endpoint"] != "TIME_SERIES_DAILY_ADJUSTED":
        raise ValidationError("Unexpected Alpha Vantage endpoint")
    versions = manifest.get("versions")
    if not isinstance(versions, dict) or any(
        not isinstance(versions.get(name), str) or not versions[name].strip()
        for name in ("pandas", "numpy", "exchange-calendars")
    ):
        raise ValidationError("Missing dependency versions in snapshot manifest")

    start, end = _date(manifest.get("start")), _date(manifest.get("end"))
    if start > end:
        raise ValidationError("Invalid snapshot date range")
    calendar = _calendar(start, end)
    sessions = calendar.sessions_in_range(start, end)
    if sessions.empty:
        raise ValidationError("Snapshot interval has no trading sessions")
    last_close = calendar.session_close(sessions[-1])
    created = _aware_timestamp(manifest.get("created_at"), "created_at")
    coverage = manifest.get("coverage")
    retrievals = manifest.get("retrievals")
    if any(not isinstance(item, dict) or set(item) != set(UNIVERSE)
           for item in (coverage, retrievals)):
        raise ValidationError("Invalid per-ticker coverage or retrieval metadata")
    for ticker, value in retrievals.items():
        retrieved = _aware_timestamp(value, f"retrieval for {ticker}")
        if not last_close <= retrieved <= created:
            raise ValidationError(f"Retrieval for {ticker} must fall between close and creation")

    hashes = manifest.get("sha256")
    raw_files = [f"raw/{ticker}.json" for ticker in UNIVERSE]
    if not isinstance(hashes, dict) or set(hashes) != {"prices.csv", *raw_files}:
        raise ValidationError("Expected checksums for prices.csv and every raw response")
    price_bytes = (path / "prices.csv").read_bytes()
    if hashlib.sha256(price_bytes).hexdigest() != hashes["prices.csv"]:
        raise ValidationError("Checksum mismatch for prices.csv")
    for name in raw_files:
        with (path / name).open("rb") as raw:
            if hashlib.file_digest(raw, "sha256").hexdigest() != hashes[name]:
                raise ValidationError(f"Checksum mismatch for {name}")

    prices = pd.read_csv(
        io.BytesIO(price_bytes),
        dtype={col: str for col in ["date", "ticker", *METADATA_COLUMNS]},
        float_precision="round_trip",
        keep_default_na=False,
    )
    if list(prices.columns) != FIELDS + METADATA_COLUMNS or prices.empty:
        raise ValidationError("Invalid Phase 1 prices.csv schema or empty snapshot")
    if not prices["ticker"].isin(UNIVERSE).all() or prices.duplicated(["date", "ticker"]).any():
        raise ValidationError("Unexpected ticker or duplicate price observation")
    expected_metadata = {
        "provider": provider,
        "currency": "USD",
        "price_basis": PRICE_BASIS,
        "total_return_verified": "False",
        "retrieved_at": prices["ticker"].map(retrievals),
    }
    for col, expected in expected_metadata.items():
        if not prices[col].eq(expected).all():
            raise ValidationError(f"Snapshot price rows disagree with {col} metadata")

    # Reuse Phase 1's market-data checks and compare the actual gaps with the manifest.
    prices_by_ticker = {ticker: rows for ticker, rows in prices.groupby("ticker")}
    for ticker in UNIVERSE:
        _, actual = validate(
            prices_by_ticker.get(ticker, prices.iloc[:0]),
            ticker,
            sessions,
            allow_missing=manifest["allow_missing"],
        )
        actual.pop("exact_duplicates_removed")  # Original count is known only at ingestion.
        recorded = coverage[ticker]
        if (
            not isinstance(recorded, dict)
            or any(recorded.get(key) != value for key, value in actual.items())
            or type(recorded.get("exact_duplicates_removed")) is not int
            or recorded["exact_duplicates_removed"] < 0
        ):
            raise ValidationError(f"Snapshot coverage disagrees for {ticker}")

    prices["date"] = pd.to_datetime(prices["date"], format="%Y-%m-%d")
    return PriceSnapshot(prices, sessions, manifest, hashlib.sha256(manifest_bytes).hexdigest())


def load_snapshot(path: str | Path) -> PriceSnapshot:
    """Verify file hashes, scheme, row semantics and coverage before use."""
    try:
        return _read_snapshot(Path(path))
    except ValidationError:
        raise
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        raise ValidationError(f"Cannot read Phase 1 snapshot: {exc}") from exc
