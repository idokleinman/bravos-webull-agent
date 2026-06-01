"""Handler control-flow integration tests (spec §3, acceptance §13). All with fakes."""

from __future__ import annotations

import handler as H
from config import Config
from notifier.telegram import CANCEL
from state.s3_state import InMemoryStateStore, State

from tests.fakes import (
    RTH,
    WEEKEND,
    FakeGmail,
    FakeNotifier,
    FakeSecrets,
    FakeTrader,
    bravos_message,
)


def make_deps(cfg, *, trader=None, notifier=None, secrets=None, gmail=None,
              state=None, now=RTH):
    trader = trader or FakeTrader()
    return H.Deps(
        config=cfg,
        secrets=secrets or FakeSecrets(),
        state_store=InMemoryStateStore(state or State()),
        gmail=gmail or FakeGmail(bravos_message()),
        notifier=notifier or FakeNotifier(),
        make_trader=lambda: trader,
        now=lambda: now,
    )


def cfg(**kw):
    base = dict(dry_run=True, trading_enabled=False, confirm_window_minutes=0,
                target_notional=11000.0, max_order_notional=12000.0)
    base.update(kw)
    return Config(**base)


# ── Notification + dry-run preview (Layer 2) ─────────────────────────────────
def test_injected_signal_dry_run_previews_no_orders():
    trader = FakeTrader()
    notifier = FakeNotifier()
    deps = make_deps(cfg(), trader=trader, notifier=notifier)
    out = H.run({"test_signal": "Aggressive", "message_id": "inj-1"}, deps)
    assert out["action"] == "preview"
    assert trader.orders == []                       # nothing placed
    joined = "\n".join(notifier.sent)
    assert "New Bravos signal: AGGRESSIVE" in joined  # #1
    assert "DRY RUN" in joined                        # #4 preview


def test_notification_dedup():
    notifier = FakeNotifier()
    store = InMemoryStateStore(State())
    deps = H.Deps(cfg(), FakeSecrets(), store, FakeGmail(bravos_message(msg_id="dup")),
                  notifier, lambda: FakeTrader(), lambda: RTH)
    first = H.run({}, deps)
    assert first["alerted"] is True
    second = H.run({}, deps)
    assert second["alerted"] is False                 # same message id ⇒ no re-ping


# ── Anti-spoof (acceptance §13) ──────────────────────────────────────────────
def test_auth_failure_blocks_and_notifies():
    notifier = FakeNotifier()
    bad = bravos_message(auth="dkim=pass; spf=fail; dmarc=pass")
    deps = make_deps(cfg(), notifier=notifier, gmail=FakeGmail(bad))
    out = H.run({}, deps)
    assert out["action"] == "auth_failed"
    assert out["alerted"] is False
    assert any("Rejected" in m for m in notifier.sent)


def test_spoofed_sender_blocked():
    bad = bravos_message(from_header="evil@elsewhere.com",
                         auth="dkim=pass; spf=pass; dmarc=pass")
    out = H.run({}, make_deps(cfg(), gmail=FakeGmail(bad)))
    assert out["action"] == "auth_failed"


def test_unparseable_email_no_trade():
    msg = bravos_message(body="Just a newsletter, nothing here")
    notifier = FakeNotifier()
    out = H.run({}, make_deps(cfg(), notifier=notifier, gmail=FakeGmail(msg)))
    assert out["action"] == "unparsed"


# ── Kill switch ──────────────────────────────────────────────────────────────
def test_kill_switch_halts():
    deps = make_deps(cfg(dry_run=False, trading_enabled=True),
                     secrets=FakeSecrets(kill=True))
    out = H.run({"test_signal": "Moderate", "message_id": "k1"}, deps)
    assert out["action"] == "kill_switch"


# ── Live armed paths ─────────────────────────────────────────────────────────
def live_cfg(**kw):
    return cfg(dry_run=False, trading_enabled=True, **kw)


def test_first_run_baseline_trades_nothing():
    trader = FakeTrader(positions={"TQQQ": 10})       # adopted position present
    deps = make_deps(live_cfg(), trader=trader, state=State())  # no last_executed ⇒ first run
    out = H.run({"test_signal": "Aggressive", "message_id": "base-1"}, deps)
    assert out["action"] == "baseline"
    assert trader.orders == []                         # adopted position untouched
    assert deps.state_store.load().last_executed_message_id == "base-1"


def test_after_hours_alerts_but_defers():
    deps = make_deps(live_cfg(), state=State(last_executed_message_id="seed"), now=WEEKEND)
    out = H.run({"test_signal": "Moderate", "message_id": "ah-1"}, deps)
    assert out["action"] == "after_hours"


def test_already_handled_no_action():
    deps = make_deps(live_cfg(), state=State(last_executed_message_id="done"))
    out = H.run({"test_signal": "Moderate", "message_id": "done"}, deps)
    assert out["action"] == "already_handled"


