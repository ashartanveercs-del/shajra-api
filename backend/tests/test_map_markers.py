import main
from fastapi.testclient import TestClient

client = TestClient(main.app)


def _member(**overrides):
    m = {
        "id": "id",
        "FullName": "Name",
        "Gender": "Male",
        "IsAlive": True,
        "CurrentCity": "",
        "CurrentCountry": "",
        "BurialLocation": "",
        "FatherRecordId": "",
        "MotherRecordId": "",
        "FatherName": "",
        "MotherName": "",
    }
    m.update(overrides)
    return m


def test_map_markers_normalizes_city_strings(monkeypatch):
    members = [
        _member(id="c", FullName="Deceased Person", IsAlive=False,
                CurrentCity="Berlin", BurialLocation="Karachi, Pakistan"),
    ]
    monkeypatch.setattr(main.db, "get_all_members", lambda: members)

    data = client.get("/api/map-markers").json()

    burials = [m for m in data["markers"] if m["type"] == "burial"]
    residences = [m for m in data["markers"] if m["type"] == "residence"]

    # 'Karachi, Pakistan' -> 'Karachi' -> burial marker (not dropped by city lookup).
    assert any(m["id"] == "c" and m["type"] == "burial" for m in burials)
    # Berlin is not in CITY_GEODATA, so no residence marker is fabricated.
    assert not any(m["id"] == "c" and m["type"] == "residence" for m in residences)


def test_map_markers_resolves_parent_by_name_and_uses_burial_fallback(monkeypatch):
    members = [
        _member(id="a", FullName="Fauzia Shafqat", Gender="Female", IsAlive=False,
                BurialLocation="Lahore, Pakistan"),
        _member(id="b", FullName="Child X", Gender="Male",
                CurrentCity="Karachi, Pakistan", MotherName="Fauzia Shafqat"),
    ]
    monkeypatch.setattr(main.db, "get_all_members", lambda: members)

    data = client.get("/api/map-markers").json()

    # Mother's burial resolved from 'Lahore, Pakistan'.
    assert any(
        m["id"] == "a" and m["type"] == "burial" for m in data["markers"]
    )
    # Child -> mother arc built via name match (no MotherRecordId) with burial coords.
    assert any(
        "Child X" in a["label"] and "Fauzia Shafqat" in a["label"] for a in data["arcs"]
    )


def test_map_markers_resolves_parent_by_record_id(monkeypatch):
    members = [
        _member(id="p", FullName="Parent", Gender="Male", CurrentCity="Dubai"),
        _member(id="c", FullName="Child", Gender="Male", CurrentCity="Karachi",
                FatherRecordId="p"),
    ]
    monkeypatch.setattr(main.db, "get_all_members", lambda: members)

    data = client.get("/api/map-markers").json()

    assert any("Child" in a["label"] and "Parent" in a["label"] for a in data["arcs"])
