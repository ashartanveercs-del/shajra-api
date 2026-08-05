from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

import pytest

from domain.commands import (
    AddFamilyUnit,
    AddPersonVersion,
    AddUnresolvedRelationship,
    RemoveUnresolvedRelationship,
    SupersedeFamilyUnit,
    SupersedeUnresolvedRelationship,
    apply_commands,
)
from domain.dates import PartialDate
from domain.ids import FamilyUnitId, LinkId, PersonId, UnresolvedRelationshipId
from domain.issues import GraphIssue, IssueSeverity, ValidationReport
from domain.models import (
    FamilyUnit,
    FamilyUnitKind,
    GraphSnapshot,
    ParentChildLink,
    ParentRole,
    Person,
    RelationshipType,
    UnionStatus,
    UnresolvedRelationship,
    UnresolvedRelationshipKind,
)
from domain.validation import validate_snapshot
from tests.fixtures.graphs import (
    CHILD,
    adoptive_cycle_snapshot,
    archived_two_parent_snapshot,
    duplicate_historical_union_snapshot,
    empty_snapshot,
    guardian_cycle_snapshot,
    remarriage_snapshot,
    two_parent_family_snapshot,
)


def _person(
    value: str,
    *,
    birth: str | None = None,
    death: str | None = None,
    primary: FamilyUnitId | None = None,
    archived: bool = False,
) -> Person:
    person_id = PersonId(value)
    return Person(
        person_id,
        value,
        birth=PartialDate.parse(birth) if birth else None,
        death=PartialDate.parse(death) if death else None,
        primary_family_unit_id=primary,
        archived=archived,
    )


def _family(
    value: str,
    adult_a: PersonId,
    adult_b: PersonId | None = None,
    *,
    kind: FamilyUnitKind | None = None,
    status: UnionStatus = UnionStatus.UNKNOWN,
    start: str | None = None,
    end: str | None = None,
    confirmed: bool = False,
) -> FamilyUnit:
    return FamilyUnit(
        FamilyUnitId(value),
        kind or (FamilyUnitKind.UNION if adult_b is not None else FamilyUnitKind.SINGLE_PARENT),
        adult_a,
        adult_b,
        status=status,
        start=PartialDate.parse(start) if start else None,
        end=PartialDate.parse(end) if end else None,
        distinct_union_confirmed=confirmed,
    )


def _link(
    value: str,
    parent: PersonId,
    child: PersonId,
    *,
    relationship: RelationshipType = RelationshipType.BIOLOGICAL,
    family: FamilyUnitId | None = None,
) -> ParentChildLink:
    return ParentChildLink(
        LinkId(value),
        parent,
        child,
        ParentRole.PARENT,
        relationship,
        family,
    )


def _unresolved(
    value: str,
    subject: PersonId,
    name: str,
    *,
    kind: UnresolvedRelationshipKind = UnresolvedRelationshipKind.PARENT,
) -> UnresolvedRelationship:
    return UnresolvedRelationship(UnresolvedRelationshipId(value), subject, kind, name)


def _snapshot(
    *,
    people: tuple[Person, ...] = (),
    families: tuple[FamilyUnit, ...] = (),
    links: tuple[ParentChildLink, ...] = (),
    unresolved: tuple[UnresolvedRelationship, ...] = (),
) -> GraphSnapshot:
    state = empty_snapshot().state
    return GraphSnapshot(
        state,
        {person.person_id: person for person in people},
        {family.family_unit_id: family for family in families},
        {link.link_id: link for link in links},
        {item.unresolved_id: item for item in unresolved},
    )


def _issue(report: ValidationReport, code: str) -> GraphIssue:
    return next(issue for issue in report.issues if issue.code == code)


def _codes(report: ValidationReport) -> set[str]:
    return {issue.code for issue in report.issues}


def _self_parent_snapshot() -> GraphSnapshot:
    person = _person("per_self")
    link = _link(
        "lnk_self", person.person_id, person.person_id, relationship=RelationshipType.GUARDIAN
    )
    return _snapshot(people=(person,), links=(link,))


