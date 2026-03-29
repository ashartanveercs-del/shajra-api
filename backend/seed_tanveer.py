import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import AIRTABLE_PAT, AIRTABLE_BASE_ID, APPROVED_MEMBERS_TABLE, PENDING_SUBMISSIONS_TABLE
from pyairtable import Api

api = Api(AIRTABLE_PAT)
members = api.table(AIRTABLE_BASE_ID, APPROVED_MEMBERS_TABLE)
pending = api.table(AIRTABLE_BASE_ID, PENDING_SUBMISSIONS_TABLE)

print("Deleting all pending submissions...")
p_records = pending.all()
if p_records:
    pending.batch_delete([r['id'] for r in p_records])

print("Deleting all existing members...")
m_records = members.all()
if m_records:
    members.batch_delete([r['id'] for r in m_records])

print("Inserting Tanveer Kamal (Father)...")
father = members.create({"FullName": "Tanveer Kamal", "Gender": "Male", "IsAlive": True, "Generation": 1})

print("Inserting Shehla Tanveer (Mother)...")
mother = members.create({"FullName": "Shehla Tanveer", "Gender": "Female", "IsAlive": True, "Generation": 1})

print("Inserting Ashar Tanveer (Son)...")
son = members.create({
    "FullName": "Ashar Tanveer", 
    "Gender": "Male", 
    "IsAlive": True, 
    "Generation": 2, 
    "FatherRecordId": father['id'], 
    "MotherRecordId": mother['id']
})

print("Database reset and seeded successfully!")
