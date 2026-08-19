import pytest

from change_history import UpstashChangeHistoryStore
from coordination import CoordinationError
from coordination.serialization import RedisKeyBuilder


def _entry(index: int) -> dict[str, object]:
    return {
        "timestamp": f"2026-08-18T00:00:0{index}+00:00",
        "action": "update",
        "record_id": f"member-{index}",
        "before": {"FullName": f"Before {index}"},
        "after": {"FullName": f"After {index}"},
    }


def _operation(action="update", record_id="member-new"):
    return {"action": action, "record_id": record_id}


def test_production_history_lua_keeps_head_until_durable_completion(production_lua):
    keys = RedisKeyBuilder("history-lua", "synthetic-secret")
    first = UpstashChangeHistoryStore(production_lua, keys)
    second = UpstashChangeHistoryStore(production_lua, keys)
    first.push(_entry(1))
    first.push(_entry(2))

    claimed = first.claim("undo-1")
    assert second.active_nonce() == "undo-1"
    context = {"action": "delete", "baseline_member_ids": ["member-1"]}
    first.mark_applying("undo-1", context)

    assert claimed.state == "claimed"
    assert second.list() == [_entry(2), _entry(1)]
    assert second.claim("undo-2").state == "busy"
    resumed = second.claim("undo-1")
    assert resumed.state == "resumed"
    assert resumed.context == context

    result = {"status": "undone", "record_id": "member-2"}
    assert first.complete("undo-1", result) == result
    assert second.active_nonce() is None
    assert second.claim("undo-1").result == result
    assert second.list() == [_entry(1)]


def test_production_history_lua_abort_and_empty_receipt_are_idempotent(
    production_lua,
):
    store = UpstashChangeHistoryStore(
        production_lua,
        RedisKeyBuilder("history-lua", "synthetic-secret"),
    )
    store.push(_entry(1))
    assert store.claim("undo-abort").state == "claimed"
    store.abort("undo-abort")
    assert store.claim("undo-abort").state == "claimed"
    store.complete("undo-abort", {"status": "undone"})

    assert store.claim("undo-empty").state == "empty"
    store.push(_entry(2))
    assert store.claim("undo-empty").state == "empty"
    assert store.claim("undo-new").state == "claimed"


def test_production_history_lua_write_guard_blocks_other_writes_and_undo(
    production_lua,
):
    keys = RedisKeyBuilder("history-lua", "synthetic-secret")
    first = UpstashChangeHistoryStore(production_lua, keys)
    second = UpstashChangeHistoryStore(production_lua, keys)
    first.push(_entry(1))

    operation = _operation("update", "member-2")
    first.begin_write("write-newer", operation)
    first.begin_write("write-newer", operation)
    first.mark_write_started("write-newer")
    first.mark_write_started("write-newer")

    with pytest.raises(CoordinationError, match="UNDO_HISTORY_GAP"):
        second.begin_write("write-other", _operation("delete", "member-other"))
    with pytest.raises(CoordinationError, match="UNDO_HISTORY_GAP"):
        second.claim("undo-older")

    first.bind_write_target("write-newer", operation)
    first.bind_write_target("write-newer", operation)
    first.commit_write("write-newer", _entry(2))
    first.commit_write("write-newer", _entry(2))

    assert second.list() == [_entry(2), _entry(1)]
    assert second.claim("undo-newer").entry == _entry(2)


def test_production_history_lua_aborts_guard_after_rolled_back_write(production_lua):
    store = UpstashChangeHistoryStore(
        production_lua,
        RedisKeyBuilder("history-lua", "synthetic-secret"),
    )
    store.push(_entry(1))

    store.begin_write("write-rolled-back", _operation())
    store.mark_write_started("write-rolled-back")
    store.abort_write("write-rolled-back")
    store.abort_write("write-rolled-back")

    assert store.claim("undo-older").entry == _entry(1)


def test_production_history_lua_repairs_a_missing_guard_before_rejecting_commit(
    production_lua,
):
    keys = RedisKeyBuilder("history-lua", "synthetic-secret")
    store = UpstashChangeHistoryStore(production_lua, keys)
    store.push(_entry(1))
    operation = _operation("update", "member-2")
    store.begin_write("write-newer", operation)
    store.mark_write_started("write-newer")
    store.bind_write_target("write-newer", operation)
    production_lua.client.delete(keys.history_write_guard())

    with pytest.raises(CoordinationError, match="UNDO_HISTORY_GAP"):
        store.commit_write("write-newer", _entry(2))
    with pytest.raises(CoordinationError, match="UNDO_HISTORY_GAP"):
        store.claim("undo-older")


