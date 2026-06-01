from datetime import datetime
from zoneinfo import ZoneInfo

from config import Config
from trader import gates

ET = ZoneInfo("America/New_York")


def test_trading_enabled_gate():
    assert gates.gate_trading_enabled(Config(trading_enabled=False)) is not None
    assert gates.gate_trading_enabled(Config(trading_enabled=True)) is None


def test_kill_switch_gate():
    assert gates.gate_kill_switch(True) is not None
    assert gates.gate_kill_switch(False) is None


def test_market_hours_gate():
    assert gates.gate_market_hours(datetime(2026, 6, 3, 12, 0, tzinfo=ET)) is None      # RTH
    assert gates.gate_market_hours(datetime(2026, 6, 3, 20, 0, tzinfo=ET)) is not None  # after-hrs
    assert gates.gate_market_hours(datetime(2026, 6, 6, 12, 0, tzinfo=ET)) is not None  # weekend


def test_symbol_allowlist_gate():
    assert gates.gate_symbol_allowlist(["QQQ", "TQQQ"]) is None
    assert gates.gate_symbol_allowlist(["QQQ", "AAPL"]) is not None
    assert gates.gate_symbol_allowlist({"SPY"}) is not None


def test_notional_gate():
    assert gates.gate_notional(10800.0, 12000.0) is None
    assert gates.gate_notional(12000.0, 12000.0) is None       # at cap = ok
    assert gates.gate_notional(12000.01, 12000.0) is not None  # over cap
