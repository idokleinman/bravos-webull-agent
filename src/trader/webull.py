"""Webull OpenAPI adapter (spec §7). Isolates every broker call behind one class.

Verified against the official SDK source (webull-python-sdk-{core,trade,mdata},
May 2026):
  * US order path = v1 ``OrderOperation.place_order_v2(account_id, stock_order_dict)``
    (the v2 OrderOperationV2.place_order is documented as not-yet-available for US).
  * GTC stop on equities supported via order_type STOP_LOSS + time_in_force GTC.

The `_pick`/`_parse_*` helpers below try plausible candidate field names for snapshot
price, position symbol/qty, and order fill avg-price/qty (not fully documented by
Webull). Correct here if live API responses use different field names.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from config import (
    ENTRUST_QTY,
    INSTRUMENT_EQUITY,
    MARKET_US,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP,
    SIDE_SELL,
    TIF_DAY,
    TIF_GTC,
    WEBULL_TRADING_SESSION,
)

# Candidate field names — correct here if live API responses differ.
_PRICE_FIELDS = ("close", "price", "last", "lastPrice", "deal", "tradePrice", "pPrice")
_SYMBOL_FIELDS = ("symbol", "ticker", "instrument")
_QTY_FIELDS = ("quantity", "qty", "position", "holdings")
_FILLED_QTY_FIELDS = ("filled_quantity", "filledQuantity", "filled_qty", "cumQty")
_AVG_PRICE_FIELDS = ("avg_fill_price", "avgFillPrice", "filled_avg_price", "avgPrice", "price")
_STATUS_FIELDS = ("order_status", "status", "orderStatus")

FILLED_STATES = {"FILLED", "FILLED_ALL", "ALL_FILLED", "FULL_FILLED"}
DEAD_STATES = {"CANCELLED", "CANCELED", "FAILED", "REJECTED", "EXPIRED"}


def _pick(d: dict, fields, default=None):
    for f in fields:
        if f in d and d[f] not in (None, ""):
            return d[f]
    return default


def build_stock_order(
    *,
    client_order_id: str,
    symbol: str,
    side: str,
    qty: int,
    order_type: str,
    tif: str,
    stop_price: float | None = None,
) -> dict:
    """Construct the US equity order body (verified shape from the SDK demo)."""
    order = {
        "client_order_id": client_order_id,
        "symbol": symbol,
        "instrument_type": INSTRUMENT_EQUITY,
        "market": MARKET_US,
        "order_type": order_type,
        "quantity": str(int(qty)),
        "support_trading_session": WEBULL_TRADING_SESSION,
        "side": side,
        "time_in_force": tif,
        "entrust_type": ENTRUST_QTY,
    }
    if stop_price is not None:
        order["stop_price"] = f"{stop_price:.2f}"
    return order


@dataclass
class OrderRef:
    client_order_id: str
    order_id: str | None
    raw: dict = field(default_factory=dict)


@dataclass
class FillResult:
    status: str
    filled_qty: int
    avg_price: float
    raw: dict = field(default_factory=dict)

    @property
    def is_filled(self) -> bool:
        return self.status.upper() in FILLED_STATES or (
            self.filled_qty > 0 and self.status.upper() not in DEAD_STATES
            and self.avg_price > 0
        )


class WebullTrader:
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        region_id: str,
        account_id: str,
        endpoint: str | None = None,
    ):
        from webullsdkcore.client import ApiClient  # lazy: keeps unit tests SDK-free
        from webullsdkmdata.common.category import Category
        from webullsdkmdata.quotes.market_data import MarketData
        from webullsdktrade.trade.account_info import Account
        from webullsdktrade.trade.order_operation import OrderOperation

        self._account_id = account_id
        self._client = ApiClient(app_key, app_secret, region_id)
        if endpoint:
            self._client.add_endpoint(region_id, endpoint)
        self._orders = OrderOperation(self._client)
        self._account = Account(self._client)
        self._md = MarketData(self._client)
        self._category = Category.US_ETF

    # ── reads ────────────────────────────────────────────────────────────────
    def get_positions(self) -> dict[str, int]:
        """Return {symbol: qty} across all holdings (paginated)."""
        out: dict[str, int] = {}
        last_instrument_id = None
        while True:
            resp = self._account.get_account_position(
                self._account_id, last_instrument_id=last_instrument_id
            )
            data = resp.json()
            holdings = data.get("holdings", []) or []
            for h in holdings:
                sym = _pick(h, _SYMBOL_FIELDS)
                if isinstance(sym, dict):
                    sym = _pick(sym, _SYMBOL_FIELDS)
                qty = int(float(_pick(h, _QTY_FIELDS, 0) or 0))
                if sym and qty:
                    out[sym] = out.get(sym, 0) + qty
                last_instrument_id = _pick(h, ("instrument_id", "instrumentId"))
            if not data.get("has_next") or not holdings:
                break
        return out

    def get_open_orders(self) -> list[dict]:
        resp = self._orders.list_open_orders(self._account_id, page_size=100)
        data = resp.json()
        return data.get("orders", data) if isinstance(data, dict) else data

    def resting_stops(self) -> dict[str, str]:
        """{symbol: client_order_id} for open GTC stop-loss SELL orders."""
        stops: dict[str, str] = {}
        for o in self.get_open_orders():
            otype = str(_pick(o, ("order_type", "orderType"), "")).upper().replace(" ", "_")
            side = str(_pick(o, ("side",), "")).upper()
            if "STOP" in otype and side == SIDE_SELL:
                sym = _pick(o, _SYMBOL_FIELDS)
                coid = _pick(o, ("client_order_id", "clientOrderId"))
                if sym and coid:
                    stops[sym] = coid
        return stops

    def snapshot_price(self, symbol: str) -> float:
        resp = self._md.get_snapshot([symbol], self._category)
        data = resp.json()
        rows = data if isinstance(data, list) else data.get("data", data.get("snapshots", []))
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            return 0.0
        return float(_pick(rows[0], _PRICE_FIELDS, 0) or 0)

    # ── writes ───────────────────────────────────────────────────────────────
    def place_market_order(self, symbol: str, side: str, qty: int, client_order_id: str) -> OrderRef:
        order = build_stock_order(
            client_order_id=client_order_id, symbol=symbol, side=side, qty=qty,
            order_type=ORDER_TYPE_MARKET, tif=TIF_DAY,
        )
        resp = self._orders.place_order_v2(self._account_id, order)
        raw = resp.json()
        return OrderRef(client_order_id, _pick(raw, ("order_id", "orderId")), raw)

    def place_stop_order(self, symbol: str, qty: int, stop_price: float, client_order_id: str) -> OrderRef:
        order = build_stock_order(
            client_order_id=client_order_id, symbol=symbol, side=SIDE_SELL, qty=qty,
            order_type=ORDER_TYPE_STOP, tif=TIF_GTC, stop_price=stop_price,
        )
        resp = self._orders.place_order_v2(self._account_id, order)
        raw = resp.json()
        return OrderRef(client_order_id, _pick(raw, ("order_id", "orderId")), raw)

    def cancel_order(self, client_order_id: str) -> dict:
        resp = self._orders.cancel_order(self._account_id, client_order_id)
        return resp.json()

    def get_order(self, client_order_id: str) -> FillResult | None:
        for o in self._today_orders():
            if _pick(o, ("client_order_id", "clientOrderId")) == client_order_id:
                return FillResult(
                    status=str(_pick(o, _STATUS_FIELDS, "")),
                    filled_qty=int(float(_pick(o, _FILLED_QTY_FIELDS, 0) or 0)),
                    avg_price=float(_pick(o, _AVG_PRICE_FIELDS, 0) or 0),
                    raw=o,
                )
        return None

    def _today_orders(self) -> list[dict]:
        resp = self._orders.list_today_orders(self._account_id, page_size=100)
        data = resp.json()
        return data.get("orders", data) if isinstance(data, dict) else data

    def poll_fill(self, client_order_id: str, timeout_s: float = 60, interval_s: float = 2) -> FillResult:
        """Poll until the order fills, dies, or times out. QQQ/TQQQ fill near-instantly in RTH."""
        deadline = time.monotonic() + timeout_s
        last = FillResult(status="UNKNOWN", filled_qty=0, avg_price=0.0)
        while time.monotonic() < deadline:
            res = self.get_order(client_order_id)
            if res:
                last = res
                if res.is_filled or res.status.upper() in DEAD_STATES:
                    return res
            time.sleep(interval_s)
        return last
