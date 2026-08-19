from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import threading

import main
import pytest
import relationship_writes
from change_history import InMemoryChangeHistoryStore
from coordination import CoordinationError
from fastapi.testclient import TestClient


client = TestClient(main.app)


class FakeStore:
    def __init__(self, members=None, pending=None):
        self.members = deepcopy(members or {})
        self.pending = deepcopy(pending or {})
        self.events = []
        self.created_ids = []
        self.create_commit_then_raise = False
        self.update_failures = set()
        self.update_commit_then_raise = set()
        self.update_partial_then_raise = {}
        self.fail_delete = False
        self.delete_commit_then_raise = False
        self.fail_pending = False

    def get_member_by_id(self, record_id):
        member = self.members.get(record_id)
        return deepcopy(member) if member else None

    def get_all_members(self):
        self.events.append("read-members")
        return [deepcopy(member) for member in self.members.values()]

    def get_all_pending(self):
        self.events.append("read-pending")
        return [deepcopy(pending) for pending in self.pending.values()]

    def create_member(self, fields):
        self.events.append("create-member")
        record_id = f"new-{len(self.created_ids) + 1}"
        member = {"id": record_id, **deepcopy(fields)}
        self.members[record_id] = member
        self.created_ids.append(record_id)
        if self.create_commit_then_raise:
            self.create_commit_then_raise = False
            raise TimeoutError("synthetic committed create timeout")
        return deepcopy(member)

    def update_member(self, record_id, fields):
        if record_id in self.update_failures:
            raise RuntimeError(f"synthetic update failure for {record_id}")
        if record_id in self.update_partial_then_raise:
            partial = self.update_partial_then_raise.pop(record_id)
            self.members[record_id].update(deepcopy(partial))
            raise RuntimeError(f"synthetic partial update timeout for {record_id}")
        if record_id in self.update_commit_then_raise:
            self.update_commit_then_raise.remove(record_id)
            self.members[record_id].update(deepcopy(fields))
            raise RuntimeError(f"synthetic committed update timeout for {record_id}")
        self.members[record_id].update(deepcopy(fields))
        return deepcopy(self.members[record_id])

    def delete_member(self, record_id):
        if self.fail_delete:
            raise RuntimeError("synthetic delete failure")
        del self.members[record_id]
        if self.delete_commit_then_raise:
            self.delete_commit_then_raise = False
            raise RuntimeError("synthetic committed delete timeout")
        return True

    def update_pending(self, record_id, fields):
        if self.fail_pending:
            raise RuntimeError("synthetic pending failure")
        self.pending[record_id].update(deepcopy(fields))
        return deepcopy(self.pending[record_id])


class RecordingLeaseManager:
    def __init__(self, events):
        self.events = events
        self.lease = object()

    def acquire(self, scope, acquisition_id, ttl_ms=15_000):
        self.events.append(("acquire", scope, acquisition_id, ttl_ms))
        return self.lease

    def assert_owned(self, lease):
        assert lease is self.lease
        self.events.append("assert-owned")

    def release(self, lease, request_nonce):
        assert lease is self.lease
        self.events.append(("release", request_nonce))
        return object()


class ReleaseFailingLeaseManager(RecordingLeaseManager):
    def release(self, lease, request_nonce):
        super().release(lease, request_nonce)
        raise CoordinationError("COORDINATION_UNAVAILABLE")


class AcquireFailingLeaseManager:
    def __init__(self, code):
        self.code = code

    def acquire(self, scope, acquisition_id, ttl_ms=15_000):
        raise CoordinationError(self.code)

    def assert_owned(self, lease):
        raise AssertionError("failed acquisition returned a lease")

    def release(self, lease, request_nonce):
        raise AssertionError("failed acquisition attempted release")


class ContendedLeaseManager:
    def __init__(self):
        self._guard = threading.Lock()
        self._held = False
        self._lease = object()
        self._second_attempted = threading.Event()

    def acquire(self, scope, acquisition_id, ttl_ms=15_000):
        with self._guard:
            if self._held:
                self._second_attempted.set()
                raise CoordinationError("LOCK_UNAVAILABLE")
            self._held = True
        assert self._second_attempted.wait(timeout=2)
        return self._lease

    def assert_owned(self, lease):
        if lease is not self._lease or not self._held:
            raise CoordinationError("LEASE_LOST")

    def release(self, lease, request_nonce):
        self.assert_owned(lease)
        with self._guard:
            self._held = False
        return object()


class ThreadSafeStore(FakeStore):
    def __init__(self, members=None, pending=None):
        super().__init__(members, pending)
        self._lock = threading.RLock()

    def get_member_by_id(self, record_id):
        with self._lock:
            return super().get_member_by_id(record_id)

    def create_member(self, fields):
        with self._lock:
            return super().create_member(fields)

    def update_member(self, record_id, fields):
        with self._lock:
            return super().update_member(record_id, fields)

    def delete_member(self, record_id):
        with self._lock:
            return super().delete_member(record_id)

    def get_all_pending(self):
        with self._lock:
            return super().get_all_pending()

    def update_pending(self, record_id, fields):
        with self._lock:
            return super().update_pending(record_id, fields)


def _member(record_id, spouse_id="", spouse_name=""):
    return {
        "id": record_id,
        "FullName": f"Person {record_id}",
        "SpouseRecordId": spouse_id,
        "SpouseName": spouse_name,
    }


def _parent_candidate(record_id, gender, **overrides):
    member = {
        **_member(record_id),
        "Gender": gender,
        "FatherRecordId": "",
        "MotherRecordId": "",
    }
    member.update(overrides)
    return member


def test_create_rejects_dangling_parent_before_writing():
    store = FakeStore()
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipConflict) as raised:
        relationship_writes.create_member(
            store,
            {"FullName": "Child", "FatherRecordId": "missing"},
            lease_manager=manager,
        )

    assert raised.value.code == "PARENT_NOT_FOUND"
    assert store.created_ids == []


def test_create_rejects_parent_with_explicitly_wrong_gender():
    store = FakeStore({"mother": _parent_candidate("mother", "Female")})
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipConflict) as raised:
        relationship_writes.create_member(
            store,
            {"FullName": "Child", "FatherRecordId": "mother"},
            lease_manager=manager,
        )

    assert raised.value.code == "PARENT_GENDER_MISMATCH"
    assert store.created_ids == []


