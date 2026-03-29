"""
Setup script to add new tables: Comments, Stories, and update ApprovedMembers.
Run this after the initial setup to add new features.
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


def add_fields_to_table(table_id, fields):
    """Add new fields to an existing table."""
    url = f"https://api.airtable.com/v0/meta/bases/{AIRTABLE_BASE_ID}/tables/{table_id}/fields"
    for field in fields:
        resp = requests.post(url, headers=HEADERS, json=field)
        if resp.status_code == 200:
            print(f"  [OK] Added field '{field['name']}'")
        else:
            print(f"  [SKIP] Field '{field['name']}': {resp.text[:150]}")


def get_tables():
    """Get all tables to find IDs."""
    url = f"https://api.airtable.com/v0/meta/bases/{AIRTABLE_BASE_ID}/tables"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 200:
        return resp.json().get("tables", [])
    return []


def setup_v2():
    print("=" * 60)
    print("  Shajra System v2 - Adding Comments, Stories, Albums")
    print("=" * 60)

    # Find existing ApprovedMembers table ID
    tables = get_tables()
    members_table = None
    for t in tables:
        if t["name"] == "ApprovedMembers":
            members_table = t
            break

    if members_table:
        print(f"\n[INFO] Found ApprovedMembers table: {members_table['id']}")
        # Add new fields
        new_fields = [
            {"name": "ProfilePhoto", "type": "multipleAttachments", "description": "Profile picture"},
            {"name": "Autobiography", "type": "multilineText", "description": "Detailed life story"},
            {"name": "HeritageStory", "type": "multilineText", "description": "Individual heritage story"},
        ]
        add_fields_to_table(members_table["id"], new_fields)
    else:
        print("[WARN] ApprovedMembers table not found!")

    # Create Comments table
    print("\n--- Creating Comments Table ---")
    comments_fields = [
        {"name": "CommentText", "type": "multilineText", "description": "The comment content"},
        {"name": "AuthorName", "type": "singleLineText", "description": "Name of person who left the comment"},
        {"name": "MemberRecordId", "type": "singleLineText", "description": "Airtable record ID of the member this comment is about"},
        {"name": "MemberName", "type": "singleLineText", "description": "Name of the member (denormalized for display)"},
        {"name": "CreatedAt", "type": "singleLineText", "description": "Timestamp of comment creation"},
    ]
    create_table("Comments", "Comments left by family members on profiles", comments_fields)

    # Create Stories table
    print("\n--- Creating Stories Table ---")
    stories_fields = [
        {"name": "Title", "type": "singleLineText", "description": "Story title"},
        {"name": "Content", "type": "multilineText", "description": "Full story content"},
        {"name": "AuthorName", "type": "singleLineText", "description": "Who wrote this story"},
        {"name": "MemberRecordId", "type": "singleLineText", "description": "If about a specific member (empty = family-wide)"},
        {"name": "MemberName", "type": "singleLineText", "description": "Name of the member (if individual story)"},
        {"name": "StoryType", "type": "singleSelect", "options": {"choices": [{"name": "Family Heritage"}, {"name": "Individual"}, {"name": "Memory"}, {"name": "Tradition"}]}},
        {"name": "Photos", "type": "multipleAttachments", "description": "Photos attached to this story"},
        {"name": "CreatedAt", "type": "singleLineText", "description": "Timestamp"},
    ]
    create_table("Stories", "Family heritage stories and individual life stories", stories_fields)

    # Create PhotoAlbums table
    print("\n--- Creating PhotoAlbums Table ---")
    albums_fields = [
        {"name": "AlbumName", "type": "singleLineText", "description": "Name of the album"},
        {"name": "Description", "type": "multilineText", "description": "Album description"},
        {"name": "MemberRecordId", "type": "singleLineText", "description": "Member this album belongs to (empty = family album)"},
        {"name": "MemberName", "type": "singleLineText", "description": "Member name (denormalized)"},
        {"name": "Photos", "type": "multipleAttachments", "description": "All photos in the album"},
        {"name": "CreatedAt", "type": "singleLineText", "description": "Timestamp"},
    ]
    create_table("PhotoAlbums", "Photo albums for members and family events", albums_fields)

    print("\n" + "=" * 60)
    print("  v2 Setup complete!")
    print("=" * 60)


if __name__ == "__main__":
    setup_v2()
