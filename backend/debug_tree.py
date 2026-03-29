"""Simulate the tree building to see the actual duplicate branch issue."""
import os, sys, json
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

import airtable_client as db

members = db.get_all_members()
lookup = {m["id"]: m for m in members}

def slim(m):
    return {"id": m.get("id"), "FullName": m.get("FullName")}

for m in members:
    m["children"] = []
    m["Spouse"] = None

def get_sid(field_val):
    if not field_val: return ""
    if isinstance(field_val, list) and len(field_val) > 0:
        return str(field_val[0]).strip()
    return str(field_val).strip()

# Phase 0: placeholders
placeholders = {}
for m in members:
    f_id = get_sid(m.get("FatherRecordId"))
    f_name = (m.get("FatherName") or "").strip()
    if not f_id and f_name and f_name not in placeholders:
        fake_id = f"__ph_f__{f_name.replace(' ','_')}"
        ph = {"id": fake_id, "FullName": f"{f_name} (Unknown)", "Gender": "Male",
              "IsAlive": False, "FatherRecordId": "", "MotherRecordId": "", "SpouseRecordId": "",
              "children": [], "Spouse": None, "IsPlaceholder": True}
        placeholders[f_name] = ph
        lookup[fake_id] = ph
        m["FatherRecordId"] = fake_id
    elif not f_id and f_name in placeholders:
        m["FatherRecordId"] = placeholders[f_name]["id"]

    m_id = get_sid(m.get("MotherRecordId"))
    m_name = (m.get("MotherName") or "").strip()
    if not m_id and m_name and m_name not in placeholders:
        fake_id = f"__ph_m__{m_name.replace(' ','_')}"
        ph = {"id": fake_id, "FullName": f"{m_name} (Unknown)", "Gender": "Female",
              "IsAlive": False, "FatherRecordId": "", "MotherRecordId": "", "SpouseRecordId": "",
              "children": [], "Spouse": None, "IsPlaceholder": True}
        placeholders[m_name] = ph
        lookup[fake_id] = ph
        m["MotherRecordId"] = fake_id
    elif not m_id and m_name in placeholders:
        m["MotherRecordId"] = placeholders[m_name]["id"]

members.extend(placeholders.values())
print(f"Placeholders created: {[ph['FullName'] for ph in placeholders.values()]}")

# Phase 1: Infer spousal links
for m in members:
    father_id = get_sid(m.get("FatherRecordId"))
    mother_id = get_sid(m.get("MotherRecordId"))
    if father_id and mother_id and father_id in lookup and mother_id in lookup:
        father = lookup[father_id]
        mother = lookup[mother_id]
        if not father["Spouse"]: father["Spouse"] = slim(mother)
        if not mother["Spouse"]: mother["Spouse"] = slim(father)

# Phase 2: Spouse linking
for m in members:
    spouse_id = get_sid(m.get("SpouseRecordId"))
    if spouse_id and spouse_id in lookup:
        if not m.get("Spouse"): m["Spouse"] = slim(lookup[spouse_id])
        target_spouse = lookup[spouse_id]
        if not target_spouse.get("Spouse"):
            target_spouse["Spouse"] = slim(m)

# Phase 3: parent-child
for m in members:
    father_id = get_sid(m.get("FatherRecordId"))
    mother_id = get_sid(m.get("MotherRecordId"))
    if father_id and father_id in lookup:
        lookup[father_id]["children"].append(m)
    elif mother_id and mother_id in lookup:
        lookup[mother_id]["children"].append(m)

# Print children assignments
print("\n=== CHILDREN ASSIGNMENTS ===")
for m in members:
    if m["children"]:
        child_names = [c.get("FullName") for c in m["children"]]
        spouse_name = m["Spouse"]["FullName"] if m["Spouse"] else "None"
        print(f"  {m['FullName']} (Spouse: {spouse_name}) has children: {child_names}")

# Phase 4: merge
def merge_spouse_children(node, depth=0):
    if node.get("Spouse"):
        spouse_id = node["Spouse"]["id"]
        if spouse_id in lookup:
            spouse_full = lookup[spouse_id]
            existing_ids = {c["id"] for c in node["children"]}
            for child in spouse_full.get("children", []):
                if child["id"] not in existing_ids:
                    print(f"  {'  '*depth}MERGE: Moving {child['FullName']} from {spouse_full['FullName']} -> {node['FullName']}")
                    node["children"].append(child)
    for child in node["children"]:
        merge_spouse_children(child, depth+1)

# Phase 5: roots
final_roots = []
processed_root_ids = set()
def sort_key(m):
    gen = m.get("Generation", 99)
    has_parents = 0 if (m.get("FatherRecordId") or m.get("MotherRecordId")) else 1
    return (gen, has_parents)

print("\n=== ROOT IDENTIFICATION ===")
for m in sorted(members, key=sort_key):
    if m["id"] in processed_root_ids:
        print(f"  SKIP (already processed): {m['FullName']}")
        continue
    father_id = get_sid(m.get("FatherRecordId"))
    mother_id = get_sid(m.get("MotherRecordId"))
    is_independent = (not father_id or father_id not in lookup) and \
                     (not mother_id or mother_id not in lookup)
    if is_independent:
        print(f"  ROOT: {m['FullName']} (Spouse: {m['Spouse']['FullName'] if m['Spouse'] else 'None'})")
        final_roots.append(m)
        processed_root_ids.add(m["id"])
        if m.get("Spouse"):
            processed_root_ids.add(m["Spouse"]["id"])
    else:
        if m.get("Spouse"):
            processed_root_ids.add(m["Spouse"]["id"])

print("\n=== MERGING SPOUSE CHILDREN ===")
for root in final_roots:
    merge_spouse_children(root)

def print_tree(node, depth=0):
    spouse_str = f" ♥ {node['Spouse']['FullName']}" if node.get("Spouse") else ""
    placeholder = " [PLACEHOLDER]" if node.get("IsPlaceholder") else ""
    print(f"{'  '*depth}├── {node['FullName']}{spouse_str}{placeholder}")
    for child in node.get("children", []):
        print_tree(child, depth+1)

print("\n=== FINAL TREE ===")
for root in final_roots:
    print_tree(root)
