"""
Shajra System — Airtable Data Access Layer v2
CRUD operations for all tables.
"""
from pyairtable import Api
from config import AIRTABLE_PAT, AIRTABLE_BASE_ID, APPROVED_MEMBERS_TABLE, PENDING_SUBMISSIONS_TABLE, APPROVED_EMAILS_TABLE

api = Api(AIRTABLE_PAT)

members_table = api.table(AIRTABLE_BASE_ID, APPROVED_MEMBERS_TABLE)
pending_table = api.table(AIRTABLE_BASE_ID, PENDING_SUBMISSIONS_TABLE)
comments_table = api.table(AIRTABLE_BASE_ID, "Comments")
stories_table = api.table(AIRTABLE_BASE_ID, "Stories")
albums_table = api.table(AIRTABLE_BASE_ID, "PhotoAlbums")
approved_emails_table = api.table(AIRTABLE_BASE_ID, APPROVED_EMAILS_TABLE)


def _flatten(r):
    fields = r.get("fields", {})
    # For linked records, Airtable returns a list of IDs.
    # We flatten these into strings for our internal search/lookup logic.
    linked_fields = ["FatherRecordId", "MotherRecordId", "SpouseRecordId"]
    for f in linked_fields:
        val = fields.get(f)
        if isinstance(val, list) and len(val) > 0:
            fields[f] = val[0]
        elif isinstance(val, list):
            fields[f] = ""

    # Ensure critical fields have safe defaults
    res = {"id": r["id"], **fields}
    if "FullName" not in res: res["FullName"] = "Unknown"
    if "Gender" not in res: res["Gender"] = "Male"
    if "IsAlive" not in res: res["IsAlive"] = True
    
    return res


# ── Approved Members ──────────────────────────────────────────

def get_all_members():
    records = members_table.all()
    return [_flatten(r) for r in records]


def get_member_by_id(record_id: str):
    try:
        return _flatten(members_table.get(record_id))
    except Exception:
        return None


def create_member(fields: dict):
    return _flatten(members_table.create(fields))


def update_member(record_id: str, fields: dict):
    return _flatten(members_table.update(record_id, fields))


def delete_member(record_id: str):
    members_table.delete(record_id)
    return True


def search_members(query: str):
    formula = f"FIND(LOWER('{query}'), LOWER({{FullName}}))"
    return [_flatten(r) for r in members_table.all(formula=formula)]


def get_members_by_filter(field: str, value: str):
    formula = f"{{{field}}} = '{value}'"
    return [_flatten(r) for r in members_table.all(formula=formula)]


# ── Pending Submissions ──────────────────────────────────────

def get_all_pending():
    return [_flatten(r) for r in pending_table.all()]


def get_pending_by_status(status: str = "Pending"):
    formula = f"{{Status}} = '{status}'"
    return [_flatten(r) for r in pending_table.all(formula=formula)]


def create_pending(fields: dict):
    return _flatten(pending_table.create(fields))


def update_pending(record_id: str, fields: dict):
    return _flatten(pending_table.update(record_id, fields))


def delete_pending(record_id: str):
    pending_table.delete(record_id)
    return True


# ── Comments ──────────────────────────────────────────────────

def get_comments_for_member(member_record_id: str):
    formula = f"{{MemberRecordId}} = '{member_record_id}'"
    return [_flatten(r) for r in comments_table.all(formula=formula)]


def get_all_comments():
    return [_flatten(r) for r in comments_table.all()]


def create_comment(fields: dict):
    return _flatten(comments_table.create(fields))


def delete_comment(record_id: str):
    comments_table.delete(record_id)
    return True


# ── Stories ───────────────────────────────────────────────────

def get_all_stories():
    return [_flatten(r) for r in stories_table.all()]


def get_family_stories():
    """Stories not tied to a specific member."""
    formula = "OR({MemberRecordId} = '', {MemberRecordId} = BLANK())"
    return [_flatten(r) for r in stories_table.all(formula=formula)]


def get_stories_for_member(member_record_id: str):
    formula = f"{{MemberRecordId}} = '{member_record_id}'"
    return [_flatten(r) for r in stories_table.all(formula=formula)]


def create_story(fields: dict):
    return _flatten(stories_table.create(fields))


def update_story(record_id: str, fields: dict):
    return _flatten(stories_table.update(record_id, fields))


def delete_story(record_id: str):
    stories_table.delete(record_id)
    return True


# ── Photo Albums ──────────────────────────────────────────────

def get_all_albums():
    return [_flatten(r) for r in albums_table.all()]


def get_albums_for_member(member_record_id: str):
    formula = f"{{MemberRecordId}} = '{member_record_id}'"
    return [_flatten(r) for r in albums_table.all(formula=formula)]


def create_album(fields: dict):
    return _flatten(albums_table.create(fields))


def update_album(record_id: str, fields: dict):
    return _flatten(albums_table.update(record_id, fields))


def delete_album(record_id: str):
    albums_table.delete(record_id)
    return True


# ── Approved Emails ───────────────────────────────────────────

def get_approved_emails():
    return [_flatten(r) for r in approved_emails_table.all()]


def is_email_approved(email: str) -> bool:
    formula = f"LOWER({{Email}}) = LOWER('{email}')"
    results = approved_emails_table.all(formula=formula)
    return len(results) > 0


def add_approved_email(fields: dict):
    return _flatten(approved_emails_table.create(fields))


def remove_approved_email(record_id: str):
    approved_emails_table.delete(record_id)
    return True