def test_already_aligned_noop_sets_executed():
    trader = FakeTrader(positions={"QQQ": 27})
    deps = make_deps(live_cfg(), trader=trader, state=State(last_executed_message_id="seed"))
    out = H.run({"test_signal": "Moderate", "message_id": "al-1"}, deps)
    assert out["action"] == "aligned"
    assert trader.orders == []
    assert deps.state_store.load().last_executed_message_id == "al-1"


def test_moderate_from_flat_buys_and_stops():
    trader = FakeTrader(positions={})
    deps = make_deps(live_cfg(), trader=trader, state=State(last_executed_message_id="seed"))
    out = H.run({"test_signal": "Moderate", "message_id": "mod-1"}, deps)
    assert out["action"] == "executed"
    buys = [o for o in trader.market_orders() if o["side"] == "BUY"]
    assert buys == [{"type": "MARKET", "side": "BUY", "symbol": "QQQ", "qty": 27,
                     "coid": buys[0]["coid"]}]
    stops = trader.stop_orders()
    assert len(stops) == 1
    assert stops[0]["symbol"] == "QQQ" and stops[0]["qty"] == 27
    assert stops[0]["stop_price"] == 360.0             # 400 * 0.90
    assert deps.state_store.load().last_executed_message_id == "mod-1"


def test_transition_sells_then_buys_with_stop_cancel():
    trader = FakeTrader(positions={"TQQQ": 8}, stops={"TQQQ": "old-stop-coid"})
    deps = make_deps(live_cfg(), trader=trader, state=State(last_executed_message_id="seed"))
    out = H.run({"test_signal": "Moderate", "message_id": "tr-1"}, deps)
    assert out["action"] == "executed"
    assert "old-stop-coid" in trader.cancels            # outgoing stop cancelled first
    sides = [(o["side"], o["symbol"]) for o in trader.market_orders()]
    assert sides == [("SELL", "TQQQ"), ("BUY", "QQQ")]  # sell first, then buy
    assert trader.stop_orders()[0]["symbol"] == "QQQ"


def test_cash_liquidates_all_no_buy():
    trader = FakeTrader(positions={"QQQ": 5, "TQQQ": 3})
    deps = make_deps(live_cfg(), trader=trader, state=State(last_executed_message_id="seed"))
    out = H.run({"test_signal": "Cash", "message_id": "cash-1"}, deps)
    assert out["action"] == "executed"
    assert {o["symbol"] for o in trader.market_orders()} == {"QQQ", "TQQQ"}
    assert all(o["side"] == "SELL" for o in trader.market_orders())
    assert trader.stop_orders() == []                   # no buy ⇒ no stop


# ── Veto window ──────────────────────────────────────────────────────────────
def test_veto_cancel_places_nothing_and_marks_skipped():
    trader = FakeTrader(positions={})
    notifier = FakeNotifier(decision=CANCEL)
    deps = make_deps(live_cfg(confirm_window_minutes=10), trader=trader,
                     notifier=notifier, state=State(last_executed_message_id="seed"))
    out = H.run({"test_signal": "Moderate", "message_id": "veto-1"}, deps)
    assert out["action"] == "vetoed"
    assert trader.orders == []
    st = deps.state_store.load()
    assert st.last_skipped_message_id == "veto-1"
    assert st.last_executed_message_id == "seed"        # unchanged


def test_vetoed_signal_not_reprompted():
    trader = FakeTrader(positions={})
    deps = make_deps(live_cfg(confirm_window_minutes=10), trader=trader,
                     state=State(last_executed_message_id="seed",
                                 last_skipped_message_id="veto-1",
                                 last_alerted_message_id="veto-1"))
    out = H.run({"test_signal": "Moderate", "message_id": "veto-1"}, deps)
    assert out["action"] == "already_handled"
    assert trader.orders == []


def test_new_message_same_level_reenters():
    # After a prior execution (or stop-out), a NEW email — even same level — re-enters.
    trader = FakeTrader(positions={})
    deps = make_deps(live_cfg(), trader=trader, state=State(last_executed_message_id="mod-1"))
    out = H.run({"test_signal": "Moderate", "message_id": "mod-2"}, deps)
    assert out["action"] == "executed"
    assert any(o["side"] == "BUY" for o in trader.market_orders())
    assert deps.state_store.load().last_executed_message_id == "mod-2"


def test_partial_fill_leaves_executed_unset_for_retry():
    trader = FakeTrader(positions={}, fail_buy=True)
    deps = make_deps(live_cfg(), trader=trader, state=State(last_executed_message_id="seed"))
    out = H.run({"test_signal": "Moderate", "message_id": "pf-1"}, deps)
    assert out["action"] == "partial"
    assert trader.stop_orders() == []                       # no fill ⇒ no stop
    assert deps.state_store.load().last_executed_message_id == "seed"  # unchanged ⇒ retries


def test_trading_disabled_live_skips():
    deps = make_deps(cfg(dry_run=False, trading_enabled=False),
                     state=State(last_executed_message_id="seed"))
    out = H.run({"test_signal": "Moderate", "message_id": "td-1"}, deps)
    assert out["action"] == "trading_disabled"
