import json

import pytest

from change_history import (
    HISTORY_ABORT_LUA,
    HISTORY_ACTIVE_LUA,
    HISTORY_CLAIM_LUA,
    HISTORY_COMPLETE_LUA,
    HISTORY_LIST_LUA,
    HISTORY_MARK_APPLYING_LUA,
    HISTORY_PUSH_LUA,
    HISTORY_READY_LUA,
    HISTORY_WRITE_ABORT_LUA,
    HISTORY_WRITE_BEGIN_LUA,
    HISTORY_WRITE_BIND_TARGET_LUA,
    HISTORY_WRITE_COMMIT_LUA,
    HISTORY_WRITE_MARK_STARTED_LUA,
    HISTORY_WRITE_RESOLVE_LUA,
    HISTORY_WRITE_STATUS_LUA,
    InMemoryChangeHistoryStore,
    UpstashChangeHistoryStore,
)
from coordination import CoordinationError
from coordination.serialization import RedisKeyBuilder


def _entry(index):
    return {
        "timestamp": f"2026-08-18T00:00:0{index}+00:00",
        "action": "update",
        "record_id": f"member-{index}",
        "before": {"FullName": f"Before {index}"},
        "after": {"FullName": f"After {index}"},
    }


def _operation(action="update", record_id="member-new"):
    return {"action": action, "record_id": record_id}


def test_in_memory_history_is_bounded_newest_first_and_restorable():
    backing = []
    store = InMemoryChangeHistoryStore(backing, max_entries=2)

    store.push(_entry(1))
    store.push(_entry(2))
    store.push(_entry(3))

    assert [entry["record_id"] for entry in store.list()] == [
        "member-3",
        "member-2",
    ]
    claimed = store.claim("undo-1")
    assert claimed.state == "claimed"
    assert claimed.entry["record_id"] == "member-3"

    store.abort("undo-1")
    retried = store.claim("undo-1")
    assert retried.state == "claimed"
    assert retried.entry["record_id"] == "member-3"


def test_in_memory_claim_blocks_newer_undo_and_replays_exact_completion():
    store = InMemoryChangeHistoryStore([_entry(1), _entry(2)])

    claimed = store.claim("undo-first")
    assert store.active_nonce() == "undo-first"
    context = {"action": "delete", "baseline_member_ids": ["member-1"]}
    store.mark_applying("undo-first", context)
    resumed = store.claim("undo-first")
    blocked = store.claim("undo-second")

    assert claimed.state == "claimed"
    assert resumed.state == "resumed"
    assert resumed.entry == claimed.entry
    assert resumed.context == context
    assert blocked.state == "busy"
    with pytest.raises(CoordinationError) as raised:
        store.ensure_ready()
    assert raised.value.code == "UNDO_IN_PROGRESS"

    result = {
        "status": "undone",
        "action": "Restored previous state",
        "record_id": "member-2",
    }
    store.complete("undo-first", result)
    assert store.active_nonce() is None

    replay = store.claim("undo-first")
    next_claim = store.claim("undo-second")
    assert replay.state == "completed"
    assert replay.result == result
    assert next_claim.state == "claimed"
    assert next_claim.entry == _entry(1)


def test_empty_undo_key_cannot_later_target_a_new_history_entry():
    store = InMemoryChangeHistoryStore()

    assert store.claim("undo-empty").state == "empty"
    store.push(_entry(1))

    assert store.claim("undo-empty").state == "empty"
    assert store.claim("undo-new").state == "claimed"


def test_in_memory_write_guard_blocks_undo_until_history_is_committed():
    store = InMemoryChangeHistoryStore([_entry(1)])

    operation = _operation("update", "member-2")
    store.begin_write("write-newer", operation)
    store.mark_write_started("write-newer")

    with pytest.raises(CoordinationError) as raised:
        store.claim("undo-older")
    assert raised.value.code == "UNDO_HISTORY_GAP"
    with pytest.raises(CoordinationError) as raised:
        store.ensure_ready()
    assert raised.value.code == "UNDO_HISTORY_GAP"

    store.bind_write_target("write-newer", operation)
    store.commit_write("write-newer", _entry(2))

    assert store.claim("undo-newer").entry == _entry(2)


