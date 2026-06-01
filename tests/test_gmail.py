import base64

from email_provider.gmail import extract_body, get_header


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def test_get_header_joins_multiple():
    headers = [
        {"name": "From", "value": "Bravos <info@bravosresearch.com>"},
        {"name": "Authentication-Results", "value": "mx1; dkim=pass"},
        {"name": "Authentication-Results", "value": "mx2; spf=pass; dmarc=pass"},
    ]
    assert get_header(headers, "From") == "Bravos <info@bravosresearch.com>"
    ar = get_header(headers, "authentication-results")  # case-insensitive
    assert "dkim=pass" in ar and "spf=pass" in ar and "dmarc=pass" in ar


def test_extract_body_prefers_plain():
    payload = {
        "mimeType": "multipart/alternative",
        "body": {},
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("PLAIN Model Signal (Cash)")}},
            {"mimeType": "text/html", "body": {"data": b64("<p>HTML</p>")}},
        ],
    }
    assert extract_body(payload) == "PLAIN Model Signal (Cash)"


def test_extract_body_falls_back_to_html():
    payload = {
        "mimeType": "text/html",
        "body": {"data": b64("<p>Model Signal (Aggressive) has been published</p>")},
    }
    assert "Aggressive" in extract_body(payload)


def test_extract_body_nested_parts():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("nested plain")}},
            ]},
        ],
    }
    assert extract_body(payload) == "nested plain"
