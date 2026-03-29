"""
Setup script to create the required Airtable tables for the Shajra System.
Run this once to initialize your Airtable base with the correct schema.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_PAT}",
    "Content-Type": "application/json",
}

META_URL = f"https://api.airtable.com/v0/meta/bases/{AIRTABLE_BASE_ID}/tables"


def create_table(name, description, fields):
    """Create a table in Airtable with given fields."""
    payload = {
        "name": name,
        "description": description,
        "fields": fields,
    }
    resp = requests.post(META_URL, headers=HEADERS, json=payload)
    if resp.status_code == 200:
        data = resp.json()
        print(f"[OK] Created table '{name}' with ID: {data['id']}")
        return data
    else:
        print(f"[FAIL] Failed to create table '{name}': {resp.status_code}")
        print(resp.text)
        return None


def setup():
    print("=" * 60)
    print("  Shajra System — Airtable Setup")
    print("=" * 60)

    # 1. Create Approved Members table
    members_fields = [
        {"name": "FullName", "type": "singleLineText", "description": "Full name of the family member"},
        {"name": "FatherName", "type": "singleLineText", "description": "Father's name (text, linked later)"},
        {"name": "MotherName", "type": "singleLineText", "description": "Mother's name"},
        {"name": "SpouseName", "type": "singleLineText", "description": "Spouse's name"},
        {"name": "DateOfBirth", "type": "singleLineText", "description": "Date of birth (text for flexibility)"},
        {"name": "DateOfDeath", "type": "singleLineText", "description": "Date of death (optional)"},
        {"name": "CurrentCity", "type": "singleLineText", "description": "Current city of residence"},
        {"name": "CurrentCountry", "type": "singleLineText", "description": "Current country of residence"},
        {"name": "BurialLocation", "type": "singleLineText", "description": "Burial location (city/address)"},
        {"name": "Latitude", "type": "number", "options": {"precision": 8}, "description": "Latitude for map pin"},
        {"name": "Longitude", "type": "number", "options": {"precision": 8}, "description": "Longitude for map pin"},
        {"name": "BurialLatitude", "type": "number", "options": {"precision": 8}, "description": "Burial latitude"},
        {"name": "BurialLongitude", "type": "number", "options": {"precision": 8}, "description": "Burial longitude"},
        {"name": "Biography", "type": "multilineText", "description": "Notes or biography"},
        {"name": "Photos", "type": "multipleAttachments", "description": "Photos of the member"},
        {"name": "Generation", "type": "number", "options": {"precision": 0}, "description": "Generation number (1 = patriarch)"},
        {"name": "FatherRecordId", "type": "singleLineText", "description": "Airtable record ID of father"},
        {"name": "MotherRecordId", "type": "singleLineText", "description": "Airtable record ID of mother"},
        {"name": "SpouseRecordId", "type": "singleLineText", "description": "Airtable record ID of spouse"},
        {"name": "Gender", "type": "singleSelect", "options": {"choices": [{"name": "Male"}, {"name": "Female"}, {"name": "Other"}]}},
        {"name": "IsAlive", "type": "checkbox", "options": {"icon": "check", "color": "greenBright"}},
        {"name": "Branch", "type": "singleLineText", "description": "Family branch name"},
    ]
    create_table("ApprovedMembers", "Published family members visible on the tree", members_fields)

    # 2. Create Pending Submissions table
    pending_fields = [
        {"name": "RawFullName", "type": "singleLineText", "description": "Name as submitted"},
        {"name": "RawFatherName", "type": "singleLineText", "description": "Father name as submitted"},
        {"name": "RawMotherName", "type": "singleLineText", "description": "Mother name as submitted"},
        {"name": "RawSpouseName", "type": "singleLineText", "description": "Spouse name as submitted"},
        {"name": "RawDateOfBirth", "type": "singleLineText", "description": "DOB as submitted"},
        {"name": "RawDateOfDeath", "type": "singleLineText", "description": "DOD as submitted"},
        {"name": "RawLocation", "type": "singleLineText", "description": "Location as submitted"},
        {"name": "RawBurialLocation", "type": "singleLineText", "description": "Burial location as submitted"},
        {"name": "RawBiography", "type": "multilineText", "description": "Bio as submitted"},
        {"name": "RawGender", "type": "singleLineText", "description": "Gender as submitted"},
        {"name": "SubmittedAt", "type": "singleLineText", "description": "Timestamp of submission"},
        # AI-processed fields
        {"name": "CleanFullName", "type": "singleLineText", "description": "AI-cleaned full name"},
        {"name": "CleanFatherName", "type": "singleLineText", "description": "AI-cleaned father name"},
        {"name": "CleanMotherName", "type": "singleLineText", "description": "AI-cleaned mother name"},
        {"name": "CleanSpouseName", "type": "singleLineText", "description": "AI-cleaned spouse name"},
        {"name": "CleanDOB", "type": "singleLineText", "description": "AI-standardized date of birth"},
        {"name": "CleanDOD", "type": "singleLineText", "description": "AI-standardized date of death"},
        {"name": "CleanCity", "type": "singleLineText", "description": "AI-standardized city"},
        {"name": "CleanCountry", "type": "singleLineText", "description": "AI-standardized country"},
        {"name": "CleanBurialLocation", "type": "singleLineText", "description": "AI-standardized burial location"},
        {"name": "CleanGender", "type": "singleSelect", "options": {"choices": [{"name": "Male"}, {"name": "Female"}, {"name": "Other"}]}},
        {"name": "AIMatchedFatherId", "type": "singleLineText", "description": "AI-suggested father record ID"},
        {"name": "AIMatchedMotherId", "type": "singleLineText", "description": "AI-suggested mother record ID"},
        {"name": "AIMatchedSpouseId", "type": "singleLineText", "description": "AI-suggested spouse record ID"},
        {"name": "AIConfidence", "type": "number", "options": {"precision": 2}, "description": "AI confidence score 0-1"},
        {"name": "AIDuplicateFlag", "type": "checkbox", "options": {"icon": "check", "color": "redBright"}, "description": "AI flagged as potential duplicate"},
        {"name": "AINotes", "type": "multilineText", "description": "AI processing notes"},
        {"name": "Status", "type": "singleSelect", "options": {"choices": [{"name": "Pending"}, {"name": "Approved"}, {"name": "Rejected"}]}},
    ]
    create_table("PendingSubmissions", "Raw submissions from Google Forms, processed by AI", pending_fields)

    print("\n" + "=" * 60)
    print("  Setup complete! Your Airtable base is ready.")
    print("=" * 60)


if __name__ == "__main__":
    setup()