def _self_partner_snapshot() -> GraphSnapshot:
    person = _person("per_self_partner")
    return _snapshot(
        people=(person,),
        families=(
            _family("fam_self", person.person_id, person.person_id),
        ),
    )


def _missing_person_snapshot() -> GraphSnapshot:
    child = _person("per_present")
    return _snapshot(
        people=(child,),
        links=(_link("lnk_missing_parent", PersonId("per_missing"), child.person_id),),
    )


def _missing_family_snapshot() -> GraphSnapshot:
    child = _person("per_child_missing_family", primary=FamilyUnitId("fam_missing"))
    return _snapshot(people=(child,))


def _duplicate_link_snapshot() -> GraphSnapshot:
    parent = _person("per_duplicate_parent")
    child = _person("per_duplicate_child")
    return _snapshot(
        people=(parent, child),
        links=(
            _link("lnk_duplicate_a", parent.person_id, child.person_id),
            _link("lnk_duplicate_b", parent.person_id, child.person_id),
        ),
    )


def _duplicate_family_snapshot() -> GraphSnapshot:
    adult_a = _person("per_duplicate_adult_a")
    adult_b = _person("per_duplicate_adult_b")
    return _snapshot(
        people=(adult_a, adult_b),
        families=(
            _family("fam_duplicate_a", adult_a.person_id, adult_b.person_id),
            _family("fam_duplicate_b", adult_b.person_id, adult_a.person_id),
        ),
    )


def _duplicate_unresolved_snapshot() -> GraphSnapshot:
    subject = _person("per_unresolved_subject")
    return _snapshot(
        people=(subject,),
        unresolved=(
            _unresolved("unr_a", subject.person_id, "Unknown Parent"),
            _unresolved("unr_b", subject.person_id, "unknown   parent"),
        ),
    )


def _primary_mismatch_snapshot() -> GraphSnapshot:
    adult_a = _person("per_primary_a")
    adult_b = _person("per_primary_b")
    family = _family("fam_primary_exact", adult_a.person_id, adult_b.person_id)
    child = _person("per_primary_child", primary=family.family_unit_id)
    return _snapshot(
        people=(adult_a, adult_b, child),
        families=(family,),
        links=(
            _link(
                "lnk_primary_only_one",
                adult_a.person_id,
                child.person_id,
                family=family.family_unit_id,
            ),
        ),
    )


def _family_parent_mismatch_snapshot() -> GraphSnapshot:
    adult_a = _person("per_family_adult_a")
    adult_b = _person("per_family_adult_b")
    outsider = _person("per_family_outsider")
    child = _person("per_family_child")
    family = _family("fam_parent_mismatch", adult_a.person_id, adult_b.person_id)
    return _snapshot(
        people=(adult_a, adult_b, outsider, child),
        families=(family,),
        links=(
            _link(
                "lnk_family_outsider",
                outsider.person_id,
                child.person_id,
                family=family.family_unit_id,
            ),
        ),
    )


def _death_before_birth_snapshot() -> GraphSnapshot:
    return _snapshot(people=(_person("per_bad_life", birth="2000", death="1999"),))


def _union_end_before_start_snapshot() -> GraphSnapshot:
    adult = _person("per_union_adult")
    adult_b = _person("per_union_adult_b")
    return _snapshot(
        people=(adult, adult_b),
        families=(
            _family(
                "fam_bad_dates",
                adult.person_id,
                adult_b.person_id,
                start="2000",
                end="1999",
            ),
        ),
    )


def _biological_age_snapshot(parent_birth: str, child_birth: str) -> GraphSnapshot:
    parent = _person("per_age_parent", birth=parent_birth)
    child = _person("per_age_child", birth=child_birth)
    return _snapshot(
        people=(parent, child),
        links=(_link("lnk_age", parent.person_id, child.person_id),),
    )


def _impossible_parent_age_snapshot() -> GraphSnapshot:
    return _biological_age_snapshot("2001-01-01", "2000-01-01")