def test_production_history_lua_reclaims_prepared_guard_after_process_death(
    production_lua,
):
    store = UpstashChangeHistoryStore(
        production_lua,
        RedisKeyBuilder("history-lua", "synthetic-secret"),
    )
    store.push(_entry(1))
    store.begin_write("crashed-before-start", _operation("update", "member-1"))

    status = store.write_status()
    assert status is not None
    assert status.phase == "prepared"
    assert status.operation == _operation("update", "member-1")

    store.begin_write("replacement", _operation("update", "member-2"))
    store.mark_write_started("replacement")
    store.bind_write_target(
        "replacement", _operation("update", "member-2")
    )
    store.commit_write("replacement", _entry(2))

    assert store.claim("undo-replacement").entry == _entry(2)


def test_production_history_lua_undo_discards_unstarted_orphan(production_lua):
    store = UpstashChangeHistoryStore(
        production_lua,
        RedisKeyBuilder("history-lua", "synthetic-secret"),
    )
    store.push(_entry(1))
    store.begin_write("crashed-before-start", _operation())

    assert store.claim("undo-existing").entry == _entry(1)
    assert store.write_status() is None


def test_production_history_lua_manual_resolution_requires_exact_nonce(
    production_lua,
):
    store = UpstashChangeHistoryStore(
        production_lua,
        RedisKeyBuilder("history-lua", "synthetic-secret"),
    )
    store.push(_entry(1))
    store.begin_write("ambiguous-write", _operation("update", "member-2"))
    store.mark_write_started("ambiguous-write")
    store.bind_write_target(
        "ambiguous-write", _operation("update", "member-2")
    )

    with pytest.raises(CoordinationError, match="UNDO_HISTORY_GAP"):
        store.resolve_write("wrong-write", None)
    store.resolve_write("ambiguous-write", _entry(2))

    assert store.write_status() is None
    assert store.claim("undo-resolved").entry == _entry(2)


@pytest.mark.parametrize(
    "mismatched_entry",
    [
        {**_entry(2), "action": "delete"},
        {**_entry(2), "record_id": "member-other"},
    ],
)
def test_production_history_lua_manual_resolution_preserves_guard_on_mismatch(
    production_lua,
    mismatched_entry,
):
    store = UpstashChangeHistoryStore(
        production_lua,
        RedisKeyBuilder("history-lua", "synthetic-secret"),
    )
    store.push(_entry(1))
    store.begin_write("ambiguous-write", _operation("update", "member-2"))
    store.mark_write_started("ambiguous-write")
    store.bind_write_target(
        "ambiguous-write", _operation("update", "member-2")
    )
    original_status = store.write_status()

    with pytest.raises(CoordinationError, match="UNDO_HISTORY_GAP"):
        store.resolve_write("ambiguous-write", mismatched_entry)

    assert store.write_status() == original_status
    assert store.list() == [_entry(1)]


@pytest.mark.parametrize(
    ("action", "initial_record_id"),
    [("create", ""), ("approve", "pending-2")],
)
def test_production_history_lua_binds_generated_target_before_resolution(
    production_lua,
    action,
    initial_record_id,
):
    store = UpstashChangeHistoryStore(
        production_lua,
        RedisKeyBuilder("history-lua", "synthetic-secret"),
    )
    store.push(_entry(1))
    store.begin_write(
        "generated-write", _operation(action, initial_record_id)
    )
    store.mark_write_started("generated-write")
    store.bind_write_target(
        "generated-write", _operation(action, "member-2")
    )
    entry = {**_entry(2), "action": action}

    status = store.write_status()
    assert status is not None
    assert status.phase == "bound"
    assert status.operation == _operation(action, "member-2")
    store.resolve_write("generated-write", entry)

    assert store.write_status() is None
    assert store.list()[0] == entry


def test_production_history_lua_manual_abort_clears_older_history(
    production_lua,
):
    store = UpstashChangeHistoryStore(
        production_lua,
        RedisKeyBuilder("history-lua", "synthetic-secret"),
    )
    store.push(_entry(1))
    store.begin_write("ambiguous-write", _operation())
    store.mark_write_started("ambiguous-write")

    store.resolve_write("ambiguous-write", None)

    assert store.write_status() is None
    assert store.list() == []