def test_update_rejects_self_parent_before_writing():
    store = FakeStore({"member": _parent_candidate("member", "Male")})
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipConflict) as raised:
        relationship_writes.update_member(
            store,
            "member",
            {"FatherRecordId": "member"},
            lease_manager=manager,
        )

    assert raised.value.code == "SELF_PARENT"
    assert store.members["member"]["FatherRecordId"] == ""


def test_update_rejects_parent_that_would_close_ancestry_cycle():
    store = FakeStore(
        {
            "member": _parent_candidate("member", "Male"),
            "descendant": _parent_candidate(
                "descendant", "Male", FatherRecordId="member"
            ),
        }
    )
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipConflict) as raised:
        relationship_writes.update_member(
            store,
            "member",
            {"FatherRecordId": "descendant"},
            lease_manager=manager,
        )

    assert raised.value.code == "ANCESTRY_CYCLE"
    assert store.members["member"]["FatherRecordId"] == ""


@pytest.mark.parametrize(
    ("parent_id", "initial_gender", "updated_gender", "parent_field"),
    [
        ("father", "Male", "Female", "FatherRecordId"),
        ("mother", "Female", "Male", "MotherRecordId"),
    ],
)
def test_update_rejects_gender_change_incompatible_with_existing_children(
    parent_id,
    initial_gender,
    updated_gender,
    parent_field,
):
    store = FakeStore(
        {
            parent_id: _parent_candidate(parent_id, initial_gender),
            "child": _parent_candidate(
                "child",
                "",
                **{parent_field: parent_id},
            ),
        }
    )
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipConflict) as raised:
        relationship_writes.update_member(
            store,
            parent_id,
            {"Gender": updated_gender},
            lease_manager=manager,
        )

    assert raised.value.code == "PARENT_ROLE_GENDER_CONFLICT"
    assert store.members[parent_id]["Gender"] == initial_gender


def test_admin_update_route_rejects_incompatible_parent_gender(monkeypatch):
    store = FakeStore(
        {
            "father": _parent_candidate("father", "Male"),
            "child": _parent_candidate(
                "child", "", FatherRecordId="father"
            ),
        }
    )
    history = InMemoryChangeHistoryStore()
    manager = RecordingLeaseManager(store.events)
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager, history)

    try:
        response = client.put(
            "/api/admin/members/father",
            json={"Gender": "Female"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PARENT_ROLE_GENDER_CONFLICT"
    assert store.members["father"]["Gender"] == "Male"
    history.ensure_ready()


def test_failed_mutation_aborts_history_guard_after_no_change_was_kept():
    store = FakeStore()
    history = InMemoryChangeHistoryStore()
    manager = RecordingLeaseManager(store.events)
    nonce = "write-missing-member"

    with pytest.raises(relationship_writes.RelationshipConflict):
        relationship_writes.update_member(
            store,
            "missing",
            {"FullName": "Still Missing"},
            lease_manager=manager,
            history_preflight=lambda: history.begin_write(
                nonce, {"action": "update", "record_id": "missing"}
            ),
            history_started=lambda: history.mark_write_started(nonce),
            history_recorder=lambda _before, _after: None,
            history_abort=lambda: history.abort_write(nonce),
        )

    history.ensure_ready()


def test_create_rejects_partner_conflict_before_writing():
    store = FakeStore(
        {
            "partner": _member("partner", "someone-else"),
            "someone-else": _member("someone-else", "partner"),
        }
    )
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipConflict):
        relationship_writes.create_member(
            store,
            {"SpouseRecordId": "partner"},
            lease_manager=manager,
        )

    assert store.created_ids == []
    assert store.members["partner"]["SpouseRecordId"] == "someone-else"


def test_create_rolls_back_new_member_when_reciprocal_write_fails():
    store = FakeStore({"partner": _member("partner")})
    store.update_failures.add("partner")
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipPersistenceError) as raised:
        relationship_writes.create_member(
            store,
            {"SpouseRecordId": "partner"},
            lease_manager=manager,
        )

    assert raised.value.rollback_incomplete is False
    assert set(store.members) == {"partner"}
    assert store.members["partner"]["SpouseRecordId"] == ""


def test_create_surfaces_incomplete_compensation():
    store = FakeStore({"partner": _member("partner")})
    store.update_failures.add("partner")
    store.fail_delete = True
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipPersistenceError) as raised:
        relationship_writes.create_member(
            store,
            {"SpouseRecordId": "partner"},
            lease_manager=manager,
        )

    assert raised.value.rollback_incomplete is True


def test_update_switches_partners_and_clears_old_reciprocal_link():
    store = FakeStore(
        {
            "member": _member("member", "old"),
            "old": _member("old", "member"),
            "new": _member("new"),
        }
    )
    manager = RecordingLeaseManager(store.events)

    result = relationship_writes.update_member(
        store,
        "member",
        {"SpouseRecordId": "new"},
        lease_manager=manager,
    )

    assert result["SpouseRecordId"] == "new"
    assert store.members["old"]["SpouseRecordId"] == ""
    assert store.members["new"]["SpouseRecordId"] == "member"


def test_update_restores_old_partner_when_new_partner_write_fails():
    store = FakeStore(
        {
            "member": _member("member", "old"),
            "old": _member("old", "member"),
            "new": _member("new"),
        }
    )
    store.update_failures.add("new")
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipPersistenceError) as raised:
        relationship_writes.update_member(
            store,
            "member",
            {"SpouseRecordId": "new"},
            lease_manager=manager,
        )

    assert raised.value.rollback_incomplete is False
    assert store.members["member"]["SpouseRecordId"] == "old"
    assert store.members["old"]["SpouseRecordId"] == "member"
    assert store.members["new"]["SpouseRecordId"] == ""


def test_update_restores_both_partners_when_member_write_fails():
    store = FakeStore(
        {
            "member": _member("member", "old"),
            "old": _member("old", "member"),
            "new": _member("new"),
        }
    )
    store.update_failures.add("member")
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipPersistenceError) as raised:
        relationship_writes.update_member(
            store,
            "member",
            {"SpouseRecordId": "new"},
            lease_manager=manager,
        )

    assert raised.value.rollback_incomplete is False
    assert store.members["member"]["SpouseRecordId"] == "old"
    assert store.members["old"]["SpouseRecordId"] == "member"
    assert store.members["new"]["SpouseRecordId"] == ""


