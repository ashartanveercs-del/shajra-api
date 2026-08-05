import json
from typing import NewType
from uuid import NAMESPACE_URL, uuid4, uuid5

PersonId = NewType("PersonId", str)
FamilyUnitId = NewType("FamilyUnitId", str)
LinkId = NewType("LinkId", str)
UnresolvedRelationshipId = NewType("UnresolvedRelationshipId", str)
OperationId = NewType("OperationId", str)
MigrationRunId = NewType("MigrationRunId", str)


def _new(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _migrated(prefix: str, source_table: str, source_record_id: str) -> str:
    value = uuid5(NAMESPACE_URL, f"shajra:{source_table}:{source_record_id}")
    return f"{prefix}_{value.hex}"


def _migrated_unresolved(
    source_table: str, source_record_id: str, source_relation_slot: str
) -> str:
    if not source_relation_slot:
        raise ValueError("source_relation_slot must be non-empty")
    canonical_name = json.dumps(
        [
            "shajra",
            "unresolved",
            "v1",
            source_table,
            source_record_id,
            source_relation_slot,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return f"unr_{uuid5(NAMESPACE_URL, canonical_name).hex}"


def new_person_id() -> PersonId:
    return PersonId(_new("per"))


def new_family_unit_id() -> FamilyUnitId:
    return FamilyUnitId(_new("fam"))


def new_link_id() -> LinkId:
    return LinkId(_new("lnk"))


def new_unresolved_relationship_id() -> UnresolvedRelationshipId:
    return UnresolvedRelationshipId(_new("unr"))


def new_operation_id() -> OperationId:
    return OperationId(_new("op"))


def migrated_person_id(source_table: str, source_record_id: str) -> PersonId:
    return PersonId(_migrated("per", source_table, source_record_id))


def migrated_family_unit_id(source_table: str, source_record_id: str) -> FamilyUnitId:
    return FamilyUnitId(_migrated("fam", source_table, source_record_id))


def migrated_link_id(source_table: str, source_record_id: str) -> LinkId:
    return LinkId(_migrated("lnk", source_table, source_record_id))


def migrated_unresolved_relationship_id(
    source_table: str, source_record_id: str, source_relation_slot: str
) -> UnresolvedRelationshipId:
    return UnresolvedRelationshipId(
        _migrated_unresolved(source_table, source_record_id, source_relation_slot)
    )


def migrated_operation_id(source_table: str, source_record_id: str) -> OperationId:
    return OperationId(_migrated("op", source_table, source_record_id))


def migrated_run_id(source_table: str, source_record_id: str) -> MigrationRunId:
    return MigrationRunId(_migrated("mig", source_table, source_record_id))
