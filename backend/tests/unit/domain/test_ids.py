from domain.ids import (
    migrated_person_id,
    migrated_unresolved_relationship_id,
    new_family_unit_id,
    new_person_id,
    new_unresolved_relationship_id,
)


def test_new_ids_have_type_prefixes_and_are_unique():
    first = new_person_id()
    second = new_person_id()
    assert str(first).startswith("per_")
    assert first != second
    assert str(new_family_unit_id()).startswith("fam_")
    assert str(new_unresolved_relationship_id()).startswith("unr_")


def test_migrated_ids_are_deterministic_and_table_scoped():
    first = migrated_person_id("ApprovedMembers", "rec123")
    assert first == migrated_person_id("ApprovedMembers", "rec123")
    assert first != migrated_person_id("PendingSubmissions", "rec123")


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
