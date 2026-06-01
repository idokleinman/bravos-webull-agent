# CLAUDE.md — Bravos → Telegram → Webull Agent

Project-specific rules. Workspace-wide rules live in `/Users/ido/AI home/CLAUDE.md`.
The authoritative behavioral spec is **`SPEC.md`** (the requirements doc). On any
conflict, SPEC.md §0 (System Rules) wins.

## Purpose
Serverless agent (AWS Lambda, hourly EventBridge) that polls Gmail for the Bravos
Research "Model Signal" email and, on a genuinely new signal: (a) pushes a Telegram
alert, and (b) — behind hard safety gates — reconciles a single Webull **cash**
account to the signal's target (Cash→flat, Moderate→~$11k QQQ, Aggressive→~$11k TQQQ).

## Non-negotiables (these are why this project exists)
- **Off by default**: `TRADING_ENABLED=false`, `DRY_RUN=true`. Never flip these for the user.
- **Reconcile-to-target**, not transition-based — idempotent and self-healing.
- **Execute each signal at most once**, on success (`last_executed_message_id`).
- **Anti-spoofing is the primary security control**: sender == info@bravosresearch.com
  AND DKIM+SPF+DMARC all pass. No shared token exists.
- **Targets derive ONLY from the parsed `{Cash|Moderate|Aggressive}` enum** — never
  from free-form email text. Email input is untrusted.
- MARKET orders, **regular hours only**. Whole shares (`floor(11000/price)`).
- Protective **GTC stop-market** on every agent buy (QQQ −10%, TQQQ −20% of fill);
  **no take-profit, ever**; **adopted positions get no stop**; no orphaned stops.
- First run = **baseline only** (record signal, trade nothing).

## Trading account (hard-coded)
Individual **Cash** account — set via `WEBULL_ACCOUNT_ID` in `.env`. Only this account, only {QQQ, TQQQ}.

## Webull SDK facts (verified against official source, May 2026)
- Packages: `webull-python-sdk-{core,trade,mdata}`. Client:
  `ApiClient(app_key, app_secret, "us")` + `add_endpoint("us", host)`.
- **US order path is v1** `OrderOperation.place_order_v2(account_id, stock_order_dict)`
  — the v2 `OrderOperationV2.place_order` is documented as *not yet available to US*.
  Keep the call isolated in `trader/webull.py`.
- GTC stop on **equities is supported** (`OrderType.STOP_LOSS` + `OrderTIF.GTC`,
  `entrust_type=QTY`). The GTC-only-on-buy restriction is **options-only**.
- No sandbox environment for US accounts — `WEBULL_ENV=prod` only.
- Positions: `Account.get_account_position`; quote: `MarketData.get_snapshot([sym], Category.US_ETF)`.
- Response field names for snapshot price, position qty, and order fill are not fully
  documented. Candidate fields are in `config.WEBULL_*` — correct there if live responses differ.

## Layout
`src/handler.py` (control flow §3) · `src/config.py` (toggles + constants + SSM secrets) ·
`src/email_provider/` (Gmail fetch + auth-results) · `src/notifier/` (Telegram send + getUpdates veto) ·
`src/trader/` (reconcile + gates + plan, MARKET orders) · `src/state/` (single S3 object).
`template.yaml` (SAM) · `bootstrap.sh` / `Makefile` (one-command setup) ·
`sync_secrets.py` (.env→SSM) · `setup_oauth.py` (Gmail refresh token).

## Conventions
- Hard safety gates evaluated in fixed order, each returning a structured rejection
  (every gate individually unit-tested). Kill switch read fresh from SSM each run.
- `client_order_id = sha256(message_id+symbol+side)[:32]`; stop uses `+"STOP"`.
- Never log tokens, app secret, or PINs. Secrets only via SSM (env fallback in tests).
- Never commit `.env`. uv/pip both fine; Lambda builds from `src/requirements.txt`.
