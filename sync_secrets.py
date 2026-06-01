#!/usr/bin/env python3
"""Push secrets from .env into SSM Parameter Store as SecureString (spec §8).

Reads SECRET_* keys (and the KILL_SWITCH) from .env and writes them under the
SSM_PREFIX. Secrets never enter the Lambda package or plaintext env vars — the
function reads them from SSM at runtime.

Usage:
    python sync_secrets.py            # uses .env in cwd
    python sync_secrets.py --dotenv path/to/.env --prefix /bravos-webull/prod
"""

from __future__ import annotations

import argparse
import sys

import boto3

# Logical secret names expected by the Lambda (see config.SECRET_KEYS).
SECRET_NAMES = [
    "WEBULL_APP_KEY",
    "WEBULL_APP_SECRET",
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "GMAIL_REFRESH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


def load_dotenv(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        sys.exit(f"ERROR: {path} not found. Copy .env.example → .env and fill it in.")
    return env


def mask(v: str) -> str:
    if not v:
        return "<empty>"
    return v[:3] + "…" + v[-2:] if len(v) > 6 else "•••"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dotenv", default=".env")
    ap.add_argument("--prefix", default=None, help="override SSM_PREFIX from .env")
    ap.add_argument("--region", default=None, help="override AWS_REGION from .env")
    args = ap.parse_args()

    env = load_dotenv(args.dotenv)
    prefix = (args.prefix or env.get("SSM_PREFIX", "/bravos-webull/prod")).rstrip("/")
    region = args.region or env.get("AWS_REGION") or None
    ssm = boto3.client("ssm", region_name=region)

    missing = [n for n in SECRET_NAMES if not env.get(f"SECRET_{n}")]
    if missing:
        sys.exit("ERROR: missing SECRET_ values in .env: " + ", ".join(missing))

    written = []
    for name in SECRET_NAMES:
        value = env[f"SECRET_{name}"]
        full = f"{prefix}/{name}"
        ssm.put_parameter(Name=full, Value=value, Type="SecureString", Overwrite=True)
        written.append((full, mask(value)))

    # KILL_SWITCH lives in SSM too so it halts without a redeploy. Empty = disarmed.
    kill = env.get("KILL_SWITCH", "")
    ssm.put_parameter(
        Name=f"{prefix}/KILL_SWITCH", Value=kill or " ", Type="SecureString", Overwrite=True
    )
    written.append((f"{prefix}/KILL_SWITCH", "<set>" if kill.strip() else "<disarmed>"))

    print(f"Synced {len(written)} params to SSM ({region or 'default region'}):")
    for full, m in written:
        print(f"  {full} = {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
