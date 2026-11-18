"""Offline IBKR adapter, reconciliation and replay checks with fictional observations."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from sectorlab.backtest import run_backtest
from sectorlab.backtest.engine import RETROSPECTIVE
from sectorlab.config import FIRST_SESSION
from sectorlab.market_data import UNIVERSE, ValidationError, import_csv, load_snapshot
from sectorlab.market_data.ibkr import (
    _calendar,
    _json_bytes,
    capture_history,
    publish_archive,
    verify_archive,
)
from sectorlab.market_data.ibkr_tws import BAR_TYPES, IBKRRequestError, TWSHistory

START, END = "2024-06-24", "2024-07-05"


class FictionalBroker:
    def __init__(self, start, end):
        self.bars = {}
        self.calls = []
        self.events = []
        self.raw = []
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        self.sessions = _calendar(start, end).sessions_in_range(start, end)
        split, dividend = self.sessions[-4], self.sessions[-3]
        for ticker in UNIVERSE:
            self.bars[ticker] = {kind: [] for kind in BAR_TYPES}
            for session in self.sessions[self.sessions >= pd.Timestamp(FIRST_SESSION[ticker])]:
                date = session.strftime("%Y-%m-%d")
                raw, trades, adjusted = 100, 100, 100
                if ticker == "SPY":
                    raw = 100 if session < split else 50
                    trades = 50
                    adjusted = 49 if session < dividend else 50
                    if session in (split, dividend):
                        self.events.append(
                            {
                                "date": date,
                                "ticker": ticker,
                                "dividend_amount": int(session == dividend),
                                "split_coefficient": 2 if session == split else 1,
                            }
                        )
                self.raw.append(
                    {"date": date, "ticker": ticker, "volume": 1000}
                    | dict.fromkeys(("open", "high", "low", "close"), raw)
                )
                for kind, price in zip(BAR_TYPES, (trades, adjusted)):
                    bar = {
                        "date": session.strftime("%Y%m%d"),
                        "volume": "1000",
                        "wap": str(price),
                        "barCount": "10",
                    }
                    self.bars[ticker][kind].append(
                        bar | dict.fromkeys(("open", "high", "low", "close"), str(price))
                    )

    @staticmethod
    def metadata():
        return {"sdk": "mock", "server_version": 0}

    @staticmethod
    def stamp():
        timestamp = datetime.now(UTC).isoformat()
        return {"requested_at": timestamp, "retrieved_at": timestamp}

    def resolve(self, ticker):
        return self.stamp() | {
            "contract": {
                "conId": UNIVERSE.index(ticker) + 1,
                "symbol": ticker,
                "secType": "STK",
                "currency": "USD",
                "primaryExchange": "ARCA",
                "timeZoneId": "US/Eastern",
            },
        }

    def history(self, contract, what, duration):
        ticker = contract["symbol"]
        self.calls.append((ticker, what, duration))
        rows = self.bars[ticker][what]
        if isinstance(rows, Exception):
            raise rows
        return self.stamp() | {"rows": rows, "response_bounds": ["begin", "end"]}

    def references(self, start, end):
        common = {
            "start": start,
            "end": end,
            "universe": list(UNIVERSE),
            "retrieved_at": datetime.now(UTC).isoformat(),
            "license_reference": "fictional test",
        }
        return {
            "broker_actions": common
            | {
                "source": "IBKR",
                "source_reference": "mock broker action history",
                "rows": deepcopy(self.events),
            },
            "independent_actions": common
            | {
                "source": "Fictional sponsor",
                "source_reference": "mock sponsor action history",
                "rows": deepcopy(self.events),
            },
            "as_traded": common
            | {
                "source": "Fictional exchange",
                "source_reference": "mock as-traded OHLCV",
                "price_basis": "as_traded",
                "rows": deepcopy(self.raw),
            },
        }


@pytest.fixture
def broker():
    return FictionalBroker(START, END)


def test_six_year_plus_probe_exports_to_phase6a_and_existing_backtest(tmp_path):
    start, end = "2018-06-18", END
    broker = FictionalBroker(start, end)
    # The empty-end API can return later rows; archive them without passing them through.
    later = deepcopy(broker.bars["XLK"]["TRADES"][-1])
    later["date"] = "20240708"
    broker.bars["XLK"]["TRADES"].append(later)
    capture = capture_history(broker, start, end)
    assert len(broker.calls) == 24
    assert all(int(duration.split()[0]) > 5 for _, _, duration in broker.calls)
    assert {what for _, what, _ in broker.calls} == set(BAR_TYPES)
    assert capture["request"]["endDateTime"] == ""
    assert capture["request"]["keepUpToDate"] is False
    report = publish_archive(
        _json_bytes(capture),
        tmp_path / "archive",
        _json_bytes(broker.references(start, end)),
    )
    assert report["phase6a_export_eligible"]
    assert report["ibkr_ohlc_satisfies_phase6a"] is False
    assert report["instruments"]["XLK"]["series"]["TRADES"]["response_last_session"] == "2024-07-08"
    assert report["instruments"]["XLC"]["expected_sessions"] == len(broker.sessions) - 1
    assert report["instruments"]["XLK"]["series"]["TRADES"]["observed_last_session"] == end
    assert (
        report["instruments"]["XLK"]["series"]["TRADES"]["observed_sessions_older_than_five_years"]
        > 0
    )
    assert report["instruments"]["SPY"]["reconciliation"]["dividend_sessions"] == 1
    assert report["instruments"]["SPY"]["reconciliation"]["split_sessions"] == 1
    replayed, raw = verify_archive(tmp_path / "archive")
    assert replayed == report
    assert raw == _json_bytes(capture)
    snapshot_path = import_csv(
        tmp_path / "archive/prices.csv",
        tmp_path / "archive/provenance.json",
        start,
        end,
        tmp_path / "snapshot",
    )
    snapshot = load_snapshot(snapshot_path)
    first = snapshot.prices.query("ticker == 'SPY'").iloc[0]
    assert (first.close, first.adjusted_price_proxy) == (100, 49)
    assert snapshot.prices.date.max() == pd.Timestamp(end)
    assert snapshot.manifest["total_return_verified"] is False
    result = run_backtest(snapshot_path)
    assert result.metadata["research_classification"] == RETROSPECTIVE
    assert result.metadata["point_in_time_history"] is False


def test_missing_duplicate_session_invalid_prices_and_request_error(tmp_path, broker):
    # July 4 and June weekends belong in the raw evidence if sent, and block the export.
    sample = broker.bars["XLK"]["TRADES"]
    sample.append(sample[0].copy())
    sample.append(sample[0] | {"date": "20240704"})
    del sample[1]
    bad = broker.bars["XLC"]["ADJUSTED_LAST"][0]
    bad["high"] = "1"
    broker.bars["XLV"]["TRADES"] = IBKRRequestError("Broker rejected request", 162)
    report = publish_archive(
        _json_bytes(capture_history(broker, START, END)),
        tmp_path / "archive",
        _json_bytes(broker.references(START, END)),
    )
    details = report["instruments"]
    xlk = details["XLK"]["series"]["TRADES"]
    assert xlk["missing_sessions"] == ["2024-06-25"]
    assert xlk["duplicate_dates"] == [START]
    assert xlk["unexpected_sessions"] == ["2024-07-04"]
    assert details["XLC"]["series"]["ADJUSTED_LAST"]["error"] == "Inconsistent OHLC"
    assert details["XLV"]["series"]["TRADES"]["error"]["code"] == 162
    assert not report["phase6a_export_eligible"]
    assert not (tmp_path / "archive/prices.csv").exists()


@pytest.mark.parametrize("problem", ["events", "missing_cash_adjustment", "split_basis", "no_refs"])
def test_reconciliation_blocks_unproven_adjustments(tmp_path, broker, problem):
    refs = broker.references(START, END)
    if problem == "events":
        refs["independent_actions"]["rows"][1]["dividend_amount"] *= 2
    if problem == "missing_cash_adjustment":
        broker.bars["SPY"]["ADJUSTED_LAST"] = broker.bars["SPY"]["TRADES"]
    if problem == "split_basis":
        bars = broker.bars["SPY"]["TRADES"]
        bars[0].update(dict.fromkeys(("open", "high", "low", "close"), "100"))
    evidence = None if problem == "no_refs" else _json_bytes(refs)
    report = publish_archive(
        _json_bytes(capture_history(broker, START, END)),
        tmp_path / "archive",
        evidence,
    )
    status = report["instruments"]["SPY"]["reconciliation"]
    assert not report["phase6a_export_eligible"]
    assert not (tmp_path / "archive/prices.csv").exists()
    if problem == "no_refs":
        assert status["status"] == "requires_complete_independent_references"
    else:
        assert status["status"] == "failed"
        assert {
            "events": "ledgers disagree",
            "missing_cash_adjustment": "ADJUSTED_LAST",
            "split_basis": "TRADES",
        }[problem] in status["reason"]


def test_archive_replay_detects_outputs_changed_even_if_rehashed(tmp_path, broker):
    path = tmp_path / "archive"
    raw = _json_bytes(capture_history(broker, START, END))
    evidence = _json_bytes(broker.references(START, END))
    publish_archive(raw, path, evidence)
    verify_archive(path)
    assert (path / "references.json").read_bytes() == evidence
    with pytest.raises(FileExistsError):
        publish_archive(raw, path, evidence)
    report = path / "report.json"
    report.write_bytes(report.read_bytes().replace(b'"ADJUSTED_LAST"', b'"OTHER"', 1))
    with pytest.raises(ValidationError, match="Checksum mismatch"):
        verify_archive(path)
    manifest = path / "manifest.json"
    payload = json.loads(manifest.read_bytes())
    payload["sha256"]["report.json"] = hashlib.sha256(report.read_bytes()).hexdigest()
    manifest.write_bytes(_json_bytes(payload))
    with pytest.raises(ValidationError, match="deterministic replay"):
        verify_archive(path)


def test_transport_requests_static_daily_regular_session_data_only():
    class FakeClient:
        def __init__(self):
            self.pending = {}
            self.fault = None
            self.requests = []

        def isConnected(self):
            return True

        def reqHistoricalData(self, request_id, contract, *args):
            self.requests.append((contract, args))
            self.pending[request_id]["end"] = ["start", "end"]
            self.pending[request_id]["done"].set()

        def cancelHistoricalData(self, _request_id):
            pytest.fail("Completed request should not be canceled")

    client = FakeClient()
    transport = TWSHistory(client, SimpleNamespace, timeout=1, interval=0)
    for kind in BAR_TYPES:
        transport.history({"conId": 1, "symbol": "XLK", "primaryExchange": "ARCA"}, kind, "7 Y")
    for kind, (contract, args) in zip(BAR_TYPES, client.requests):
        assert (contract.conId, contract.secType, contract.exchange) == (1, "STK", "SMART")
        assert args == ("", "7 Y", "1 day", kind, 1, 1, False, [])
    assert client.pending == {}


def test_verify_cli_replays_bar_only_archive_and_preserves_bad_reference_bytes(tmp_path, broker):
    path = tmp_path / "capture"
    raw = _json_bytes(capture_history(broker, START, END))
    malformed_references = b"invalid JSON"
    report = publish_archive(raw, path, malformed_references)
    assert report["reference_error"] == "Invalid IBKR archive JSON"
    assert (path / "references.json").read_bytes() == malformed_references
    assert (path / "capture.json").read_bytes() == raw
    cli = Path(__file__).resolve().parents[1] / "scripts" / "ibkr_history.py"
    result = subprocess.run(
        [sys.executable, str(cli), "verify", "--archive", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Evaluated 12 ETFs" in result.stdout
    assert "Phase 6A export blocked" in result.stdout
    assert not result.stderr
