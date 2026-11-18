"""Version 1 CSV contract for locally held, pre-aggregated ETF valuations."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import BENCHMARK, FIRST_SESSION, VALUATION_METRICS

CSV_COLUMNS = (
    "ticker", "metric", "observation_date", "observed_at", "value", "denominator", "fiscal_period",
    "metric_definition", "vendor", "source", "provenance", "published_at", "available_at",
    "history_type", "data_license",
)
HISTORY_LICENSES = {
    "point_in_time": {"private", "public_redistributable"},
    "reconstructed": {"private", "public_redistributable"},
    "synthetic": {"synthetic"},
}
BENCHMARK_METRICS = frozenset({"forward_pe", "dividend_yield"})
YIELD_METRICS = frozenset(
    metric for metrics in VALUATION_METRICS.values()
    for metric, (_, higher_is_cheap) in metrics.items() if higher_is_cheap
)
MARKET_TZ = "America/New_York"
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
ISO_INSTANT = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})"
)


class ValuationDataError(ValueError):
    """A file violates the valuation import contract."""


@dataclass(frozen=True)
class ValuationDataset:
    """Validated rows and exact input fingerprint for reproducible research."""

    _records: pd.DataFrame
    sha256: str
    history_type: str


def utc_instant(value: str | pd.Timestamp) -> pd.Timestamp:
    """Require an explicit timezone and normalize to UTC."""
    if isinstance(value, str) and ISO_INSTANT.fullmatch(value):
        value = pd.Timestamp(value)
    if not isinstance(value, pd.Timestamp) or pd.isna(value) or value.tzinfo is None:
        raise ValueError("Expected an ISO-8601 timestamp with seconds and UTC offset")
    return value.tz_convert("UTC")


def _validate_row(row: dict[str, str], *, line: int) -> dict:
    try:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("wrong number of columns")
        row = {key: value.strip() for key, value in row.items()}
        if any(not row[key] for key in CSV_COLUMNS if key not in ("value", "denominator")):
            raise ValueError("missing required metadata")
        ticker, metric = row["ticker"], row["metric"]
        allowed = BENCHMARK_METRICS if ticker == BENCHMARK else VALUATION_METRICS.get(ticker, {})
        if metric not in allowed:
            raise ValueError("unsupported ticker/metric pair")
        if row["data_license"] not in HISTORY_LICENSES.get(row["history_type"], set()):
            raise ValueError("invalid history_type or data_license")

        date = row["observation_date"]
        if not ISO_DATE.fullmatch(date):
            raise ValueError("observation_date must be YYYY-MM-DD")
        date = pd.Timestamp(date)
        if date < pd.Timestamp(FIRST_SESSION[ticker]):
            raise ValueError("observation predates ETF inception")
        observed, published, available = (
            utc_instant(row[key]) for key in ("observed_at", "published_at", "available_at")
        )
        if observed.tz_convert(MARKET_TZ).date() != date.date():
            raise ValueError("observed_at must occur on observation_date in New York")
        if not observed <= published <= available:
            raise ValueError("require observation <= publication <= first availability")
        value, denominator = (float(row[key] or "nan") for key in ("value", "denominator"))
        valid_value = math.isfinite(value)
        if metric == "dividend_yield":
            valid_value = valid_value and value >= 0
        elif metric not in YIELD_METRICS:
            valid_value = valid_value and value > 0
        return row | {
            "observation_date": date,
            "month": date.to_period("M"),
            "observed_at": observed,
            "published_at": published,
            "available_at": available,
            "value": value,
            "denominator": denominator,
            "valid_value": valid_value,
            "valid_denominator": math.isfinite(denominator) and denominator > 0,
        }
    except (ValueError, OverflowError) as exc:
        raise ValuationDataError(f"CSV line {line}: {exc}") from exc


def parse_valuation_csv(payload: bytes) -> ValuationDataset:
    """Read one CSV containing a single history type, without inferring provenance."""
    try:
        data = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
        if not data.fieldnames or sorted(data.fieldnames) != sorted(CSV_COLUMNS):
            raise ValuationDataError("Expected exactly the version 1 valuation CSV columns")
        rows = [_validate_row(row, line=data.line_num) for row in data]
    except (UnicodeError, csv.Error) as exc:
        raise ValuationDataError(f"Invalid UTF-8 CSV: {exc}") from exc

    if not rows:
        raise ValuationDataError("The valuation file contains no observations")
    records = pd.DataFrame(rows)
    if records["history_type"].nunique() != 1:
        raise ValuationDataError("Keep point-in-time, reconstructed and synthetic histories separate")
    vintage_key = ["ticker", "metric", "observation_date", "available_at"]
    if records.duplicated(vintage_key).any():
        raise ValuationDataError("Duplicate ticker/metric/date/available_at vintage")
    records = records.sort_values(vintage_key, ignore_index=True)
    return ValuationDataset(records, hashlib.sha256(payload).hexdigest(), rows[0]["history_type"])


def load_valuation_csv(path: str | Path) -> ValuationDataset:
    return parse_valuation_csv(Path(path).read_bytes())
