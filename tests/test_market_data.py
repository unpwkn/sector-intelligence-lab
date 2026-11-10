"""Fictional fixtures for software validation only; no network access or financial claims."""

import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest

from sectorlab.market_data import (
    UNIVERSE,
    AlphaVantageProvider,
    PriceResponse,
    ProviderError,
    ValidationError,
    ingest,
)
from sectorlab.market_data.pipeline import validate
from sectorlab.market_data.providers import FIELDS

RETRIEVED = datetime(2025, 1, 10, tzinfo=UTC)
SESSIONS = pd.DatetimeIndex(["2024-07-03", "2024-07-05"])


def rows(ticker="SPY", dates=SESSIONS):
    return pd.DataFrame(
        [
            dict(zip(FIELDS, [str(day.date()), ticker, 10, 12, 9, 11, 8, 1000, 0, 1]))
            for day in dates
        ],
        columns=FIELDS,
    )


def payload(ticker="SPY"):
    return {
        "Meta Data": {"2. Symbol": ticker},
        "Time Series (Daily)": {
            "2024-07-03": {
                "1. open": "10",
                "2. high": "12",
                "3. low": "9",
                "4. close": "11",
                "5. adjusted close": "8",
                "6. volume": "1000",
                "7. dividend amount": "0.25",
                "8. split coefficient": "2",
            }
        },
    }


class MockProvider:
    name = "mock"
    price_basis = "adjusted_price_proxy"

    def __init__(self, transform=lambda frame: frame):
        self.calls = []
        self.transform = transform

    def fetch(self, ticker, start, end):
        self.calls.append(ticker)
        return PriceResponse(self.transform(rows(ticker)), {"fixture": "FICTIONAL"}, RETRIEVED)


def run(tmp_path, provider=None, **kwargs):
    return ingest(
        provider or MockProvider(),
        "2024-07-03",
        "2024-07-05",
        tmp_path / "snapshot",
        now=RETRIEVED,
        **kwargs,
    )


def test_adapter_request_mapping_and_corporate_actions(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "fictional-test-secret")
    requests = []

    def transport(url):
        requests.append(parse_qs(urlsplit(url).query))
        return payload()

    response = AlphaVantageProvider(transport=transport).fetch("SPY", "2024-07-03", "2024-07-05")
    assert requests[0]["function"] == ["TIME_SERIES_DAILY_ADJUSTED"]
    assert requests[0]["outputsize"] == ["full"]
    frame, _ = validate(response.records, "SPY", SESSIONS[:1])
    assert frame.iloc[0].adjusted_price_proxy == 8
    assert frame.iloc[0].dividend_amount == 0.25
    assert frame.iloc[0].split_coefficient == 2
    assert frame.iloc[0].close == 11  # No double adjustment applied.
    assert response.retrieved_at.tzinfo is not None


@pytest.mark.parametrize(
    "bad",
    [
        {"Note": "rate limit"},
        {"Information": "premium"},
        {"Error Message": "invalid"},
        {},
        [],
        payload("XLK"),
    ],
)
def test_provider_rejects_error_payloads(bad):
    with pytest.raises(ProviderError):
        AlphaVantageProvider("test", transport=lambda _: bad).fetch(
            "SPY", "2024-01-01", "2025-01-01"
        )