def test_approval_rolls_back_member_and_partner_when_pending_update_fails():
    store = FakeStore(
        {"partner": _member("partner")},
        {
            "pending": {
                "id": "pending",
                "Status": "Pending",
                "CleanFullName": "New Person",
                "RawFatherName": "",
                "RawMotherName": "",
                "RawSpouseName": "Person partner",
                "RawGender": "Female",
            }
        },
    )
    store.fail_pending = True
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipPersistenceError) as raised:
        relationship_writes.approve_member(
            store,
            "pending",
            lease_manager=manager,
        )

    assert raised.value.rollback_incomplete is False
    assert set(store.members) == {"partner"}
    assert store.members["partner"]["SpouseRecordId"] == ""
    assert store.pending["pending"]["Status"] == "Pending"


def test_approval_revalidates_all_raw_relationship_ids_after_lease_acquisition():
    store = FakeStore(
        {
            "father-current": {
                **_member("father-current"),
                "FullName": "Tanveer Kamal Rasheed",
                "Gender": "Male",
            },
            "mother-a": {
                **_member("mother-a"),
                "FullName": "Ambiguous Mother",
                "Gender": "Female",
            },
            "mother-b": {
                **_member("mother-b"),
                "FullName": "AMBIGUOUS   MOTHER",
                "Gender": "Female",
            },
            "spouse-current": {
                **_member("spouse-current"),
                "FullName": "Current Spouse",
                "Gender": "Male",
            },
        },
        {
            "pending": {
                "id": "pending",
                "Status": "Pending",
                "CleanFullName": "New Person",
                "RawFatherName": "Tanveer Kamal Rasheed",
                "RawMotherName": "Ambiguous Mother",
                "RawSpouseName": "Current Spouse",
                "RawGender": "Female",
                "AIMatchedFatherId": "stale-father",
                "AIMatchedMotherId": "stale-mother",
                "AIMatchedSpouseId": "stale-spouse",
            }
        },
    )
    manager = RecordingLeaseManager(store.events)

    created = relationship_writes.approve_member(
        store,
        "pending",
        lease_manager=manager,
    )

    assert created["FatherRecordId"] == "father-current"
    assert created["MotherRecordId"] == ""
    assert created["SpouseRecordId"] == "spouse-current"
    assert store.members["spouse-current"]["SpouseRecordId"] == created["id"]
    assert store.events[0][0] == "acquire"
    assert store.events.index("read-pending") < store.events.index("read-members")
    assert store.events.index("read-members") < store.events.index("create-member")
    assert store.events[-1][0] == "release"


def test_double_approval_rechecks_pending_status_and_creates_only_once():
    store = FakeStore(
        pending={
            "pending": {
                "id": "pending",
                "Status": "Pending",
                "CleanFullName": "Only Once",
                "RawFatherName": "",
                "RawMotherName": "",
                "RawSpouseName": "",
                "RawGender": "Other",
            }
        }
    )
    manager = RecordingLeaseManager(store.events)

    first = relationship_writes.approve_member(
        store,
        "pending",
        lease_manager=manager,
    )
    with pytest.raises(relationship_writes.RelationshipConflict) as raised:
        relationship_writes.approve_member(
            store,
            "pending",
            lease_manager=manager,
        )

    assert first["FullName"] == "Only Once"
    assert raised.value.code == "PENDING_ALREADY_RESOLVED"
    assert store.created_ids == [first["id"]]
    assert store.pending["pending"]["Status"] == "Approved"


def test_approval_recovers_committed_create_timeout_without_duplicate_retry():
    store = FakeStore(
        pending={
            "pending": {
                "id": "pending",
                "Status": "Pending",
                "CleanFullName": "Committed Once",
                "RawFatherName": "",
                "RawMotherName": "",
                "RawSpouseName": "",
                "RawGender": "Other",
            }
        }
    )
    store.create_commit_then_raise = True
    manager = RecordingLeaseManager(store.events)

    created = relationship_writes.approve_member(
        store,
        "pending",
        lease_manager=manager,
    )
    with pytest.raises(relationship_writes.RelationshipConflict) as raised:
        relationship_writes.approve_member(
            store,
            "pending",
            lease_manager=manager,
        )

    assert created["FullName"] == "Committed Once"
    assert raised.value.code == "PENDING_ALREADY_RESOLVED"
    assert store.created_ids == [created["id"]]
    assert store.pending["pending"]["Status"] == "Approved"


def test_concurrent_approve_and_reject_allow_at_most_one_terminal_action():
    store = ThreadSafeStore(
        pending={
            "pending": {
                "id": "pending",
                "Status": "Pending",
                "CleanFullName": "Race Candidate",
                "RawFatherName": "",
                "RawMotherName": "",
                "RawSpouseName": "",
                "RawGender": "Other",
            }
        }
    )
    manager = ContendedLeaseManager()
    start = threading.Barrier(2)

    def terminal_action(action):
        start.wait(timeout=2)
        try:
            if action == "approve":
                result = relationship_writes.approve_member(
                    store,
                    "pending",
                    lease_manager=manager,
                )
            else:
                result = relationship_writes.reject_pending(
                    store,
                    "pending",
                    lease_manager=manager,
                )
            return action, "ok", result
        except Exception as error:  # noqa: BLE001 - inspect the competing outcome.
            return action, "error", error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(terminal_action, ("approve", "reject")))

    winners = [outcome for outcome in outcomes if outcome[1] == "ok"]
    failures = [outcome for outcome in outcomes if outcome[1] == "error"]
    assert len(winners) == 1
    assert len(failures) == 1
    assert isinstance(failures[0][2], CoordinationError)
    assert failures[0][2].code == "LOCK_UNAVAILABLE"
    terminal_status = store.pending["pending"]["Status"]
    assert terminal_status in {"Approved", "Rejected"}
    assert len(store.created_ids) == (1 if terminal_status == "Approved" else 0)


def test_direct_relationship_writes_require_a_lease_manager():
    store = FakeStore()

    with pytest.raises(TypeError):
        relationship_writes.create_member(store, {"FullName": "Unsafe Create"})


