"""Telegram notify + veto-reply reader (spec §6).

Outbound `sendMessage` for the four messages; inbound veto via `getUpdates`
long-poll (NO webhook — we even defensively `deleteWebhook` once, since a stray
webhook silently breaks getUpdates). `classify_reply` is pure and unit-tested.
"""

from __future__ import annotations

import time
from typing import Callable

import requests

from config import CANCEL_WORDS, GO_WORDS

API = "https://api.telegram.org/bot{token}/{method}"

# Decisions returned by the veto poll.
CANCEL = "cancel"
GO = "go"
TIMEOUT = "timeout"  # silence ⇒ proceed


def classify_reply(text: str) -> str | None:
    """Map a reply to CANCEL / GO / None (undecided). Case-insensitive, first token."""
    if not text:
        return None
    token = text.strip().split()[0].upper() if text.strip() else ""
    if token in CANCEL_WORDS:
        return CANCEL
    if token in GO_WORDS:
        return GO
    return None


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, session: requests.Session | None = None):
        self._token = token
        self._chat_id = str(chat_id)
        self._http = session or requests.Session()
        self._offset: int | None = None  # getUpdates cursor

    def _call(self, method: str, params: dict, timeout: float) -> dict:
        resp = self._http.post(
            API.format(token=self._token, method=method), data=params, timeout=timeout
        )
        resp.raise_for_status()
        return resp.json()

    def send(self, text: str) -> None:
        self._call(
            "sendMessage",
            {"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )

    def delete_webhook(self) -> None:
        """Defensive: ensure no webhook is set so getUpdates works (spec §6)."""
        try:
            self._call("deleteWebhook", {"drop_pending_updates": "false"}, timeout=15)
        except Exception:
            pass  # best-effort; never block the run on this

    def drain_updates(self) -> None:
        """Advance the offset past any backlog so the veto window only sees replies
        sent AFTER the plan message."""
        data = self._call("getUpdates", {"timeout": 0, "offset": -1}, timeout=20)
        for upd in data.get("result", []):
            self._offset = upd["update_id"] + 1

    def poll_for_decision(
        self,
        window_seconds: int,
        after_ts: int,
        now: Callable[[], float] = time.monotonic,
    ) -> str:
        """Long-poll getUpdates until a decisive reply or the window expires.

        Returns CANCEL, GO, or TIMEOUT. Only replies from the configured chat whose
        Telegram `date` >= after_ts count. Silence ⇒ TIMEOUT (caller proceeds).
        """
        deadline = now() + window_seconds
        while True:
            remaining = deadline - now()
            if remaining <= 0:
                return TIMEOUT
            long_poll = max(1, min(50, int(remaining)))
            try:
                data = self._call(
                    "getUpdates",
                    {"timeout": long_poll, "offset": self._offset} if self._offset
                    else {"timeout": long_poll},
                    timeout=long_poll + 10,
                )
            except Exception:
                # Can't read replies → spec §6.1: proceed (veto, not gate). Treat as
                # timeout only after the window; here, brief backoff then retry.
                time.sleep(2)
                continue
            for upd in data.get("result", []):
                self._offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message") or {}
                if str(msg.get("chat", {}).get("id")) != self._chat_id:
                    continue
                if int(msg.get("date", 0)) < after_ts:
                    continue
                decision = classify_reply(msg.get("text", ""))
                if decision in (CANCEL, GO):
                    return decision
