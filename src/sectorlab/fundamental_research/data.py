"""Strict CSV ingestion for holdings, analyst vintages, and quality ratio operands."""

from __future__ import annotations

import csv
import hashlib
import io
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..config import FIRST_SESSION, SECTORS
from ..valuation_data import HISTORY_LICENSES, ISO_DATE, MARKET_TZ, utc_instant
from .quality_definitions import METRICS, QUALITY_MODELS

CSV_COLUMNS = (
    "kind",
    "ticker",
    "issuer_id",
    "subindustry",
    "analyst_id",
    "metric_definition",
    "observation_date",
    "observed_at",
    "published_at",
    "available_at",
    "period_start",
    "period_end",
    "basis",
    "value",
    "denominator",
    "status",
    "vendor",
    "source",
    "provenance",
    "audit_ref",
    "history_type",
    "data_license",
)
DEFINITIONS = {
    "holding": {"etf_nav_weight_v1"},
    "eps": {"quarterly_diluted_adjusted_eps_v1"},
    "recommendation": {"analyst_rating_1_to_5_v1"},
    "quality": {f"{metric}_v1" for metric in METRICS},
}
RESIDUAL = "RESIDUAL"


class ResearchDataError(ValueError):
    """A research bundle violates the import contract."""


@dataclass(frozen=True)
class ResearchDataset:
    """Validated input, with a hash of the original CSV for reproducibility."""

    _rows: pd.DataFrame
    sha256: str
    history_type: str


def _date(value: str) -> pd.Timestamp:
    if not ISO_DATE.fullmatch(value):
        raise ValueError("Expected a YYYY-MM-DD date")
    return pd.Timestamp(value)


def _number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Expected a finite numeric field")
    return number


def _row(raw: dict, cutoff: pd.Timestamp) -> dict:
    if None in raw or any(value is None for value in raw.values()):
        raise ValueError("Wrong number of columns")
    row = {key: value.strip() for key, value in raw.items()}
    kind = row["kind"]
    required = (
        "issuer_id",
        "vendor",
        "source",
        "provenance",
        "audit_ref",
        "basis",
        "observation_date",
        "observed_at",
        "published_at",
        "available_at",
    )
    if any(not row[key] for key in required):
        raise ValueError("Missing required metadata")
    history_type = row["history_type"]
    if history_type not in {"point_in_time", "synthetic"} or (
        row["data_license"] not in HISTORY_LICENSES[history_type]
    ):
        raise ValueError("Expected point_in_time or synthetic with a matching license")
    definition = row["metric_definition"]
    if definition not in DEFINITIONS.get(kind, set()):
        raise ValueError("Unsupported metric definition for row kind")
    observed, published, available = (
        utc_instant(row[key]) for key in ("observed_at", "published_at", "available_at")
    )
    date = _date(row["observation_date"])
    if observed.tz_convert(MARKET_TZ).date() != date.date():
        raise ValueError("observed_at must occur on observation_date in New York")
    if not observed <= published <= available <= cutoff:
        raise ValueError("Require observation <= publication <= availability <= import cutoff")

    if kind == "holding":
        ticker = row["ticker"]
        if ticker not in SECTORS or date < _date(FIRST_SESSION[ticker]):
            raise ValueError("Unsupported ETF or holding predates first session")
        subindustry = row["subindustry"]
        if row["issuer_id"] == RESIDUAL:
            if subindustry != "residual":
                raise ValueError("RESIDUAL must use the residual classification")
        elif subindustry not in QUALITY_MODELS[ticker] and subindustry != "unmapped":
            raise ValueError("Unsupported ETF/subindustry mapping; use unmapped if unknown")
        if row["basis"] != "ETF_NAV":
            raise ValueError("Holdings must use ETF NAV weights")
    elif row["ticker"] or row["subindustry"] or row["issuer_id"] == RESIDUAL:
        raise ValueError("Only holdings carry ETF, subindustry, and residual attribution")

    analyst_row = kind in {"eps", "recommendation"}
    if bool(row["analyst_id"]) != analyst_row:
        raise ValueError("Analyst ID required only on EPS and recommendation rows")
    if kind == "eps" and not row["basis"].startswith("USD/share:"):
        raise ValueError("EPS basis must identify the USD/share corporate-action basis")
    if kind == "eps" and not row["basis"].removeprefix("USD/share:").strip():
        raise ValueError("EPS corporate-action basis is empty")
    if kind == "recommendation" and row["basis"] != "ordinal":
        raise ValueError("Recommendations must use the canonical ordinal scale")
    if kind == "quality" and row["basis"] != "ratio":
        raise ValueError("Quality operands must be on the canonical ratio scale")
    start, end = pd.NaT, pd.NaT
    if kind in {"eps", "quality"}:
        start, end = _date(row["period_start"]), _date(row["period_end"])
        if kind == "eps" and (not 77 <= (end - start).days <= 105 or date >= end):
            raise ValueError("EPS needs a fiscal quarter and an observation before its end")
        if kind == "quality" and (not 350 <= (end - start).days <= 380 or end > date):
            raise ValueError("Quality needs a completed trailing-year fiscal anchor")
    elif row["period_start"] or row["period_end"]:
        raise ValueError("Holdings and ratings have no fiscal period")

    status = row["status"]
    if status not in {"valid", "invalid", "withdrawn"} or (kind == "holding" and status != "valid"):
        raise ValueError("Unsupported status for row kind")
    value, denominator = math.nan, math.nan
    if status != "valid":
        if row["value"] or row["denominator"]:
            raise ValueError("Invalid/withdrawn observations require empty value and denominator")
    else:
        value = _number(row["value"])
        if kind == "quality":
            denominator = _number(row["denominator"])
            metric = METRICS[definition.removesuffix("_v1")]
            if metric.year_count is not None and (
                denominator != metric.year_count or not value.is_integer()
            ):
                raise ValueError("Invalid year count; require integer years and full window")
            ratio = value / denominator if denominator > 0 else math.nan
            if (
                not math.isfinite(ratio)
                or (metric.minimum is not None and ratio < metric.minimum)
                or (metric.maximum is not None and ratio > metric.maximum)
            ):
                raise ValueError("Invalid quality ratio or denominator; mark row invalid")
        elif row["denominator"]:
            raise ValueError("Only quality rows supply a denominator")
        if kind == "holding" and not 0 < value <= 1:
            raise ValueError("Holding weight must be a positive fraction of ETF NAV")
        if kind == "recommendation" and value not in {1, 2, 3, 4, 5}:
            raise ValueError("Recommendation must be an integer from 1 (sell) to 5 (buy)")

    return row | {
        "observation_date": date,
        "observed_at": observed,
        "published_at": published,
        "available_at": available,
        "period_start": start,
        "period_end": end,
        "value": value,
        "denominator": denominator,
    }


