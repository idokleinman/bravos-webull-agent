"""Deterministic client order IDs (spec §7.4, §7.4a).

`client_order_id = sha256(message_id + symbol + tag)` truncated to 32 hex chars
(Webull's field max is 40). Determinism gives Webull-side idempotency on top of the
state-based execute-once: re-submitting the same logical order is a no-op/duplicate.

The entry uses tag = side ('BUY'/'SELL'); the protective stop uses tag = 'STOP' so it
is distinct from the entry order for the same symbol/message.
"""

from __future__ import annotations

import hashlib

STOP_TAG = "STOP"


def client_order_id(message_id: str, symbol: str, tag: str) -> str:
    raw = f"{message_id}|{symbol}|{tag}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]
