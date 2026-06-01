"""Anti-spoofing: verify sender + email authentication (spec §7.5.8).

This is the PRIMARY security control. There is no shared secret possible (Bravos is
a third-party vendor), so a trade may only proceed when the message is genuinely
`from:info@bravosresearch.com` AND its `Authentication-Results` header reports
**dkim=pass, spf=pass, dmarc=pass**. Any missing or non-pass verdict ⇒ reject.

Pure functions — no I/O. Operate on header strings already pulled from Gmail.
"""

from __future__ import annotations

import re

from config import BRAVOS_SENDER, REQUIRED_AUTH_RESULTS

# Matches `dkim=pass`, `spf = pass`, `dmarc=pass (...)`, case-insensitive, tolerant
# of surrounding whitespace and trailing comments/params.
_METHOD_RE = {
    method: re.compile(rf"\b{method}\s*=\s*([a-z]+)", re.IGNORECASE)
    for method in REQUIRED_AUTH_RESULTS
}

# Extract an email address from a From header value like `Bravos <info@bravosresearch.com>`.
_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+")


def extract_address(from_header: str) -> str:
    if not from_header:
        return ""
    m = _ADDR_RE.search(from_header)
    return m.group(0).lower() if m else ""


def sender_is_bravos(from_header: str) -> bool:
    return extract_address(from_header) == BRAVOS_SENDER.lower()


def auth_verdicts(authentication_results: str) -> dict[str, str]:
    """Return {'dkim': 'pass'|'fail'|..., 'spf': ..., 'dmarc': ...}.

    Gmail may emit several Authentication-Results headers; pass them concatenated
    (newline-joined). A method absent from the header is reported as 'none'.
    """
    text = authentication_results or ""
    out: dict[str, str] = {}
    for method, rx in _METHOD_RE.items():
        m = rx.search(text)
        out[method] = m.group(1).lower() if m else "none"
    return out


def all_auth_pass(authentication_results: str) -> bool:
    verdicts = auth_verdicts(authentication_results)
    return all(verdicts[m] == "pass" for m in REQUIRED_AUTH_RESULTS)


def verify(from_header: str, authentication_results: str) -> tuple[bool, str]:
    """Combined check. Returns (ok, reason).

    reason is a short human string for logging/Telegram on rejection; '' on success.
    """
    if not sender_is_bravos(from_header):
        return False, f"sender not {BRAVOS_SENDER}: {extract_address(from_header)!r}"
    verdicts = auth_verdicts(authentication_results)
    failed = [f"{m}={verdicts[m]}" for m in REQUIRED_AUTH_RESULTS if verdicts[m] != "pass"]
    if failed:
        return False, "email-auth not all pass: " + ", ".join(failed)
    return True, ""
