from email_provider import authres

PASS = "mx.google.com; dkim=pass header.i=@bravosresearch.com; spf=pass; dmarc=pass"
FROM_OK = "Bravos Research <info@bravosresearch.com>"


def test_extract_address():
    assert authres.extract_address(FROM_OK) == "info@bravosresearch.com"
    assert authres.extract_address("info@bravosresearch.com") == "info@bravosresearch.com"
    assert authres.extract_address("") == ""


def test_sender_match():
    assert authres.sender_is_bravos(FROM_OK)
    assert authres.sender_is_bravos("INFO@BravosResearch.com")
    assert not authres.sender_is_bravos("Imposter <info@bravos-research.com>")
    assert not authres.sender_is_bravos("attacker@evil.com")


def test_all_auth_pass():
    assert authres.all_auth_pass(PASS)
    assert authres.all_auth_pass("dkim=pass\nspf=pass\ndmarc=pass")


def test_each_method_failure_blocks():
    assert not authres.all_auth_pass("dkim=fail; spf=pass; dmarc=pass")
    assert not authres.all_auth_pass("dkim=pass; spf=softfail; dmarc=pass")
    assert not authres.all_auth_pass("dkim=pass; spf=pass; dmarc=fail")
    # missing method entirely ⇒ 'none' ⇒ blocked
    assert not authres.all_auth_pass("dkim=pass; spf=pass")
    assert not authres.all_auth_pass("")


def test_verify_combined():
    ok, reason = authres.verify(FROM_OK, PASS)
    assert ok and reason == ""

    ok, reason = authres.verify("attacker@evil.com", PASS)
    assert not ok and "sender not" in reason

    ok, reason = authres.verify(FROM_OK, "dkim=pass; spf=fail; dmarc=pass")
    assert not ok and "spf=fail" in reason


def test_spoof_same_sentence_wrong_sender():
    # The exact trigger sentence from a non-Bravos sender with passing auth on a
    # DIFFERENT domain must still be rejected.
    ok, _ = authres.verify("evil@elsewhere.com", "dkim=pass; spf=pass; dmarc=pass")
    assert not ok