def test_relationship_transaction_uses_global_scope_max_ttl_and_fresh_nonces():
    store = FakeStore()
    manager = RecordingLeaseManager(store.events)

    first = relationship_writes.create_member(
        store,
        {"FullName": "First Person"},
        lease_manager=manager,
    )
    second = relationship_writes.create_member(
        store,
        {"FullName": "Second Person"},
        lease_manager=manager,
    )

    acquisitions = [event for event in store.events if event[0] == "acquire"]
    releases = [event for event in store.events if event[0] == "release"]
    assert first["id"] != second["id"]
    assert [event[1] for event in acquisitions] == [
        "legacy-relationships",
        "legacy-relationships",
    ]
    assert [event[3] for event in acquisitions] == [300_000, 300_000]
    assert acquisitions[0][2] != acquisitions[1][2]
    assert releases[0][1] != acquisitions[0][2]
    assert releases[1][1] != acquisitions[1][2]


def test_release_failure_does_not_turn_completed_create_into_client_failure():
    store = FakeStore()
    manager = ReleaseFailingLeaseManager(store.events)

    created = relationship_writes.create_member(
        store,
        {"FullName": "Committed Person"},
        lease_manager=manager,
    )

    assert created["id"] in store.members


def test_direct_create_recovers_a_committed_timeout_without_a_duplicate():
    store = FakeStore()
    store.create_commit_then_raise = True
    manager = RecordingLeaseManager(store.events)

    created = relationship_writes.create_member(
        store,
        {"FullName": "Committed Direct Create"},
        lease_manager=manager,
    )

    assert created["FullName"] == "Committed Direct Create"
    assert store.created_ids == [created["id"]]


def test_create_timeout_recovery_matches_only_fields_the_store_persists():
    class FilteringStore(FakeStore):
        @staticmethod
        def prepare_member_create_fields(fields):
            return {key: value for key, value in fields.items() if key != "CardStyle"}

    store = FilteringStore()
    store.create_commit_then_raise = True
    manager = RecordingLeaseManager(store.events)

    created = relationship_writes.create_member(
        store,
        {
            "FullName": "Persisted Field Match",
            "CardStyle": "frontend-only-style",
        },
        lease_manager=manager,
    )

    assert created["FullName"] == "Persisted Field Match"
    assert "CardStyle" not in created
    assert store.created_ids == [created["id"]]


def test_update_timeout_recovery_matches_only_fields_the_store_persists():
    class FilteringStore(FakeStore):
        @staticmethod
        def prepare_member_update_fields(fields):
            return {key: value for key, value in fields.items() if key != "CardStyle"}

    store = FilteringStore({"member": _member("member")})
    store.update_commit_then_raise.add("member")
    manager = RecordingLeaseManager(store.events)

    updated = relationship_writes.update_member(
        store,
        "member",
        {
            "FullName": "Persisted Update Match",
            "CardStyle": "frontend-only-style",
        },
        lease_manager=manager,
    )

    assert updated["FullName"] == "Persisted Update Match"
    assert "CardStyle" not in updated


def test_concurrent_creates_for_one_spouse_have_exactly_one_winner():
    store = ThreadSafeStore(
        {
            "partner": {
                **_member("partner"),
                "FullName": "Shared Partner",
            }
        }
    )
    manager = ContendedLeaseManager()
    start = threading.Barrier(2)

    def create_candidate(name):
        start.wait(timeout=2)
        try:
            return relationship_writes.create_member(
                store,
                {"FullName": name, "SpouseRecordId": "partner"},
                lease_manager=manager,
            )
        except Exception as error:  # noqa: BLE001 - inspect the competing outcome.
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create_candidate, ("Candidate A", "Candidate B")))

    winners = [outcome for outcome in outcomes if isinstance(outcome, dict)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(winners) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], CoordinationError)
    assert failures[0].code == "LOCK_UNAVAILABLE"
    winner = winners[0]
    assert store.members["partner"]["SpouseRecordId"] == winner["id"]
    assert store.members["partner"]["SpouseName"] == winner["FullName"]
    assert store.members[winner["id"]]["SpouseRecordId"] == "partner"
    assert store.members[winner["id"]]["SpouseName"] == "Shared Partner"


def test_create_sets_canonical_reciprocal_spouse_ids_and_names():
    store = FakeStore(
        {"partner": {**_member("partner"), "FullName": "Canonical Partner"}}
    )
    manager = RecordingLeaseManager(store.events)

    created = relationship_writes.create_member(
        store,
        {
            "FullName": "Canonical Member",
            "SpouseRecordId": "partner",
            "SpouseName": "Caller Supplied Wrong Name",
        },
        lease_manager=manager,
    )

    assert created["SpouseRecordId"] == "partner"
    assert created["SpouseName"] == "Canonical Partner"
    assert store.members["partner"]["SpouseRecordId"] == created["id"]
    assert store.members["partner"]["SpouseName"] == "Canonical Member"


def test_update_clear_and_switch_keep_both_spouse_fields_consistent():
    store = FakeStore(
        {
            "member": _member("member", "old", "Person old"),
            "old": _member("old", "member", "Person member"),
            "new": _member("new"),
        }
    )
    manager = RecordingLeaseManager(store.events)

    cleared = relationship_writes.update_member(
        store,
        "member",
        {"SpouseRecordId": ""},
        lease_manager=manager,
    )

    assert cleared["SpouseRecordId"] == ""
    assert cleared["SpouseName"] == ""
    assert store.members["old"]["SpouseRecordId"] == ""
    assert store.members["old"]["SpouseName"] == ""

    switched = relationship_writes.update_member(
        store,
        "member",
        {"SpouseRecordId": "new", "SpouseName": "Wrong New Name"},
        lease_manager=manager,
    )

    assert switched["SpouseRecordId"] == "new"
    assert switched["SpouseName"] == "Person new"
    assert store.members["new"]["SpouseRecordId"] == "member"
    assert store.members["new"]["SpouseName"] == "Person member"


