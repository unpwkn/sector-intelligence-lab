"""Independent Phase 6C adjusted-close snapshots with exact evidence replay."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from sectorlab.config import FIRST_SESSION

from .ibkr import _REQUEST, _contract, _retrieved, verify_archive
from .pipeline import (
    CALENDAR_LABEL,
    RETROSPECTIVE,
    SYNTHETIC,
    UNIVERSE,
    ValidationError,
    _calendar,
    _date,
)
from .providers import _unique_object
from .snapshot import PRICE_BASIS, _aware_timestamp

SCHEMA = "price_only_1.0"
ASSURANCE = "PRICE_ONLY_UNRECONCILED"
ADJUSTMENT = "split_and_distribution_adjusted"
CSV_COLUMNS = ["date", "ticker", "adjusted_close"]
VERSIONS = ("pandas", "numpy", "exchange-calendars")
DECLARATION = {
    "provider",
    "source_id",
    "source_reference",
    "license_reference",
    "retrieved_at",
    "adjustment_basis",
    "data_kind",
    "raw_sha256",
}


@dataclass(frozen=True)
class PriceOnlySnapshot:
    """Validated price-only observations for the separate exploratory entry point."""

    prices: pd.DataFrame
    sessions: pd.DatetimeIndex
    manifest: dict
    manifest_sha256: str

    @property
    def research_classification(self) -> str:
        return self.manifest["research_classification"]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: bytes) -> dict:
    result = json.loads(data, object_pairs_hook=_unique_object)
    if not isinstance(result, dict):
        raise ValidationError("Expected JSON object")
    return result


def _encode(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _csv(raw: bytes) -> pd.DataFrame:
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    if next(reader, None) != CSV_COLUMNS:
        raise ValidationError("Expected date,ticker,adjusted_close CSV header")
    rows = []
    for row in reader:
        if len(row) != len(CSV_COLUMNS):
            raise ValidationError(f"Unexpected CSV width at line {reader.line_num}")
        rows.append(row)
    return pd.DataFrame(rows, columns=CSV_COLUMNS)


def _ibkr_rows(raw: bytes, start: str, end: str) -> tuple[pd.DataFrame, dict, str]:
    capture = _json(raw)
    request = capture["request"]
    if (
        capture.get("schema") != 1
        or capture.get("source") != "IBKR TWS API"
        or {key: val for key, val in request.items() if key != "durationStr"} != _REQUEST
        or set(capture["instruments"]) != set(UNIVERSE)
        or start != capture["start"]
        or end != capture["end"]
    ):
        raise ValidationError("Expected a complete Phase 6B TWS capture envelope")
    transport = capture.get("transport", {})
    sdk, server = transport.get("sdk"), transport.get("server_version")
    if (
        not isinstance(sdk, str)
        or not sdk.strip()
        or type(server) is not int
        or (server <= 0 and sdk != "mock")
    ):
        raise ValidationError("Missing TWS SDK/server evidence")
    data_kind = "synthetic" if sdk == "mock" else "market"
    began = _aware_timestamp(capture["started_at"], "capture start")
    finished = _aware_timestamp(capture["finished_at"], "capture end")
    rows, retrievals, conids = [], {}, set()
    for ticker in UNIVERSE:
        item = capture["instruments"][ticker]
        history = item["histories"].get("ADJUSTED_LAST")
        if "resolution_error" in item or history is None or "error" in history:
            raise ValidationError(f"{ticker} lacks confirmed TWS ADJUSTED_LAST")
        contract = item["resolution"]["contract"]
        _contract(ticker, contract)
        if contract["conId"] in conids:
            raise ValidationError("Duplicate IBKR contract identifier")
        conids.add(contract["conId"])
        bounds = history.get("response_bounds")
        if (
            not isinstance(bounds, list)
            or len(bounds) != 2
            or not all(isinstance(value, str) and value for value in bounds)
        ):
            raise ValidationError(f"{ticker} lacks a completed TWS historical callback")
        retrievals[ticker] = _retrieved(history, began, finished).isoformat()
        for bar in history["rows"]:
            label = bar["date"]
            if (
                not isinstance(label, str)
                or len(label) != 8
                or not label.isascii()
                or not label.isdigit()
            ):
                raise ValidationError(f"Invalid TWS bar date for {ticker}")
            day = f"{label[:4]}-{label[4:6]}-{label[6:]}"
            _date(day)
            if start <= day <= end:
                rows.append((day, ticker, bar["close"]))
    return pd.DataFrame(rows, columns=CSV_COLUMNS), retrievals, data_kind


def _normalize(records: pd.DataFrame, sessions: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict]:
    records = records.rename(columns={"adjusted_close": PRICE_BASIS})
    records["date"] = pd.to_datetime(records["date"].map(_date))
    if not records["ticker"].isin(UNIVERSE).all():
        raise ValidationError("Unexpected ticker in price-only evidence")
    if records.duplicated(["ticker", "date"]).any():
        raise ValidationError("Duplicate ticker/session in price-only evidence")
    column = records[PRICE_BASIS]
    if column.map(lambda value: isinstance(value, bool)).any():
        raise ValidationError("Expected numeric adjusted-close prices")
    column = pd.to_numeric(column, errors="coerce")
    if not np.isfinite(column.to_numpy(dtype=float)).all() or column.le(0).any():
        raise ValidationError("Adjusted-close prices must be positive and finite")
    records[PRICE_BASIS] = column
    coverage = {}
    gaps = []
    grouped = records.groupby("ticker")["date"]
    for ticker in UNIVERSE:
        first = FIRST_SESSION[ticker]
        expected = sessions[sessions >= pd.Timestamp(first)]
        observed = (
            pd.DatetimeIndex(grouped.get_group(ticker))
            if ticker in grouped.groups
            else pd.DatetimeIndex([])
        )
        if not observed.isin(expected).all():
            raise ValidationError(f"{ticker} has an out-of-window, pre-listing or non-XNYS bar")
        missing = expected.difference(observed)
        if len(missing):
            gaps.append(f"{ticker} {len(missing)} missing (first {missing[0].date()})")
        coverage[ticker] = {
            "first_trading_session": first,
            "expected_sessions": len(expected),
            "observations": len(observed),
            "first_observation": observed.min().date().isoformat() if len(observed) else None,
            "last_observation": observed.max().date().isoformat() if len(observed) else None,
            "missing_sessions": [],
        }
    if gaps:
        raise ValidationError("Incomplete 12-instrument price-only panel. " + "; ".join(gaps))
    return records.sort_values(["date", "ticker"]).reset_index(drop=True), coverage


def _build(
    raw: bytes,
    declaration: bytes,
    evidence: str,
    start: str,
    end: str,
    created: str,
    versions: dict,
) -> tuple[PriceOnlySnapshot, bytes]:
    source = _json(declaration)
    if set(source) != DECLARATION or any(
        not isinstance(value, str) or not value.strip() for value in source.values()
    ):
        raise ValidationError("Expected all price-only provenance fields")
    if source["adjustment_basis"] != ADJUSTMENT:
        raise ValidationError("Require split-and-distribution-adjusted closes")
    if source["data_kind"] not in {"market", "synthetic"}:
        raise ValidationError("Specify market or synthetic data_kind")
    if source["raw_sha256"] != _digest(raw):
        raise ValidationError("Raw evidence checksum does not match provenance")
    if set(versions) != set(VERSIONS) or any(
        not isinstance(value, str) or not value for value in versions.values()
    ):
        raise ValidationError("Missing dependency versions")
    start_date, end_date = _date(start), _date(end)
    if start_date > end_date:
        raise ValidationError("start must be <= end")
    calendar = _calendar(start_date, end_date)
    sessions = calendar.sessions_in_range(start_date, end_date)
    if sessions.empty or sessions[-1] < pd.Timestamp(max(FIRST_SESSION.values())):
        raise ValidationError("Choose an interval including sessions with all 12 ETFs listed")
    retrieved = _aware_timestamp(source["retrieved_at"], "original retrieval")
    creation = _aware_timestamp(created, "snapshot creation")
    last_close = calendar.session_close(sessions[-1])
    if not last_close <= retrieved <= creation <= pd.Timestamp.now(tz="UTC"):
        raise ValidationError("Retrieval and snapshot creation must follow the last session close")
    if evidence == "ibkr_tws_capture.json":
        if source["provider"] != "IBKR" or source["source_id"] != "TWS ADJUSTED_LAST":
            raise ValidationError("Expected IBKR TWS ADJUSTED_LAST provenance")
        records, retrievals, data_kind = _ibkr_rows(raw, start, end)
        if source["data_kind"] != data_kind:
            raise ValidationError("IBKR classification disagrees with capture evidence")
        times = [_aware_timestamp(value, "IBKR retrieval") for value in retrievals.values()]
        if min(times) < last_close or max(times) != retrieved:
            raise ValidationError("IBKR original retrievals disagree with the snapshot")
    elif evidence == "observations.csv":
        identifiers = "".join(
            char for char in (source["provider"] + source["source_id"]).casefold() if char.isalnum()
        )
        if "ibkr" in identifiers or "interactivebrokers" in identifiers:
            raise ValidationError("IBKR requires an original Phase 6B TWS capture")
        records = _csv(raw)
        retrievals = dict.fromkeys(UNIVERSE, retrieved.isoformat())
    else:
        raise ValidationError("Unknown price-only evidence format")
    prices, coverage = _normalize(records, sessions)
    normalized = prices.to_csv(index=False, date_format="%Y-%m-%d").encode()
    market = source["data_kind"] == "market"
    manifest = {
        "schema_version": SCHEMA,
        "data_assurance": ASSURANCE,
        "research_classification": RETROSPECTIVE if market else SYNTHETIC,
        "empirical_claim_status": "EXPLORATORY" if market else "NONE",
        "point_in_time_history": False,
        "total_return_verified": False,
        "start": start,
        "end": end,
        "created_at": created,
        "calendar": CALENDAR_LABEL,
        "universe": list(UNIVERSE),
        "provider": source["provider"],
        "price_basis": PRICE_BASIS,
        "provenance": source,
        "retrievals": retrievals,
        "coverage": coverage,
        "versions": versions,
        "evidence": evidence,
        "sha256": {
            evidence: _digest(raw),
            "provenance.json": _digest(declaration),
            "prices.csv": _digest(normalized),
        },
    }
    return PriceOnlySnapshot(prices, sessions, manifest, _digest(_encode(manifest))), normalized


def _publish(
    raw: bytes,
    declaration: bytes,
    evidence: str,
    start: str,
    end: str,
    output: Path,
) -> PriceOnlySnapshot:
    snapshot, normalized = _build(
        raw,
        declaration,
        evidence,
        start,
        end,
        datetime.now(UTC).isoformat(),
        {name: importlib.metadata.version(name) for name in VERSIONS},
    )
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".price-only-", dir=output.parent) as tmp:
        stage = Path(tmp) / "snapshot"
        stage.mkdir(mode=0o700)
        (stage / evidence).write_bytes(raw)
        (stage / "provenance.json").write_bytes(declaration)
        (stage / "prices.csv").write_bytes(normalized)
        (stage / "manifest.json").write_bytes(_encode(snapshot.manifest))
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        stage.rename(output)
    return snapshot


def import_price_only(
    observations: Path,
    provenance: Path,
    start: str,
    end: str,
    output: Path,
) -> PriceOnlySnapshot:
    """Import a non-IBKR vendor adjusted-close-only file."""
    return _publish(
        observations.read_bytes(),
        provenance.read_bytes(),
        "observations.csv",
        start,
        end,
        output,
    )


def import_ibkr_price_only(
    archive: Path,
    output: Path,
    *,
    source_reference: str,
    license_reference: str,
) -> PriceOnlySnapshot:
    """Use only original ADJUSTED_LAST observations in a verified local Phase 6B archive."""
    _, raw = verify_archive(archive)
    capture = _json(raw)
    start, end = capture["start"], capture["end"]
    _, retrievals, data_kind = _ibkr_rows(raw, start, end)
    declaration = _encode(
        {
            "provider": "IBKR",
            "source_id": "TWS ADJUSTED_LAST",
            "source_reference": source_reference,
            "license_reference": license_reference,
            "retrieved_at": max(map(pd.Timestamp, retrievals.values())).isoformat(),
            "adjustment_basis": ADJUSTMENT,
            "data_kind": data_kind,
            "raw_sha256": _digest(raw),
        }
    )
    return _publish(raw, declaration, "ibkr_tws_capture.json", start, end, output)


def load_price_only(path: str | Path) -> PriceOnlySnapshot:
    """Check raw and normalized evidence byte-for-byte and replay the complete panel."""
    path = Path(path)
    try:
        manifest_bytes = (path / "manifest.json").read_bytes()
        manifest = _json(manifest_bytes)
        if manifest.get("schema_version") != SCHEMA:
            raise ValidationError("Expected a Phase 6C price-only snapshot")
        evidence = manifest["evidence"]
        if evidence not in ("observations.csv", "ibkr_tws_capture.json"):
            raise ValidationError("Unknown price-only evidence format")
        names = {evidence, "provenance.json", "prices.csv"}
        if set(manifest["sha256"]) != names:
            raise ValidationError("Incomplete price-only evidence checksums")
        files = {name: (path / name).read_bytes() for name in names}
        for name, data in files.items():
            if manifest["sha256"][name] != _digest(data):
                raise ValidationError(f"Checksum mismatch for {name}")
        snapshot, normalized = _build(
            files[evidence],
            files["provenance.json"],
            evidence,
            manifest["start"],
            manifest["end"],
            manifest["created_at"],
            manifest["versions"],
        )
        if _encode(snapshot.manifest) != manifest_bytes or normalized != files["prices.csv"]:
            raise ValidationError("Price-only snapshot disagrees with raw evidence replay")
        return snapshot
    except (KeyError, TypeError, AttributeError, OSError, csv.Error, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(f"Cannot read price-only snapshot: {exc}") from exc