def test_no_key_or_credential_leak(monkeypatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ALPHAVANTAGE_API_KEY"):
        AlphaVantageProvider()

    def broken(url):
        raise OSError(url)

    with pytest.raises(ProviderError) as error:
        AlphaVantageProvider("secret", transport=broken).fetch("SPY", "2024-01-01", "2025-01-01")
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__


def test_malformed_daily_field():
    bad = payload()
    del bad["Time Series (Daily)"]["2024-07-03"]["5. adjusted close"]
    with pytest.raises(ProviderError, match="Malformed"):
        AlphaVantageProvider("test", transport=lambda _: bad).fetch(
            "SPY", "2024-07-03", "2024-07-05"
        )


def test_snapshot_full_universe_holiday_early_close_and_hashes(tmp_path):
    provider = MockProvider()
    out = run(tmp_path, provider)
    frame = pd.read_csv(out / "prices.csv")
    manifest = json.loads((out / "manifest.json").read_text())
    assert set(provider.calls) == set(UNIVERSE) and len(provider.calls) == 12
    assert len(frame) == 24  # July 4 holiday; July 3 early close is a valid session.
    assert not frame.total_return_verified.any()
    assert set(frame.price_basis) == {"adjusted_price_proxy"}
    assert "available_at" not in frame
    assert manifest["point_in_time_history"] is False
    assert not manifest["coverage"]["SPY"]["missing_sessions"]
    for path, digest in manifest["sha256"].items():
        assert hashlib.sha256((out / path).read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        run(tmp_path)


def test_missing_sessions_strict_or_explicit(tmp_path):
    with pytest.raises(ValidationError, match="missing sessions"):
        run(tmp_path, MockProvider(lambda f: f.iloc[:1]))
    assert not (tmp_path / "snapshot").exists()
    assert not list(tmp_path.glob(".ingest-*"))
    out = run(tmp_path, MockProvider(lambda f: f.iloc[:1]), allow_missing=True)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["coverage"]["SPY"]["missing_sessions"] == ["2024-07-05"]
    assert len(pd.read_csv(out / "prices.csv")) == 12  # No fabricated rows.


def test_identical_duplicates_collapse_conflicts_fail():
    data = rows()
    clean, report = validate(pd.concat([data, data]), "SPY", SESSIONS)
    assert len(clean) == 2 and report["exact_duplicates_removed"] == 2
    conflict = data.copy()
    conflict.loc[0, "adjusted_price_proxy"] = 9
    with pytest.raises(ValidationError, match="Conflicting"):
        validate(pd.concat([data, conflict]), "SPY", SESSIONS)


@pytest.mark.parametrize(
    "column,value",
    [
        ("adjusted_price_proxy", None),
        ("close", float("inf")),
        ("open", -1),
        ("volume", -1),
        ("volume", 1.5),
        ("dividend_amount", -1),
        ("split_coefficient", 0),
        ("high", 8),
        ("low", 12),
        ("close", "garbage"),
        ("ticker", "UNKNOWN"),
        ("date", "2024-07-04"),
        ("date", "2024-07-06"),
        ("date", "2024-07-03T12:00:00Z"),
        ("date", "not-a-date"),
    ],
)
def test_invalid_observations(column, value):
    data = rows().astype(object)
    data.loc[0, column] = value
    with pytest.raises(ValidationError):
        validate(data, "SPY", SESSIONS)


def test_empty_active_symbol_rejected_even_with_override():
    with pytest.raises(ValidationError, match="No observations"):
        validate(rows().iloc[:0], "SPY", SESSIONS, allow_missing=True)


def test_pre_launch_not_a_gap_and_no_sector_backfill():
    sessions = pd.DatetimeIndex(["2018-06-18", "2018-06-19", "2018-06-20"])
    clean, report = validate(rows("XLC", sessions[1:]), "XLC", sessions)
    assert len(clean) == report["expected_sessions"] == 2
    with pytest.raises(ValidationError, match="pre-launch"):
        validate(rows("XLC", sessions), "XLC", sessions)
    clean, report = validate(rows("XLC", sessions[:0]), "XLC", sessions[:1])
    assert clean.empty and report["expected_sessions"] == 0


def test_failed_later_symbol_never_publishes(tmp_path):
    provider = MockProvider()
    original = provider.fetch

    def fetch(ticker, start, end):
        if ticker == "SPY":
            raise ProviderError("unavailable")
        return original(ticker, start, end)

    provider.fetch = fetch
    with pytest.raises(ProviderError):
        run(tmp_path, provider)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "start,end",
    [("2024-07-06", "2024-07-07"), ("2024-07-05", "2024-07-03"), ("2027-01-04", "2027-01-05")],
)
def test_invalid_or_future_intervals(tmp_path, start, end):
    provider = MockProvider()
    with pytest.raises(ValidationError):
        ingest(provider, start, end, tmp_path / "snapshot", now=RETRIEVED)
    assert not provider.calls


def test_unclosed_early_close_session_rejected(tmp_path):
    with pytest.raises(ValidationError, match="unclosed"):
        ingest(
            MockProvider(),
            "2024-07-03",
            "2024-07-03",
            tmp_path / "snapshot",
            now=datetime(2024, 7, 3, 16, tzinfo=UTC),
        )


def test_duplicate_json_keys_not_silently_discarded():
    from sectorlab.market_data.providers import _unique_object

    with pytest.raises(ValueError, match="Duplicate JSON"):
        json.loads('{"2024-07-03": 1, "2024-07-03": 2}', object_pairs_hook=_unique_object)


@pytest.mark.parametrize("start", ["2024-7-03", "2024-07-03T00:00:00", "garbage"])
def test_noncanonical_request_dates(tmp_path, start):
    with pytest.raises(ValidationError):
        ingest(MockProvider(), start, "2024-07-05", tmp_path / "snapshot", now=RETRIEVED)
