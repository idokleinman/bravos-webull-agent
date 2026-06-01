#!/usr/bin/env bash
# One-command setup from a filled .env (spec §12.8). Idempotent & re-runnable.
# Never arms trading — that stays a deliberate manual step.
set -euo pipefail

STACK_NAME="${STACK_NAME:-bravos-webull-agent}"
ENV_FILE="${ENV_FILE:-.env}"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bold() { printf "\033[1m%s\033[0m\n" "$1"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
die()  { printf "\033[31m✗ %s\033[0m\n" "$1" >&2; exit 1; }

REQUIRED_KEYS=(
  SECRET_WEBULL_APP_KEY SECRET_WEBULL_APP_SECRET WEBULL_ACCOUNT_ID
  SECRET_GMAIL_CLIENT_ID SECRET_GMAIL_CLIENT_SECRET
  SECRET_TELEGRAM_BOT_TOKEN SECRET_TELEGRAM_CHAT_ID
)

# ── 1. Preflight ─────────────────────────────────────────────────────────────
bold "1/6 Preflight"
command -v sam >/dev/null || die "AWS SAM CLI not found. Install it, or run from AWS CloudShell."
command -v aws >/dev/null || die "AWS CLI not found. Install it, or run from AWS CloudShell."
aws sts get-caller-identity >/dev/null 2>&1 || die "AWS credentials not resolving. Configure the CLI or use CloudShell."
ok "sam + aws present, credentials resolve"

[ -f "$ENV_FILE" ] || die "$ENV_FILE missing. Copy .env.example → .env and fill it in."
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a
missing=()
for k in "${REQUIRED_KEYS[@]}"; do [ -n "${!k:-}" ] || missing+=("$k"); done
[ ${#missing[@]} -eq 0 ] || die "Missing in $ENV_FILE: ${missing[*]}"
ok ".env has all required keys"

# Local venv for helper scripts only (sync_secrets needs boto3; setup_oauth needs google libs).
# Do NOT install src/requirements.txt here — the Webull SDK's grpcio-tools build dep
# fails in uv pip install. SAM builds the Lambda deps separately in its own environment.
HELPER_DEPS="boto3 python-dotenv google-auth-oauthlib google-api-python-client google-auth"
if command -v uv >/dev/null; then
  uv venv --python 3.12 .venv >/dev/null 2>&1 || true
  uv pip install --python .venv/bin/python -q $HELPER_DEPS >/dev/null
else
  python3 -m venv .venv
  ./.venv/bin/pip install -q $HELPER_DEPS
fi
PY="./.venv/bin/python"
ok "helper venv ready"

# ── 2. Gmail consent (the one interactive moment) ────────────────────────────
bold "2/6 Gmail OAuth"
if [ -z "${SECRET_GMAIL_REFRESH_TOKEN:-}" ]; then
  echo "  No refresh token yet — opening browser for consent…"
  "$PY" setup_oauth.py --write
  set -a; source "$ENV_FILE"; set +a
  ok "refresh token captured into $ENV_FILE"
else
  ok "refresh token already present"
fi

# ── 3. Deploy ────────────────────────────────────────────────────────────────
bold "3/6 Deploy (SAM)"
sam build >/dev/null
if [ -f samconfig.toml ]; then
  sam deploy --no-confirm-changeset --no-fail-on-empty-changeset
else
  echo "  First deploy — answer the guided prompts (defaults are safe)."
  sam deploy --guided --stack-name "$STACK_NAME"
fi
ok "stack deployed"

# ── 4. Secrets → SSM ─────────────────────────────────────────────────────────
bold "4/6 Sync secrets to SSM"
"$PY" sync_secrets.py --dotenv "$ENV_FILE"
ok "secrets synced"

# ── 5. Smoke test (dry-run injection) ────────────────────────────────────────
bold "5/6 Smoke test (Layer 2, dry-run)"
FN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
      --query "Stacks[0].Outputs[?OutputKey=='FunctionName'].OutputValue" --output text)
[ -n "$FN" ] && [ "$FN" != "None" ] || die "Could not resolve function name from stack outputs."
OUT=$(mktemp)
aws lambda invoke --function-name "$FN" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"test_signal":"Moderate","message_id":"bootstrap-smoke"}' \
  "$OUT" >/dev/null
ACTION=$(grep -o '"action":[^,}]*' "$OUT" | head -1 || true)
echo "  invoke result: ${ACTION:-<no action field>}"
if echo "$ACTION" | grep -q "preview"; then
  ok "PASS — dry-run preview ran. Check Telegram for the fully-formed (not placed) orders."
else
  printf "  \033[33m⚠ Smoke test did not return action=preview. Inspect: %s and CloudWatch logs.\033[0m\n" "$OUT"
fi

# ── 6. Finish ────────────────────────────────────────────────────────────────
bold "6/6 Done"
cat <<EOF
  The agent is deployed, polling hourly, and is OFF + DRY by default.

  To go live (deliberate, separate step):
    1) In $ENV_FILE set DRY_RUN=false then TRADING_ENABLED=true (one at a time).
    2) Redeploy the toggles:  sam deploy --no-confirm-changeset \\
         --parameter-overrides DryRun=false TradingEnabled=true
    3) Keep the first real signal small; watch the four Telegram messages.

  Kill switch (halts instantly, no redeploy): set KILL_SWITCH=1 in $ENV_FILE then
    ./.venv/bin/python sync_secrets.py
EOF
