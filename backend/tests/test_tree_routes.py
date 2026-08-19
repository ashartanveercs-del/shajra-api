from copy import deepcopy

import main
from fastapi.testclient import TestClient


client = TestClient(main.app)


def _member(record_id, name, **overrides):
    member = {
        "id": record_id,
        "FullName": name,
        "Gender": "Male",
        "IsAlive": True,
        "FatherName": "",
        "MotherName": "",
        "SpouseName": "",
        "FatherRecordId": "",
        "MotherRecordId": "",
        "SpouseRecordId": "",
        "Generation": None,
        "Email": "private-email-marker",
        "PhoneNumber": "private-phone-marker",
    }
    member.update(overrides)
    return member


def _walk_primary(nodes):
    for node in nodes:
        yield node
        yield from _walk_primary(node.get("children", []))


def _all_real_ids(nodes):
    ids = set()
    for node in _walk_primary(nodes):
        if not str(node["id"]).startswith("__name__"):
            ids.add(node["id"])
        spouse = node.get("Spouse")
        if spouse and not str(spouse["id"]).startswith("__name__"):
            ids.add(spouse["id"])
    return ids


def _primary_node(nodes, record_id):
    return next(
        (node for node in _walk_primary(nodes) if node["id"] == record_id), None
    )


def _assert_no_repeated_id_on_path(nodes, path=()):
    for node in nodes:
        assert node["id"] not in path
        _assert_no_repeated_id_on_path(
            node.get("children", []), (*path, node["id"])
        )


def test_tree_is_stable_acyclic_and_preserves_non_cycle_parent_links(monkeypatch):
    members = [
        _member("a", "Person A", FatherRecordId="b"),
        _member("b", "Person B", FatherRecordId="a", MotherRecordId="c"),
        _member("c", "Person C", Gender="Female"),
    ]

    outputs = []
    for ordering in (members, list(reversed(members))):
        monkeypatch.setattr(
            main.db, "get_all_members", lambda ordering=ordering: deepcopy(ordering)
        )
        response = client.get("/api/tree")
        assert response.status_code == 200
        outputs.append(response.json())

    assert outputs[0] == outputs[1]
    tree = outputs[0]
    assert _all_real_ids(tree) == {"a", "b", "c"}
    assert [root["id"] for root in tree] == ["c"]
    assert _primary_node(tree, "b")["MotherRecordId"] == "c"
    assert _primary_node(tree, "b")["FatherRecordId"] == ""


def test_cycle_repair_does_not_leave_spouse_inferred_from_removed_parent_edge(
    monkeypatch,
):
    members = [
        _member("a", "Person A", FatherRecordId="b"),
        _member("b", "Person B", FatherRecordId="a", MotherRecordId="c"),
        _member("c", "Person C", Gender="Female"),
    ]
    monkeypatch.setattr(main.db, "get_all_members", lambda: deepcopy(members))

    tree = client.get("/api/tree").json()
    node_a = _primary_node(tree, "a")
    node_c = _primary_node(tree, "c")

    assert not node_a.get("Spouse") or node_a["Spouse"]["id"] != "c"
    assert not node_c.get("Spouse") or node_c["Spouse"]["id"] != "a"


def test_tree_rejects_truncated_and_ambiguous_name_matches(monkeypatch):
    members = [
        _member("short", "Tanveer Kamal"),
        _member("duplicate-a", "Jose Khan"),
        _member("duplicate-b", "JOS\u00c9   KHAN"),
        _member("child-a", "Child A", FatherName="Tanveer Kamal Rasheed"),
        _member("child-b", "Child B", FatherName="Jose Khan"),
    ]
    monkeypatch.setattr(main.db, "get_all_members", lambda: deepcopy(members))

    tree = client.get("/api/tree").json()

    for real_parent_id in ("short", "duplicate-a", "duplicate-b"):
        parent = _primary_node(tree, real_parent_id)
        assert not parent or not {
            child["id"] for child in parent.get("children", [])
        }.intersection({"child-a", "child-b"})
    assert _all_real_ids(tree) == {
        "short",
        "duplicate-a",
        "duplicate-b",
        "child-a",
        "child-b",
    }


