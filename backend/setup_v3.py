"""
Setup script to add the ApprovedEmails table and add AuthorEmail field to Comments.
Run this once to create the email allowlist feature.
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
    payload = {"name": name, "description": description, "fields": fields}
    resp = requests.post(META_URL, headers=HEADERS, json=payload)
    if resp.status_code == 200:
        data = resp.json()
        print(f"[OK] Created table '{name}' with ID: {data['id']}")
        return data
    else:
        print(f"[FAIL] Table '{name}': {resp.status_code} - {resp.text[:200]}")
        return None


def get_tables():
    resp = requests.get(META_URL, headers=HEADERS)
    if resp.status_code == 200:
        return resp.json().get("tables", [])
    return []


def add_fields_to_table(table_id, fields):
    url = f"https://api.airtable.com/v0/meta/bases/{AIRTABLE_BASE_ID}/tables/{table_id}/fields"
    for field in fields:
        resp = requests.post(url, headers=HEADERS, json=field)
        if resp.status_code == 200:
            print(f"  [OK] Added field '{field['name']}'")
        else:
            print(f"  [SKIP] Field '{field['name']}': {resp.text[:150]}")


def setup_v3():
    print("=" * 60)
    print("  Shajra System v3 - Adding ApprovedEmails + Email fields")
    print("=" * 60)

    tables = get_tables()
    table_names = {t["name"]: t for t in tables}

    # ── Create ApprovedEmails table ──────────────────────────
    if "ApprovedEmails" not in table_names:
        print("\n--- Creating ApprovedEmails Table ---")
        email_fields = [
            {"name": "Email", "type": "email", "description": "Family member's approved email address"},
            {"name": "Name", "type": "singleLineText", "description": "Display name of this person"},
            {"name": "Notes", "type": "singleLineText", "description": "Admin notes about this person"},
            {"name": "AddedAt", "type": "singleLineText", "description": "When this email was approved"},
        ]
        create_table("ApprovedEmails", "Allowlist of approved family member emails for commenting", email_fields)
    else:
        print("\n[INFO] ApprovedEmails table already exists — skipping")

    # ── Add AuthorEmail field to Comments table ──────────────
    if "Comments" in table_names:
        print("\n--- Adding AuthorEmail to Comments table ---")
        add_fields_to_table(table_names["Comments"]["id"], [
            {"name": "AuthorEmail", "type": "email", "description": "Email of the person who left the comment"},
        ])
    else:
        print("\n[WARN] Comments table not found — run setup_v2.py first")

    print("\n" + "=" * 60)
    print("  v3 Setup complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Go to your Airtable base")
    print("  2. Open the ApprovedEmails table")
    print("  3. Add your family members' email addresses")
    print("     OR use the admin panel at /admin to manage emails")


if __name__ == "__main__":
    setup_v3()