def _suspicious_parent_age_snapshot() -> GraphSnapshot:
    return _biological_age_snapshot("1991-01-01", "2000-01-01")


@pytest.mark.parametrize(
    ("code", "severity", "factory"),
    [
        ("SELF_PARENT", IssueSeverity.ERROR, _self_parent_snapshot),
        ("SELF_PARTNER", IssueSeverity.ERROR, _self_partner_snapshot),
        ("MISSING_PERSON", IssueSeverity.ERROR, _missing_person_snapshot),
        ("MISSING_FAMILY_UNIT", IssueSeverity.ERROR, _missing_family_snapshot),
        ("DUPLICATE_LINK", IssueSeverity.ERROR, _duplicate_link_snapshot),
        ("DUPLICATE_FAMILY_UNIT", IssueSeverity.ERROR, _duplicate_family_snapshot),
        (
            "DUPLICATE_UNRESOLVED_RELATIONSHIP",
            IssueSeverity.WARNING,
            _duplicate_unresolved_snapshot,
        ),
        ("ANCESTRY_CYCLE", IssueSeverity.ERROR, adoptive_cycle_snapshot),
        ("PRIMARY_UNIT_MISMATCH", IssueSeverity.ERROR, _primary_mismatch_snapshot),
        (
            "FAMILY_UNIT_PARENT_MISMATCH",
            IssueSeverity.ERROR,
            _family_parent_mismatch_snapshot,
        ),
        ("DEATH_BEFORE_BIRTH", IssueSeverity.ERROR, _death_before_birth_snapshot),
        (
            "UNION_END_BEFORE_START",
            IssueSeverity.ERROR,
            _union_end_before_start_snapshot,
        ),
        (
            "IMPOSSIBLE_PARENT_AGE",
            IssueSeverity.ERROR,
            _impossible_parent_age_snapshot,
        ),
        (
            "SUSPICIOUS_PARENT_AGE",
            IssueSeverity.WARNING,
            _suspicious_parent_age_snapshot,
        ),
        (
            "ARCHIVED_RELATIONSHIP_OMITTED",
            IssueSeverity.WARNING,
            archived_two_parent_snapshot,
        ),
    ],
)
def test_invariant_matrix_emits_each_stable_code_with_resolved_severity(
    code: str, severity: IssueSeverity, factory
):
    issue = _issue(validate_snapshot(factory()), code)

    assert issue.severity is severity
    assert issue.message


def test_structured_issues_are_immutable_and_error_state_is_severity_driven():
    warning = GraphIssue("WARN", IssueSeverity.WARNING, "warning")
    error = GraphIssue("ERROR", IssueSeverity.ERROR, "error")

    assert ValidationReport((warning,)).has_errors is False
    assert ValidationReport((warning, error)).has_errors is True
    with pytest.raises(FrozenInstanceError):
        warning.message = "mutated"


def test_missing_required_references_do_not_cascade_into_derived_issues():
    adult = _person("per_existing_adult")
    child = _person("per_existing_child", primary=FamilyUnitId("fam_broken"))
    family = _family(
        "fam_broken", adult.person_id, PersonId("per_missing_family_adult")
    )
    snapshot = _snapshot(
        people=(adult, child),
        families=(family,),
        links=(
            _link(
                "lnk_existing_adult",
                adult.person_id,
                child.person_id,
                family=family.family_unit_id,
            ),
        ),
    )

    assert _codes(validate_snapshot(snapshot)) == {"MISSING_PERSON"}


def test_all_reference_sources_are_checked_without_duplicate_missing_codes():
    present = _person("per_present_references", primary=FamilyUnitId("fam_missing_primary"))
    family = _family("fam_missing_adult", PersonId("per_missing_adult"))
    link = _link(
        "lnk_missing_everywhere",
        PersonId("per_missing_parent"),
        PersonId("per_missing_child"),
        family=FamilyUnitId("fam_missing_link"),
    )
    unresolved = _unresolved("unr_missing_subject", PersonId("per_missing_subject"), "Missing")

    report = validate_snapshot(
        _snapshot(people=(present,), families=(family,), links=(link,), unresolved=(unresolved,))
    )

    assert len([issue for issue in report.issues if issue.code == "MISSING_PERSON"]) == 4
    assert len([issue for issue in report.issues if issue.code == "MISSING_FAMILY_UNIT"]) == 2


