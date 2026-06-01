import pytest

from email_provider.signal import parse_signal


@pytest.mark.parametrize(
    "body,expected",
    [
        ("Model Signal (Cash) has been published", "Cash"),
        ("Model Signal (Moderate) has been published", "Moderate"),
        ("Model Signal (Aggressive) has been published", "Aggressive"),
        # case-insensitive
        ("model signal (aggressive) HAS BEEN published", "Aggressive"),
        # tolerant whitespace
        ("Model  Signal ( Moderate )  has  been  published", "Moderate"),
        # embedded in a larger HTML-ish body
        ("<p>Hello. Model Signal (Cash) has been published today.</p>", "Cash"),
    ],
)
def test_valid_signals(body, expected):
    assert parse_signal(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        "",
        "Model Signal (Balanced) has been published",   # not an allowed level
        "Model Signal has been published",               # missing level
        "Model Signal (Cash) is coming soon",            # wrong verb phrase
        "A totally unrelated Bravos newsletter",
        "Model Signal (Cash)",                            # truncated
    ],
)
def test_invalid_signals_return_none(body):
    assert parse_signal(body) is None
