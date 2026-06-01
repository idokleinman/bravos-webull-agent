"""Hard safety gates (spec §7.5) — PURE predicates, each individually testable.

Each gate returns ``None`` when it passes, or a short reason string when it blocks.
The handler (§3) calls them at the right control-flow points; keeping them as small
pure functions lets every gate be unit-tested in isolation (acceptance §13).

Distinction:
  * `gate_trading_enabled`, `gate_kill_switch`, `gate_market_hours` are *skip* gates
    (don't trade now; maybe later / never).
  * `gate_symbol_allowlist`, `gate_notional` are *reject* gates (the computed action
    is unsafe — never place it).
"""

from __future__ import annotations

from datetime import datetime

from config import ALLOWED_SYMBOLS, Config
from trader.market_hours import is_regular_hours, session_note


def gate_trading_enabled(config: Config) -> str | None:
    if not config.trading_enabled:
        return "TRADING_ENABLED is false"
    return None


def gate_kill_switch(engaged: bool) -> str | None:
    if engaged:
        return "KILL_SWITCH engaged"
    return None


def gate_market_hours(now: datetime) -> str | None:
    if not is_regular_hours(now):
        return f"outside regular trading hours ({session_note(now)})"
    return None


def gate_symbol_allowlist(symbols: list[str] | set[str]) -> str | None:
    bad = sorted({s for s in symbols if s not in ALLOWED_SYMBOLS})
    if bad:
        return f"symbol(s) not in allowlist {sorted(ALLOWED_SYMBOLS)}: {bad}"
    return None


def gate_notional(est_notional: float, max_order_notional: float) -> str | None:
    if est_notional > max_order_notional:
        return f"order notional ${est_notional:.2f} exceeds cap ${max_order_notional:.2f}"
    return None