def test_in_memory_write_guard_can_be_aborted_after_a_rolled_back_mutation():
    store = InMemoryChangeHistoryStore([_entry(1)])

    store.begin_write("write-rolled-back", _operation())
    store.mark_write_started("write-rolled-back")
    store.abort_write("write-rolled-back")
    store.abort_write("write-rolled-back")

    assert store.claim("undo-older").entry == _entry(1)


def test_in_memory_prepared_guard_is_reclaimed_after_process_death():
    store = InMemoryChangeHistoryStore([_entry(1)])

    store.begin_write("crashed-before-start", _operation("update", "member-1"))
    status = store.write_status()
    assert status is not None
    assert status.phase == "prepared"

    store.begin_write("replacement", _operation("update", "member-2"))
    store.mark_write_started("replacement")
    store.bind_write_target(
        "replacement", _operation("update", "member-2")
    )
    store.commit_write("replacement", _entry(2))

    assert store.claim("undo-replacement").entry == _entry(2)


def test_in_memory_undo_discards_a_provably_unstarted_orphan_guard():
    store = InMemoryChangeHistoryStore([_entry(1)])
    store.begin_write("crashed-before-start", _operation())

    assert store.claim("undo-existing").entry == _entry(1)
    assert store.write_status() is None


@pytest.mark.parametrize(
    "mismatched_entry",
    [
        {**_entry(2), "action": "delete"},
        {**_entry(2), "record_id": "member-other"},
    ],
)
def test_in_memory_manual_resolution_rejects_operation_mismatch(
    mismatched_entry,
):
    store = InMemoryChangeHistoryStore([_entry(1)])
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
def test_in_memory_generated_target_is_bound_before_manual_resolution(
    action,
    initial_record_id,
):
    store = InMemoryChangeHistoryStore([_entry(1)])
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


def test_in_memory_manual_abort_clears_older_history_before_resuming_writes():
    store = InMemoryChangeHistoryStore([_entry(1)])
    store.begin_write("ambiguous-write", _operation())
    store.mark_write_started("ambiguous-write")

    store.resolve_write("ambiguous-write", None)

    assert store.write_status() is None
    assert store.list() == []


def test_recovery_context_has_independent_capacity_from_http_result_receipts():
    store = InMemoryChangeHistoryStore([_entry(1)])
    store.claim("undo-large-context")
    context = {
        "action": "delete",
        "baseline_member_ids": [f"member-{index:06d}" for index in range(10_000)],
    }

    stored = store.mark_applying("undo-large-context", context)

    assert stored == context
    assert store.claim("undo-large-context").context == context


