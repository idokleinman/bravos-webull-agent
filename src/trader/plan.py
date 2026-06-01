"""Reconcile plan builder (spec §7.3) — PURE, no broker I/O.

Given the parsed signal, current positions, and which positions carry a resting
stop, produce the *locked* plan that the executor will carry out:

  * SELL legs  — every held instrument that is NOT the target, full queried qty.
                 (`had_stop` flags whether its resting stop must be cancelled first.)
  * BUY leg    — only when the account is FLAT in the target (adopted/already-held
                 target is left alone: no resize, no new stop). Sized
                 `floor(target_notional / ref_price)`, whole shares.
  * stop est.  — for the buy, an ESTIMATE off the snapshot price; the real stop is
                 recomputed from the actual fill price at execution.

Quantities here are what execute unchanged (spec §6.1); only fill prices float.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

from config import ALLOWED_SYMBOLS, SIGNAL_TARGETS


def round_to_tick(price: float) -> float:
    """Round to a valid US-equity penny tick (>$1 ⇒ $0.01)."""
    return float(Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class SellLeg:
    symbol: str
    qty: int
    had_stop: bool  # cancel this resting stop before selling (tolerant if absent)


@dataclass(frozen=True)
class BuyLeg:
    symbol: str
    shares: int
    ref_price: float        # snapshot used to size; NOT the fill
    est_notional: float
    stop_pct: float
    est_stop_price: float   # estimate; recomputed from fill at execution


@dataclass(frozen=True)
class Plan:
    signal: str
    target_symbol: str | None
    sells: list[SellLeg] = field(default_factory=list)
    buy: BuyLeg | None = None
    # Set when the target buy cannot be placed (price too high / over notional cap /
    # bad price). Sells still execute; the buy is skipped and reported.
    buy_reject_reason: str | None = None

    @property
    def no_action(self) -> bool:
        """True ⇒ already aligned: nothing to sell, nothing to buy."""
        return not self.sells and self.buy is None and self.buy_reject_reason is None


def build_plan(
    signal: str,
    positions: dict[str, int],
    positions_with_stop: set[str],
    price_lookup: Callable[[str], float],
    *,
    target_notional: float,
    stop_pct_lookup: Callable[[str], float],
    max_order_notional: float,
) -> Plan:
    target = SIGNAL_TARGETS[signal]

    held = {s: q for s, q in positions.items() if q > 0 and s in ALLOWED_SYMBOLS}

    sells = [
        SellLeg(symbol=s, qty=held[s], had_stop=s in positions_with_stop)
        for s in sorted(held)
        if s != target
    ]

    buy: BuyLeg | None = None
    reject: str | None = None

    if target is not None and target not in held:
        # Flat in the target → fresh entry. (If target is already held — adopted or
        # agent-bought — we leave it entirely alone: no buy, no resize.)
        price = price_lookup(target)
        if price <= 0:
            reject = f"invalid snapshot price for {target}: {price}"
        else:
            shares = math.floor(target_notional / price)
            if shares < 1:
                reject = f"{target} price {price:.2f} too high for ${target_notional:.0f} notional"
            else:
                est_notional = shares * price
                if est_notional > max_order_notional:
                    reject = (
                        f"est notional ${est_notional:.2f} exceeds cap "
                        f"${max_order_notional:.2f}"
                    )
                else:
                    stop_pct = stop_pct_lookup(target)
                    buy = BuyLeg(
                        symbol=target,
                        shares=shares,
                        ref_price=price,
                        est_notional=est_notional,
                        stop_pct=stop_pct,
                        est_stop_price=round_to_tick(price * (1 - stop_pct)),
                    )

    return Plan(
        signal=signal,
        target_symbol=target,
        sells=sells,
        buy=buy,
        buy_reject_reason=reject,
    )


def stop_price_from_fill(fill_price: float, stop_pct: float) -> float:
    """Protective stop based on the ACTUAL average fill (spec §7.4a)."""
    return round_to_tick(fill_price * (1 - stop_pct))