def test_ambiguous_name_references_remain_separate_unresolved_nodes(monkeypatch):
    members = [
        _member("duplicate-a", "Jose Khan"),
        _member("duplicate-b", "JOS\u00c9   KHAN"),
        _member("child-a", "Child A", FatherName="Jose Khan"),
        _member("child-b", "Child B", FatherName="Jose Khan"),
    ]
    monkeypatch.setattr(main.db, "get_all_members", lambda: deepcopy(members))

    tree = client.get("/api/tree").json()
    unresolved = [
        node
        for node in _walk_primary(tree)
        if str(node["id"]).startswith("__name__")
        and main.normalize_person_name(node["FullName"]) == "jose khan"
    ]

    assert len(unresolved) == 2
    assert sorted(len(node["children"]) for node in unresolved) == [1, 1]


def test_tree_covers_explicit_spouses_and_redacts_nested_contacts(monkeypatch):
    members = [
        _member("left", "Left Partner", SpouseRecordId="right"),
        _member(
            "right",
            "Right Partner",
            Gender="Female",
            SpouseRecordId="left",
        ),
    ]
    monkeypatch.setattr(main.db, "get_all_members", lambda: deepcopy(members))

    tree = client.get("/api/tree").json()
    serialized = str(tree)

    assert _all_real_ids(tree) == {"left", "right"}
    assert "private-email-marker" not in serialized
    assert "private-phone-marker" not in serialized


def test_tree_drops_one_sided_spouse_link_when_target_has_another_partner(
    monkeypatch,
):
    members = [
        _member("a", "Partner A", SpouseRecordId="b"),
        _member("b", "Partner B", Gender="Female", SpouseRecordId="a"),
        _member("c", "Conflicting C", SpouseRecordId="b"),
    ]
    monkeypatch.setattr(main.db, "get_all_members", lambda: deepcopy(members))

    tree = client.get("/api/tree").json()
    node_a = _primary_node(tree, "a")
    node_c = _primary_node(tree, "c")

    assert node_a["Spouse"]["id"] == "b"
    assert not node_c.get("Spouse")


def test_tree_rejects_spouse_child_edge_back_to_an_ancestor(monkeypatch):
    members = [
        _member("root", "Root Person"),
        _member("ancestor", "Ancestor Spouse", FatherRecordId="root", SpouseRecordId="descendant"),
        _member("middle", "Middle Person", FatherRecordId="ancestor"),
        _member(
            "descendant",
            "Descendant Spouse",
            Gender="Female",
            FatherRecordId="middle",
            SpouseRecordId="ancestor",
        ),
    ]
    safe_client = TestClient(main.app, raise_server_exceptions=False)
    outputs = []

    for ordering in (members, list(reversed(members))):
        monkeypatch.setattr(
            main.db, "get_all_members", lambda ordering=ordering: deepcopy(ordering)
        )
        response = safe_client.get("/api/tree")
        assert response.status_code == 200
        outputs.append(response.json())

    assert outputs[0] == outputs[1]
    tree = outputs[0]
    _assert_no_repeated_id_on_path(tree)
    assert _all_real_ids(tree) == {"root", "ancestor", "middle", "descendant"}
    assert [child["id"] for child in _primary_node(tree, "ancestor")["children"]] == [
        "middle"
    ]
    assert [child["id"] for child in _primary_node(tree, "middle")["children"]] == [
        "descendant"
    ]
    assert _primary_node(tree, "descendant")["children"] == []


def test_tree_keeps_parent_root_when_child_malformedly_links_parent_as_spouse(
    monkeypatch,
):
    members = [
        _member(
            "child",
            "Dependent Child",
            FatherRecordId="parent",
            SpouseRecordId="parent",
        ),
        _member("parent", "Independent Parent"),
    ]

    outputs = []
    for ordering in (members, list(reversed(members))):
        monkeypatch.setattr(
            main.db, "get_all_members", lambda ordering=ordering: deepcopy(ordering)
        )
        response = client.get("/api/tree")
        assert response.status_code == 200
        outputs.append(response.json())

    assert outputs[0] == outputs[1]
    tree = outputs[0]
    assert [root["id"] for root in tree] == ["parent"]
    assert _all_real_ids(tree) == {"child", "parent"}
    assert [child["id"] for child in tree[0]["children"]] == ["child"]