def _validate_bundle(rows: pd.DataFrame) -> None:
    if rows["history_type"].nunique() != 1:
        raise ResearchDataError("Keep synthetic and point-in-time claims in separate bundles")
    keys = [
        "kind",
        "ticker",
        "issuer_id",
        "analyst_id",
        "metric_definition",
        "period_start",
        "period_end",
        "observed_at",
        "available_at",
    ]
    if rows.duplicated(keys).any():
        raise ResearchDataError("Duplicate observation vintage")
    assets = rows.loc[rows["kind"] == "holding"]
    if assets.empty:
        raise ResearchDataError("At least one dated ETF holdings snapshot is required")
    if assets.groupby(["ticker", "observation_date"])["observed_at"].nunique().gt(1).any():
        raise ResearchDataError(
            "Holdings corrections must preserve the original observation instant"
        )
    snapshot_keys = ["ticker", "observation_date", "available_at"]
    for _, snapshot in assets.groupby(snapshot_keys, sort=False):
        metadata = ["vendor", "published_at", "observed_at", "source", "audit_ref", "data_license"]
        if snapshot[metadata].nunique().max() != 1:
            raise ResearchDataError("Holdings snapshot must share source and vintage metadata")
        if not math.isclose(math.fsum(snapshot["value"]), 1, rel_tol=0, abs_tol=1e-6):
            raise ResearchDataError("Holdings snapshot must sum to full ETF NAV including residual")
    for kind in ("eps", "recommendation"):
        if rows.loc[rows["kind"] == kind, "vendor"].nunique() > 1:
            raise ResearchDataError(f"Use one comparable {kind} vendor per bundle")

    quality = rows.loc[rows["kind"] == "quality"]
    series = quality.groupby(["issuer_id", "metric_definition"])["vendor"].nunique()
    if series.gt(1).any():
        raise ResearchDataError("Use one comparable vendor per issuer/quality metric series")


def parse_research_csv(
    payload: bytes,
    *,
    import_cutoff: str | pd.Timestamp | None = None,
) -> ResearchDataset:
    """Validate a bundle at ingestion. Research queries enforce their own earlier cutoff."""
    cutoff = utc_instant(import_cutoff) if import_cutoff is not None else pd.Timestamp.now(tz="UTC")
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
        if not reader.fieldnames or sorted(reader.fieldnames) != sorted(CSV_COLUMNS):
            raise ResearchDataError("Expected exactly the Phase 4 version 1 CSV columns")
        rows = []
        for raw in reader:
            try:
                rows.append(_row(raw, cutoff))
            except (ValueError, OverflowError) as exc:
                raise ResearchDataError(f"CSV line {reader.line_num}: {exc}") from exc
    except (UnicodeError, csv.Error) as exc:
        raise ResearchDataError(f"Invalid UTF-8 CSV: {exc}") from exc
    if not rows:
        raise ResearchDataError("No observations in CSV")
    records = pd.DataFrame(rows)
    _validate_bundle(records)
    records = records.sort_values(
        [
            "observed_at",
            "available_at",
            "ticker",
            "issuer_id",
            "analyst_id",
            "metric_definition",
            "period_start",
            "period_end",
        ],
        ignore_index=True,
    )
    return ResearchDataset(records, hashlib.sha256(payload).hexdigest(), rows[0]["history_type"])


def load_research_csv(path: str | Path) -> ResearchDataset:
    return parse_research_csv(Path(path).read_bytes())
