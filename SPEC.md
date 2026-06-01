# Requirements: Serverless Gmail Watcher → Telegram Alert + Webull Trade (Bravos Model Signal)

> **Authoritative behavioral spec.** On any conflict, **§0 System Rules** wins.
> Implementation notes for Claude Code live in `CLAUDE.md`.

---

## 0. TL;DR — System Rules (authoritative)

**Signal**
- Act only on email from `info@bravosresearch.com` matching `Model Signal (Cash|Moderate|Aggressive) has been published` **with DKIM + SPF + DMARC all passing**. Anything else: no trade.
- Each distinct signal email is acted on **at most once** (tracked by `last_executed_message_id`, set only on success). Repeat/standing emails don't re-trade.
- **First run = baseline:** record the current signal, trade nothing, leave the account as found.

**Targets**
- **Cash** → hold nothing. **Moderate** → ~$11k QQQ, no TQQQ. **Aggressive** → ~$11k TQQQ, no QQQ.

**Trades**
- Buy = `floor($11,000 / price)`, whole shares. **Market orders, regular hours only.**
- Sell = the **full current quantity**, market order.
- On a switch: **sell first, then buy.**
- Already holding the target → **leave it alone** (no resize, no extra buy).
- A pre-existing/**adopted** position → **left alone** until a signal moves off it, then liquidated.

**Stops**
- Every position the agent **buys** gets one **GTC stop-market sell**: QQQ 10% below fill, TQQQ 20% below fill.
- **No take-profit, ever.**
- Stop is cancelled when its position is exited; **no orphaned stops**.
- **Adopted positions get no stop.**
- After a stop-out → **stay out until a new signal email arrives** (a new email, even same level, may re-enter).

**Notifications (Telegram) & veto**
- Four messages: **(1)** signal received (mirror) → **(2)** planned trade (exact units to sell/buy) → **(3)** a **10-min veto window** during market hours → **(4)** execution report (fills + stop).
- Reply **STOP/ABORT** to cancel (sticky — that signal won't re-prompt); **OK** to go now; **silence → execute**. Window applies only to live trades during RTH; under dry-run it's a preview with no wait.

**Safety**
- **Off by default** (`TRADING_ENABLED=false`) and **dry-run by default** (`DRY_RUN=true`).
- **Kill switch** halts trading instantly. Only **QQQ/TQQQ**, only the one **cash account**, per-order notional cap.
- Never trades outside regular hours (alerts still fire; execute next open).
- Never enters credentials anywhere — keys supplied via `.env`, synced to SSM.

---

## 1. Summary & Approach

A **polling** agent on **AWS Lambda**, triggered by an **EventBridge schedule (hourly, 24/7)**. Each invocation:
1. Queries Gmail for the most recent Bravos Model Signal email and verifies sender + email-auth.
2. If it's a new email (not previously alerted on), parses the signal and sends a Telegram push.
3. If trading is armed, the market is open, and this signal hasn't been executed yet: **reconciles** the Webull cash account to the signal's target (sell non-target holdings, buy the target). First run records a baseline and trades nothing.
4. Updates a single small state object in S3 and logs an audit trail.

**Why polling, hourly:** The signal is not published at minute resolution, so hourly is ample. Polling avoids the public webhook + weekly re-registration that Gmail push (Pub/Sub) requires.

**Why reconcile-to-target (not transition-based):** Each signal fully determines the desired holdings. Driving current → target is idempotent and self-healing: a re-seen email, a missed poll, an overnight signal, or a restart cannot cause a double trade, because once holdings match the target the agent places no orders.

---

## 2. Architecture

| Concern | Choice | Why |
|---|---|---|
| Compute | **AWS Lambda** (Python 3.12) | Free tier. Most runs finish in ~1s; a run about to trade holds for the veto window (≤~11 min, still free). |
| Schedule | **EventBridge Scheduler** — hourly cron, 24/7 | No standing server. Free. Trading executes only during market hours (guard in code). |
| State | **One S3 object** (`state.json`) | Replaces DynamoDB. Holds last-alerted / last-executed / last-skipped IDs + audit. One GET + one PUT per run. |
| Secrets | **SSM Parameter Store** `SecureString`, populated from `.env` at deploy time | Free for standard params. Keeps secrets out of the code package and out of plaintext Lambda env vars. |
| Notification + veto | **Telegram bot** (outbound send + inbound `getUpdates`) | Free, instant. The 10-min confirmation window reads replies via `getUpdates` — **no webhook/API Gateway needed**. |
| Trading | **Webull OpenAPI** (official) | OAuth 2.0 + app key/secret; HTTP order endpoints; `client_order_id` for idempotency. |
| Logs/alarms | **CloudWatch Logs** + error alarm → Telegram | Free tier. Surface auth failures (e.g. expired Gmail token) immediately. |
| Deploy | **AWS SAM** (single `sam deploy`) + a `sync_secrets` step | One template wires Lambda + Scheduler + S3 + IAM + SSM refs. |

---

## 3. Handler Control Flow

Each invocation, in order:

1. Load config; read secrets from SSM.
   - **1a. Test-injection short-circuit (§16):** if the invocation event contains `test_signal` ∈ {Cash,Moderate,Aggressive}, **skip the Gmail fetch** and set `target_signal` directly from it (using a synthetic, caller-supplied `message_id`). Trusted because caller authenticated via AWS IAM. Does **not** relax any email/sender check.
2. **Fetch newest signal:** Gmail query for latest `from:info@bravosresearch.com` matching email. Verify sender **and** DKIM/SPF/DMARC pass (§7.5.8). Parse level via regex (§4) → `target_signal`. If none / malformed / auth fails → log, notify, exit.
3. **Notification (novelty-gated):** if `message_id != state.last_alerted_message_id` → send Telegram, update state. (Dedup so a standing email isn't re-pinged hourly.)
4. **Trading — execute each distinct signal at most once, on success:**
   - if `TRADING_ENABLED` is false → skip
   - if `KILL_SWITCH` set → skip + notify
   - **first run / no baseline** (`state.last_executed_message_id` is null): record the current `message_id` as the baseline **without trading**. Notify "baseline set."
   - else if `message_id ∈ {last_executed_message_id, last_skipped_message_id}` → **already handled → do nothing**
   - else (a signal not yet handled):
     - if not within US regular trading hours → skip (later RTH invocation will handle it)
     - else:
       a. **Compute the locked plan:** query positions → N to sell; snapshot → M = `floor(11000/price)` to buy; stop estimate. Post **Planned trade** (#2). If "already aligned / no action" → set `last_executed_message_id`, done.
       b. **Veto window (§6.1)** — only when armed and not `DRY_RUN`: poll Telegram up to `CONFIRM_WINDOW_MINUTES`. **Cancel reply** → set `last_skipped_message_id`, post "Aborted," done. **OK / timeout** → proceed.
       c. **Execute** the locked plan via reconcile (§7); post the report (#4). On **full success** set `last_executed_message_id`; on partial/failure leave it unset so the next RTH run retries.

### 3.1 Decision flow diagram

```
Hourly trigger
  → Valid Bravos email? (sender + auth + regex)
      No  → Log + skip
      Yes → Telegram #1 if new signal
          → Trading enabled?
              No  → Skip
              Yes → Kill switch?
                      Yes → Halt + notify
                      No  → First run?
                              Yes → Record baseline — done
                              No  → Already handled (executed or skipped)?
                                      Yes → No action
                                      No  → Regular hours?
                                              No  → Wait for RTH run
                                              Yes → Compute plan (N sell, M=floor 11k/price buy)
                                                  → Already aligned?
                                                      Yes → Notify aligned — done
                                                      No  → Post Telegram #2 (locked N, M, stop est)
                                                          → Dry run?
                                                              Yes → Post preview — done
                                                              No  → 10-min veto window
                                                                  → STOP/ABORT?
                                                                      Yes → Set skipped — done
                                                                      No  → Execute: sell N, buy M, GTC stop
                                                                          → Telegram #4 report — set last_executed
```

---

## 4. The Signal Email — match & parse

- **From:** `info@bravosresearch.com` (exact).
- **Body contains:** `Model Signal (<LEVEL>) has been published`, `<LEVEL>` ∈ {`Cash`, `Moderate`, `Aggressive`}.
- **Parser** (case-insensitive regex):
  `Model\s+Signal\s*\(\s*(Cash|Moderate|Aggressive)\s*\)\s*has\s+been\s+published`
  Extract group 1. No match → do **not** trade; notify "unrecognized Bravos email."
- **Gmail query:** `from:info@bravosresearch.com newer_than:2d`, take the **most recent** match.

---

## 5. State & De-duplication (single S3 object)

`s3://<bucket>/state.json`:
```json
{
  "last_alerted_message_id": "<gmail-msg-id>",
  "last_executed_message_id": "<gmail-msg-id-or-null>",
  "last_skipped_message_id": "<gmail-msg-id-or-null>",
  "last_signal": "Moderate",
  "last_action_at": "2026-05-31T14:00:00Z",
  "audit": [ { "ts": "...", "message_id": "...", "signal": "...", "orders": [ ... ], "result": "..." } ]
}
```

- **Notification dedup:** ping Telegram only when newest email's `message_id` differs from `last_alerted_message_id`.
- **Execute-once / handled-once:** a signal is acted on only when its `message_id` matches **neither** `last_executed_message_id` **nor** `last_skipped_message_id`. `last_executed` set on full success; `last_skipped` set when you veto. This single mechanism delivers: leave adopted positions alone, one action per signal, no re-prompt after abort, no re-entry after stop-out until new email.
- **First-run baseline:** when `last_executed_message_id` is null, agent records current signal's id and trades nothing.
- **Trade idempotency** (independent of state): reconcile no-op + deterministic `client_order_id = hash(message_id + symbol + side)`.
- Lambda **reserved concurrency = 1** so runs can't race.
- Keep `audit` bounded (last ~50 entries).

---

## 6. Notifications & Confirmation Window — Telegram

Telegram is used both to notify **and** to receive a veto. **No webhook / API Gateway** — Lambda reads replies with `getUpdates` (long-poll). Bot token + chat ID live in SSM.

**The four messages, in order:**
1. **Signal received** — once when new signal email is detected, regardless of market hours or arming. E.g. *"📩 New Bravos signal: AGGRESSIVE."*
2. **Planned trade** — on the run about to execute. Exact, locked plan. E.g. *"Plan (AGGRESSIVE): SELL 42 QQQ, BUY 18 TQQQ (~$11k), GTC stop ≈ $X. Reply STOP/ABORT within 10 min to cancel, or OK to go now."*
3. **(the 10-min veto window itself)**
4. **Execution report** — after acting. E.g. *"✅ SOLD 42 QQQ @ $b avg; BOUGHT 18 TQQQ @ $c avg; GTC stop placed @ $X (qty 18)."*

Plus every rejection (with reason) and every error/auth failure.

### 6.1 Confirmation window mechanics

- **Applies only to real trades** (`TRADING_ENABLED` and **not** `DRY_RUN`) during RTH. Under `DRY_RUN`: send #1, #2, and a #4 "would have" preview — **no waiting**.
- Plan (symbols, N to sell, M = `floor(11000/snapshot)` to buy, stop estimate) is **computed and locked at announce**; approved **quantities are exactly what execute** — only fill prices float.
- After posting #2, Lambda polls `getUpdates` (≤50s long-poll per call), filtered to configured chat ID, only replies sent **after** #2. Case-insensitive:
  - **Cancel** (`STOP`/`ABORT`/`NO`/`CANCEL`) → place nothing; set `last_skipped_message_id`; post "Aborted."
  - **Go now** (`OK`/`YES`/`GO`/`CONFIRM`) → stop waiting, execute immediately.
  - **No decisive reply within `CONFIRM_WINDOW_MINUTES` (default 10)** → execute (silence = proceed).
- **Veto, not gate:** silence executes. If Telegram is unreachable for the whole window, the agent proceeds.
- Lambda timeout = window + execution headroom (~11 min); reserved concurrency = 1.

---

## 7. Trading Module — Webull OpenAPI (safety-critical)

> **Off by default.** Email-derived input is **untrusted**. The reconcile target derives **only** from the parsed `{Cash|Moderate|Aggressive}` enum — never from free-form email text.

### 7.1 Account & API

- **Account:** Individual **Cash** account `#CVU8A3K5`, `account_id = 8PHS8R40VL7GC7G9LJTK62DEB9`. Trade **only** this account.
- A pre-existing (adopted) QQQ and/or TQQQ position may already be in the account at startup. Adopted positions are **left alone** — not resized, **not given a stop** — until a signal targets something else, at which point they are liquidated using the **queried** quantity.
- Official **Webull OpenAPI** (provisioned). App key + app secret supplied via `.env`; synced into SSM `SecureString` at deploy.

### 7.2 Target portfolio by signal

| Signal | Target holdings |
|---|---|
| **Cash** | Flat — hold **no** QQQ and **no** TQQQ. |
| **Moderate** | ~**$11,000** of **QQQ**, and **no** TQQQ. |
| **Aggressive** | ~**$11,000** of **TQQQ**, and **no** QQQ. |

### 7.3 Reconcile algorithm

1. Query current QQQ and TQQQ positions (share quantities) **and all open orders** (incl. resting GTC stops).
2. **Sells first:** for any held instrument that is **not** the target, **cancel its resting stop if one exists** (tolerant), then liquidate the **full queried share quantity** via market sell. Poll each sell to filled.
3. **Target instrument:**
   - **If already held (adopted or previously agent-bought):** **leave it entirely alone** — no resize, no new stop, no cancel.
   - **If flat (fresh entry):** fetch snapshot price; `shares = floor(11000 / price)`; skip + notify if `shares < 1` or buying power insufficient; place market buy; **poll to filled**; read **average fill price** and **filled quantity**; place the GTC protective stop (§7.4a) based on **fill price**.
4. If actual already equals target → place/cancel **nothing** (idempotent no-op; any resting stop preserved).

### 7.4 Order construction (entry)

- **MARKET orders, regular trading hours only.** No limit orders, no extended-hours orders.
- `time_in_force = DAY` for entries.
- `client_order_id = hash(message_id + symbol + side)` (deterministic → idempotent), truncated to 32 hex chars.
- Sequence: sells → confirm filled → buy → confirm filled → place stop.
- **Notional cap is a pre-trade estimate guard:** before a buy, compute `est_notional = shares × snapshot_price` and reject if `> MAX_ORDER_NOTIONAL`.

### 7.4a Protective stop-loss (only on positions the agent buys)

- **Exactly one GTC stop-loss sell per agent-bought position. No take-profit, ever.**
- **Adopted positions get no stop.**
- **Stop type:** stop-market (`STOP_LOSS`), **GTC**, full filled quantity.
- **Stop price, based on actual average fill price:**
  - **QQQ:** `stop = round_to_tick(fill_price × (1 − 0.10))`
  - **TQQQ:** `stop = round_to_tick(fill_price × (1 − 0.20))`
- **No self-healing:** the stop is placed once, at entry. Agent does not re-create a missing stop.
- **Idempotency:** stop `client_order_id = hash(message_id + symbol + "STOP")`.
- **Dry-run:** log + Telegram the fully-formed stop but place nothing.

### 7.5 Hard safety gates (ALL required)

1. **`TRADING_ENABLED`** — default **false**.
2. **`DRY_RUN`** — default **true**.
3. **`KILL_SWITCH`** (SSM param) — if set, refuse all trading immediately, no redeploy.
4. **Symbol allowlist = {QQQ, TQQQ}** — reject anything else.
5. **Per-order notional cap** (`$12,000`) — pre-trade estimate guard.
6. **Market-hours guard** — execute only during US regular trading hours.
7. **One reconcile per signal** — never auto-trade more than necessary to reach target.
8. **Anti-spoofing:** email must be `from:info@bravosresearch.com` **AND** show **DKIM=pass, SPF=pass, DMARC=pass**. Any failure → reject.

### 7.6 Settlement note (cash account)

Equity settlement is T+1. A switch sells one ETF and buys the other in the same run, using unsettled proceeds (permitted). Log a warning if ever selling an instrument acquired <1 settlement day prior.

### 7.7 Audit

Record every decision to S3 `audit`, CloudWatch, and Telegram (for any order activity).

---

## 8. Secrets handling (`.env` → SSM)

- Developer fills **one `.env`** with: Gmail OAuth (client id/secret/refresh token), Telegram (bot token, chat id), Webull (app key, app secret, account id).
- A **`sync_secrets`** step reads `.env` and writes each value to **SSM `SecureString`**.
- Lambda reads from SSM at runtime. **Secrets never bundled into Lambda package or stored as plaintext env vars.**
- **Least-privilege IAM:** Lambda may read only its SSM param prefix, get/put only the one S3 state object, write logs.

---

## 9. Configuration Surface

**Operational toggles:**
- `TRADING_ENABLED` (default false), `DRY_RUN` (default true), `KILL_SWITCH`
- `TARGET_NOTIONAL=11000`, `MAX_ORDER_NOTIONAL=12000`
- `STOP_PCT_QQQ=0.10`, `STOP_PCT_TQQQ=0.20`
- `CONFIRM_WINDOW_MINUTES=10`
- `STATE_BUCKET`, `WEBULL_ACCOUNT_ID=8PHS8R40VL7GC7G9LJTK62DEB9`
- `WEBULL_ENV=prod` (US accounts have no sandbox; prod only)
- Secret paths in SSM (synced from `.env`): Gmail, Telegram, Webull.

**Fixed constants (hard-coded, NOT tunable):** symbols `{QQQ,TQQQ}`, `ORDER_TYPE=MARKET`, regular-hours-only, whole shares, stop-market GTC, stop basis = fill price, no take-profit, first-run baseline, hourly schedule, `STATE_KEY=state.json`, reply vocab, silence=proceed.

**Testing only:** `TEST_EMAIL_MODE` (honored **only** when `DRY_RUN=true`) + `TEST_SENDER`. `test_signal` injection read from invocation **event**.

---

## 10. Observability

- CloudWatch alarm on Lambda **error ≥ 1** → SNS → (optional) email + Telegram.
- Alarm on **auth errors** so breakage surfaces immediately.
- Structured JSON logs with per-invocation correlation ID.

---

## 11. Cost (steady state)

Lambda (~720 invocations), EventBridge Scheduler, S3 (one tiny object), SSM (standard params), CloudWatch Logs (<5GB) — all within free tier. **≈ $0/month.**

---

## 12. Deliverables

1. `template.yaml` (SAM): Lambda, hourly EventBridge schedule, S3 bucket, IAM role, SSM refs, error alarm.
2. Lambda source (Python 3.12), modular: `email_provider/` (Gmail API + auth-results check), `notifier/` (Telegram send + `getUpdates` reply-reading for veto window), `trader/` (Webull reconcile + gates, MARKET orders), `state/` (S3), `config.py`, `handler.py` (control flow per §3).
3. `setup_oauth.py` — one-time Gmail consent helper.
4. `sync_secrets.py` — reads `.env`, writes SSM SecureString params.
5. `README.md` — beginner-grade, step-by-step (Phase A–F + "where people get stuck").
6. Unit tests: signal regex; anti-spoof; notification dedup vs. trade-execution decoupling; reconcile for all three signals + all transitions + already-at-target + no-resize; sell-full-queried-quantity; floor sizing; whole-shares; every safety-gate rejection; deterministic `client_order_id`; stop lifecycle; execute-once; partial-reconcile retry; veto window full sequence.
7. `.env.example` listing every required secret/config value.
8. `bootstrap` script (`make bootstrap`) — one command, idempotent, with preflight checks, Gmail consent, deploy, secrets sync, smoke test, and clear next steps.

---

## 13. Acceptance Criteria

- [ ] Single `sam deploy` (+ `sync_secrets`); runs hourly with laptop off.
- [ ] New Bravos email → exactly **one** Telegram push; later hourly polls of same email → no repeat pings.
- [ ] Regex extracts Cash/Moderate/Aggressive; malformed/lookalike email trades nothing and notifies.
- [ ] Spoof test: non-Bravos sender, or failing DKIM/SPF/DMARC → rejected, no trade.
- [ ] `DRY_RUN=true`: logs fully-formed MARKET orders + Telegram, places nothing.
- [ ] Armed reconcile reaches correct target from each start state; already-at-target is no-op; no resize of existing.
- [ ] Every **agent buy** followed by exactly **one GTC stop-market sell** — QQQ 10% / TQQQ 20% below fill — no take-profit ever.
- [ ] Adopted position (matching signal) → left completely alone, no stop, no resize.
- [ ] Adopted position (contrary signal) → liquidated (tolerant cancel-if-stop-present), then target bought with stop.
- [ ] First-run baseline: records signal, trades nothing, adopted position untouched.
- [ ] No-op reconcile leaves resting stop in place; transition cancels outgoing stop before liquidating; no orphaned stop.
- [ ] After stop-out, no re-buy until new signal email; new email (even same level) re-enters.
- [ ] Same signal email executed at most once; partial/failed reconciles retry on next RTH run.
- [ ] Orders are MARKET, placed only during RTH; out-of-RTH signal alerts immediately, executes on first RTH run.
- [ ] Re-running same signal places zero additional orders.
- [ ] Every safety gate individually unit-tested and rejecting correctly; no secret ever logged.
- [ ] Notification flow: signal received once → planned trade with exact N/M → execution report with fills + stop price.
- [ ] Veto window: STOP/ABORT → no orders, signal marked skipped, no re-prompt; OK → executes immediately; no reply → executes.
- [ ] Replies from another chat or sent before plan message are ignored.
- [ ] Steady-state AWS bill ≈ $0.

---

## 14. Resolved Decisions

1. **No resize of existing positions.** Sells always liquidate the **full current queried quantity**.
2. **MARKET orders, regular hours only.**
3. **Webull provisioned;** app key + secret supplied via `.env`.
4. **No fractional shares** — whole-share floor sizing.
5. **Buying power** from settled cash or same-run sale proceeds.
6. **Protective stop only on positions the agent buys:** one GTC stop-market sell, QQQ 10% / TQQQ 20% below fill. No take-profit. Adopted positions get no stop.
7. **Execute each signal once.** No re-entry after stop-out until new email.
8. **Stops cancelled on exit.** Any instrument being liquidated has its resting stop cancelled first.
9. **Adopted positions left alone** until a signal targets something else; **first-run baseline** trades nothing.
10. **10-minute veto window.** Silence = execute. Implemented as in-Lambda `getUpdates` poll. Skipped under DRY_RUN.

---

## 15. Out of Scope (v1)

- Gmail push / Pub/Sub real-time path.
- Multi-account / margin / IRA.
- Web UI / dashboard.
- Limit/extended-hours orders.
- Any decision logic about *what* the signal should be.

---

## 16. Testing Without a Live Bravos Email

| Layer | How | Safe while live? |
|---|---|---|
| **1 Unit/integration** | `make test` (102 tests, no AWS/network) | n/a |
| **2 Injection** | `aws lambda invoke … --payload '{"test_signal":"Aggressive","message_id":"uniq"}'` | **Yes** — IAM-trusted, skips Gmail |
| **3 Real Gmail, relaxed sender** | `TEST_EMAIL_MODE=true` + `TEST_SENDER=you@…`, email yourself the trigger sentence | Dry-run only — refuses to arm |
| **4 Real Bravos email + DRY_RUN** | Final check before arming | — |

---

## 17. Provisioning Runbook

**Fast path:** Phases A–C are unavoidable portal clickwork. Phases D–F collapse into `make bootstrap`.

**Phase A — Accounts (one-time, free)**
1. AWS account (free tier)
2. Google Cloud account (Gmail API OAuth)
3. Telegram (install app)
4. Webull OpenAPI provisioned — obtain app key + app secret

**Phase B — Tools** (skip if using CloudShell): Python 3.12, AWS CLI, SAM CLI, git.

**Phase C — Gather credentials**
1. **Telegram:** @BotFather → `/newbot` → bot token. Message bot once → get chat id via `getUpdates` URL.
2. **Gmail OAuth:** Google Cloud Console → create project → enable Gmail API → OAuth consent screen → **publish to "In production"** (critical: Testing mode = 7-day token expiry) → Credentials → OAuth client ID → Desktop app → download JSON for client id + secret. Run `setup_oauth.py --write` for refresh token.
3. **Webull:** app key + app secret + account id.

**Phase D — Configure**
Copy `.env.example` → `.env`, fill in everything. Leave `TRADING_ENABLED=false`, `DRY_RUN=true`.

**Phase E — Deploy**
```bash
make bootstrap
```
Idempotently: preflight → Gmail consent (if needed) → `sam build` + `sam deploy` → `sync_secrets` → smoke test (Layer 2 injection, confirms Telegram message).

**Phase F — Verify & arm**
1. Confirm Telegram dry-run message looks correct.
2. Optionally: Layer 3 (`TEST_EMAIL_MODE=true`) to test live Gmail path.
3. When satisfied: `make arm` (sets DryRun=false + TradingEnabled=true, redeploys).

**Where people get stuck:**
- Google OAuth consent screen still in "Testing" → 7-day token death
- Gmail API not enabled on the project
- SSM params not re-synced after editing `.env`
- Deploying without AWS credentials (use CloudShell)
- Webull API not provisioned for trading
- Telegram veto broken by a stray webhook (agent calls `deleteWebhook` defensively)
- No Nasdaq Basic Non-Display subscription → snapshot quotes return empty
