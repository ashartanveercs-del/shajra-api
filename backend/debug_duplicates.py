"""Debug script to find duplicate members and the Abrar branch issue."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

import airtable_client as db

members = db.get_all_members()
print(f"Total members: {len(members)}\n")

# Find all members and their parent links
name_counts = {}
for m in members:
    name = m.get("FullName", "Unknown")
    name_counts[name] = name_counts.get(name, 0) + 1

print("=== DUPLICATE NAMES ===")
for name, count in sorted(name_counts.items()):
    if count > 1:
        print(f"  '{name}' appears {count} times")
        for m in members:
            if m.get("FullName") == name:
                print(f"    ID={m['id']}, Father={m.get('FatherName','')}, FatherRecId={m.get('FatherRecordId','')}, Mother={m.get('MotherName','')}, MotherRecId={m.get('MotherRecordId','')}, Spouse={m.get('SpouseName','')}, SpouseRecId={m.get('SpouseRecordId','')}")

print("\n=== ALL MEMBERS (sorted by name) ===")
for m in sorted(members, key=lambda x: x.get("FullName", "")):
    name = m.get("FullName", "?")
    fid = m.get("FatherRecordId", "")
    mid = m.get("MotherRecordId", "")
    sid = m.get("SpouseRecordId", "")
    fname = m.get("FatherName", "")
    mname = m.get("MotherName", "")
    sname = m.get("SpouseName", "")
    gen = m.get("Generation", "?")
    print(f"  {name:30s} ID={m['id']:20s} Gen={gen} F={fname}({fid}) M={mname}({mid}) S={sname}({sid})")

print("\n=== ABRAR-RELATED MEMBERS ===")
for m in members:
    name = (m.get("FullName") or "").lower()
    fname = (m.get("FatherName") or "").lower()
    mname = (m.get("MotherName") or "").lower()
    sname = (m.get("SpouseName") or "").lower()
    if "abrar" in name or "abrar" in fname or "abrar" in mname or "abrar" in sname:
        print(f"  {m.get('FullName'):30s} ID={m['id']:20s} F={m.get('FatherName','')}({m.get('FatherRecordId','')}) M={m.get('MotherName','')}({m.get('MotherRecordId','')}) S={m.get('SpouseName','')}({m.get('SpouseRecordId','')})")
