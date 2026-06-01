"""Execute a locked plan against the broker (spec §7.3). Live path only — the
handler renders a preview for DRY_RUN without calling this.

Order of operations (the orphan-stop invariant depends on it):
  1. For each non-target holding: cancel its resting stop (tolerant), then market-sell
     the FULL queried qty; poll to filled.
  2. If the target is a fresh entry: market-buy floor(11k/price) shares; poll to filled;
     place ONE GTC stop-market off the ACTUAL average fill (QQQ −10% / TQQQ −20%).

A resting stop only ever exists on a held instrument and is always cancelled before
that instrument is sold, so no stop can survive on a zero-position symbol.
"""

from __future__ import annotations

from trader.ids import STOP_TAG, client_order_id
from trader.plan import Plan, stop_price_from_fill


def execute_plan(trader, plan: Plan, message_id: str) -> dict:
    report: dict = {
        "signal": plan.signal,
        "sells": [],
        "buy": None,
        "stop": None,
        "buy_skipped_reason": plan.buy_reject_reason,
        "success": True,
        "notes": [],
    }

    resting = trader.resting_stops() if any(s.had_stop for s in plan.sells) else {}

    # ── 1. sells first ───────────────────────────────────────────────────────
    for leg in plan.sells:
        if leg.had_stop:
            coid = resting.get(leg.symbol)
            if coid:
                try:
                    trader.cancel_order(coid)
                except Exception as e:  # noqa: BLE001 — tolerant: adopted positions may have none
                    report["notes"].append(f"cancel stop {leg.symbol} failed: {e}")

        sell_coid = client_order_id(message_id, leg.symbol, "SELL")
        trader.place_market_order(leg.symbol, "SELL", leg.qty, sell_coid)
        fill = trader.poll_fill(sell_coid)
        report["sells"].append(
            {
                "symbol": leg.symbol,
                "qty": leg.qty,
                "filled_qty": fill.filled_qty,
                "avg_price": fill.avg_price,
                "status": fill.status,
            }
        )
        if not fill.is_filled:
            report["success"] = False
            report["notes"].append(f"sell {leg.symbol} not filled ({fill.status})")

    # ── 2. buy + protective stop ─────────────────────────────────────────────
    if plan.buy and report["success"]:
        b = plan.buy
        buy_coid = client_order_id(message_id, b.symbol, "BUY")
        trader.place_market_order(b.symbol, "BUY", b.shares, buy_coid)
        fill = trader.poll_fill(buy_coid)
        report["buy"] = {
            "symbol": b.symbol,
            "shares": b.shares,
            "filled_qty": fill.filled_qty,
            "avg_price": fill.avg_price,
            "status": fill.status,
        }
        if fill.is_filled and fill.avg_price > 0 and fill.filled_qty > 0:
            stop_price = stop_price_from_fill(fill.avg_price, b.stop_pct)
            stop_coid = client_order_id(message_id, b.symbol, STOP_TAG)
            trader.place_stop_order(b.symbol, fill.filled_qty, stop_price, stop_coid)
            report["stop"] = {"symbol": b.symbol, "qty": fill.filled_qty, "stop_price": stop_price}
        else:
            report["success"] = False
            report["notes"].append(f"buy {b.symbol} not filled — no stop placed")
    elif plan.buy and not report["success"]:
        report["notes"].append("sells incomplete — buy deferred to next RTH run")

    return report
