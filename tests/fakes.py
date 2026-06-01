"""Test doubles for handler integration tests (Layer 1)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from email_provider.gmail import Message
from notifier.telegram import TIMEOUT
from trader.webull import FillResult

ET = ZoneInfo("America/New_York")
RTH = datetime(2026, 6, 3, 12, 0, tzinfo=ET)        # Wed midday
WEEKEND = datetime(2026, 6, 6, 12, 0, tzinfo=ET)    # Saturday


class FakeTrader:
    def __init__(self, positions=None, prices=None, stops=None, fail_buy=False):
        self.positions = dict(positions or {})
        self.prices = dict(prices or {"QQQ": 400.0, "TQQQ": 60.0})
        self.stops = dict(stops or {})       # symbol -> client_order_id
        self.orders: list[dict] = []          # every place_* call
        self.cancels: list[str] = []
        self.fail_buy = fail_buy              # simulate a buy that never fills

    def get_positions(self):
        return dict(self.positions)

    def resting_stops(self):
        return dict(self.stops)

    def snapshot_price(self, symbol):
        return self.prices[symbol]

    def place_market_order(self, symbol, side, qty, client_order_id):
        self.orders.append(
            {"type": "MARKET", "side": side, "symbol": symbol, "qty": qty, "coid": client_order_id}
        )
        return type("Ref", (), {"client_order_id": client_order_id, "order_id": "o-" + client_order_id})

    def place_stop_order(self, symbol, qty, stop_price, client_order_id):
        self.orders.append(
            {"type": "STOP", "side": "SELL", "symbol": symbol, "qty": qty,
             "stop_price": stop_price, "coid": client_order_id}
        )
        return type("Ref", (), {"client_order_id": client_order_id, "order_id": "s-" + client_order_id})

    def cancel_order(self, client_order_id):
        self.cancels.append(client_order_id)
        return {"ok": True}

    def poll_fill(self, client_order_id):
        # Find the matching market order; fill fully at the symbol's snapshot price.
        for o in self.orders:
            if o["coid"] == client_order_id and o["type"] == "MARKET":
                if self.fail_buy and o["side"] == "BUY":
                    return FillResult(status="PENDING", filled_qty=0, avg_price=0.0)
                return FillResult(status="FILLED", filled_qty=o["qty"],
                                  avg_price=self.prices[o["symbol"]])
        return FillResult(status="UNKNOWN", filled_qty=0, avg_price=0.0)

    # convenience for assertions
    def market_orders(self):
        return [o for o in self.orders if o["type"] == "MARKET"]

    def stop_orders(self):
        return [o for o in self.orders if o["type"] == "STOP"]


class FakeNotifier:
    def __init__(self, decision=TIMEOUT):
        self.sent: list[str] = []
        self._decision = decision

    def send(self, text):
        self.sent.append(text)

    def delete_webhook(self):
        pass

    def drain_updates(self):
        pass

    def poll_for_decision(self, window_seconds, after_ts, now=None):
        return self._decision


class FakeSecrets:
    def __init__(self, kill=False):
        self._kill = kill

    def get(self, name):
        return f"fake-{name}"

    def kill_switch_engaged(self):
        return self._kill


class FakeGmail:
    def __init__(self, message: Message | None):
        self._msg = message

    def fetch_latest(self):
        return self._msg

    def fetch_latest_from(self, sender):
        return self._msg


def bravos_message(body="Model Signal (Moderate) has been published", msg_id="m-1",
                   from_header="Bravos <info@bravosresearch.com>",
                   auth="dkim=pass; spf=pass; dmarc=pass"):
    return Message(id=msg_id, from_header=from_header, auth_results=auth,
                   body=body, internal_date=1)
