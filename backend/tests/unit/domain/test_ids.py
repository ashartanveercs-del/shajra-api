import pytest

from domain.ids import (
    migrated_family_unit_id,
    migrated_link_id,
    migrated_operation_id,
    migrated_person_id,
    migrated_run_id,
    migrated_unresolved_relationship_id,
    new_family_unit_id,
    new_link_id,
    new_operation_id,
    new_person_id,
    new_unresolved_relationship_id,
)


def test_new_ids_have_type_prefixes_and_are_unique():
    first = new_person_id()
    second = new_person_id()

    assert str(first).startswith("per_")
    assert first != second
    assert str(new_family_unit_id()).startswith("fam_")
    assert str(new_link_id()).startswith("lnk_")
    assert str(new_unresolved_relationship_id()).startswith("unr_")
    assert str(new_operation_id()).startswith("op_")


def test_migrated_ids_are_deterministic_and_table_scoped():
    first = migrated_person_id("ApprovedMembers", "rec123")
    assert first == migrated_person_id("ApprovedMembers", "rec123")
    assert first != migrated_person_id("PendingSubmissions", "rec123")


@pytest.mark.parametrize(
    ("factory", "prefix"),
    [
        (migrated_family_unit_id, "fam_"),
        (migrated_link_id, "lnk_"),
        (migrated_operation_id, "op_"),
        (migrated_run_id, "mig_"),
    ],
)
def test_migrated_ids_keep_type_prefixes_and_remain_table_scoped(factory, prefix):
    first = factory("ApprovedMembers", "rec123")

    assert str(first).startswith(prefix)
    assert first == factory("ApprovedMembers", "rec123")
    assert first != factory("PendingSubmissions", "rec123")


def test_migrated_unresolved_ids_are_relation_slot_scoped_and_idempotent():
    slots = ("FatherName#0", "MotherName#0", "SpouseName#0")
    first_run = [
        migrated_unresolved_relationship_id("ApprovedMembers", "rec123", slot)
        for slot in slots
    ]
    second_run = [
        migrated_unresolved_relationship_id("ApprovedMembers", "rec123", slot)
        for slot in slots
    ]
    assert len(set(first_run)) == len(slots)
    assert first_run == second_run


def test_migrated_unresolved_id_requires_a_relation_slot():
    with pytest.raises(ValueError, match="source_relation_slot must be non-empty"):
        migrated_unresolved_relationship_id("ApprovedMembers", "rec123", "")
