"""Reconcile plan matrix — covers acceptance §13 trading rows (pure logic)."""

import pytest

from trader.plan import build_plan, round_to_tick, stop_price_from_fill

PRICES = {"QQQ": 400.0, "TQQQ": 60.0}     # floor(11000/400)=27 ; floor(11000/60)=183
STOP_PCT = {"QQQ": 0.10, "TQQQ": 0.20}


def plan(signal, positions=None, stops=None, prices=None, target=11000.0, cap=12000.0):
    return build_plan(
        signal,
        positions or {},
        set(stops or set()),
        (prices or PRICES).__getitem__,
        target_notional=target,
        stop_pct_lookup=STOP_PCT.__getitem__,
        max_order_notional=cap,
    )


# ── Cash ────────────────────────────────────────────────────────────────────
def test_cash_liquidates_all_full_qty_and_cancels_only_present_stops():
    p = plan("Cash", positions={"QQQ": 10, "TQQQ": 5}, stops={"QQQ"})
    assert {s.symbol: (s.qty, s.had_stop) for s in p.sells} == {
        "QQQ": (10, True),    # full queried qty, had a resting stop → cancel first
        "TQQQ": (5, False),   # adopted, no stop → tolerant (nothing to cancel)
    }
    assert p.buy is None
    assert not p.no_action


def test_cash_when_flat_is_noop():
    p = plan("Cash", positions={})
    assert p.no_action
    assert p.sells == [] and p.buy is None


# ── Moderate ─────────────────────────────────────────────────────────────────
def test_moderate_from_flat_buys_qqq_with_stop_estimate():
    p = plan("Moderate", positions={})
    assert p.sells == []
    assert p.buy.symbol == "QQQ"
    assert p.buy.shares == 27                 # floor(11000/400)
    assert p.buy.est_notional == pytest.approx(10800.0)
    assert p.buy.est_stop_price == 360.0      # 400 * 0.90
    assert not p.no_action


def test_moderate_already_holding_qqq_is_left_alone_no_resize():
    p = plan("Moderate", positions={"QQQ": 5})    # adopted, undersized vs $11k
    assert p.no_action                            # no buy, no resize, no sell
    assert p.buy is None and p.sells == []


def test_moderate_from_aggressive_sells_tqqq_then_buys_qqq():
    p = plan("Moderate", positions={"TQQQ": 8}, stops={"TQQQ"})
    assert [(s.symbol, s.qty, s.had_stop) for s in p.sells] == [("TQQQ", 8, True)]
    assert p.buy.symbol == "QQQ" and p.buy.shares == 27


# ── Aggressive ───────────────────────────────────────────────────────────────
def test_aggressive_from_flat_buys_tqqq():
    p = plan("Aggressive", positions={})
    assert p.buy.symbol == "TQQQ"
    assert p.buy.shares == 183                # floor(11000/60)
    assert p.buy.est_stop_price == 48.0       # 60 * 0.80


def test_aggressive_from_moderate_sells_qqq_then_buys_tqqq():
    p = plan("Aggressive", positions={"QQQ": 27}, stops={"QQQ"})
    assert [(s.symbol, s.qty) for s in p.sells] == [("QQQ", 27)]
    assert p.buy.symbol == "TQQQ" and p.buy.shares == 183


def test_aggressive_already_holding_tqqq_left_alone():
    p = plan("Aggressive", positions={"TQQQ": 183})
    assert p.no_action


# ── Sizing / guard edges ─────────────────────────────────────────────────────
def test_whole_shares_floor():
    p = plan("Moderate", positions={}, prices={"QQQ": 401.0, "TQQQ": 60.0})
    assert p.buy.shares == 27                 # floor(11000/401)=27, not 27.4


def test_notional_cap_rejects_buy_but_keeps_sells():
    # Switch to Moderate while cap is tiny: TQQQ still liquidated, QQQ buy skipped.
    p = plan("Moderate", positions={"TQQQ": 8}, cap=5000.0)
    assert [(s.symbol, s.qty) for s in p.sells] == [("TQQQ", 8)]
    assert p.buy is None
    assert "exceeds cap" in p.buy_reject_reason
    assert not p.no_action


def test_price_too_high_rejects_buy():
    p = plan("Moderate", positions={}, prices={"QQQ": 20000.0, "TQQQ": 60.0})
    assert p.buy is None
    assert "too high" in p.buy_reject_reason


def test_sell_uses_full_queried_quantity():
    p = plan("Cash", positions={"QQQ": 13})
    assert p.sells[0].qty == 13


# ── Math helpers ─────────────────────────────────────────────────────────────
def test_round_to_tick():
    assert round_to_tick(360.005) == 360.01
    assert round_to_tick(47.999) == 48.0


def test_stop_price_from_fill_uses_actual_fill():
    assert stop_price_from_fill(402.37, 0.10) == 362.13   # 402.37*0.9 = 362.133
    assert stop_price_from_fill(61.25, 0.20) == 49.0      # 61.25*0.8 = 49.00