def test_explicit_unresolved_spouse_name_survives_only_without_a_record_id():
    store = FakeStore({"member": _member("member")})
    manager = RecordingLeaseManager(store.events)

    unresolved = relationship_writes.update_member(
        store,
        "member",
        {"SpouseRecordId": "", "SpouseName": "Unresolved Person"},
        lease_manager=manager,
    )
    cleared = relationship_writes.update_member(
        store,
        "member",
        {"SpouseRecordId": ""},
        lease_manager=manager,
    )

    assert unresolved["SpouseName"] == "Unresolved Person"
    assert cleared["SpouseName"] == ""


def test_partner_commit_then_timeout_is_journaled_before_primary_failure():
    store = FakeStore(
        {
            "member": _member("member", "old", "Person old"),
            "old": _member("old", "member", "Person member"),
            "new": _member("new"),
        }
    )
    store.update_commit_then_raise.add("new")
    store.update_failures.add("member")
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipPersistenceError) as raised:
        relationship_writes.update_member(
            store,
            "member",
            {"SpouseRecordId": "new"},
            lease_manager=manager,
        )

    assert raised.value.rollback_incomplete is False
    assert store.members["member"]["SpouseRecordId"] == "old"
    assert store.members["member"]["SpouseName"] == "Person old"
    assert store.members["old"]["SpouseRecordId"] == "member"
    assert store.members["old"]["SpouseName"] == "Person member"
    assert store.members["new"]["SpouseRecordId"] == ""
    assert store.members["new"]["SpouseName"] == ""


def test_partial_primary_commit_then_timeout_restores_primary_and_partners():
    store = FakeStore(
        {
            "member": _member("member", "old", "Person old"),
            "old": _member("old", "member", "Person member"),
            "new": _member("new"),
        }
    )
    store.update_partial_then_raise["member"] = {"SpouseRecordId": "new"}
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipPersistenceError) as raised:
        relationship_writes.update_member(
            store,
            "member",
            {"SpouseRecordId": "new"},
            lease_manager=manager,
        )

    assert raised.value.rollback_incomplete is False
    assert store.members["member"]["SpouseRecordId"] == "old"
    assert store.members["member"]["SpouseName"] == "Person old"
    assert store.members["old"]["SpouseRecordId"] == "member"
    assert store.members["old"]["SpouseName"] == "Person member"
    assert store.members["new"]["SpouseRecordId"] == ""
    assert store.members["new"]["SpouseName"] == ""


def test_full_primary_commit_then_timeout_is_treated_as_committed():
    store = FakeStore(
        {
            "member": _member("member", "old", "Person old"),
            "old": _member("old", "member", "Person member"),
            "new": _member("new"),
        }
    )
    store.update_commit_then_raise.add("member")
    manager = RecordingLeaseManager(store.events)

    result = relationship_writes.update_member(
        store,
        "member",
        {"SpouseRecordId": "new"},
        lease_manager=manager,
    )

    assert result["SpouseRecordId"] == "new"
    assert result["SpouseName"] == "Person new"
    assert store.members["old"]["SpouseRecordId"] == ""
    assert store.members["new"]["SpouseRecordId"] == "member"


def test_delete_clears_reciprocal_fields_and_accepts_committed_timeout():
    store = FakeStore(
        {
            "member": _member("member", "partner", "Person partner"),
            "partner": _member("partner", "member", "Person member"),
        }
    )
    store.delete_commit_then_raise = True
    manager = RecordingLeaseManager(store.events)

    deleted = relationship_writes.delete_member(
        store,
        "member",
        lease_manager=manager,
    )

    assert deleted is True
    assert "member" not in store.members
    assert store.members["partner"]["SpouseRecordId"] == ""
    assert store.members["partner"]["SpouseName"] == ""


def test_delete_failure_restores_reciprocal_fields_when_member_still_exists():
    store = FakeStore(
        {
            "member": _member("member", "partner", "Person partner"),
            "partner": _member("partner", "member", "Person member"),
        }
    )
    store.fail_delete = True
    manager = RecordingLeaseManager(store.events)

    with pytest.raises(relationship_writes.RelationshipPersistenceError) as raised:
        relationship_writes.delete_member(
            store,
            "member",
            lease_manager=manager,
        )

    assert raised.value.rollback_incomplete is False
    assert store.members["member"]["SpouseRecordId"] == "partner"
    assert store.members["partner"]["SpouseRecordId"] == "member"
    assert store.members["partner"]["SpouseName"] == "Person member"