@pytest.mark.parametrize(
    ("kind", "adult_b"),
    [
        (FamilyUnitKind.SINGLE_PARENT, PersonId("per_unexpected_second_adult")),
        (FamilyUnitKind.UNION, None),
    ],
)
def test_family_shape_contract_is_blocking(kind: FamilyUnitKind, adult_b: PersonId | None):
    adult_a = _person("per_shape_a")
    people = [adult_a]
    if adult_b is not None:
        people.append(Person(adult_b, "Second Adult"))
    family = _family("fam_bad_shape", adult_a.person_id, adult_b, kind=kind)

    report = validate_snapshot(_snapshot(people=tuple(people), families=(family,)))

    assert _issue(report, "FAMILY_UNIT_PARENT_MISMATCH").severity is IssueSeverity.ERROR


def test_valid_remarriage_and_two_parent_fixtures_have_exact_primary_membership():
    assert validate_snapshot(two_parent_family_snapshot()).issues == ()
    assert validate_snapshot(remarriage_snapshot()).issues == ()


@pytest.mark.parametrize("extra_mode", ["missing", "extra", "duplicate"])
def test_primary_family_requires_exactly_one_link_from_each_adult(extra_mode: str):
    adult_a = _person("per_exact_a")
    adult_b = _person("per_exact_b")
    outsider = _person("per_exact_outsider")
    family = _family("fam_exact", adult_a.person_id, adult_b.person_id)
    child = _person("per_exact_child", primary=family.family_unit_id)
    links = [
        _link("lnk_exact_a", adult_a.person_id, child.person_id, family=family.family_unit_id),
    ]
    if extra_mode != "missing":
        links.append(
            _link("lnk_exact_b", adult_b.person_id, child.person_id, family=family.family_unit_id)
        )
    if extra_mode == "extra":
        links.append(
            _link("lnk_exact_extra", outsider.person_id, child.person_id, family=family.family_unit_id)
        )
    if extra_mode == "duplicate":
        links.append(
            _link("lnk_exact_duplicate", adult_a.person_id, child.person_id, family=family.family_unit_id)
        )

    report = validate_snapshot(
        _snapshot(people=(adult_a, adult_b, outsider, child), families=(family,), links=tuple(links))
    )

    assert "PRIMARY_UNIT_MISMATCH" in _codes(report)


def test_non_primary_family_links_remain_valid_detail_relationships():
    snapshot = two_parent_family_snapshot()
    child = snapshot.people[CHILD]
    without_primary = GraphSnapshot(
        snapshot.state,
        {**snapshot.people, CHILD: replace(child, primary_family_unit_id=None)},
        snapshot.family_units,
        snapshot.links,
        snapshot.unresolved,
    )

    assert "PRIMARY_UNIT_MISMATCH" not in _codes(validate_snapshot(without_primary))


def test_non_primary_adoptive_cycle_is_blocking():
    report = validate_snapshot(adoptive_cycle_snapshot())
    assert report.has_errors
    assert "ANCESTRY_CYCLE" in {issue.code for issue in report.issues}


def test_guardian_cycle_is_excluded_from_ancestry_detection():
    assert "ANCESTRY_CYCLE" not in _codes(validate_snapshot(guardian_cycle_snapshot()))


def test_deep_acyclic_ancestry_chain_does_not_consume_python_call_stack():
    depth = 1_500
    people = tuple(_person(f"per_deep_{index:04d}") for index in range(depth))
    links = tuple(
        _link(
            f"lnk_deep_{index:04d}",
            people[index].person_id,
            people[index + 1].person_id,
        )
        for index in range(depth - 1)
    )

    report = validate_snapshot(_snapshot(people=people, links=links))

    assert report.issues == ()