class FakeHistoryRedis:
    def __init__(self):
        self.entries = []
        self.active = None
        self.claims = {}
        self.results = {}
        self.contexts = {}
        self.write_guard = None

    def eval(self, script, keys, args, *, nonce_idempotent):
        if script == HISTORY_ACTIVE_LUA:
            return ["ACTIVE", self.active] if self.active is not None else ["INACTIVE"]
        if script == HISTORY_READY_LUA:
            if self.active is not None:
                return ["BUSY"]
            return ["GAP"] if self.write_guard is not None else ["READY"]
        if script == HISTORY_PUSH_LUA:
            if self.active is not None:
                return ["BUSY"]
            if self.write_guard is not None:
                return ["GAP"]
            encoded = args[0]
            self.entries = [item for item in self.entries if item != encoded]
            self.entries.insert(0, encoded)
            self.entries = self.entries[: int(args[1])]
            return ["PUSHED"]
        if script == HISTORY_WRITE_BEGIN_LUA:
            encoded, nonce = args
            if self.active is not None:
                return ["BUSY"]
            if self.write_guard is not None:
                guard = json.loads(self.write_guard)
                if guard["nonce"] == nonce:
                    return ["BEGUN"]
                if guard["phase"] != "prepared":
                    return ["GAP"]
                self.write_guard = encoded
                return ["RECLAIMED"]
            self.write_guard = encoded
            return ["BEGUN"]
        if script == HISTORY_WRITE_MARK_STARTED_LUA:
            nonce, started_at = args
            if self.write_guard is None:
                return ["CORRUPT"]
            guard = json.loads(self.write_guard)
            if guard["nonce"] != nonce:
                return ["GAP"]
            if guard["phase"] == "prepared":
                guard["phase"] = "started"
                guard["started_at"] = started_at
                self.write_guard = json.dumps(
                    guard, ensure_ascii=True, separators=(",", ":"), sort_keys=True
                )
            return ["STARTED", self.write_guard]
        if script == HISTORY_WRITE_BIND_TARGET_LUA:
            nonce, encoded_operation, bound_at = args
            if self.active is not None:
                return ["BUSY"]
            if self.write_guard is None:
                return ["CORRUPT"]
            guard = json.loads(self.write_guard)
            operation = json.loads(encoded_operation)
            if guard["nonce"] != nonce:
                return ["GAP"]
            if guard["operation"]["action"] != operation["action"]:
                return ["MISMATCH"]
            if guard["phase"] == "bound":
                if guard["operation"] != operation:
                    return ["MISMATCH"]
                return ["BOUND", self.write_guard]
            if guard["phase"] != "started":
                return ["CORRUPT"]
            if (
                operation["action"] in {"update", "delete"}
                and guard["operation"]["record_id"] != operation["record_id"]
            ):
                return ["MISMATCH"]
            guard["phase"] = "bound"
            guard["operation"] = operation
            guard["bound_at"] = bound_at
            self.write_guard = json.dumps(
                guard, ensure_ascii=True, separators=(",", ":"), sort_keys=True
            )
            return ["BOUND", self.write_guard]
        if script == HISTORY_WRITE_COMMIT_LUA:
            nonce, encoded, maximum, repair_guard = args
            if self.active is not None:
                return ["BUSY"]
            if self.write_guard is None:
                if self.entries and self.entries[0] == encoded:
                    return ["COMMITTED"]
                self.write_guard = repair_guard
                return ["GAP"]
            guard = json.loads(self.write_guard)
            if guard["nonce"] != nonce:
                return ["GAP"]
            if guard["phase"] != "bound":
                return ["GAP"]
            entry = json.loads(encoded)
            if guard["operation"] != {
                "action": entry["action"],
                "record_id": entry["record_id"],
            }:
                return ["MISMATCH"]
            self.entries = [item for item in self.entries if item != encoded]
            self.entries.insert(0, encoded)
            self.entries = self.entries[: int(maximum)]
            self.write_guard = None
            return ["COMMITTED"]
        if script == HISTORY_WRITE_ABORT_LUA:
            nonce = args[0]
            if self.write_guard is None:
                return ["ABORTED"]
            if json.loads(self.write_guard)["nonce"] != nonce:
                return ["GAP"]
            self.write_guard = None
            return ["ABORTED"]
        if script == HISTORY_WRITE_STATUS_LUA:
            return (
                ["ACTIVE", self.write_guard]
                if self.write_guard is not None
                else ["INACTIVE"]
            )
        if script == HISTORY_WRITE_RESOLVE_LUA:
            nonce, resolution, encoded, maximum = args
            if self.active is not None:
                return ["BUSY"]
            if self.write_guard is None:
                return ["INACTIVE"]
            guard = json.loads(self.write_guard)
            if guard["nonce"] != nonce:
                return ["GAP"]
            if resolution == "commit":
                entry = json.loads(encoded)
                if guard["operation"] != {
                    "action": entry["action"],
                    "record_id": entry["record_id"],
                }:
                    return ["MISMATCH"]
                self.entries = [item for item in self.entries if item != encoded]
                self.entries.insert(0, encoded)
                self.entries = self.entries[: int(maximum)]
            else:
                self.entries.clear()
            self.write_guard = None
            return ["RESOLVED"]
        if script == HISTORY_LIST_LUA:
            return ["HISTORY", *self.entries[: int(args[0])]]
        if script == HISTORY_CLAIM_LUA:
            nonce = args[0]
            if keys[3] in self.results:
                return ["TERMINAL", self.results[keys[3]]]
            if keys[2] in self.claims:
                assert self.active == nonce
                return ["RESUMED", self.claims[keys[2]], self.contexts.get(keys[4], "")]
            if self.active is not None:
                return ["BUSY"]
            if self.write_guard is not None:
                if json.loads(self.write_guard)["phase"] != "prepared":
                    return ["GAP"]
                self.write_guard = None
            if not self.entries:
                self.results[keys[3]] = args[1]
                return ["TERMINAL", args[1]]
            encoded = self.entries[0]
            self.active = nonce
            self.claims[keys[2]] = encoded
            return ["CLAIMED", encoded, ""]
        if script == HISTORY_MARK_APPLYING_LUA:
            nonce, encoded_context = args
            assert self.active == nonce
            assert self.entries[0] == self.claims[keys[2]]
            existing = self.contexts.get(keys[4])
            if existing is not None and existing != encoded_context:
                return ["CONFLICT"]
            self.contexts[keys[4]] = encoded_context
            return ["APPLYING", encoded_context]
        if script == HISTORY_COMPLETE_LUA:
            nonce, encoded_result = args
            if keys[2] in self.results:
                return ["TERMINAL", self.results[keys[2]]]
            assert self.active == nonce
            assert keys[1] in self.claims
            assert self.entries[0] == self.claims[keys[1]]
            self.entries.pop(0)
            self.results[keys[2]] = encoded_result
            self.claims.pop(keys[1])
            self.contexts.pop(keys[4], None)
            self.active = None
            return ["TERMINAL", encoded_result]
        if script == HISTORY_ABORT_LUA:
            nonce = args[0]
            assert self.active == nonce
            assert self.entries[0] == self.claims.pop(keys[2])
            self.contexts.pop(keys[4], None)
            self.active = None
            return ["RETRYABLE"]
        raise AssertionError("unexpected history script")


