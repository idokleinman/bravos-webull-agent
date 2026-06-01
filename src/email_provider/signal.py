"""Parse the Bravos 'Model Signal' level from an email body (spec §4).

Pure function — no I/O. The reconcile target derives ONLY from the returned enum,
never from any other free-form text in the email.
"""

from __future__ import annotations

import re

from config import SIGNAL_REGEX

_PATTERN = re.compile(SIGNAL_REGEX, re.IGNORECASE)

# Canonical capitalization regardless of how the email cased it.
_CANONICAL = {"cash": "Cash", "moderate": "Moderate", "aggressive": "Aggressive"}


def parse_signal(body: str) -> str | None:
    """Return 'Cash' | 'Moderate' | 'Aggressive', or None if no valid match.

    None means: do NOT trade (malformed / lookalike / unrelated Bravos email).
    """
    if not body:
        return None
    m = _PATTERN.search(body)
    if not m:
        return None
    return _CANONICAL[m.group(1).lower()]