def test_one_cycle_component_emits_one_issue_with_all_sorted_affected_ids():
    people = tuple(_person(f"per_cycle_{name}") for name in ("c", "a", "b"))
    by_id = {person.person_id: person for person in people}
    links = (
        _link("lnk_cycle_c_a", PersonId("per_cycle_c"), PersonId("per_cycle_a"), relationship=RelationshipType.UNKNOWN),
        _link("lnk_cycle_b_c", PersonId("per_cycle_b"), PersonId("per_cycle_c"), relationship=RelationshipType.STEP),
        _link("lnk_cycle_a_b", PersonId("per_cycle_a"), PersonId("per_cycle_b"), relationship=RelationshipType.ADOPTIVE),
    )

    issues = [
        issue
        for issue in validate_snapshot(_snapshot(people=tuple(by_id.values()), links=links)).issues
        if issue.code == "ANCESTRY_CYCLE"
    ]

    assert len(issues) == 1
    assert issues[0].person_ids == tuple(sorted(by_id))
    assert issues[0].link_ids == tuple(sorted(link.link_id for link in links))


@pytest.mark.parametrize("status", list(UnionStatus))
@pytest.mark.parametrize("ended", [False, True])
def test_every_union_status_and_end_combination_requires_duplicate_confirmation(
    status: UnionStatus, ended: bool
):
    report = validate_snapshot(duplicate_historical_union_snapshot(False, status, ended))

    assert "DUPLICATE_FAMILY_UNIT" in _codes(report)


def test_duplicate_family_group_is_allowed_only_when_every_unit_is_confirmed():
    confirmed = duplicate_historical_union_snapshot(True, UnionStatus.DIVORCED, True)
    mixed_units = dict(confirmed.family_units)
    first_id = sorted(mixed_units)[0]
    mixed_units[first_id] = replace(mixed_units[first_id], distinct_union_confirmed=False)
    mixed = GraphSnapshot(
        confirmed.state,
        confirmed.people,
        mixed_units,
        confirmed.links,
        confirmed.unresolved,
    )

    assert "DUPLICATE_FAMILY_UNIT" not in _codes(validate_snapshot(confirmed))
    assert "DUPLICATE_FAMILY_UNIT" in _codes(validate_snapshot(mixed))


def test_superseded_family_version_is_one_current_unit_under_its_stable_id():
    adult_a = _person("per_reducer_a")
    adult_b = _person("per_reducer_b")
    original = _family("fam_reducer", adult_a.person_id, adult_b.person_id)
    snapshot = apply_commands(
        empty_snapshot(),
        [AddPersonVersion(adult_a), AddPersonVersion(adult_b), AddFamilyUnit(original)],
    )
    replacement = replace(
        original, status=UnionStatus.DIVORCED, end=PartialDate.parse("2020"), created_revision=2
    )

    result = apply_commands(
        snapshot, [SupersedeFamilyUnit(original.family_unit_id, replacement)]
    )

    assert len(result.family_units) == 1
    assert "DUPLICATE_FAMILY_UNIT" not in _codes(validate_snapshot(result))


def test_unresolved_subject_must_exist():
    annotation = _unresolved("unr_orphan", PersonId("per_missing_subject"), "Unknown")

    issue = _issue(validate_snapshot(_snapshot(unresolved=(annotation,))), "MISSING_PERSON")

    assert issue.person_ids == (PersonId("per_missing_subject"),)


def test_unresolved_duplicates_casefold_the_already_whitespace_normalized_name():
    report = validate_snapshot(_duplicate_unresolved_snapshot())
    issue = _issue(report, "DUPLICATE_UNRESOLVED_RELATIONSHIP")

    assert issue.severity is IssueSeverity.WARNING
    assert issue.person_ids == (PersonId("per_unresolved_subject"),)