def test_upstash_history_survives_instances_and_has_durable_undo_states():
    redis = FakeHistoryRedis()
    keys = RedisKeyBuilder("test-1", "synthetic-secret")
    first = UpstashChangeHistoryStore(redis, keys, max_entries=2)
    second = UpstashChangeHistoryStore(redis, keys, max_entries=2)

    first.push(_entry(1))
    first.push(_entry(2))
    assert second.list() == [_entry(2), _entry(1)]

    claimed = second.claim("undo-remote")
    assert first.active_nonce() == "undo-remote"
    context = {"action": "delete", "baseline_member_ids": ["member-1"]}
    second.mark_applying("undo-remote", context)
    resumed = first.claim("undo-remote")
    blocked = first.claim("undo-other")
    assert claimed.state == "claimed"
    assert claimed.entry == _entry(2)
    assert resumed.state == "resumed"
    assert resumed.entry == claimed.entry
    assert resumed.context == context
    assert blocked.state == "busy"

    result = {"status": "undone", "record_id": "member-2"}
    second.complete("undo-remote", result)
    assert first.active_nonce() is None
    replay = first.claim("undo-remote")
    assert replay.state == "completed"
    assert replay.result == result

    next_claim = first.claim("undo-other")
    assert next_claim.state == "claimed"
    assert next_claim.entry == _entry(1)
    second.abort("undo-other")
    retried = first.claim("undo-other")
    assert retried.state == "claimed"
    assert retried.entry == _entry(1)


def test_upstash_completed_receipt_is_replayed_without_expiry():
    redis = FakeHistoryRedis()
    store = UpstashChangeHistoryStore(
        redis,
        RedisKeyBuilder("test-1", "synthetic-secret"),
    )
    store.push(_entry(1))
    store.claim("undo-permanent")

    store.complete("undo-permanent", {"status": "undone"})

    assert store.claim("undo-permanent").state == "completed"
