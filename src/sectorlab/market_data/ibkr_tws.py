"""Local, request-only transport for the official IBKR TWS Python SDK."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from threading import Event, Thread
from time import monotonic, sleep

from .providers import ProviderError

BAR_TYPES = ("TRADES", "ADJUSTED_LAST")
BAR_FIELDS = ("date", "open", "high", "low", "close", "volume", "wap", "barCount")

# Connection/farm notifications from IBKR's documented message codes.
_INFO_CODES = {2104, 2106, 2107, 2108, 2158}


class IBKRRequestError(ProviderError):
    """A failed request. Store codes, never full broker or session messages."""

    def __init__(self, reason: str, code: int | None = None, *, partial: dict | None = None):
        self.reason = reason
        self.code = code
        self.partial = partial
        super().__init__(f"{reason} (IBKR code {code})" if code is not None else reason)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


class TWSHistory:
    """Resolve SMART US ETF contracts and make one historical request at a time."""

    def __init__(self, client, contract_type, timeout: float, interval: float):
        self._client = client
        self._contract_type = contract_type
        self._timeout = timeout
        self._interval = interval
        self._last_history = float("-inf")
        self._request_id = 0

    def metadata(self) -> dict:
        try:
            sdk = version("ibapi")
        except PackageNotFoundError:
            sdk = "unpackaged official SDK"
        return {"sdk": sdk, "server_version": self._client.serverVersion()}

    def _request(self, send: Callable, cancel: Callable | None = None) -> dict:
        self._request_id += 1
        request_id = self._request_id
        pending = {"done": Event(), "rows": [], "end": None, "error": None}
        self._client.pending[request_id] = pending
        requested_at = _timestamp()

        def response() -> dict:
            return {
                "requested_at": requested_at,
                "retrieved_at": _timestamp(),
                "rows": list(pending["rows"]),
                "response_bounds": pending["end"],
            }

        try:
            if not self._client.isConnected() or self._client.fault is not None:
                raise IBKRRequestError("TWS API connection interrupted", self._client.fault)
            send(request_id)
            if not pending["done"].wait(self._timeout):
                if cancel is not None and self._client.isConnected():
                    cancel(request_id)
                # Contract-details requests have no cancel method. Stop the session on timeout.
                if cancel is None:
                    self._client.disconnect()
                raise IBKRRequestError("Request timed out", partial=response())
            if pending["error"] is not None:
                raise IBKRRequestError(
                    "Broker rejected request", pending["error"], partial=response()
                )
            if self._client.fault is not None or not self._client.isConnected():
                raise IBKRRequestError(
                    "TWS API connection interrupted",
                    self._client.fault,
                    partial=response(),
                )
            return response()
        finally:
            self._client.pending.pop(request_id, None)

    def resolve(self, ticker: str) -> dict:
        contract = self._contract_type()
        contract.symbol, contract.secType, contract.currency = ticker, "STK", "USD"
        contract.exchange, contract.primaryExchange = "SMART", "ARCA"
        result = self._request(lambda req_id: self._client.reqContractDetails(req_id, contract))
        if len(result["rows"]) != 1:
            raise IBKRRequestError("Expected one SMART/ARCA USD ETF contract")
        result["contract"] = result.pop("rows")[0]
        result.pop("response_bounds")
        return result

    def history(self, contract: dict, what: str, duration: str) -> dict:
        if what not in BAR_TYPES:
            raise ValueError("Unsupported historical data type")
        instrument = self._contract_type()
        instrument.conId = contract["conId"]
        instrument.symbol = contract["symbol"]
        instrument.exchange, instrument.primaryExchange = "SMART", contract["primaryExchange"]
        instrument.secType, instrument.currency = "STK", "USD"
        sleep(max(0, self._interval - (monotonic() - self._last_history)))
        self._last_history = monotonic()
        # ADJUSTED_LAST needs a blank end time. Use the same Now anchor for TRADES.
        return self._request(
            lambda req_id: self._client.reqHistoricalData(
                req_id,
                instrument,
                "",
                duration,
                "1 day",
                what,
                1,
                1,
                False,
                [],
            ),
            self._client.cancelHistoricalData,
        )


@contextmanager
def connect_tws(port: int, client_id: int, timeout: float) -> Iterator[TWSHistory]:
    """Connect only to an already authenticated local TWS or IB Gateway."""
    try:
        from ibapi.client import EClient
        from ibapi.contract import Contract
        from ibapi.wrapper import EWrapper
    except ImportError:
        raise ProviderError("Install the official IBKR TWS Python SDK locally") from None

    class Client(EWrapper, EClient):
        def __init__(self):
            EWrapper.__init__(self)
            EClient.__init__(self, self)
            self.ready = Event()
            self.pending = {}
            self.fault = None

        def nextValidId(self, _order_id):
            self.ready.set()

        def connectionClosed(self):
            self.fault = 504
            self.ready.set()
            for item in tuple(self.pending.values()):
                item["done"].set()

        def error(self, reqId, *args):
            # Recent SDKs include errorTime before errorCode. Earlier ones omit it.
            code = args[1] if len(args) >= 3 and isinstance(args[1], int) else args[0]
            if code in _INFO_CODES:
                return
            if reqId < 0:
                # Global connectivity and pacing failures affect pending requests as well.
                self.fault = code
                self.ready.set()
                for item in tuple(self.pending.values()):
                    item["done"].set()
            elif item := self.pending.get(reqId):
                item["error"] = code
                item["done"].set()

        def contractDetails(self, reqId, contractDetails):
            if item := self.pending.get(reqId):
                contract = contractDetails.contract
                fields = (
                    "conId",
                    "symbol",
                    "localSymbol",
                    "secType",
                    "currency",
                    "exchange",
                    "primaryExchange",
                    "tradingClass",
                )
                item["rows"].append(
                    {key: getattr(contract, key) for key in fields}
                    | {"timeZoneId": contractDetails.timeZoneId}
                )

        def contractDetailsEnd(self, reqId):
            if item := self.pending.get(reqId):
                item["done"].set()

        def historicalData(self, reqId, bar):
            if item := self.pending.get(reqId):
                # Represent wire-decoded decimals as strings, including all bar fields.
                item["rows"].append({key: str(getattr(bar, key)) for key in BAR_FIELDS})

        def historicalDataEnd(self, reqId, start, end):
            if item := self.pending.get(reqId):
                item["end"] = [start, end]
                item["done"].set()

    client = Client()
    thread = None
    try:
        try:
            client.connect("127.0.0.1", port, clientId=client_id)
        except (OSError, ValueError):
            raise IBKRRequestError("Cannot connect to loopback TWS or IB Gateway") from None
        if not client.isConnected():
            raise IBKRRequestError("Cannot connect to loopback TWS or IB Gateway", client.fault)
        thread = Thread(target=client.run, daemon=True)
        thread.start()
        if not client.ready.wait(timeout) or client.fault is not None:
            raise IBKRRequestError("TWS API handshake failed", client.fault)
        yield TWSHistory(client, Contract, timeout, interval=2)
    finally:
        client.disconnect()
        if thread is not None:
            thread.join(timeout=2)
