"""Pure Telegram message formatters (spec §6). No I/O — easy to unit-test."""

from __future__ import annotations

from trader.plan import Plan


def signal_received(signal: str) -> str:
    return f"📩 New Bravos signal: {signal.upper()}."


def _plan_legs(plan: Plan) -> str:
    parts = []
    for s in plan.sells:
        parts.append(f"SELL {s.qty} {s.symbol}")
    if plan.buy:
        parts.append(
            f"BUY {plan.buy.shares} {plan.buy.symbol} "
            f"(~${plan.buy.est_notional:,.0f}), GTC stop ≈ ${plan.buy.est_stop_price:,.2f}"
        )
    return "; ".join(parts) if parts else "no action"


def planned_trade(plan: Plan, *, window_minutes: int, live: bool) -> str:
    if plan.target_symbol is None and plan.sells:
        body = f"SELL all → flat ({_plan_legs(plan)}), no buy"
    elif plan.no_action:
        return f"Plan ({plan.signal.upper()}): already aligned — no action."
    else:
        body = _plan_legs(plan)
    msg = f"Plan ({plan.signal.upper()}): {body}."
    if plan.buy_reject_reason:
        msg += f"\n⚠️ Buy skipped: {plan.buy_reject_reason}."
    if live:
        if window_minutes > 0:
            msg += (
                f"\nReply STOP/ABORT within {window_minutes} min to cancel, "
                f"or OK to go now."
            )
        else:
            msg += "\nExecuting immediately (no veto window configured)."
    return msg


def dry_run_preview(plan: Plan) -> str:
    return f"🧪 DRY RUN — no orders placed. Would have: {_plan_legs(plan)}."


def baseline_set(signal: str) -> str:
    return (
        f"📌 Baseline set to {signal.upper()} — first run, no trade. "
        f"The account was left exactly as found."
    )


def aborted() -> str:
    return "🛑 Aborted by you — no action. This signal will not re-prompt."


def execution_report(report: dict) -> str:
    lines = ["✅ Execution report:"]
    for s in report.get("sells", []):
        lines.append(f"  SOLD {s['filled_qty']} {s['symbol']} @ ${s['avg_price']:,.2f}")
    buy = report.get("buy")
    if buy:
        lines.append(f"  BOUGHT {buy['filled_qty']} {buy['symbol']} @ ${buy['avg_price']:,.2f}")
    stop = report.get("stop")
    if stop:
        lines.append(
            f"  GTC stop placed @ ${stop['stop_price']:,.2f} (qty {stop['qty']} {stop['symbol']})"
        )
    if report.get("buy_skipped_reason"):
        lines.append(f"  ⚠️ Buy skipped: {report['buy_skipped_reason']}")
    if not report.get("success", False):
        lines.append("  ⚠️ Partial/failed — will retry on the next regular-hours run.")
    if len(lines) == 1:
        lines.append("  (nothing to do — already aligned)")
    return "\n".join(lines)


def rejected(reason: str) -> str:
    return f"⛔ Rejected: {reason}. No trade."


def error(reason: str) -> str:
    return f"❗ Agent error: {reason}"
