"""Configuration surface for the Bravos→Telegram→Webull agent.

Two kinds of settings (per spec §9):

* **Operational toggles** — read from environment variables (set by the SAM
  template as Lambda env vars). These are the only things you'd normally touch.
* **Fixed constants** — hard-coded here, NOT tunable, because exposing them only
  adds risk (symbols, MARKET order type, stop-market GTC, reply vocab, etc.).

Secrets never live in env vars or the code package: they are pulled at runtime
from SSM Parameter Store (`SecretStore`). For local unit tests, `SecretStore`
falls back to plain environment variables so no AWS calls are needed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

# ─────────────────────────────────────────────────────────────────────────────
# FIXED CONSTANTS — the rules. Do not promote these to env vars.
# ─────────────────────────────────────────────────────────────────────────────

# Only these two symbols may ever be traded (hard allowlist, gate §7.5.4).
QQQ = "QQQ"
TQQQ = "TQQQ"
ALLOWED_SYMBOLS: frozenset[str] = frozenset({QQQ, TQQQ})

ORDER_TYPE_MARKET = "MARKET"
ORDER_TYPE_STOP = "STOP_LOSS"          # stop-market (no limit price) per Webull SDK enum
TIF_DAY = "DAY"                        # entries
TIF_GTC = "GTC"                        # protective stops
SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
ENTRUST_QTY = "QTY"
INSTRUMENT_EQUITY = "EQUITY"
MARKET_US = "US"

# Sender / signal matching (spec §4, §7.5.8).
BRAVOS_SENDER = "info@bravosresearch.com"
SIGNAL_REGEX = (
    r"Model\s+Signal\s*\(\s*(Cash|Moderate|Aggressive)\s*\)\s*has\s+been\s+published"
)
GMAIL_QUERY = f'from:{BRAVOS_SENDER} subject:"Model Signal" newer_than:2d'

# Email-auth verdicts that must ALL be present (anti-spoofing, the primary control).
REQUIRED_AUTH_RESULTS = ("dkim", "spf", "dmarc")

# Target portfolio per signal (spec §7.2). The reconcile target derives ONLY from
# this enum — never from free-form email text.
#   None        → flat (hold nothing)
#   "QQQ"/"TQQQ" → ~TARGET_NOTIONAL of that symbol, nothing else
SIGNAL_TARGETS: dict[str, str | None] = {
    "Cash": None,
    "Moderate": QQQ,
    "Aggressive": TQQQ,
}

# Telegram reply vocabulary (case-insensitive). Silence ⇒ proceed (veto, not gate).
CANCEL_WORDS: frozenset[str] = frozenset({"STOP", "ABORT", "NO", "CANCEL"})
GO_WORDS: frozenset[str] = frozenset({"OK", "YES", "GO", "CONFIRM"})

# State object key (single S3 object).
STATE_KEY = "state.json"
AUDIT_MAX_ENTRIES = 50

# Webull US trading session value for regular-hours-only orders.
# NOTE: the v2 demo uses "N" (no extended hours); the REST doc shows "CORE".
# Verified-at-build item — overridable here without touching call sites.
WEBULL_TRADING_SESSION = "N"
WEBULL_CATEGORY = "US_STOCK"

WEBULL_ENDPOINTS = {
    "prod": None,  # SDK default production host for region "us"
}


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONAL TOGGLES — environment-driven.
# ─────────────────────────────────────────────────────────────────────────────


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Config:
    # Safety (defaults SAFE).
    trading_enabled: bool = field(default_factory=lambda: _env_bool("TRADING_ENABLED", False))
    dry_run: bool = field(default_factory=lambda: _env_bool("DRY_RUN", True))

    # Sizing / risk.
    target_notional: float = field(default_factory=lambda: _env_float("TARGET_NOTIONAL", 11000.0))
    max_order_notional: float = field(
        default_factory=lambda: _env_float("MAX_ORDER_NOTIONAL", 12000.0)
    )
    stop_pct_qqq: float = field(default_factory=lambda: _env_float("STOP_PCT_QQQ", 0.10))
    stop_pct_tqqq: float = field(default_factory=lambda: _env_float("STOP_PCT_TQQQ", 0.20))
    confirm_window_minutes: int = field(
        default_factory=lambda: _env_int("CONFIRM_WINDOW_MINUTES", 10)
    )

    # Infra.
    state_bucket: str = field(default_factory=lambda: os.environ.get("STATE_BUCKET", ""))
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "us-east-1"))
    ssm_prefix: str = field(
        default_factory=lambda: os.environ.get("SSM_PREFIX", "/bravos-webull/prod")
    )

    # Webull.
    webull_env: str = field(default_factory=lambda: os.environ.get("WEBULL_ENV", "prod"))
    webull_region_id: str = field(
        default_factory=lambda: os.environ.get("WEBULL_REGION_ID", "us")
    )
    webull_account_id: str = field(
        default_factory=lambda: os.environ.get("WEBULL_ACCOUNT_ID", "")
    )

    # Testing (Layer 3). Honored ONLY under dry-run; see validate().
    test_email_mode: bool = field(default_factory=lambda: _env_bool("TEST_EMAIL_MODE", False))
    test_sender: str = field(default_factory=lambda: os.environ.get("TEST_SENDER", ""))

    def stop_pct(self, symbol: str) -> float:
        return self.stop_pct_qqq if symbol == QQQ else self.stop_pct_tqqq

    def validate(self) -> None:
        """Fail fast on unsafe combinations. Called once at handler start."""
        if self.test_email_mode and not self.dry_run:
            raise RuntimeError(
                "TEST_EMAIL_MODE requires DRY_RUN=true (refusing to relax the sender "
                "gate while live trading is possible)."
            )
        if self.webull_env not in WEBULL_ENDPOINTS:
            raise RuntimeError(f"WEBULL_ENV must be one of {list(WEBULL_ENDPOINTS)}")

    @property
    def webull_endpoint(self) -> str | None:
        return WEBULL_ENDPOINTS[self.webull_env]


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()


# ─────────────────────────────────────────────────────────────────────────────
# SECRETS — SSM SecureString with in-memory cache; env fallback for local tests.
# ─────────────────────────────────────────────────────────────────────────────

# Logical secret name → SSM param leaf / env var leaf. The KILL_SWITCH lives here
# too so it can halt trading without a redeploy (read fresh each invocation).
SECRET_KEYS = (
    "WEBULL_APP_KEY",
    "WEBULL_APP_SECRET",
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "GMAIL_REFRESH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)
KILL_SWITCH_KEY = "KILL_SWITCH"


class SecretStore:
    """Reads SecureString params from SSM under ``ssm_prefix``.

    Local/test fallback: if ``use_env`` is set (or boto3 is unavailable), reads
    ``SECRET_<NAME>`` / ``KILL_SWITCH`` from the environment instead, so unit tests
    never touch AWS.
    """

    def __init__(self, prefix: str, region: str, use_env: bool | None = None):
        self._prefix = prefix.rstrip("/")
        self._region = region
        self._cache: dict[str, str] = {}
        self._use_env = _env_bool("SECRETS_FROM_ENV", False) if use_env is None else use_env
        self._client = None

    def _ssm(self):
        if self._client is None:
            import boto3  # lazy: keeps unit tests AWS-free

            self._client = boto3.client("ssm", region_name=self._region)
        return self._client

    def get(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]
        if self._use_env:
            value = os.environ.get(f"SECRET_{name}", os.environ.get(name, ""))
        else:
            resp = self._ssm().get_parameter(
                Name=f"{self._prefix}/{name}", WithDecryption=True
            )
            value = resp["Parameter"]["Value"]
        self._cache[name] = value
        return value

    def kill_switch_engaged(self) -> bool:
        """True if the kill switch is set (any non-empty value). Read fresh — not cached
        across invocations because the store is reconstructed per Lambda run."""
        try:
            if self._use_env:
                return bool(os.environ.get(KILL_SWITCH_KEY, "").strip())
            resp = self._ssm().get_parameter(
                Name=f"{self._prefix}/{KILL_SWITCH_KEY}", WithDecryption=True
            )
            return bool(resp["Parameter"]["Value"].strip())
        except Exception:
            # Missing param ⇒ not engaged. (Param absence is the normal "armed" state.)
            return False