def test_unresolved_add_supersede_remove_lifecycle_tracks_only_current_annotations():
    subject = _person("per_lifecycle_subject")
    first = _unresolved("unr_lifecycle_a", subject.person_id, "Unknown Parent")
    duplicate = _unresolved("unr_lifecycle_b", subject.person_id, "unknown parent")
    snapshot = apply_commands(
        empty_snapshot(),
        [
            AddPersonVersion(subject),
            AddUnresolvedRelationship(first),
            AddUnresolvedRelationship(duplicate),
        ],
    )
    assert "DUPLICATE_UNRESOLVED_RELATIONSHIP" in _codes(validate_snapshot(snapshot))

    superseded = apply_commands(
        snapshot,
        [
            SupersedeUnresolvedRelationship(
                duplicate.unresolved_id, replace(duplicate, unresolved_name="Known Later")
            )
        ],
    )
    removed = apply_commands(
        superseded, [RemoveUnresolvedRelationship(first.unresolved_id)]
    )

    assert "DUPLICATE_UNRESOLVED_RELATIONSHIP" not in _codes(validate_snapshot(superseded))
    assert validate_snapshot(removed).issues == ()
    assert first.unresolved_id in snapshot.unresolved


def test_isolated_archived_person_does_not_emit_topology_warning():
    archived = _person("per_archived_isolated", archived=True)

    assert "ARCHIVED_RELATIONSHIP_OMITTED" not in _codes(
        validate_snapshot(_snapshot(people=(archived,)))
    )


def test_archived_topology_is_aggregated_deterministically():
    snapshot = archived_two_parent_snapshot()
    issue = _issue(validate_snapshot(snapshot), "ARCHIVED_RELATIONSHIP_OMITTED")
    archived_id = next(person.person_id for person in snapshot.people.values() if person.archived)
    direct_links = tuple(
        sorted(
            link.link_id
            for link in snapshot.links.values()
            if archived_id in (link.parent_id, link.child_id)
        )
    )

    assert issue.person_ids == (archived_id,)
    assert issue.family_unit_ids == tuple(sorted(snapshot.family_units))
    assert issue.link_ids == direct_links


@pytest.mark.parametrize(
    ("birth", "death", "expected"),
    [
        ("2000", "1999", True),
        ("2000", "2000", False),
        ("2000-02", "2000-01", True),
        ("2000-02", "2000", False),
    ],
)
def test_death_before_birth_uses_interval_impossibility(
    birth: str, death: str, expected: bool
):
    report = validate_snapshot(
        _snapshot(people=(_person("per_life_interval", birth=birth, death=death),))
    )

    assert ("DEATH_BEFORE_BIRTH" in _codes(report)) is expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2000", "1999", True),
        ("2000", "2000", False),
        ("2000-02", "2000-01", True),
        ("2000-02", "2000", False),
    ],
)
def test_union_end_before_start_uses_interval_impossibility(
    start: str, end: str, expected: bool
):
    adult = _person("per_union_interval")
    adult_b = _person("per_union_interval_b")
    family = _family(
        "fam_union_interval", adult.person_id, adult_b.person_id, start=start, end=end
    )

    report = validate_snapshot(_snapshot(people=(adult, adult_b), families=(family,)))

    assert ("UNION_END_BEFORE_START" in _codes(report)) is expected


@pytest.mark.parametrize(
    ("parent_birth", "child_birth", "impossible"),
    [
        ("2000", "2000-01-01", False),
        ("2001-01-01", "2000", True),
        ("2000", "2000", False),
    ],
)
def test_parent_birth_order_uses_interval_possibility(
    parent_birth: str, child_birth: str, impossible: bool
):
    report = validate_snapshot(_biological_age_snapshot(parent_birth, child_birth))

    assert ("IMPOSSIBLE_PARENT_AGE" in _codes(report)) is impossible


