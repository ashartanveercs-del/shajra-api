"""Invariant-aware coordination for legacy reciprocal spouse writes."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Callable, NoReturn, TypeVar

from change_history import ChangeHistoryStore
from coordination import CoordinationError, Lease, LeaseManager, new_acquisition_id
from public_data import exact_relationship_ids


_RELATIONSHIP_SCOPE = "legacy-relationships"
_RELATIONSHIP_TTL_MS = 300_000
_T = TypeVar("_T")


class RelationshipConflict(ValueError):
    def __init__(self, message: str, *, code: str = "SPOUSE_CONFLICT") -> None:
        super().__init__(message)
        self.code = code


class RelationshipPersistenceError(RuntimeError):
    def __init__(self, *, rollback_incomplete: bool = False) -> None:
        super().__init__("relationship write failed")
        self.rollback_incomplete = rollback_incomplete


@dataclass(frozen=True, slots=True)
class _UpdateEntry:
    record_id: str
    before: dict[str, object]
    intended: dict[str, object]


class _Transaction:
    def __init__(self, store: Any, lease_manager: LeaseManager, lease: Lease) -> None:
        self.store = store
        self.lease_manager = lease_manager
        self.lease = lease
        self.member_updates: list[_UpdateEntry] = []
        self.pending_updates: list[_UpdateEntry] = []

    def assert_owned(self) -> None:
        self.lease_manager.assert_owned(self.lease)

    def read_member(self, record_id: str) -> dict[str, object] | None:
        return self.store.get_member_by_id(record_id)

    def read_pending(self, record_id: str) -> dict[str, object] | None:
        if hasattr(self.store, "get_pending_by_id"):
            return self.store.get_pending_by_id(record_id)
        for pending in self.store.get_all_pending():
            if str(pending.get("id", "")) == record_id:
                return pending
        return None

    def create_member(
        self,
        fields: dict[str, object],
        *,
        known_member_ids: set[str] | None = None,
    ) -> dict[str, object]:
        persisted_fields = _prepare_member_create_fields(self.store, fields)
        if known_member_ids is None:
            known_member_ids = {
                _record_id(member.get("id"))
                for member in self.store.get_all_members()
            }
        self.assert_owned()
        try:
            return self.store.create_member(persisted_fields)
        except Exception as error:
            try:
                candidates = [
                    member
                    for member in self.store.get_all_members()
                    if _record_id(member.get("id")) not in known_member_ids
                    and _fields_match(member, persisted_fields)
                ]
            except Exception as read_error:
                raise RelationshipPersistenceError(
                    rollback_incomplete=True
                ) from read_error
            if len(candidates) == 1:
                return candidates[0]
            raise RelationshipPersistenceError(rollback_incomplete=True) from error

    def update_member(
        self, record_id: str, fields: Mapping[str, object]
    ) -> dict[str, object]:
        before = self.read_member(record_id)
        if before is None:
            raise RelationshipConflict("Member not found", code="MEMBER_NOT_FOUND")
        persisted_fields = _prepare_member_update_fields(self.store, fields)
        if not persisted_fields:
            return before
        self.member_updates.append(
            _UpdateEntry(record_id, before, persisted_fields)
        )
        self.assert_owned()
        try:
            return self.store.update_member(record_id, persisted_fields)
        except Exception as error:
            try:
                current = self.read_member(record_id)
            except Exception:  # noqa: BLE001 - failed readback leaves commit state unknown.
                raise error from None
            if current is not None and _fields_match(current, persisted_fields):
                return current
            raise

    def update_pending(
        self, record_id: str, fields: Mapping[str, object]
    ) -> dict[str, object]:
        before = self.read_pending(record_id)
        if before is None:
            raise RelationshipPersistenceError(rollback_incomplete=True)
        self.pending_updates.append(_UpdateEntry(record_id, before, dict(fields)))
        self.assert_owned()
        try:
            return self.store.update_pending(record_id, dict(fields))
        except Exception as error:
            try:
                current = self.read_pending(record_id)
            except Exception:  # noqa: BLE001 - failed readback leaves commit state unknown.
                raise error from None
            if current is not None and _fields_match(current, fields):
                return current
            raise

    def compensate_members(self) -> bool:
        return self._compensate(
            self.member_updates,
            self.store.update_member,
            self.read_member,
        )

    def compensate_pending(self) -> bool:
        return self._compensate(
            self.pending_updates,
            self.store.update_pending,
            self.read_pending,
        )

    def _compensate(
        self,
        entries: list[_UpdateEntry],
        update: Callable[[str, dict[str, object]], object],
        read: Callable[[str], dict[str, object] | None],
    ) -> bool:
        complete = True
        for entry in reversed(entries):
            restore = {key: entry.before.get(key, "") for key in entry.intended}
            try:
                self.assert_owned()
                try:
                    update(entry.record_id, restore)
                except Exception:  # noqa: BLE001 - verify restoration by readback below.
                    pass
                current = read(entry.record_id)
            except Exception:  # noqa: BLE001 - any unverifiable rollback is incomplete.
                complete = False
                continue
            if current is None or not _fields_match(current, restore):
                complete = False
        return complete


def create_member(
    store: Any,
    fields: dict[str, object],
    *,
    lease_manager: LeaseManager,
    history_preflight: Callable[[], None] | None = None,
    history_started: Callable[[], None] | None = None,
    history_recorder: Callable[[dict[str, object]], None] | None = None,
    history_abort: Callable[[], None] | None = None,
) -> dict[str, object]:
    def create(transaction: _Transaction) -> dict[str, object]:
        _begin_history_guard(
            transaction,
            history_preflight,
            history_started,
            history_recorder,
            history_abort,
        )
        try:
            created = _create_member(transaction, fields)
        except Exception as error:
            _abort_history_guard_after_failure(error, history_abort)
            raise
        if history_recorder is not None:
            history_recorder(created)
        return created

    return _coordinated(
        store,
        lease_manager,
        create,
    )


def approve_member(
    store: Any,
    pending_id: str,
    *,
    lease_manager: LeaseManager,
    history_preflight: Callable[[], None] | None = None,
    history_started: Callable[[], None] | None = None,
    history_recorder: Callable[[dict[str, object]], None] | None = None,
    history_abort: Callable[[], None] | None = None,
) -> dict[str, object]:
    def approve(transaction: _Transaction) -> dict[str, object]:
        _begin_history_guard(
            transaction,
            history_preflight,
            history_started,
            history_recorder,
            history_abort,
        )
        try:
            pending = _pending_for_terminal_action(transaction, pending_id)
            members = store.get_all_members()
            relationship_ids = exact_relationship_ids(
                members,
                father_name=pending.get("RawFatherName"),
                mother_name=pending.get("RawMotherName"),
                spouse_name=pending.get("RawSpouseName"),
                subject_gender=pending.get("RawGender")
                or pending.get("CleanGender", ""),
            )
            current_fields = {**_approved_member_fields(pending), **relationship_ids}
            known_member_ids = {
                _record_id(member.get("id")) for member in members
            }
            created = _create_member(
                transaction,
                current_fields,
                known_member_ids=known_member_ids,
            )
            try:
                transaction.update_pending(pending_id, {"Status": "Approved"})
            except RelationshipPersistenceError:
                complete = _rollback_approved_member(transaction, created)
                raise RelationshipPersistenceError(
                    rollback_incomplete=not complete
                ) from None
            except Exception as error:
                complete = _rollback_approved_member(transaction, created)
                if isinstance(error, CoordinationError) and complete:
                    raise
                raise RelationshipPersistenceError(
                    rollback_incomplete=not complete
                ) from error
        except Exception as error:
            _abort_history_guard_after_failure(error, history_abort)
            raise
        if history_recorder is not None:
            history_recorder(created)
        return created

    return _coordinated(store, lease_manager, approve)


def reject_pending(
    store: Any,
    pending_id: str,
    *,
    lease_manager: LeaseManager,
) -> dict[str, object]:
    def reject(transaction: _Transaction) -> dict[str, object]:
        _pending_for_terminal_action(transaction, pending_id)
        try:
            return transaction.update_pending(pending_id, {"Status": "Rejected"})
        except Exception as error:
            complete = transaction.compensate_pending()
            if isinstance(error, CoordinationError) and complete:
                raise
            raise RelationshipPersistenceError(
                rollback_incomplete=not complete
            ) from error

    return _coordinated(store, lease_manager, reject)


def update_member(
    store: Any,
    member_id: str,
    fields: dict[str, object],
    *,
    lease_manager: LeaseManager,
    history_preflight: Callable[[], None] | None = None,
    history_started: Callable[[], None] | None = None,
    history_recorder: Callable[
        [dict[str, object], dict[str, object]], None
    ]
    | None = None,
    history_abort: Callable[[], None] | None = None,
) -> dict[str, object]:
    def update(transaction: _Transaction) -> dict[str, object]:
        _begin_history_guard(
            transaction,
            history_preflight,
            history_started,
            history_recorder,
            history_abort,
        )
        try:
            before = transaction.read_member(member_id)
            if before is None:
                raise RelationshipConflict("Member not found", code="MEMBER_NOT_FOUND")
            updated = _update_member(transaction, member_id, fields, before=before)
        except Exception as error:
            _abort_history_guard_after_failure(error, history_abort)
            raise
        if history_recorder is not None:
            history_recorder(before, updated)
        return updated

    return _coordinated(store, lease_manager, update)


def delete_member(
    store: Any,
    member_id: str,
    *,
    lease_manager: LeaseManager,
) -> bool:
    return _coordinated(
        store,
        lease_manager,
        lambda transaction: _delete_member(transaction, member_id),
    )


def delete_member_with_snapshot(
    store: Any,
    member_id: str,
    *,
    lease_manager: LeaseManager,
    history_preflight: Callable[[], None] | None = None,
    history_started: Callable[[], None] | None = None,
    history_recorder: Callable[[dict[str, object]], None] | None = None,
    history_abort: Callable[[], None] | None = None,
) -> dict[str, object]:
    def delete(transaction: _Transaction) -> dict[str, object]:
        _begin_history_guard(
            transaction,
            history_preflight,
            history_started,
            history_recorder,
            history_abort,
        )
        try:
            snapshot = _delete_member_with_snapshot(transaction, member_id)
        except Exception as error:
            _abort_history_guard_after_failure(error, history_abort)
            raise
        if history_recorder is not None:
            history_recorder(snapshot)
        return snapshot

    return _coordinated(store, lease_manager, delete)


def restore_deleted_member(
    store: Any,
    fields: dict[str, object],
    child_references: list[dict[str, object]],
    *,
    lease_manager: LeaseManager,
) -> dict[str, object]:
    return _coordinated(
        store,
        lease_manager,
        lambda transaction: _restore_deleted_member(
            transaction, fields, child_references
        ),
    )


def reconcile_history_write(
    store: Any,
    history_store: ChangeHistoryStore,
    request_nonce: str,
    entry: dict[str, object] | None,
    *,
    lease_manager: LeaseManager,
) -> None:
    def reconcile(transaction: _Transaction) -> None:
        transaction.assert_owned()
        history_store.resolve_write(request_nonce, entry)

    _coordinated(store, lease_manager, reconcile)


def undo_approval(
    store: Any,
    member_id: str,
    pending_id: str,
    *,
    lease_manager: LeaseManager,
) -> bool:
    return _coordinated(
        store,
        lease_manager,
        lambda transaction: _undo_approval(
            transaction, member_id, pending_id
        ),
    )


def undo_last_change(
    store: Any,
    history_store: ChangeHistoryStore,
    request_nonce: str,
    *,
    lease_manager: LeaseManager,
) -> dict[str, object]:
    """Claim, apply, and finalize one undo under the relationship lease."""

    def undo(transaction: _Transaction) -> dict[str, object]:
        claim = history_store.claim(request_nonce)
        if claim.state == "completed":
            if claim.result is None:
                raise CoordinationError("COORDINATION_STATE_CORRUPT")
            return claim.result
        if claim.state == "empty":
            raise RelationshipConflict("No changes to undo", code="UNDO_EMPTY")
        if claim.state == "busy":
            raise RelationshipConflict(
                "Another undo is awaiting completion", code="UNDO_BUSY"
            )
        if claim.entry is None:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")

        mutation_started = False
        try:
            delete_baseline_ids: set[str] | None = None
            if claim.entry.get("action") == "delete":
                context = claim.context
                if context is None:
                    baseline_ids = sorted(
                        {
                            record_id
                            for member in transaction.store.get_all_members()
                            if (record_id := _record_id(member.get("id")))
                        }
                    )
                    context = history_store.mark_applying(
                        request_nonce,
                        {
                            "action": "delete",
                            "baseline_member_ids": baseline_ids,
                        },
                    )
                delete_baseline_ids = _delete_recovery_baseline(context)
            elif claim.context is not None:
                raise CoordinationError("COORDINATION_STATE_CORRUPT")

            mutation_started = True
            result = _undo_entry(
                transaction,
                claim.entry,
                delete_baseline_ids=delete_baseline_ids,
            )
        except RelationshipConflict:
            _abort_undo_history(history_store, request_nonce)
            raise
        except RelationshipPersistenceError as error:
            if not error.rollback_incomplete:
                _abort_undo_history(history_store, request_nonce)
            raise
        except CoordinationError:
            if not mutation_started:
                _abort_undo_history(history_store, request_nonce)
            raise
        except Exception:
            if not mutation_started:
                _abort_undo_history(history_store, request_nonce)
            raise

        return history_store.complete(request_nonce, result)

    return _coordinated(store, lease_manager, undo)


def _abort_undo_history(
    history_store: ChangeHistoryStore, request_nonce: str
) -> None:
    try:
        history_store.abort(request_nonce)
    except CoordinationError:
        raise CoordinationError("UNDO_HISTORY_RESTORE_FAILED") from None


def _delete_recovery_baseline(context: dict[str, object]) -> set[str]:
    if set(context) != {"action", "baseline_member_ids"}:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    raw_ids = context.get("baseline_member_ids")
    if context.get("action") != "delete" or not isinstance(raw_ids, list):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    if not all(isinstance(record_id, str) and record_id for record_id in raw_ids):
        raise CoordinationError("COORDINATION_STATE_CORRUPT")
    return set(raw_ids)


def _coordinated(
    store: Any,
    lease_manager: LeaseManager,
    operation: Callable[[_Transaction], _T],
) -> _T:
    acquisition_id = new_acquisition_id()
    lease = lease_manager.acquire(
        _RELATIONSHIP_SCOPE,
        acquisition_id,
        ttl_ms=_RELATIONSHIP_TTL_MS,
    )
    try:
        return operation(_Transaction(store, lease_manager, lease))
    finally:
        release_nonce = new_acquisition_id()
        try:
            lease_manager.release(lease, release_nonce)
        except Exception:  # noqa: BLE001 - a completed transaction must remain successful.
            pass


def _begin_history_guard(
    transaction: _Transaction,
    history_preflight: Callable[[], None] | None,
    history_started: Callable[[], None] | None,
    history_recorder: Callable[..., None] | None,
    history_abort: Callable[[], None] | None,
) -> None:
    callbacks = (
        history_preflight,
        history_started,
        history_recorder,
        history_abort,
    )
    if all(callback is None for callback in callbacks):
        return
    if any(callback is None for callback in callbacks):
        raise ValueError("history callbacks must be configured together")
    assert history_preflight is not None
    assert history_started is not None
    history_preflight()
    transaction.assert_owned()
    history_started()


def _abort_history_guard_after_failure(
    error: Exception,
    history_abort: Callable[[], None] | None,
) -> None:
    if history_abort is None:
        return
    if (
        isinstance(error, RelationshipPersistenceError)
        and error.rollback_incomplete
    ):
        return
    try:
        history_abort()
    except CoordinationError:
        raise CoordinationError("UNDO_HISTORY_RESTORE_FAILED") from None


def _create_member(
    transaction: _Transaction,
    fields: dict[str, object],
    *,
    known_member_ids: set[str] | None = None,
) -> dict[str, object]:
    _validate_parent_relationships(transaction.store, None, fields)
    desired_spouse_id = _record_id(fields.get("SpouseRecordId"))
    desired_partner = _validate_desired_partner(
        transaction.store, None, desired_spouse_id
    )
    write_fields = dict(fields)
    if desired_partner:
        write_fields["SpouseRecordId"] = desired_spouse_id
        write_fields["SpouseName"] = _full_name(desired_partner)
    elif "SpouseRecordId" in write_fields:
        write_fields["SpouseRecordId"] = ""
        if "SpouseName" not in fields:
            write_fields["SpouseName"] = ""

    created = transaction.create_member(
        write_fields, known_member_ids=known_member_ids
    )
    member_id = _record_id(created.get("id"))
    if not member_id:
        raise RelationshipPersistenceError(rollback_incomplete=True)
    if not desired_partner:
        return created

    try:
        transaction.update_member(
            desired_spouse_id,
            {
                "SpouseRecordId": member_id,
                "SpouseName": _full_name(created),
            },
        )
    except Exception as error:
        complete = _rollback_created_member(transaction, created)
        if isinstance(error, CoordinationError) and complete:
            raise
        raise RelationshipPersistenceError(
            rollback_incomplete=not complete
        ) from error
    return created


def _restore_deleted_member(
    transaction: _Transaction,
    fields: dict[str, object],
    child_references: list[dict[str, object]],
    *,
    recovery_baseline_ids: set[str] | None = None,
) -> dict[str, object]:
    persisted_fields = _prepare_member_create_fields(transaction.store, fields)
    matching = (
        [
            member
            for member in transaction.store.get_all_members()
            if _record_id(member.get("id")) not in recovery_baseline_ids
            and _fields_match(member, persisted_fields)
        ]
        if recovery_baseline_ids is not None
        else []
    )
    if len(matching) > 1:
        raise RelationshipConflict(
            "Restored member recovery is ambiguous",
            code="UNDO_RECOVERY_AMBIGUOUS",
        )
    if matching:
        existing_id = _record_id(matching[0].get("id"))
        if not existing_id:
            raise RelationshipPersistenceError(rollback_incomplete=True)
        created = _update_member(
            transaction,
            existing_id,
            fields,
            before=matching[0],
        )
    else:
        created = _create_member(transaction, fields)
    member_id = _record_id(created.get("id"))
    try:
        for reference in child_references:
            child_id = _record_id(reference.get("record_id"))
            raw_fields = reference.get("fields")
            if not child_id or not isinstance(raw_fields, Mapping):
                raise RelationshipPersistenceError(rollback_incomplete=True)
            parent_fields = {
                field: member_id
                for field in ("FatherRecordId", "MotherRecordId")
                if field in raw_fields
            }
            if parent_fields:
                transaction.update_member(child_id, parent_fields)
    except Exception as error:
        complete = _rollback_created_member(transaction, created)
        if isinstance(error, CoordinationError) and complete:
            raise
        raise RelationshipPersistenceError(
            rollback_incomplete=not complete
        ) from error
    return created


def _update_member(
    transaction: _Transaction,
    member_id: str,
    fields: dict[str, object],
    *,
    before: dict[str, object] | None = None,
) -> dict[str, object]:
    before = before if before is not None else transaction.read_member(member_id)
    if not before:
        raise RelationshipConflict("Member not found", code="MEMBER_NOT_FOUND")

    _validate_parent_relationships(
        transaction.store,
        member_id,
        fields,
        before=before,
    )
    _validate_reverse_parent_gender(transaction.store, member_id, fields)

    old_spouse_id = _record_id(before.get("SpouseRecordId"))
    relationship_touched = (
        "SpouseRecordId" in fields
        or "SpouseName" in fields
        or ("FullName" in fields and bool(old_spouse_id))
    )
    if not relationship_touched:
        try:
            return transaction.update_member(member_id, fields)
        except Exception as error:  # noqa: BLE001 - compensate every datastore failure.
            _raise_after_compensation(transaction, error)

    desired_spouse_id = (
        _record_id(fields.get("SpouseRecordId"))
        if "SpouseRecordId" in fields
        else old_spouse_id
    )
    if desired_spouse_id == member_id:
        raise RelationshipConflict(
            "A member cannot be their own spouse", code="SELF_SPOUSE"
        )
    desired_partner = _validate_desired_partner(
        transaction.store, member_id, desired_spouse_id
    )
    old_partner = (
        transaction.read_member(old_spouse_id)
        if old_spouse_id and old_spouse_id != desired_spouse_id
        else None
    )
    write_fields = dict(fields)
    member_name = str(fields.get("FullName") or before.get("FullName") or "").strip()

    if desired_partner:
        write_fields["SpouseRecordId"] = desired_spouse_id
        write_fields["SpouseName"] = _full_name(desired_partner)
    elif "SpouseRecordId" in fields:
        write_fields["SpouseRecordId"] = ""
        write_fields["SpouseName"] = (
            str(fields.get("SpouseName") or "").strip()
            if "SpouseName" in fields
            else ""
        )

    try:
        if old_partner and _record_id(old_partner.get("SpouseRecordId")) == member_id:
            transaction.update_member(
                old_spouse_id,
                {"SpouseRecordId": "", "SpouseName": ""},
            )

        if desired_partner:
            intended_partner = {
                "SpouseRecordId": member_id,
                "SpouseName": member_name,
            }
            if not _fields_match(desired_partner, intended_partner):
                transaction.update_member(desired_spouse_id, intended_partner)

        return transaction.update_member(member_id, write_fields)
    except Exception as error:  # noqa: BLE001 - compensate every datastore failure.
        _raise_after_compensation(transaction, error)


def _delete_member(transaction: _Transaction, member_id: str) -> bool:
    before = transaction.read_member(member_id)
    if not before:
        raise RelationshipConflict("Member not found", code="MEMBER_NOT_FOUND")
    spouse_id = _record_id(before.get("SpouseRecordId"))
    partner = transaction.read_member(spouse_id) if spouse_id else None

    try:
        if partner and _record_id(partner.get("SpouseRecordId")) == member_id:
            transaction.update_member(
                spouse_id,
                {"SpouseRecordId": "", "SpouseName": ""},
            )
        transaction.assert_owned()
    except Exception as error:  # noqa: BLE001 - compensate every datastore failure.
        _raise_after_compensation(transaction, error)

    try:
        transaction.store.delete_member(member_id)
        return True
    except Exception as error:
        try:
            current = transaction.read_member(member_id)
        except Exception as read_error:
            raise RelationshipPersistenceError(rollback_incomplete=True) from read_error
        if current is None:
            return True
        complete = transaction.compensate_members()
        if isinstance(error, CoordinationError) and complete:
            raise
        raise RelationshipPersistenceError(
            rollback_incomplete=not complete
        ) from error


def _delete_member_with_snapshot(
    transaction: _Transaction, member_id: str
) -> dict[str, object]:
    transaction.assert_owned()
    before = transaction.read_member(member_id)
    if not before:
        raise RelationshipConflict("Member not found", code="MEMBER_NOT_FOUND")

    child_references = []
    for candidate in transaction.store.get_all_members():
        fields = {
            field: member_id
            for field in ("FatherRecordId", "MotherRecordId")
            if _record_id(candidate.get(field)) == member_id
        }
        if fields:
            child_references.append(
                {
                    "record_id": _record_id(candidate.get("id")),
                    "fields": fields,
                }
            )

    _delete_member(transaction, member_id)
    return {"member": before, "child_references": child_references}


def _undo_approval(
    transaction: _Transaction, member_id: str, pending_id: str
) -> bool:
    pending = transaction.read_pending(pending_id)
    if pending is None:
        raise RelationshipConflict(
            "Pending submission not found", code="PENDING_NOT_FOUND"
        )
    pending_status = str(pending.get("Status") or "").strip().casefold()
    if pending_status not in {"approved", "pending"}:
        raise RelationshipConflict(
            "Pending submission cannot be restored",
            code="PENDING_STATUS_CONFLICT",
        )
    member = transaction.read_member(member_id)
    if pending_status == "pending" and member is None:
        return True

    try:
        if pending_status == "approved":
            transaction.update_pending(pending_id, {"Status": "Pending"})
        if member is not None:
            _delete_member(transaction, member_id)
    except Exception as error:
        pending_complete = transaction.compensate_pending()
        if isinstance(error, CoordinationError) and pending_complete:
            raise
        if isinstance(error, RelationshipPersistenceError):
            raise RelationshipPersistenceError(
                rollback_incomplete=(
                    error.rollback_incomplete or not pending_complete
                )
            ) from error
        if isinstance(error, RelationshipConflict) and pending_complete:
            raise
        raise RelationshipPersistenceError(
            rollback_incomplete=not pending_complete
        ) from error
    return True


def _undo_entry(
    transaction: _Transaction,
    entry: dict[str, object],
    *,
    delete_baseline_ids: set[str] | None,
) -> dict[str, object]:
    action_value = entry.get("action")
    record_id_value = entry.get("record_id")
    before_value = entry.get("before")
    after_value = entry.get("after")
    action = action_value if isinstance(action_value, str) else ""
    record_id = record_id_value if isinstance(record_id_value, str) else ""
    before = before_value if isinstance(before_value, dict) else None
    after = after_value if isinstance(after_value, dict) else None
    if not action or not record_id:
        raise CoordinationError("COORDINATION_STATE_CORRUPT")

    if action == "create":
        if transaction.read_member(record_id) is not None:
            _delete_member(transaction, record_id)
        elif after is not None:
            spouse_id = _record_id(after.get("SpouseRecordId"))
            partner = transaction.read_member(spouse_id) if spouse_id else None
            if partner and _record_id(partner.get("SpouseRecordId")) == record_id:
                transaction.update_member(
                    spouse_id,
                    {"SpouseRecordId": "", "SpouseName": ""},
                )
        return {
            "status": "undone",
            "action": "Deleted created member",
            "record_id": record_id,
        }

    if action == "approve":
        pending_id = str((before or {}).get("pending_id") or "")
        if not pending_id:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        _undo_approval(transaction, record_id, pending_id)
        return {
            "status": "undone",
            "action": "Removed approved member and restored the pending submission",
            "record_id": record_id,
        }

    if action == "update":
        if before is None:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        restore_fields = {key: value for key, value in before.items() if key != "id"}
        _update_member(transaction, record_id, restore_fields)
        return {
            "status": "undone",
            "action": f"Restored {before.get('FullName', record_id)} to previous state",
        }

    if action == "delete":
        if before is None or delete_baseline_ids is None:
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        member_value = before.get("member", before)
        references_value = before.get("child_references", [])
        if not isinstance(member_value, dict) or not isinstance(references_value, list):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        if not all(isinstance(reference, dict) for reference in references_value):
            raise CoordinationError("COORDINATION_STATE_CORRUPT")
        restore_fields = {
            key: value for key, value in member_value.items() if key != "id"
        }
        restored = _restore_deleted_member(
            transaction,
            restore_fields,
            references_value,
            recovery_baseline_ids=delete_baseline_ids,
        )
        return {
            "status": "undone",
            "action": f"Restored deleted member {member_value.get('FullName', '')}",
            "new_id": restored["id"],
        }

    raise CoordinationError("COORDINATION_STATE_CORRUPT")


def _validate_desired_partner(
    store: Any,
    member_id: str | None,
    desired_spouse_id: str,
) -> dict[str, object] | None:
    if not desired_spouse_id:
        return None
    if member_id and desired_spouse_id == member_id:
        raise RelationshipConflict(
            "A member cannot be their own spouse", code="SELF_SPOUSE"
        )
    partner = store.get_member_by_id(desired_spouse_id)
    if not partner:
        raise RelationshipConflict("Spouse record not found", code="SPOUSE_NOT_FOUND")
    partner_spouse_id = _record_id(partner.get("SpouseRecordId"))
    if partner_spouse_id and partner_spouse_id != member_id:
        raise RelationshipConflict("Spouse is already linked to another member")
    return partner


def _validate_parent_relationships(
    store: Any,
    member_id: str | None,
    fields: Mapping[str, object],
    *,
    before: Mapping[str, object] | None = None,
) -> None:
    parent_specs = {
        "FatherRecordId": "Male",
        "MotherRecordId": "Female",
    }
    effective_ids = {
        field: _record_id(
            fields[field]
            if field in fields
            else (before or {}).get(field)
        )
        for field in parent_specs
    }
    father_id = effective_ids["FatherRecordId"]
    mother_id = effective_ids["MotherRecordId"]
    if father_id and father_id == mother_id:
        raise RelationshipConflict(
            "Father and mother must be different members",
            code="PARENTS_NOT_DISTINCT",
        )
    if not father_id and not mother_id:
        return

    members = store.get_all_members()
    lookup = {
        record_id: member
        for member in members
        if (record_id := _record_id(member.get("id")))
    }
    for field, expected_gender in parent_specs.items():
        parent_id = effective_ids[field]
        if not parent_id:
            continue
        if member_id and parent_id == member_id:
            raise RelationshipConflict(
                "A member cannot be their own parent",
                code="SELF_PARENT",
            )
        parent = lookup.get(parent_id)
        if parent is None:
            raise RelationshipConflict(
                "Parent record not found",
                code="PARENT_NOT_FOUND",
            )
        gender = str(parent.get("Gender") or "").strip()
        if gender and gender.casefold() != expected_gender.casefold():
            raise RelationshipConflict(
                f"{expected_gender} parent field references a {gender} member",
                code="PARENT_GENDER_MISMATCH",
            )
        if member_id and _ancestor_chain_contains(lookup, parent_id, member_id):
            raise RelationshipConflict(
                "Parent relationship would create an ancestry cycle",
                code="ANCESTRY_CYCLE",
            )


def _validate_reverse_parent_gender(
    store: Any,
    member_id: str,
    fields: Mapping[str, object],
) -> None:
    if "Gender" not in fields:
        return
    gender = str(fields.get("Gender") or "").strip()
    if not gender:
        return
    normalized_gender = gender.casefold()
    parent_roles = {
        "FatherRecordId": "Male",
        "MotherRecordId": "Female",
    }
    for candidate in store.get_all_members():
        for parent_field, expected_gender in parent_roles.items():
            if _record_id(candidate.get(parent_field)) != member_id:
                continue
            if normalized_gender == expected_gender.casefold():
                continue
            raise RelationshipConflict(
                f"A member referenced as {expected_gender.lower()} cannot be changed "
                f"to {gender}",
                code="PARENT_ROLE_GENDER_CONFLICT",
            )


def _ancestor_chain_contains(
    members: Mapping[str, Mapping[str, object]],
    start_id: str,
    target_id: str,
) -> bool:
    pending = [start_id]
    visited: set[str] = set()
    while pending:
        current_id = pending.pop()
        if current_id == target_id:
            return True
        if current_id in visited:
            continue
        visited.add(current_id)
        current = members.get(current_id)
        if current is None:
            continue
        pending.extend(
            parent_id
            for field in ("FatherRecordId", "MotherRecordId")
            if (parent_id := _record_id(current.get(field)))
        )
    return False


def _pending_for_terminal_action(
    transaction: _Transaction, pending_id: str
) -> dict[str, object]:
    pending = transaction.read_pending(pending_id)
    if pending is None:
        raise RelationshipConflict(
            "Pending submission not found", code="PENDING_NOT_FOUND"
        )
    if str(pending.get("Status") or "").strip().casefold() != "pending":
        raise RelationshipConflict(
            "Pending submission has already been resolved",
            code="PENDING_ALREADY_RESOLVED",
        )
    return pending


def _approved_member_fields(pending: dict[str, object]) -> dict[str, object]:
    fields = {
        "FullName": pending.get("CleanFullName", pending.get("RawFullName", "")),
        "FatherName": pending.get("CleanFatherName", ""),
        "MotherName": pending.get("CleanMotherName", ""),
        "SpouseName": pending.get("CleanSpouseName", ""),
        "DateOfBirth": pending.get("CleanDOB", ""),
        "DateOfDeath": pending.get("CleanDOD", ""),
        "CurrentCity": pending.get("CleanCity", ""),
        "CurrentCountry": pending.get("CleanCountry", ""),
        "BurialLocation": pending.get("CleanBurialLocation", ""),
        "Gender": pending.get("CleanGender", ""),
        "Email": pending.get("CleanEmail", pending.get("RawEmail", "")),
        "PhoneNumber": pending.get(
            "CleanPhoneNumber", pending.get("RawPhoneNumber", "")
        ),
        "ProfileImageUrl": pending.get(
            "CleanProfileImage", pending.get("RawProfileImage", "")
        ),
        "Biography": pending.get("RawBiography", ""),
    }
    clean_dod = str(pending.get("CleanDOD", "") or "").strip().casefold()
    fields["IsAlive"] = clean_dod in {"", "n/a", "unknown", "none", "na", "-"}
    return {key: value for key, value in fields.items() if value is not None}


def _rollback_approved_member(
    transaction: _Transaction, created: dict[str, object]
) -> bool:
    pending_complete = transaction.compensate_pending()
    created_complete = _rollback_created_member(transaction, created)
    return pending_complete and created_complete


def _rollback_created_member(
    transaction: _Transaction, created: dict[str, object]
) -> bool:
    members_complete = transaction.compensate_members()
    member_id = _record_id(created.get("id"))
    if not member_id:
        return False
    try:
        transaction.assert_owned()
        transaction.store.delete_member(member_id)
        deleted = True
    except Exception:  # noqa: BLE001 - delete outcome is resolved by readback.
        try:
            deleted = transaction.read_member(member_id) is None
        except Exception:  # noqa: BLE001 - an unreadable delete outcome is incomplete.
            deleted = False
    return members_complete and deleted


def _raise_after_compensation(
    transaction: _Transaction, error: Exception
) -> NoReturn:
    complete = transaction.compensate_members()
    if isinstance(error, CoordinationError) and complete:
        raise error
    raise RelationshipPersistenceError(rollback_incomplete=not complete) from error


def _fields_match(
    record: dict[str, object], expected: Mapping[str, object]
) -> bool:
    for key, value in expected.items():
        current = record.get(key, "")
        if key.endswith("RecordId"):
            if _record_id(current) != _record_id(value):
                return False
        elif current != value:
            return False
    return True


def _full_name(member: dict[str, object]) -> str:
    return str(member.get("FullName") or "").strip()


def _record_id(value: object) -> str:
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    return str(value).strip() if value else ""


def _prepare_member_create_fields(
    store: Any, fields: Mapping[str, object]
) -> dict[str, object]:
    prepare = getattr(store, "prepare_member_create_fields", None)
    if callable(prepare):
        return dict(prepare(dict(fields)))
    return dict(fields)


def _prepare_member_update_fields(
    store: Any, fields: Mapping[str, object]
) -> dict[str, object]:
    prepare = getattr(store, "prepare_member_update_fields", None)
    if callable(prepare):
        return dict(prepare(dict(fields)))
    return dict(fields)