def test_admin_create_returns_conflict_instead_of_overwriting_partner(monkeypatch):
    write_gates = __import__("write_gates")
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    main.app.dependency_overrides[write_gates.require_relationship_writes] = lambda: (
        None
    )
    main.app.dependency_overrides[main.get_relationship_lease_manager] = lambda: (
        RecordingLeaseManager([])
    )
    monkeypatch.setattr(
        main.db,
        "get_member_by_id",
        lambda _record_id: _member("partner", "someone-else"),
    )
    monkeypatch.setattr(
        main.db,
        "create_member",
        lambda _fields: pytest.fail("conflicting relationship reached create"),
    )
    try:
        response = client.post(
            "/api/admin/members",
            json={"FullName": "New Person", "SpouseRecordId": "partner"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SPOUSE_CONFLICT"


def test_admin_create_reports_failed_reciprocal_write_and_compensates(monkeypatch):
    write_gates = __import__("write_gates")
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    main.app.dependency_overrides[write_gates.require_relationship_writes] = lambda: (
        None
    )
    main.app.dependency_overrides[main.get_relationship_lease_manager] = lambda: (
        RecordingLeaseManager([])
    )
    calls = []
    monkeypatch.setattr(
        main.db, "get_member_by_id", lambda _record_id: _member("partner")
    )
    monkeypatch.setattr(main.db, "get_all_members", lambda: [_member("partner")])
    monkeypatch.setattr(
        main.db,
        "create_member",
        lambda fields: {"id": "created", **fields},
    )
    monkeypatch.setattr(
        main.db,
        "update_member",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )
    monkeypatch.setattr(
        main.db, "delete_member", lambda record_id: calls.append(record_id)
    )
    try:
        response = client.post(
            "/api/admin/members",
            json={"FullName": "New Person", "SpouseRecordId": "partner"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "RELATIONSHIP_WRITE_FAILED"
    assert calls == ["created"]


def test_admin_relationship_write_fails_closed_when_coordination_is_unconfigured(
    monkeypatch,
):
    from config import Settings

    dependency = getattr(main, "get_relationship_lease_manager", None)
    assert dependency is not None
    write_gates = __import__("write_gates")
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    main.app.dependency_overrides[write_gates.require_relationship_writes] = lambda: None
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: Settings(app_env="test", _env_file=None),
    )
    monkeypatch.setattr(
        main.db,
        "create_member",
        lambda _fields: pytest.fail("unconfigured coordination reached Airtable"),
    )
    try:
        response = client.post(
            "/api/admin/members",
            json={"FullName": "Blocked Person"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "COORDINATION_UNINITIALIZED",
        "message": "Relationship coordination is not initialized.",
    }


@pytest.mark.parametrize(
    ("coordination_code", "message"),
    [
        (
            "COORDINATION_UNAVAILABLE",
            "Relationship coordination is temporarily unavailable.",
        ),
        ("LOCK_UNAVAILABLE", "Another relationship update is in progress."),
    ],
)
def test_admin_relationship_write_maps_coordination_failures_to_stable_503(
    monkeypatch, coordination_code, message
):
    dependency = getattr(main, "get_relationship_lease_manager", None)
    assert dependency is not None
    write_gates = __import__("write_gates")
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    main.app.dependency_overrides[write_gates.require_relationship_writes] = lambda: None
    main.app.dependency_overrides[dependency] = lambda: AcquireFailingLeaseManager(
        coordination_code
    )
    monkeypatch.setattr(
        main.db,
        "create_member",
        lambda _fields: pytest.fail("failed coordination reached Airtable"),
    )
    try:
        response = client.post(
            "/api/admin/members",
            json={"FullName": "Blocked Person"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": coordination_code,
        "message": message,
    }


def _install_admin_write_dependencies(manager, history_store=None):
    dependency = getattr(main, "get_relationship_lease_manager", None)
    assert dependency is not None
    write_gates = __import__("write_gates")
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    main.app.dependency_overrides[write_gates.require_relationship_writes] = lambda: None
    main.app.dependency_overrides[dependency] = lambda: manager
    if history_store is not None:
        main.app.dependency_overrides[main.get_change_history_store] = lambda: history_store


def test_undo_create_uses_coordinated_delete_and_clears_reciprocal_spouse(
    monkeypatch,
):
    store = FakeStore(
        {
            "created": _member("created", "partner", "Person partner"),
            "partner": _member("partner", "created", "Person created"),
        }
    )
    manager = RecordingLeaseManager(store.events)
    previous_history = deepcopy(main._change_history)
    main._change_history[:] = [
        {
            "timestamp": "2026-08-18T00:00:00+00:00",
            "action": "create",
            "record_id": "created",
            "before": None,
            "after": deepcopy(store.members["created"]),
        }
    ]
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager)

    try:
        response = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-create-reciprocal"},
        )
    finally:
        main.app.dependency_overrides.clear()
        main._change_history[:] = previous_history

    assert response.status_code == 200
    assert "created" not in store.members
    assert store.members["partner"]["SpouseRecordId"] == ""
    assert store.members["partner"]["SpouseName"] == ""


def test_undo_claim_is_inside_lease_and_replay_returns_exact_result(monkeypatch):
    class CountingStore(FakeStore):
        def delete_member(self, record_id):
            self.events.append("delete-member")
            return super().delete_member(record_id)

    class RecordingHistory(InMemoryChangeHistoryStore):
        def claim(self, request_nonce):
            store.events.append("history-claim")
            return super().claim(request_nonce)

        def complete(self, request_nonce, result):
            store.events.append("history-complete")
            return super().complete(request_nonce, result)

    store = CountingStore({"created": _member("created")})
    history = RecordingHistory()
    history.push(
        {
            "timestamp": "2026-08-18T00:00:00+00:00",
            "action": "create",
            "record_id": "created",
            "before": None,
            "after": deepcopy(store.members["created"]),
        }
    )
    manager = RecordingLeaseManager(store.events)
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager, history)

    try:
        first = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-exact-replay"},
        )
        replay = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-exact-replay"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert store.events.count("delete-member") == 1
    assert store.events.index("history-claim") > next(
        index for index, event in enumerate(store.events) if event[0] == "acquire"
    )
    assert store.events.index("history-complete") < next(
        index for index, event in enumerate(store.events) if event[0] == "release"
    )


def test_undo_requires_idempotency_key_before_touching_history(monkeypatch):
    store = FakeStore({"created": _member("created")})
    history = InMemoryChangeHistoryStore()
    history.push(
        {
            "timestamp": "2026-08-18T00:00:00+00:00",
            "action": "create",
            "record_id": "created",
            "before": None,
            "after": deepcopy(store.members["created"]),
        }
    )
    manager = RecordingLeaseManager(store.events)
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager, history)

    try:
        response = client.post("/api/admin/undo")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert history.list()[0]["record_id"] == "created"
    assert "created" in store.members


def test_undo_resumes_claim_left_by_terminated_request(monkeypatch):
    store = FakeStore({"created": _member("created")})
    history = InMemoryChangeHistoryStore()
    history.push(
        {
            "timestamp": "2026-08-18T00:00:00+00:00",
            "action": "create",
            "record_id": "created",
            "before": None,
            "after": deepcopy(store.members["created"]),
        }
    )
    assert history.claim("undo-after-termination").state == "claimed"
    manager = RecordingLeaseManager(store.events)
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager, history)

    try:
        response = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-after-termination"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "undone"
    assert "created" not in store.members


def test_undo_status_exposes_recoverable_active_nonce_to_authenticated_admin():
    history = InMemoryChangeHistoryStore([{
        "timestamp": "2026-08-18T00:00:00+00:00",
        "action": "create",
        "record_id": "created",
        "before": None,
        "after": _member("created"),
    }])
    assert history.claim("recover-this-undo").state == "claimed"
    main.app.dependency_overrides[main.get_current_admin] = lambda: {"sub": "admin"}
    main.app.dependency_overrides[main.get_change_history_store] = lambda: history

    try:
        response = client.get("/api/admin/undo/status")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "active": True,
        "idempotencyKey": "recover-this-undo",
    }


