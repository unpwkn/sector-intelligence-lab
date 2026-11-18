"""Capture, independently reconcile and replay private IBKR daily history."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from sectorlab.config import ETF_INCEPTION, FIRST_SESSION

from .ibkr_tws import BAR_FIELDS, BAR_TYPES, IBKRRequestError
from .pipeline import UNIVERSE, ValidationError, _calendar, _date
from .providers import FIELDS, _unique_object
from .snapshot import _aware_timestamp

_REQUEST = {
    "endDateTime": "",
    "barSizeSetting": "1 day",
    "useRTH": 1,
    "formatDate": 1,
    "keepUpToDate": False,
}
_BASIS = {
    "TRADES": "split_adjusted",
    "ADJUSTED_LAST": "split_and_distribution_adjusted",
}
_MAX_DURATION_YEARS = 68  # Published TWS duration limit for one-day bars.
_RETURN_TOLERANCE = 0.0005  # Five basis points, applied to daily ratios.
_OHLCV = ["open", "high", "low", "close", "volume"]
_CAPTURE = "capture.json"
_REFERENCES = "references.json"
_REPORT = "report.json"
_PHASE6A_PRICES = "prices.csv"
_PHASE6A_PROVENANCE = "provenance.json"


class HistoryTransport(Protocol):
    def metadata(self) -> dict: ...

    def resolve(self, ticker: str) -> dict: ...

    def history(self, contract: dict, what: str, duration: str) -> dict: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _load_json(data: bytes) -> dict:
    try:
        result = json.loads(data, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise ValidationError("Invalid IBKR archive JSON") from exc
    if not isinstance(result, dict):
        raise ValidationError("Expected a JSON object")
    return result


def _sessions(start: str, end: str, asof: pd.Timestamp) -> pd.DatetimeIndex:
    start_date, end_date = _date(start), _date(end)
    if start_date > end_date:
        raise ValidationError("start must be <= end")
    calendar = _calendar(start_date, end_date)
    sessions = calendar.sessions_in_range(start_date, end_date)
    if sessions.empty or calendar.session_close(sessions[-1]) > asof:
        raise ValidationError("Choose an interval with completed XNYS sessions")
    if sessions[-1] < pd.Timestamp(max(FIRST_SESSION.values())):
        raise ValidationError("Choose an end date when all 12 ETFs were listed")
    return sessions


def _contract(ticker: str, contract: dict) -> None:
    if (
        contract.get("symbol") != ticker
        or contract.get("secType") != "STK"
        or contract.get("currency") != "USD"
        or contract.get("primaryExchange") != "ARCA"
        or type(contract.get("conId")) is not int
        or contract["conId"] <= 0
    ):
        raise ValidationError(f"{ticker} did not resolve to a USD ARCA ETF")


def capture_history(transport: HistoryTransport, start: str, end: str) -> dict:
    """Capture both same-vintage daily series, recording per-request failures."""
    began = _utc_now()
    _sessions(start, end, pd.Timestamp(began))
    # An integer year request from Now must reach the user-selected start date.
    # Use a small buffer to accommodate leap years and request-boundary behavior.
    age = (began.date() - _date(start).date()).days
    years = max(1, (age + 7) // 365 + 1)
    if years > _MAX_DURATION_YEARS:
        raise ValidationError("Requested start exceeds IBKR's documented daily duration")
    duration = f"{years} Y"
    instruments = {}
    for ticker in UNIVERSE:
        entry = {"histories": {}}
        instruments[ticker] = entry
        try:
            resolved = transport.resolve(ticker)
            _contract(ticker, resolved["contract"])
            entry["resolution"] = resolved
        except (ValidationError, IBKRRequestError) as exc:
            entry["resolution_error"] = _failure(exc)
            continue
        for what in BAR_TYPES:
            try:
                entry["histories"][what] = transport.history(resolved["contract"], what, duration)
            except IBKRRequestError as exc:
                entry["histories"][what] = (exc.partial or {}) | {"error": _failure(exc)}
    return {
        "schema": 1,
        "source": "IBKR TWS API",
        "transport": transport.metadata(),
        "started_at": began.isoformat(),
        "finished_at": _utc_now().isoformat(),
        "start": start,
        "end": end,
        "request": _REQUEST | {"durationStr": duration},
        "instruments": instruments,
    }


def _failure(exc: ValidationError | IBKRRequestError) -> dict:
    if isinstance(exc, IBKRRequestError):
        return {"reason": exc.reason, "code": exc.code}
    return {"reason": str(exc)}


def _retrieved(item: dict, began: pd.Timestamp, finished: pd.Timestamp) -> pd.Timestamp:
    requested = _aware_timestamp(item.get("requested_at"), "requested_at")
    received = _aware_timestamp(item.get("retrieved_at"), "retrieved_at")
    if not began <= requested <= received <= finished:
        raise ValidationError("Request timestamps fall outside the capture interval")
    return received


def _ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    values = frame[_OHLCV].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValidationError("Non-finite or missing OHLCV")
    if values[_OHLCV[:4]].le(0).any().any() or values.volume.lt(0).any():
        raise ValidationError("Nonpositive price or negative volume")
    if (
        values.high.lt(values[["open", "low", "close"]].max(axis=1)).any()
        or values.low.gt(values[["open", "high", "close"]].min(axis=1)).any()
    ):
        raise ValidationError("Inconsistent OHLC")
    return values


def _series(
    item: dict,
    expected: pd.DatetimeIndex,
    ticker: str,
    began: pd.Timestamp,
    finished: pd.Timestamp,
    start: str,
    end: str,
) -> tuple[pd.DataFrame | None, dict]:
    if "error" in item:
        coverage = {"status": "request_failed", "error": item["error"]}
        if "requested_at" in item:
            coverage["retrieved_at"] = _retrieved(item, began, finished).isoformat()
            coverage["partial_response_bars"] = len(item["rows"])
        return None, coverage
    received = _retrieved(item, began, finished)
    bounds = item.get("response_bounds")
    if (
        not isinstance(bounds, list)
        or len(bounds) != 2
        or not all(isinstance(v, str) for v in bounds)
    ):
        raise ValidationError("Missing historicalDataEnd callback bounds")
    rows = item.get("rows")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or set(row) != set(BAR_FIELDS) for row in rows
    ):
        raise ValidationError("Unexpected historical-bar fields")
    dates = []
    for row in rows:
        day = row["date"]
        if not isinstance(day, str) or not re.fullmatch(r"[0-9]{8}", day):
            raise ValidationError("Expected IBKR daily bar label YYYYMMDD")
        dates.append(_date(f"{day[:4]}-{day[4:6]}-{day[6:]}"))
    labels = pd.DatetimeIndex(dates)
    observed = labels.unique().sort_values()
    listed_observations = expected.intersection(observed)
    missing = expected.difference(observed)
    five_year_cutoff = began.tz_convert("America/New_York").normalize().tz_localize(
        None
    ) - pd.DateOffset(years=5)
    duplicates = sorted(
        day.strftime("%Y-%m-%d") for day, count in Counter(dates).items() if count > 1
    )
    in_window = (observed >= _date(start)) & (observed <= _date(end))
    extras = observed[in_window].difference(expected)
    prelisting = observed[observed < pd.Timestamp(FIRST_SESSION[ticker])]
    coverage = {
        "status": "complete",
        "response_bars": len(rows),
        "response_first_session": observed[0].strftime("%Y-%m-%d") if len(observed) else None,
        "response_last_session": observed[-1].strftime("%Y-%m-%d") if len(observed) else None,
        "expected_sessions": len(expected),
        "observed_sessions": len(listed_observations),
        "observed_first_session": (
            listed_observations[0].strftime("%Y-%m-%d") if len(listed_observations) else None
        ),
        "observed_last_session": (
            listed_observations[-1].strftime("%Y-%m-%d") if len(listed_observations) else None
        ),
        "observed_sessions_older_than_five_years": int(
            (listed_observations < five_year_cutoff).sum()
        ),
        "missing_sessions": missing.strftime("%Y-%m-%d").tolist(),
        "duplicate_dates": duplicates,
        "unexpected_sessions": extras.strftime("%Y-%m-%d").tolist(),
        "prelisting_sessions": prelisting.strftime("%Y-%m-%d").tolist(),
        "retrieved_at": received.isoformat(),
    }
    selected = pd.DataFrame(rows, columns=BAR_FIELDS, index=labels)
    try:
        values = _ohlcv(selected.loc[labels.isin(expected)]).sort_index()
    except ValidationError as exc:
        return None, coverage | {"status": "invalid_prices", "error": str(exc)}
    if len(missing) or duplicates or len(extras) or len(prelisting):
        return None, coverage | {"status": "invalid_coverage"}
    return values, coverage


def _declaration(bundle: dict, label: str, start: str, end: str, last_close: pd.Timestamp) -> None:
    for key in ("source", "source_reference", "license_reference"):
        if not isinstance(bundle.get(key), str) or not bundle[key].strip():
            raise ValidationError(f"{label} requires {key}")
    retrieved = _aware_timestamp(bundle.get("retrieved_at"), f"{label} retrieved_at")
    if (
        bundle.get("start") != start
        or bundle.get("end") != end
        or bundle.get("universe") != list(UNIVERSE)
    ):
        raise ValidationError(f"{label} must attest to the same requested bounds and all 12 ETFs")
    if not last_close <= retrieved <= pd.Timestamp(_utc_now()):
        raise ValidationError(f"{label} retrieval must fall between last session close and now")
    rows = bundle.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValidationError(f"{label} requires a rows list")


def _reference_rows(bundle: dict, fields: list[str]) -> dict[str, pd.DataFrame]:
    rows = bundle["rows"]
    if any(set(row) != set(fields) for row in rows):
        raise ValidationError("Unexpected reference fields")
    if any(isinstance(value, bool) for row in rows for value in row.values()):
        raise ValidationError("Reference fields cannot contain boolean values")
    data = pd.DataFrame(rows, columns=fields)
    if not data.ticker.isin(UNIVERSE).all():
        raise ValidationError("Unexpected ticker in references")
    data["date"] = pd.to_datetime(data.date.map(_date))
    if data.duplicated(["ticker", "date"]).any():
        raise ValidationError("Duplicate ticker and date in references")
    return {
        ticker: rows.drop(columns="ticker").set_index("date").sort_index()
        for ticker, rows in data.groupby("ticker")
    }


def _events(groups: dict, ticker: str, expected: pd.DatetimeIndex) -> pd.DataFrame:
    fields = ["dividend_amount", "split_coefficient"]
    events = groups.get(ticker, pd.DataFrame(columns=fields, index=pd.DatetimeIndex([])))
    events = events.apply(pd.to_numeric, errors="coerce").astype(float)
    if (
        not events.index.isin(expected).all()
        or not np.isfinite(events.to_numpy(dtype=float)).all()
        or events.dividend_amount.lt(0).any()
        or events.split_coefficient.le(0).any()
        or (events.dividend_amount.eq(0) & events.split_coefficient.eq(1)).any()
    ):
        raise ValidationError("Invalid reference action date, dividend or split")
    return events


def _reconcile(
    ticker: str,
    expected: pd.DatetimeIndex,
    series: dict,
    groups: dict,
) -> tuple[pd.DataFrame, dict]:
    broker = _events(groups["broker_actions"], ticker, expected)
    independent = _events(groups["independent_actions"], ticker, expected)
    if not broker.index.equals(independent.index) or not np.allclose(
        broker.to_numpy(dtype=float),
        independent.to_numpy(dtype=float),
        atol=1e-6,
        rtol=1e-8,
    ):
        raise ValidationError("IBKR and independent corporate-action ledgers disagree")
    if len(expected) < 2:
        raise ValidationError("At least two sessions are needed for adjustment reconciliation")
    if expected[0] in independent.index:
        raise ValidationError("Include one preceding session to reconcile the first action")
    raw = groups["as_traded"].get(ticker)
    if raw is None or not raw.index.equals(expected):
        raise ValidationError("Independent as-traded OHLCV lacks exact session coverage")
    raw = _ohlcv(raw)
    if raw.volume.mod(1).ne(0).any():
        raise ValidationError("Independent as-traded volume must be integral shares")
    actions = independent.reindex(expected, fill_value=0)
    actions["split_coefficient"] = independent.split_coefficient.reindex(expected, fill_value=1)
    trades, adjusted = (series[what].close for what in BAR_TYPES)

    # Ratios cancel the provider's absolute price scale, including later splits.
    # Cash distributions are per post-split share, effective on the stated date.
    previous_post_split = raw.close.shift() / actions.split_coefficient
    split_move = raw.close / previous_post_split
    cash_factor = 1 - actions.dividend_amount / previous_post_split
    if not cash_factor.iloc[1:].gt(0).all():
        raise ValidationError("Distribution exceeds reference close under the supported convention")
    split_residual = (trades / trades.shift()) / split_move - 1
    cash_residual = ((adjusted / adjusted.shift()) / (trades / trades.shift())) * cash_factor - 1
    cash_tolerance = (1 - cash_factor).abs().div(2).clip(upper=_RETURN_TOLERANCE)
    cash_tolerance = cash_tolerance.where(actions.dividend_amount.gt(0), _RETURN_TOLERANCE)
    for residual, tolerance, label in (
        (split_residual, _RETURN_TOLERANCE, "split-adjusted TRADES"),
        (cash_residual, cash_tolerance, "dividend-adjusted ADJUSTED_LAST"),
    ):
        fail = residual.iloc[1:].abs().gt(tolerance)
        if fail.any() or not np.isfinite(residual.iloc[1:]).all():
            first = fail[fail].index[0].strftime("%Y-%m-%d") if fail.any() else "unknown date"
            raise ValidationError(f"{label} failed independent price reconciliation at {first}")
    prices = (
        raw.assign(
            ticker=ticker,
            adjusted_price_proxy=adjusted,
            dividend_amount=actions.dividend_amount,
            split_coefficient=actions.split_coefficient,
        )
        .rename_axis("date")
        .reset_index()
    )
    return prices[FIELDS], {
        "status": "reconciled",
        "dividend_sessions": int(independent.dividend_amount.gt(0).sum()),
        "split_sessions": int(independent.split_coefficient.ne(1).sum()),
        "max_trades_residual": float(split_residual.iloc[1:].abs().max()),
        "max_adjusted_residual": float(cash_residual.iloc[1:].abs().max()),
    }


def _references(refs: dict, start: str, end: str, last_close: pd.Timestamp) -> dict:
    names = ("broker_actions", "independent_actions", "as_traded")
    if set(refs) != set(names) or any(not isinstance(refs[name], dict) for name in names):
        raise ValidationError("Expected broker_actions, independent_actions and as_traded")
    for name in names:
        _declaration(refs[name], name, start, end, last_close)
    broker, independent, as_traded = (refs[name] for name in names)
    if broker["source"].strip().casefold() != "ibkr":
        raise ValidationError("Broker actions must identify source IBKR")
    if any(
        source["source"].strip().casefold() in {"ibkr", "interactive brokers", "ibkr tws api"}
        or source["source_reference"] == broker["source_reference"]
        for source in (independent, as_traded)
    ):
        raise ValidationError("Independent inputs must originate outside IBKR")
    if as_traded.get("price_basis") != "as_traded":
        raise ValidationError("Independent OHLCV must explicitly declare an as-traded price basis")
    action_fields = ["date", "ticker", "dividend_amount", "split_coefficient"]
    return {
        "broker_actions": _reference_rows(broker, action_fields),
        "independent_actions": _reference_rows(independent, action_fields),
        "as_traded": _reference_rows(as_traded, ["date", "ticker", *_OHLCV]),
    }


def _evaluate(capture: dict, refs: dict | None) -> tuple[dict, pd.DataFrame | None]:
    began = _aware_timestamp(capture.get("started_at"), "capture start")
    finished = _aware_timestamp(capture.get("finished_at"), "capture end")
    if not began <= finished <= pd.Timestamp(_utc_now()):
        raise ValidationError("Invalid capture interval")
    start, end = capture.get("start"), capture.get("end")
    sessions = _sessions(start, end, began)
    instruments = capture.get("instruments")
    request = capture.get("request")
    if (
        capture.get("schema") != 1
        or capture.get("source") != "IBKR TWS API"
        or not isinstance(instruments, dict)
        or set(instruments) != set(UNIVERSE)
        or not isinstance(request, dict)
        or {key: val for key, val in request.items() if key != "durationStr"} != _REQUEST
        or not re.fullmatch(r"[1-9][0-9]? Y", str(request.get("durationStr")))
        or int(request["durationStr"].split()[0]) > _MAX_DURATION_YEARS
    ):
        raise ValidationError("Invalid IBKR capture schema, universe or request")
    audit = {
        "start": start,
        "end": end,
        "requested_duration": request["durationStr"],
        "scope": "RETROSPECTIVE_ADJUSTED_PRICE_RESEARCH",
        "point_in_time_history": False,
        "total_return_verified": False,
        "price_bases": _BASIS,
        "ibkr_ohlc_satisfies_phase6a": False,
        "phase6a_export_eligible": False,
        "return_ratio_tolerance": _RETURN_TOLERANCE,
        "instruments": {},
    }
    groups = None
    if refs is not None:
        try:
            last_close = _calendar(_date(start), _date(end)).session_close(sessions[-1])
            groups = _references(refs, start, end, last_close)
        except ValidationError as exc:
            audit["reference_error"] = str(exc)

    frames = []
    contract_ids = set()
    for ticker in UNIVERSE:
        info = instruments[ticker]
        expected = sessions[sessions >= pd.Timestamp(FIRST_SESSION[ticker])]
        result = {
            "fund_inception": ETF_INCEPTION[ticker],
            "first_trading_session": FIRST_SESSION[ticker],
            "expected_sessions": len(expected),
            "reconciliation": {"status": "blocked"},
            "series": {},
        }
        audit["instruments"][ticker] = result
        if "resolution_error" in info:
            result["contract_error"] = info["resolution_error"]
            continue
        resolution = info["resolution"]
        contract = resolution["contract"]
        _contract(ticker, contract)
        _retrieved(resolution, began, finished)
        if contract["conId"] in contract_ids:
            raise ValidationError("Contract identifier reused for multiple tickers")
        contract_ids.add(contract["conId"])
        result["conId"] = contract["conId"]
        series = {}
        for what in BAR_TYPES:
            try:
                data, coverage = _series(
                    info["histories"][what],
                    expected,
                    ticker,
                    began,
                    finished,
                    start,
                    end,
                )
                if data is not None:
                    series[what] = data
            except ValidationError as exc:
                coverage = {"status": "invalid_response", "error": str(exc)}
            result["series"][what] = coverage
        if len(series) != len(BAR_TYPES):
            continue
        if groups is None:
            result["reconciliation"] = {"status": "requires_complete_independent_references"}
            continue
        try:
            prices, summary = _reconcile(ticker, expected, series, groups)
        except ValidationError as exc:
            result["reconciliation"] = {"status": "failed", "reason": str(exc)}
        else:
            result["reconciliation"] = summary
            frames.append(prices)
    if len(frames) != len(UNIVERSE):
        return audit, None
    audit["phase6a_export_eligible"] = True
    prices = pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"])
    prices["date"] = prices["date"].dt.strftime("%Y-%m-%d")
    return audit, prices


def _phase6a_provenance(capture: dict, refs: dict, capture_sha: str, references_sha: str) -> dict:
    vintage = max(
        pd.Timestamp(capture["finished_at"]),
        *(pd.Timestamp(source["retrieved_at"]) for source in refs.values()),
    )
    return {
        "source": "IBKR ADJUSTED_LAST with independent as-traded OHLCV and action references",
        "source_reference": (
            f"Private IBKR archive capture.json SHA-256 {capture_sha}; "
            f"references.json SHA-256 {references_sha}"
        ),
        "license_reference": "Private archive references.json and applicable IBKR agreements",
        "retrieved_at": vintage.isoformat(),
        "data_kind": "market",
        "ohlc_basis": "as_traded",
        "adjusted_close_basis": "split_and_distribution_adjusted",
        "corporate_action_method": (
            "IBKR ADJUSTED_LAST (split and cash distribution adjusted). OHLCV from a "
            "separate as-traded source. Broker and independent ex-date cash distributions "
            "and new/old split ratios were matched. Daily split and dividend adjustment "
            "ratios were cross-checked against observed as-traded close and event terms. "
            "Exact IBKR reinvestment and historical availability are unverified."
        ),
    }


def _artifacts(capture_bytes: bytes, reference_bytes: bytes | None) -> dict[str, bytes]:
    capture = _load_json(capture_bytes)
    refs, reference_error = None, None
    if reference_bytes is not None:
        try:
            refs = _load_json(reference_bytes)
        except ValidationError as exc:
            reference_error = str(exc)
    report, prices = _evaluate(capture, refs)
    if reference_error is not None:
        report["reference_error"] = reference_error
    artifacts = {_CAPTURE: capture_bytes, _REPORT: _json_bytes(report)}
    if reference_bytes is not None:
        artifacts[_REFERENCES] = reference_bytes
    if prices is not None:
        digest = hashlib.sha256(capture_bytes).hexdigest()
        artifacts[_PHASE6A_PRICES] = prices.to_csv(index=False).encode()
        references_digest = hashlib.sha256(reference_bytes).hexdigest()
        artifacts[_PHASE6A_PROVENANCE] = _json_bytes(
            _phase6a_provenance(capture, refs, digest, references_digest)
        )
    return artifacts


def publish_archive(capture_bytes: bytes, output: Path, references: bytes | None = None) -> dict:
    """Save raw observations even if coverage prevents a research export."""
    artifacts = _artifacts(capture_bytes, references)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": 1,
        "created_at": _utc_now().isoformat(),
        "versions": {
            pkg: importlib.metadata.version(pkg)
            for pkg in ("pandas", "numpy", "exchange-calendars")
        },
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()},
    }
    with tempfile.TemporaryDirectory(prefix=".ibkr-", dir=output.parent) as tmp:
        stage = Path(tmp) / "archive"
        stage.mkdir(mode=0o700)
        for name, data in (artifacts | {"manifest.json": _json_bytes(manifest)}).items():
            (stage / name).write_bytes(data)
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        os.rename(stage, output)
    return _load_json(artifacts[_REPORT])


def verify_archive(path: Path) -> tuple[dict, bytes]:
    """Verify archived bytes and deterministically replay the report and any export."""
    manifest = _load_json((path / "manifest.json").read_bytes())
    names = {_CAPTURE, _REFERENCES, _REPORT, _PHASE6A_PRICES, _PHASE6A_PROVENANCE}
    hashes = manifest.get("sha256")
    if (
        manifest.get("schema") != 1
        or not isinstance(hashes, dict)
        or set(hashes) - names
        or not {_CAPTURE, _REPORT}.issubset(hashes)
    ):
        raise ValidationError("Invalid IBKR archive manifest")
    if {file.name for file in path.iterdir()} != {*hashes, "manifest.json"}:
        raise ValidationError("IBKR archive contains missing or unexpected files")
    files = {}
    for name, expected in hashes.items():
        content = files[name] = (path / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValidationError(f"Checksum mismatch for {name}")
    replayed = _artifacts(files[_CAPTURE], files.get(_REFERENCES))
    if files != replayed:
        raise ValidationError("Archive outputs differ from deterministic replay")
    return _load_json(files[_REPORT]), files[_CAPTURE]
