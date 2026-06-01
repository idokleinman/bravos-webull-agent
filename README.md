# Bravos → Telegram → Webull Agent

> **Repo:** https://github.com/idokleinman/bravos-webull-agent

A serverless agent that watches your Gmail for the **Bravos Research "Model Signal"**
email and, on a genuinely new signal, (1) pushes a **Telegram** alert to your phone
and (2) — only when you've deliberately armed it — reconciles your **Webull cash
account** to the signal's target. It runs on AWS Lambda on an hourly schedule, so it
keeps working with your laptop off, for **≈ $0/month**.

> **It ships safe.** Out of the box `TRADING_ENABLED=false` and `DRY_RUN=true`: it will
> alert and *show* you the exact orders it would place, but **place nothing** until you
> flip two switches on purpose. Full behavior spec: [`SPEC.md`](SPEC.md).

| Signal | What it targets in the cash account |
|---|---|
| **Cash** | Hold nothing (sell everything) |
| **Moderate** | ~$11,000 of **QQQ**, no TQQQ |
| **Aggressive** | ~$11,000 of **TQQQ**, no QQQ |

Every position the agent *buys* gets one **GTC stop-market** (QQQ −10%, TQQQ −20% of the
fill). It never sets a take-profit, never touches a position you already held until a
signal moves off it, and trades each signal email **at most once**.

---

## The whole flow in 5 steps

Phases A–C are unavoidable portal clickwork (make accounts, copy keys). Everything after
collapses into **one command**.

1. **Create accounts & copy keys** (Phase A–C below)
2. **Fill `.env`** (Phase D)
3. **Run `make bootstrap`** (deploys + syncs secrets + smoke-tests)
4. **Approve the Google screen** when the browser pops up
5. **Check the test Telegram message**

Arming live trading is a separate, deliberate step (`make arm`).

---

## Phase A — Accounts (one-time, free)