def test_parent_death_before_conception_uses_310_day_boundary():
    child_birth = PartialDate.parse("2000-01-01")
    boundary = child_birth.earliest - timedelta(days=310)
    parent = _person("per_conception_parent", birth="1950-01-01")
    child = _person("per_conception_child", birth=child_birth.value)

    def report_for(death_offset: int) -> ValidationReport:
        dated_parent = replace(
            parent,
            death=PartialDate.parse((boundary + timedelta(days=death_offset)).isoformat()),
        )
        return validate_snapshot(
            _snapshot(
                people=(dated_parent, child),
                links=(_link("lnk_conception", parent.person_id, child.person_id),),
            )
        )

    assert "IMPOSSIBLE_PARENT_AGE" in _codes(report_for(-1))
    assert "IMPOSSIBLE_PARENT_AGE" not in _codes(report_for(0))


@pytest.mark.parametrize(
    ("parent_birth", "child_birth", "suspicious"),
    [
        ("2000-02-29", "2010-02-27", True),
        ("2000-02-29", "2010-02-28", False),
        ("1940-02-29", "2020-02-29", False),
        ("1940-02-29", "2020-03-01", True),
        ("1900", "1980", True),
    ],
)
def test_suspicious_parent_age_observes_exact_10_80_and_leap_day_boundaries(
    parent_birth: str, child_birth: str, suspicious: bool
):
    report = validate_snapshot(_biological_age_snapshot(parent_birth, child_birth))

    assert ("SUSPICIOUS_PARENT_AGE" in _codes(report)) is suspicious
    if suspicious:
        assert _issue(report, "SUSPICIOUS_PARENT_AGE").severity is IssueSeverity.WARNING


def test_guardian_links_do_not_run_biological_age_or_conception_checks():
    parent = _person("per_guardian_age_parent", birth="2001", death="1990")
    child = _person("per_guardian_age_child", birth="2000")
    link = _link(
        "lnk_guardian_age",
        parent.person_id,
        child.person_id,
        relationship=RelationshipType.GUARDIAN,
    )

    codes = _codes(validate_snapshot(_snapshot(people=(parent, child), links=(link,))))

    assert "IMPOSSIBLE_PARENT_AGE" not in codes
    assert "SUSPICIOUS_PARENT_AGE" not in codes


def test_validation_is_pure_and_deterministic_under_shuffled_input_maps():
    base = archived_two_parent_snapshot()
    duplicate_links = dict(base.links)
    original = next(iter(base.links.values()))
    duplicate = replace(original, link_id=LinkId("lnk_aaa_duplicate"))
    duplicate_links[duplicate.link_id] = duplicate
    unresolved = {
        UnresolvedRelationshipId("unr_z"): _unresolved("unr_z", CHILD, "Unknown Parent"),
        UnresolvedRelationshipId("unr_a"): _unresolved("unr_a", CHILD, "unknown parent"),
    }
    ordered = GraphSnapshot(
        base.state, base.people, base.family_units, duplicate_links, unresolved
    )
    shuffled = GraphSnapshot(
        base.state,
        dict(reversed(list(base.people.items()))),
        dict(reversed(list(base.family_units.items()))),
        dict(reversed(list(duplicate_links.items()))),
        dict(reversed(list(unresolved.items()))),
    )
    before = (
        dict(ordered.people),
        dict(ordered.family_units),
        dict(ordered.links),
        dict(ordered.unresolved),
    )

    first = validate_snapshot(ordered)
    second = validate_snapshot(shuffled)

    assert first == second
    assert before == (
        dict(ordered.people),
        dict(ordered.family_units),
        dict(ordered.links),
        dict(ordered.unresolved),
    )
    sort_keys = [
        (
            issue.severity.value,
            issue.code,
            issue.person_ids,
            issue.family_unit_ids,
            issue.link_ids,
        )
        for issue in first.issues
    ]
    assert sort_keys == sorted(sort_keys)
    for issue in first.issues:
        assert issue.person_ids == tuple(sorted(issue.person_ids))
        assert issue.family_unit_ids == tuple(sorted(issue.family_unit_ids))
        assert issue.link_ids == tuple(sorted(issue.link_ids))
