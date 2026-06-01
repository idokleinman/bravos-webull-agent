import pytest

from config import QQQ, TQQQ, Config


def test_test_email_mode_refuses_to_arm():
    # Honored only under dry-run; refuses if live trading is possible.
    with pytest.raises(RuntimeError):
        Config(test_email_mode=True, dry_run=False).validate()
    # OK under dry-run.
    Config(test_email_mode=True, dry_run=True).validate()


def test_bad_webull_env_rejected():
    with pytest.raises(RuntimeError):
        Config(webull_env="staging").validate()
    with pytest.raises(RuntimeError):
        Config(webull_env="uat").validate()
    Config(webull_env="prod").validate()


def test_stop_pct_lookup():
    c = Config(stop_pct_qqq=0.10, stop_pct_tqqq=0.20)
    assert c.stop_pct(QQQ) == 0.10
    assert c.stop_pct(TQQQ) == 0.20


def test_safe_defaults():
    c = Config()
    assert c.dry_run is True
    assert c.trading_enabled is False
