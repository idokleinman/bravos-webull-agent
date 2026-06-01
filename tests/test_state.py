from state.s3_state import InMemoryStateStore, State


def test_first_run_when_no_executed_id():
    assert State().is_first_run
    assert not State(last_executed_message_id="m1").is_first_run


def test_is_handled_executed_or_skipped():
    s = State(last_executed_message_id="exec", last_skipped_message_id="veto")
    assert s.is_handled("exec")
    assert s.is_handled("veto")
    assert not s.is_handled("fresh")


def test_audit_bounded(monkeypatch):
    import state.s3_state as mod

    monkeypatch.setattr(mod, "AUDIT_MAX_ENTRIES", 5)
    s = State()
    for i in range(20):
        s.add_audit({"i": i})
    assert len(s.audit) == 5
    assert s.audit[0]["i"] == 15 and s.audit[-1]["i"] == 19


def test_json_roundtrip():
    s = State(
        last_alerted_message_id="a", last_executed_message_id="b",
        last_skipped_message_id=None, last_signal="Moderate",
        last_action_at="2026-06-01T00:00:00Z", audit=[{"x": 1}],
    )
    s2 = State.from_json(s.to_json())
    assert s2 == s


def test_from_json_empty():
    assert State.from_json("") == State()
    assert State.from_json("{}").is_first_run


def test_in_memory_store_roundtrip():
    store = InMemoryStateStore()
    st = store.load()
    st.last_signal = "Aggressive"
    store.save(st)
    assert store.load().last_signal == "Aggressive"