def test_admin_can_inspect_and_abort_an_exact_ambiguous_history_write(monkeypatch):
    store = FakeStore()
    history = InMemoryChangeHistoryStore(
        [
            {
                "timestamp": "2026-08-18T00:00:00+00:00",
                "action": "update",
                "record_id": "older-member",
                "before": _member("older-member"),
                "after": _member("older-member"),
            }
        ]
    )
    history.begin_write(
        "ambiguous-write",
        {"action": "update", "record_id": "member-1"},
    )
    history.mark_write_started("ambiguous-write")
    manager = RecordingLeaseManager(store.events)
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager, history)

    try:
        status = client.get("/api/admin/history/write-status")
        unconfirmed = client.post(
            "/api/admin/history/reconcile",
            json={
                "idempotencyKey": "ambiguous-write",
                "resolution": "abort",
                "confirmation": "not-verified",
            },
        )
        resolved = client.post(
            "/api/admin/history/reconcile",
            json={
                "idempotencyKey": "ambiguous-write",
                "resolution": "abort",
                "confirmation": "I_HAVE_VERIFIED_THE_DATASTORE",
            },
        )
    finally:
        main.app.dependency_overrides.clear()

    assert status.status_code == 200
    assert status.json()["active"] is True
    assert status.json()["phase"] == "started"
    assert status.json()["operation"] == {
        "action": "update",
        "record_id": "member-1",
    }
    assert unconfirmed.status_code == 400
    assert unconfirmed.json()["detail"]["code"] == "DATASTORE_VERIFICATION_REQUIRED"
    assert resolved.status_code == 200
    assert resolved.json() == {"status": "resolved", "resolution": "abort"}
    assert history.write_status() is None
    assert history.list() == []


def test_failed_approval_undo_restores_history_and_same_key_can_retry(monkeypatch):
    store = FakeStore(
        {"approved-member": _member("approved-member")},
        {"pending": {"id": "pending", "Status": "Approved"}},
    )
    store.fail_delete = True
    history = InMemoryChangeHistoryStore()
    history.push(
        {
            "timestamp": "2026-08-18T00:00:00+00:00",
            "action": "approve",
            "record_id": "approved-member",
            "before": {"pending_id": "pending"},
            "after": deepcopy(store.members["approved-member"]),
        }
    )
    manager = RecordingLeaseManager(store.events)
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager, history)

    try:
        failed = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-approval-retry"},
        )
        assert failed.status_code == 502
        assert store.pending["pending"]["Status"] == "Approved"
        assert history.list()[0]["record_id"] == "approved-member"
        store.fail_delete = False
        retried = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-approval-retry"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert store.pending["pending"]["Status"] == "Pending"
    assert retried.status_code == 200
    assert "approved-member" not in store.members


def test_admin_create_preflights_and_records_history_inside_relationship_lease(
    monkeypatch,
):
    class RecordingHistory(InMemoryChangeHistoryStore):
        def begin_write(self, request_nonce, operation):
            store.events.append("history-begin")
            return super().begin_write(request_nonce, operation)

        def mark_write_started(self, request_nonce):
            store.events.append("history-started")
            return super().mark_write_started(request_nonce)

        def bind_write_target(self, request_nonce, operation):
            store.events.append(("history-bound", operation))
            return super().bind_write_target(request_nonce, operation)

        def commit_write(self, request_nonce, entry):
            store.events.append("history-commit")
            return super().commit_write(request_nonce, entry)

    store = FakeStore()
    history = RecordingHistory()
    manager = RecordingLeaseManager(store.events)
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager, history)

    try:
        response = client.post(
            "/api/admin/members",
            json={"FullName": "History Protected Create"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["undoAvailable"] is True
    assert store.events.index("history-begin") < store.events.index("create-member")
    assert store.events.index("history-started") < store.events.index("create-member")
    bound_event = next(
        event
        for event in store.events
        if isinstance(event, tuple) and event[0] == "history-bound"
    )
    assert bound_event[1] == {
        "action": "create",
        "record_id": response.json()["id"],
    }
    assert store.events.index("create-member") < store.events.index(bound_event)
    assert store.events.index(bound_event) < store.events.index("history-commit")
    assert store.events.index("history-commit") < next(
        index for index, event in enumerate(store.events) if event[0] == "release"
    )


def test_committed_admin_create_leaves_durable_barrier_when_history_is_uncertain(
    monkeypatch,
):
    class FailingHistory(InMemoryChangeHistoryStore):
        def commit_write(self, request_nonce, entry):
            raise CoordinationError("COORDINATION_UNAVAILABLE")

    store = FakeStore()
    history = FailingHistory(
        [
            {
                "timestamp": "2026-08-18T00:00:00+00:00",
                "action": "create",
                "record_id": "older-member",
                "before": None,
                "after": _member("older-member"),
            }
        ]
    )
    manager = RecordingLeaseManager(store.events)
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager, history)

    try:
        response = client.post(
            "/api/admin/members",
            json={"FullName": "Committed Without Undo"},
        )
        blocked = client.post(
            "/api/admin/members",
            json={"FullName": "Must Not Be Created"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["undoAvailable"] is False
    assert "saved but cannot be undone" in response.json()["undoWarning"]
    assert len(store.members) == 1
    assert blocked.status_code == 503
    assert blocked.json()["detail"]["code"] == "UNDO_HISTORY_GAP"
    with pytest.raises(CoordinationError) as raised:
        history.claim("undo-from-another-device")
    assert raised.value.code == "UNDO_HISTORY_GAP"


def test_undo_approval_deletes_member_and_returns_submission_to_pending(
    monkeypatch,
):
    store = FakeStore(
        {"approved-member": _member("approved-member")},
        {"pending": {"id": "pending", "Status": "Approved"}},
    )
    manager = RecordingLeaseManager(store.events)
    previous_history = deepcopy(main._change_history)
    main._change_history[:] = [
        {
            "timestamp": "2026-08-18T00:00:00+00:00",
            "action": "approve",
            "record_id": "approved-member",
            "before": {"pending_id": "pending"},
            "after": deepcopy(store.members["approved-member"]),
        }
    ]
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager)

    try:
        response = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-approval"},
        )
    finally:
        main.app.dependency_overrides.clear()
        main._change_history[:] = previous_history

    assert response.status_code == 200
    assert "approved-member" not in store.members
    assert store.pending["pending"]["Status"] == "Pending"


def test_undo_update_uses_coordinated_switch_and_restores_reciprocity(monkeypatch):
    before = _member("member", "old", "Person old")
    store = FakeStore(
        {
            "member": _member("member", "new", "Person new"),
            "old": _member("old"),
            "new": _member("new", "member", "Person member"),
        }
    )
    manager = RecordingLeaseManager(store.events)
    previous_history = deepcopy(main._change_history)
    main._change_history[:] = [
        {
            "timestamp": "2026-08-18T00:00:00+00:00",
            "action": "update",
            "record_id": "member",
            "before": before,
            "after": deepcopy(store.members["member"]),
        }
    ]
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager)

    try:
        response = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-update-reciprocal"},
        )
    finally:
        main.app.dependency_overrides.clear()
        main._change_history[:] = previous_history

    assert response.status_code == 200
    assert store.members["member"]["SpouseRecordId"] == "old"
    assert store.members["old"]["SpouseRecordId"] == "member"
    assert store.members["old"]["SpouseName"] == "Person member"
    assert store.members["new"]["SpouseRecordId"] == ""
    assert store.members["new"]["SpouseName"] == ""


