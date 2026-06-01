from trader.ids import STOP_TAG, client_order_id


def test_deterministic():
    a = client_order_id("msg-1", "QQQ", "BUY")
    b = client_order_id("msg-1", "QQQ", "BUY")
    assert a == b
    assert len(a) == 32


def test_distinct_by_side_symbol_message_and_stop():
    base = client_order_id("msg-1", "QQQ", "BUY")
    assert base != client_order_id("msg-1", "QQQ", "SELL")
    assert base != client_order_id("msg-1", "TQQQ", "BUY")
    assert base != client_order_id("msg-2", "QQQ", "BUY")
    assert base != client_order_id("msg-1", "QQQ", STOP_TAG)   # entry vs stop distinct
