"""Lambda entry point + control flow (spec §3).

`run(event, deps)` is dependency-injected so the whole flow is unit-testable with
fakes (no AWS / Gmail / Telegram / Webull). `lambda_handler` builds the real deps.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from config import SIGNAL_TARGETS, STATE_KEY, Config, SecretStore, get_config
from email_provider import authres
from email_provider.signal import parse_signal
from notifier import messages
from notifier.telegram import CANCEL
from trader.market_hours import is_regular_hours
from trader.plan import build_plan
from trader.reconcile import execute_plan

log = logging.getLogger("bravos")
log.setLevel(logging.INFO)


@dataclass
class Deps:
    config: Config
    secrets: SecretStore
    state_store: object          # .load() / .save(state)
    gmail: object                # .fetch_latest() / .fetch_latest_from(sender)
    notifier: object             # .send / .delete_webhook / .drain_updates / .poll_for_decision
    make_trader: Callable[[], object]
    now: Callable[[], datetime]


def _compute_plan(trader, cfg: Config, signal: str):
    positions = trader.get_positions()
    stops = set(trader.resting_stops().keys())
    return build_plan(
        signal,
        positions,
        stops,
        trader.snapshot_price,
        target_notional=cfg.target_notional,
        stop_pct_lookup=cfg.stop_pct,
        max_order_notional=cfg.max_order_notional,
    )


def _audit(state, message_id, signal, action, extra=None):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "message_id": message_id,
        "signal": signal,
        "action": action,
    }
    if extra:
        entry["detail"] = extra
    state.add_audit(entry)


def run(event: dict, deps: Deps) -> dict:
    cfg = deps.config
    cfg.validate()
    result: dict = {"alerted": False, "action": None, "signal": None, "message_id": None}

    # ── 1 / 1a: resolve the signal ───────────────────────────────────────────
    test_signal = (event or {}).get("test_signal")
    if test_signal:
        # IAM-trusted injection (§16 L2). Does NOT relax any email check — it skips
        # the email entirely. Caller supplies a unique message_id.
        if test_signal not in SIGNAL_TARGETS:
            deps.notifier.send(messages.rejected(f"unknown test_signal {test_signal!r}"))
            return {**result, "action": "rejected", "reason": "bad test_signal"}
        message_id = str((event or {}).get("message_id") or f"test-{uuid.uuid4().hex}")
        signal = test_signal
    else:
        if cfg.test_email_mode:
            msg = deps.gmail.fetch_latest_from(cfg.test_sender)
        else:
            msg = deps.gmail.fetch_latest()
        if msg is None:
            return {**result, "action": "no_email"}
        message_id = msg.id
        if not cfg.test_email_mode:
            ok, reason = authres.verify(msg.from_header, msg.auth_results)
            if not ok:
                log.warning("anti-spoof reject: %s", reason)
                deps.notifier.send(messages.rejected(reason))
                return {**result, "action": "auth_failed", "reason": reason, "message_id": message_id}
        signal = parse_signal(msg.body)
        if signal is None:
            deps.notifier.send(messages.rejected("unrecognized Bravos email"))
            return {**result, "action": "unparsed", "message_id": message_id}

    result["signal"] = signal
    result["message_id"] = message_id

    # ── 3: state + notification dedup ────────────────────────────────────────
    state = deps.state_store.load()
    if message_id != state.last_alerted_message_id:
        deps.notifier.send(messages.signal_received(signal))
        state.last_alerted_message_id = message_id
        state.touch(signal)
        _audit(state, message_id, signal, "alerted")
        deps.state_store.save(state)
        result["alerted"] = True

    # ── 4: trading ───────────────────────────────────────────────────────────
    if deps.secrets.kill_switch_engaged():
        deps.notifier.send("⛔ KILL_SWITCH engaged — trading halted.")
        return {**result, "action": "kill_switch"}

    # DRY_RUN preview — works even when TRADING_ENABLED is false so you can validate
    # the exact orders before arming (§16 Layer 2/4). Places nothing; mutates no
    # execute/baseline state.
    if cfg.dry_run:
        trader = deps.make_trader()
        plan = _compute_plan(trader, cfg, signal)
        deps.notifier.send(
            messages.planned_trade(plan, window_minutes=cfg.confirm_window_minutes, live=False)
        )
        deps.notifier.send(messages.dry_run_preview(plan))
        _audit(state, message_id, signal, "dry_run_preview")
        deps.state_store.save(state)
        return {**result, "action": "preview"}

    # Live from here.
    if not cfg.trading_enabled:
        return {**result, "action": "trading_disabled"}

    if state.is_first_run:
        state.last_executed_message_id = message_id
        state.touch(signal)
        _audit(state, message_id, signal, "baseline")
        deps.state_store.save(state)
        deps.notifier.send(messages.baseline_set(signal))
        return {**result, "action": "baseline"}

    if state.is_handled(message_id):
        return {**result, "action": "already_handled"}

    now = deps.now()
    if not is_regular_hours(now):
        return {**result, "action": "after_hours"}

    trader = deps.make_trader()
    plan = _compute_plan(trader, cfg, signal)

    if plan.no_action:
        state.last_executed_message_id = message_id
        state.touch(signal)
        _audit(state, message_id, signal, "aligned")
        deps.state_store.save(state)
        deps.notifier.send(
            messages.planned_trade(plan, window_minutes=cfg.confirm_window_minutes, live=True)
        )
        return {**result, "action": "aligned"}

    # Post the locked plan (#2) and run the veto window.
    after_ts = int(now.timestamp())
    deps.notifier.drain_updates()
    deps.notifier.send(
        messages.planned_trade(plan, window_minutes=cfg.confirm_window_minutes, live=True)
    )

    if cfg.confirm_window_minutes > 0:
        decision = deps.notifier.poll_for_decision(cfg.confirm_window_minutes * 60, after_ts)
        if decision == CANCEL:
            state.last_skipped_message_id = message_id
            state.touch(signal)
            _audit(state, message_id, signal, "vetoed")
            deps.state_store.save(state)
            deps.notifier.send(messages.aborted())
            return {**result, "action": "vetoed"}

    # Execute (#4).
    report = execute_plan(trader, plan, message_id)
    deps.notifier.send(messages.execution_report(report))
    if report["success"]:
        state.last_executed_message_id = message_id
    state.touch(signal)
    _audit(state, message_id, signal, "executed" if report["success"] else "partial", report)
    deps.state_store.save(state)
    return {**result, "action": "executed" if report["success"] else "partial", "report": report}


# ─────────────────────────────────────────────────────────────────────────────
# Lambda entry point — builds real dependencies.
# ─────────────────────────────────────────────────────────────────────────────
def _build_deps(cfg: Config) -> Deps:
    from email_provider.gmail import GmailClient
    from notifier.telegram import TelegramNotifier
    from state.s3_state import S3StateStore
    from trader.webull import WebullTrader

    secrets = SecretStore(cfg.ssm_prefix, cfg.aws_region)
    state_store = S3StateStore(cfg.state_bucket, STATE_KEY, cfg.aws_region)
    gmail = GmailClient(
        secrets.get("GMAIL_CLIENT_ID"),
        secrets.get("GMAIL_CLIENT_SECRET"),
        secrets.get("GMAIL_REFRESH_TOKEN"),
    )
    notifier = TelegramNotifier(
        secrets.get("TELEGRAM_BOT_TOKEN"), secrets.get("TELEGRAM_CHAT_ID")
    )
    notifier.delete_webhook()  # defensive — getUpdates must not be shadowed by a webhook

    _cache: dict = {}

    def make_trader():
        if "t" not in _cache:
            _cache["t"] = WebullTrader(
                secrets.get("WEBULL_APP_KEY"),
                secrets.get("WEBULL_APP_SECRET"),
                cfg.webull_region_id,
                cfg.webull_account_id,
                endpoint=cfg.webull_endpoint,
            )
        return _cache["t"]

    return Deps(
        config=cfg,
        secrets=secrets,
        state_store=state_store,
        gmail=gmail,
        notifier=notifier,
        make_trader=make_trader,
        now=lambda: datetime.now(timezone.utc),
    )


def lambda_handler(event, context):  # noqa: ARG001
    cfg = get_config()
    deps = _build_deps(cfg)
    try:
        out = run(event or {}, deps)
        log.info("run result: %s", {k: v for k, v in out.items() if k != "report"})
        return out
    except Exception as e:  # noqa: BLE001
        log.exception("handler error")
        try:
            deps.notifier.send(messages.error(str(e)))
        except Exception:
            pass
        raise
