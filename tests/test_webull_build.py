from trader.webull import _pick, build_stock_order


def test_build_market_order():
    o = build_stock_order(
        client_order_id="abc", symbol="QQQ", side="BUY", qty=27,
        order_type="MARKET", tif="DAY",
    )
    assert o == {
        "client_order_id": "abc",
        "symbol": "QQQ",
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "MARKET",
        "quantity": "27",
        "support_trading_session": "N",
        "side": "BUY",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    assert "stop_price" not in o


def test_build_gtc_stop_order():
    o = build_stock_order(
        client_order_id="def", symbol="TQQQ", side="SELL", qty=183,
        order_type="STOP_LOSS", tif="GTC", stop_price=48.0,
    )
    assert o["order_type"] == "STOP_LOSS"
    assert o["time_in_force"] == "GTC"
    assert o["side"] == "SELL"
    assert o["stop_price"] == "48.00"
    assert o["quantity"] == "183"


def test_pick_first_present():
    assert _pick({"close": 10, "price": 20}, ("close", "price")) == 10
    assert _pick({"price": 20}, ("close", "price")) == 20
    assert _pick({"close": None, "price": ""}, ("close", "price"), default=0) == 0