1. **AWS** — sign up at [aws.amazon.com](https://aws.amazon.com). Free tier covers this.
   *Tip: do the AWS steps from **AWS CloudShell** (a browser terminal that's already
   logged in) to skip installing/credentialing the CLI locally.*
2. **Google Cloud** — needed for Gmail API OAuth (free).
3. **Telegram** — install the app.
4. **Webull OpenAPI** — confirm your account is provisioned for API trading and get your
   **app key + app secret** from the Webull developer portal.

## Phase B — Tools (skip if using CloudShell for the AWS parts)

Python 3.12, AWS CLI, AWS SAM CLI, git. On macOS:
```bash
brew install python@3.12 awscli aws-sam-cli git
# uv (used for the local helper venv) — optional but recommended:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Phase C — Gather credentials

### C1. Telegram bot + chat id
1. In Telegram, message **@BotFather** → `/newbot` → copy the **bot token**.
2. Message your new bot once (say "hi"), then get your **chat id**: open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read
   `result[0].message.chat.id`. (Or message **@userinfobot**.)

### C2. Gmail API OAuth app — do this carefully
1. [Google Cloud Console](https://console.cloud.google.com) → **create a project**.
2. **APIs & Services → Library → enable the Gmail API**.
3. **OAuth consent screen** → set it up (External is fine) → **Publish app → "In
   production"**. ⚠️ **This matters:** if it stays in "Testing", your refresh token dies
   every 7 days and the agent silently stops.
4. **Credentials → Create credentials → OAuth client ID → Desktop app** → copy the
   **client ID + client secret**.
5. The refresh token is produced for you in Phase E by `setup_oauth.py` (or
   automatically inside `make bootstrap`).

### C3. Webull
Have your **app key**, **app secret**, and **account id** ready.

## Phase D — Configure

```bash
cp .env.example .env
# edit .env and paste in everything from Phase C
```
Leave the safety defaults exactly as shipped: `TRADING_ENABLED=false`, `DRY_RUN=true`.
`.env` is git-ignored — keep it private.

## Configuration reference

All tuneable parameters are set during `make bootstrap` (the guided `sam deploy` prompt)
and saved to `samconfig.toml` for subsequent deploys. You can change any of them later
with `sam deploy --parameter-overrides Key=Value`.

| Parameter | Default | What it controls |
|---|---|---|
| `TargetNotional` | `11000` | Dollar amount deployed per Moderate/Aggressive signal |
| `MaxOrderNotional` | `12000` | Hard cap — rejects any single order above this |
| `StopPctQqq` | `0.10` | GTC stop-market distance for QQQ (10% below fill) |
| `StopPctTqqq` | `0.20` | GTC stop-market distance for TQQQ (20% below fill) |
| `ConfirmWindowMinutes` | `10` | Minutes to reply STOP/OK in Telegram before auto-execute |
| `WebullAccountId` | *(yours)* | Webull cash account ID to reconcile |
| `WebullRegionId` | `us` | Webull API region |
| `AlarmEmail` | *(blank)* | Optional email for CloudWatch error alarm (e.g. your@email.com) |
| `SsmPrefix` | `/bravos-webull/prod` | SSM path prefix for secrets — change if running multiple stacks |

**Safety toggles** (also SAM parameters, changed via `make arm` / `make disarm`):

| Parameter | Default | Meaning |
|---|---|---|
| `TradingEnabled` | `false` | Master arm switch — must be `true` to place any order |
| `DryRun` | `true` | When `true`, logs fully-formed orders but places nothing |

**Runtime toggles** (in `.env`, applied instantly via `make sync-secrets` — no redeploy):

| Variable | Default | Meaning |
|---|---|---|
| `KILL_SWITCH` | *(blank)* | Set to `1` to halt all trading immediately |

## Phase E — Deploy + verify (one command)

```bash
make bootstrap
```
This will, idempotently:
- **preflight** — check sam/aws are present and your AWS creds resolve, and that `.env`
  has every required key (it names any that are missing);
- **Gmail consent** — if you don't have a refresh token yet, open the browser; you
  approve once and it's captured into `.env`;
- **deploy** — `sam build` + `sam deploy` (guided the first time, remembered after):
  creates the Lambda, the hourly schedule, the S3 state bucket, IAM role, and error alarm;
- **secrets** — push your `.env` secrets into SSM SecureString (they never sit in the
  code package);
- **smoke test** — invoke the function with `{"test_signal":"Moderate"}` under dry-run
  and tell you PASS/FAIL. **Check your phone**: you should get Telegram messages showing
  the fully-formed (but *not placed*) orders.

Prefer to run the pieces yourself? `make oauth`, `make deploy`, `make sync-secrets`,
`make smoke`.

## Phase F — Arm (deliberate, separate)

When you're satisfied with the dry-run output:
```bash
# Flip to live. Keep your first real signal small and watch the four Telegram messages.
make arm        # sets DryRun=false + TradingEnabled=true and redeploys
```

Back to safe at any time:
```bash
make disarm     # DryRun=true + TradingEnabled=false
```

---

## The four Telegram messages (when armed, during market hours)

1. **📩 Signal received** — fires once per new email (even after hours).
2. **Plan** — the exact, locked orders: *"SELL 8 TQQQ, BUY 27 QQQ (~$11k), GTC stop ≈
   $360. Reply STOP/ABORT within 10 min to cancel, or OK to go now."*
3. *(the 10-minute veto window)* — reply **STOP/ABORT** to cancel (sticky — it won't
   re-ask), **OK** to go immediately, or **say nothing** and it executes.
4. **✅ Execution report** — actual fills + the stop that was placed.

Under dry-run you get #1, #2, and a #4 *"would have"* preview — no waiting, nothing placed.

## Kill switch (halt instantly, no redeploy)

```bash
# in .env:
KILL_SWITCH=1
make sync-secrets
```
The next run refuses all trading and tells you. Clear it (blank the value, re-sync) to resume.

The kill switch works because the Lambda reads SSM on **every invocation** — not at deploy time. So pushing a new SSM value takes effect within seconds, no CloudFormation update needed.

`TRADING_ENABLED` and `DRY_RUN` are CloudFormation parameters baked into the Lambda's environment at deploy time — changing them requires `make arm` / `make disarm` (~1 min redeploy). Use the kill switch for emergencies; use `make disarm` to cleanly return to the safe state.

---

## Testing without a live Bravos email

| Layer | How | Safe while live? |
|---|---|---|
| **1 Unit/integration** | `make test` (96 tests, no AWS/network) | n/a |
| **2 Injection** | Get your function name from CloudFormation outputs (`aws cloudformation describe-stacks --stack-name bravos-webull-agent --region us-west-2 --query 'Stacks[0].Outputs'`), then: `aws lambda invoke --function-name <your-function-name> --region us-west-2 --cli-binary-format raw-in-base64-out --payload '{"test_signal":"Aggressive","message_id":"uniq-123"}' /tmp/out.json && cat /tmp/out.json` — use a **unique** `message_id` each run. |
| **3 Real Gmail, relaxed sender** | set `TEST_EMAIL_MODE=true` + `TEST_SENDER=you@…`, email yourself the trigger sentence | Dry-run only — the agent refuses to start if `TEST_EMAIL_MODE` is set while `TRADING_ENABLED=true`. |
| **4 Real Bravos email + dry-run** | final check before arming | — |

Re-trigger Layer 3 by clearing `last_alerted_message_id` in the S3 `state.json`, or just
send a fresh email.

---

## Where people get stuck

- **Google OAuth screen still in "Testing"** → refresh token dies after 7 days. Publish
  to **In production**.
- **Gmail API not enabled** on the project → fetch fails.
- **Edited `.env` but forgot `make sync-secrets`** → the Lambda still reads the old SSM
  values.
- **Deploying with no AWS credentials** → use **CloudShell**, or `aws configure`.
- **Webull not actually provisioned for API trading** → order calls fail. Confirm your
  OpenAPI access is active in the Webull developer portal.
- **Telegram veto silent** → a stray webhook shadows `getUpdates`. The agent calls
  `deleteWebhook` defensively, but if you set one elsewhere, remove it.

---

## How it works (architecture)

```
EventBridge (hourly) → Lambda(handler.py)
  ├─ SSM SecureString  → secrets (Gmail / Telegram / Webull / kill switch)
  ├─ S3 state.json     → de-dup + execute-once + audit
  ├─ Gmail API         → newest signal, verify sender + DKIM/SPF/DMARC
  ├─ Telegram          → 4 messages + getUpdates veto (no webhook)
  └─ Webull OpenAPI    → reconcile cash account (MARKET, RTH only) + GTC stop
CloudWatch error alarm → SNS (optional email) ; handler also self-reports errors to Telegram
```
The Lambda only ever calls *outward* — there is no public endpoint; even your STOP/OK
reply is *fetched* from Telegram. Reserved concurrency = 1 so runs never race.

Code map: `src/handler.py` (control flow), `src/config.py` (toggles + constants + SSM),
`src/email_provider/` (Gmail + anti-spoof), `src/notifier/` (Telegram + messages),
`src/trader/` (plan, gates, reconcile, Webull adapter, market hours), `src/state/`.

## Deployed infrastructure

**Region:** us-west-2 (Oregon) — make sure the AWS Console region selector shows this.

| Resource | Name / ARN |
|---|---|
| CloudFormation stack | `bravos-webull-agent` |
| Lambda function | `bravos-webull-agent-SignalFunction-<id>` |
| S3 state bucket | `bravos-webull-agent-statebucket-<id>` |
| EventBridge schedule | `SignalFunctionHourly` |
| SSM parameters | `/bravos-webull/prod/*` |
| SNS error topic | `bravos-webull-agent-ErrorTopic-<id>` |
| CloudWatch logs | `/aws/lambda/bravos-webull-agent-SignalFunction-*` |

**AWS Console shortcuts (set region to us-west-2 first):**
- **CloudFormation → Stacks** — full resource tree
- **Lambda → Functions** — code, config, test invocations
- **EventBridge → Schedules** — hourly cron
- **Systems Manager → Parameter Store** — secrets (values hidden by default)
- **CloudWatch → Log groups** — execution logs per run

## Cost

Lambda (~720 runs/mo), EventBridge, S3 (one tiny object), SSM standard params,
CloudWatch logs — all within free tier. **≈ $0/month.**

## Developer reference

```bash
make help          # list targets
make test          # run the suite
make lint          # ruff
```
Behavioral rules of record live in [`SPEC.md`](SPEC.md); build notes in [`CLAUDE.md`](CLAUDE.md).
