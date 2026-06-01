import pytest

from notifier.telegram import CANCEL, GO, classify_reply


@pytest.mark.parametrize("text", ["STOP", "stop", "Abort", "no", "CANCEL", " cancel please"])
def test_cancel_words(text):
    assert classify_reply(text) == CANCEL


@pytest.mark.parametrize("text", ["OK", "ok", "Yes", "go", "CONFIRM", "ok go ahead"])
def test_go_words(text):
    assert classify_reply(text) == GO


@pytest.mark.parametrize("text", ["", "maybe", "hello there", "what?", "stopx"])
def test_undecided(text):
    assert classify_reply(text) is None
