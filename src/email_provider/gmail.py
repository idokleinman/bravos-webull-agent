"""Gmail fetch of the newest Bravos signal email (spec §4, §16 Layer 3).

Read-only Gmail API via an installed-app OAuth refresh token. Pure helpers
(`extract_body`, `get_header`) are unit-tested without any network.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from config import BRAVOS_SENDER, GMAIL_QUERY

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_URI = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True)
class Message:
    id: str
    from_header: str
    auth_results: str   # all Authentication-Results headers, newline-joined
    body: str
    internal_date: int  # epoch ms


def get_header(headers: list[dict], name: str) -> str:
    """Join all header values with the given name (case-insensitive)."""
    name = name.lower()
    vals = [h.get("value", "") for h in headers if h.get("name", "").lower() == name]
    return "\n".join(vals)


def _b64url(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")


def extract_body(payload: dict) -> str:
    """Recursively pull the message body, preferring text/plain over text/html."""
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if data:
            if mime == "text/plain":
                plain.append(_b64url(data))
            elif mime == "text/html":
                html.append(_b64url(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain)
    return "\n".join(html)


class GmailClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, service=None):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._service = service

    def _svc(self):
        if self._service is None:
            from google.oauth2.credentials import Credentials  # lazy
            from googleapiclient.discovery import build

            creds = Credentials(
                token=None,
                refresh_token=self._refresh_token,
                client_id=self._client_id,
                client_secret=self._client_secret,
                token_uri=TOKEN_URI,
                scopes=[GMAIL_SCOPE],
            )
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def fetch_latest(self, query: str | None = None) -> Message | None:
        """Return the newest message matching `query` (default = Bravos query), or None."""
        q = query or GMAIL_QUERY
        svc = self._svc()
        listing = (
            svc.users().messages().list(userId="me", q=q, maxResults=5).execute()
        )
        msgs = listing.get("messages", [])
        if not msgs:
            return None
        # Gmail returns newest first.
        msg_id = msgs[0]["id"]
        full = (
            svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        )
        payload = full.get("payload", {})
        headers = payload.get("headers", [])
        return Message(
            id=msg_id,
            from_header=get_header(headers, "From"),
            auth_results=get_header(headers, "Authentication-Results"),
            body=extract_body(payload),
            internal_date=int(full.get("internalDate", 0)),
        )

    def fetch_latest_from(self, sender: str) -> Message | None:
        """TEST_EMAIL_MODE path: newest message from an arbitrary sender (spec §16 L3)."""
        return self.fetch_latest(query=f"from:{sender} newer_than:2d")


def bravos_query() -> str:
    return GMAIL_QUERY


def bravos_sender() -> str:
    return BRAVOS_SENDER