def test_undo_delete_uses_coordinated_create_and_relinks_reciprocal_spouse(
    monkeypatch,
):
    deleted_snapshot = _member("deleted", "partner", "Person partner")
    store = FakeStore({"partner": _member("partner")})
    manager = RecordingLeaseManager(store.events)
    previous_history = deepcopy(main._change_history)
    main._change_history[:] = [
        {
            "timestamp": "2026-08-18T00:00:00+00:00",
            "action": "delete",
            "record_id": "deleted",
            "before": deleted_snapshot,
            "after": None,
        }
    ]
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager)

    try:
        response = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-delete-reciprocal"},
        )
    finally:
        main.app.dependency_overrides.clear()
        main._change_history[:] = previous_history

    assert response.status_code == 200
    new_id = response.json()["new_id"]
    assert store.members[new_id]["SpouseRecordId"] == "partner"
    assert store.members[new_id]["SpouseName"] == "Person partner"
    assert store.members["partner"]["SpouseRecordId"] == new_id
    assert store.members["partner"]["SpouseName"] == "Person deleted"


def test_undo_delete_never_adopts_preexisting_identical_member(monkeypatch):
    deleted_snapshot = _member("deleted")
    preexisting = {**_member("unrelated"), "FullName": "Person deleted"}
    store = FakeStore({"unrelated": preexisting})
    history = InMemoryChangeHistoryStore()
    history.push(
        {
            "timestamp": "2026-08-18T00:00:00+00:00",
            "action": "delete",
            "record_id": "deleted",
            "before": deleted_snapshot,
            "after": None,
        }
    )
    manager = RecordingLeaseManager(store.events)
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager, history)

    try:
        response = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-delete-identical"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["new_id"] != "unrelated"
    assert store.members["unrelated"] == preexisting
    assert len(store.created_ids) == 1


def test_interrupted_delete_undo_recovers_only_member_created_after_baseline(
    monkeypatch,
):
    deleted_snapshot = _member("deleted")
    preexisting = {**_member("unrelated"), "FullName": "Person deleted"}
    recovered = {**_member("restored-after-crash"), "FullName": "Person deleted"}
    store = FakeStore(
        {
            "unrelated": preexisting,
            "restored-after-crash": recovered,
        }
    )
    history = InMemoryChangeHistoryStore()
    history.push(
        {
            "timestamp": "2026-08-18T00:00:00+00:00",
            "action": "delete",
            "record_id": "deleted",
            "before": deleted_snapshot,
            "after": None,
        }
    )
    assert history.claim("undo-delete-resume").state == "claimed"
    history.mark_applying(
        "undo-delete-resume",
        {"action": "delete", "baseline_member_ids": ["unrelated"]},
    )
    manager = RecordingLeaseManager(store.events)
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager, history)

    try:
        response = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-delete-resume"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["new_id"] == "restored-after-crash"
    assert store.members["unrelated"] == preexisting
    assert store.created_ids == []


def test_delete_undo_context_failure_releases_claim_before_any_mutation(monkeypatch):
    class FailingContextHistory(InMemoryChangeHistoryStore):
        def mark_applying(self, request_nonce, context):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")

    store = FakeStore()
    history = FailingContextHistory()
    history.push(
        {
            "timestamp": "2026-08-18T00:00:00+00:00",
            "action": "delete",
            "record_id": "deleted",
            "before": _member("deleted"),
            "after": None,
        }
    )
    manager = RecordingLeaseManager(store.events)
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager, history)

    try:
        response = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-delete-context-failure"},
        )
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 503
    history.ensure_ready()
    assert history.list()[0]["record_id"] == "deleted"


def test_delete_then_undo_restores_child_parent_links_to_recreated_member(
    monkeypatch,
):
    class AirtableLikeDeleteStore(FakeStore):
        def delete_member(self, record_id):
            for member in self.members.values():
                for field in ("FatherRecordId", "MotherRecordId"):
                    if member.get(field) == record_id:
                        member[field] = ""
            return super().delete_member(record_id)

    store = AirtableLikeDeleteStore(
        {
            "deleted": _member("deleted"),
            "child-father": {
                **_member("child-father"),
                "FatherRecordId": "deleted",
            },
            "child-mother": {
                **_member("child-mother"),
                "MotherRecordId": "deleted",
            },
        }
    )
    manager = RecordingLeaseManager(store.events)
    previous_history = deepcopy(main._change_history)
    main._change_history.clear()
    monkeypatch.setattr(main, "db", store)
    _install_admin_write_dependencies(manager)

    try:
        deleted = client.delete("/api/admin/members/deleted")
        restored = client.post(
            "/api/admin/undo",
            headers={"x-idempotency-key": "undo-delete-parent-links"},
        )
    finally:
        main.app.dependency_overrides.clear()
        main._change_history[:] = previous_history

    assert deleted.status_code == 200
    assert restored.status_code == 200
    assert store.events[0][0] == "acquire"
    assert store.events.index("read-members") > 0
    new_id = restored.json()["new_id"]
    assert store.members["child-father"]["FatherRecordId"] == new_id
    assert store.members["child-mother"]["MotherRecordId"] == new_id
