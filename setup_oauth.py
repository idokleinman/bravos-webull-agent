#!/usr/bin/env python3
"""One-time Gmail OAuth consent → prints (and can write) a refresh token (spec §12.3).

Run once locally. It opens a browser, you approve read-only Gmail access, and it
prints the refresh token to paste into .env as SECRET_GMAIL_REFRESH_TOKEN.

Prereqs in .env (from Google Cloud Console → OAuth client ID → Desktop app):
    SECRET_GMAIL_CLIENT_ID, SECRET_GMAIL_CLIENT_SECRET
IMPORTANT: publish the OAuth consent screen to "In production" — "Testing" mode
expires refresh tokens after 7 days.

Usage:
    python setup_oauth.py            # prints the token
    python setup_oauth.py --write    # also writes it back into .env
"""

from __future__ import annotations

import argparse
import sys

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def load_dotenv(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def write_back(path: str, token: str) -> None:
    lines = []
    found = False
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("SECRET_GMAIL_REFRESH_TOKEN="):
                lines.append(f"SECRET_GMAIL_REFRESH_TOKEN={token}\n")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"SECRET_GMAIL_REFRESH_TOKEN={token}\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dotenv", default=".env")
    ap.add_argument("--write", action="store_true", help="write the token back into .env")
    args = ap.parse_args()

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.exit("Install deps first: pip install google-auth-oauthlib (or `uv sync`).")

    env = load_dotenv(args.dotenv)
    cid = env.get("SECRET_GMAIL_CLIENT_ID")
    csec = env.get("SECRET_GMAIL_CLIENT_SECRET")
    if not cid or not csec:
        sys.exit("ERROR: set SECRET_GMAIL_CLIENT_ID and SECRET_GMAIL_CLIENT_SECRET in .env first.")

    client_config = {
        "installed": {
            "client_id": cid,
            "client_secret": csec,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    # access_type=offline + prompt=consent guarantees a refresh token is returned.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        sys.exit("No refresh token returned. Re-run; ensure prompt=consent and a fresh grant.")

    print("\n✅ Refresh token obtained:\n")
    print(creds.refresh_token)
    print("\nPaste this into .env as SECRET_GMAIL_REFRESH_TOKEN.")
    if args.write:
        write_back(args.dotenv, creds.refresh_token)
        print(f"(written to {args.dotenv})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
