"""
Shajra System — Main FastAPI Application v2
"""
import os
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Load .env only if it exists (local dev). On Vercel, env vars come from dashboard.
_env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(_env_path):
    load_dotenv(_env_path)

import cloudinary
import cloudinary.uploader
from requests.exceptions import HTTPError

cloudinary.config(secure=True)

import ai_service
import airtable_client as db
from auth import create_access_token, decode_access_token, verify_admin
from config import get_settings
from write_gates import require_public_writes, require_relationship_writes

app = FastAPI(
    title="Shajra System API",
    description="Family Genealogy Backend — Tree, Map, Comments, Stories, AI Processing",
    version="2.0.0",
)

# CORS — allow frontend to call backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth Dependency ─────────────────────────────────────────────

def get_current_admin(authorization: str = Header(None)):
    """Dependency: validate JWT from Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


# ── Pydantic Models ─────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class MemberCreate(BaseModel):
    FullName: str
    FatherName: Optional[str] = ""
    MotherName: Optional[str] = ""
    SpouseName: Optional[str] = ""
    DateOfBirth: Optional[str] = ""
    DateOfDeath: Optional[str] = ""
    CurrentCity: Optional[str] = ""
    CurrentCountry: Optional[str] = ""
    BurialLocation: Optional[str] = ""
    Latitude: Optional[float] = None
    Longitude: Optional[float] = None
    BurialLatitude: Optional[float] = None
    BurialLongitude: Optional[float] = None
    Biography: Optional[str] = ""
    Autobiography: Optional[str] = ""
    HeritageStory: Optional[str] = ""
    Generation: Optional[int] = None
    FatherRecordId: Optional[str] = ""
    MotherRecordId: Optional[str] = ""
    SpouseRecordId: Optional[str] = ""
    Gender: Optional[str] = ""
    IsAlive: Optional[bool] = True
    Branch: Optional[str] = ""
    Email: Optional[str] = ""
    PhoneNumber: Optional[str] = ""
    ProfileImageUrl: Optional[str] = ""
    CardStyle: Optional[str] = ""

class MemberUpdate(BaseModel):
    FullName: Optional[str] = None
    FatherName: Optional[str] = None
    MotherName: Optional[str] = None
    SpouseName: Optional[str] = None
    DateOfBirth: Optional[str] = None
    DateOfDeath: Optional[str] = None
    CurrentCity: Optional[str] = None
    CurrentCountry: Optional[str] = None
    BurialLocation: Optional[str] = None
    Latitude: Optional[float] = None
    Longitude: Optional[float] = None
    BurialLatitude: Optional[float] = None
    BurialLongitude: Optional[float] = None
    Biography: Optional[str] = None
    Autobiography: Optional[str] = None
    HeritageStory: Optional[str] = None
    Generation: Optional[int] = None
    FatherRecordId: Optional[str] = None
    MotherRecordId: Optional[str] = None
    SpouseRecordId: Optional[str] = None
    Gender: Optional[str] = None
    IsAlive: Optional[bool] = None
    Branch: Optional[str] = None
    Email: Optional[str] = None
    PhoneNumber: Optional[str] = None
    ProfileImageUrl: Optional[str] = None
    CardStyle: Optional[str] = None

class GoogleFormWebhook(BaseModel):
    """Payload from Google Apps Script webhook."""
    fullName: Optional[str] = ""
    fatherName: Optional[str] = ""
    motherName: Optional[str] = ""
    spouseName: Optional[str] = ""
    dateOfBirth: Optional[str] = ""
    dateOfDeath: Optional[str] = ""
    location: Optional[str] = ""
    burialLocation: Optional[str] = ""
    biography: Optional[str] = ""
    gender: Optional[str] = ""
    email: Optional[str] = ""
    phoneNumber: Optional[str] = ""
    profileImage: Optional[str] = ""
    timestamp: Optional[str] = ""

class DirectSubmission(BaseModel):
    """Direct form submission from the frontend (bypasses Google Form)."""
    fullName: str
    fatherName: Optional[str] = ""
    motherName: Optional[str] = ""
    spouseName: Optional[str] = ""
    dateOfBirth: Optional[str] = ""
    dateOfDeath: Optional[str] = ""
    location: Optional[str] = ""
    burialLocation: Optional[str] = ""
    biography: Optional[str] = ""
    gender: Optional[str] = ""
    email: Optional[str] = ""
    phoneNumber: Optional[str] = ""
    profileImage: Optional[str] = ""

class CommentCreate(BaseModel):
    MemberRecordId: str
    MemberName: Optional[str] = ""
    AuthorName: str
    AuthorEmail: str
    CommentText: str

class StoryCreate(BaseModel):
    Title: str
    Content: str
    AuthorName: str
    AuthorEmail: Optional[str] = ""
    MemberRecordId: Optional[str] = ""
    MemberName: Optional[str] = ""
    StoryType: Optional[str] = "Family Heritage"

class PhotoAlbumCreate(BaseModel):
    MemberRecordId: str
    MemberName: Optional[str] = ""
    ImageUrl: str
    Caption: Optional[str] = ""

class ApprovedEmailCreate(BaseModel):
    Email: str
    Name: Optional[str] = ""
    Notes: Optional[str] = ""

# ══════════════════════════════════════════════════════════════
#   PUBLIC ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.get("/")
def root():
    return {"message": "Shajra System API v2 is running", "version": "2.0.0"}


@app.get("/api/health/live")
def health_live():
    return {"status": "ok"}


@app.get("/api/health/ready")
def health_ready():
    settings = get_settings()
    return {
        "status": "ready",
        "environment": settings.app_env,
        "configured": {
            "airtable": bool(settings.airtable_pat and settings.airtable_base_id),
            "groq": bool(settings.groq_api_key),
            "cloudinary": bool(settings.cloudinary_url),
        },
        "writes": {
            "public": settings.public_writes_enabled,
            "relationships": settings.relationship_writes_enabled,
        },
        "normalizedReads": settings.normalized_reads_enabled,
    }


@app.get("/api/members")
def list_members():
    """Get all approved family members (for tree & map)."""
    return db.get_all_members()


@app.get("/api/members/{record_id}")
def get_member(record_id: str):
    """Get a single member by ID."""
    member = db.get_member_by_id(record_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


@app.get("/api/search")
def search_members(q: str = ""):
    """Search members by name."""
    if not q or len(q) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")
    return db.search_members(q)


@app.get("/api/tree")
def get_tree():
    """Build hierarchical tree data with Marital Grouping (no duplicates).
    
    v3: Smart Phase 0 prevents phantom duplicates by checking spouse of linked parent.
        Full spouse data returned (not slim copies) for full-identity rendering.
    """
    members = db.get_all_members()
    lookup = {m["id"]: m for m in members}

    # Helper: create a spouse snapshot that carries all useful fields
    # but avoids circular references (no children/Spouse nesting)
    SPOUSE_FIELDS = [
        "id", "FullName", "Gender", "IsAlive", "ProfileImageUrl",
        "DateOfBirth", "DateOfDeath", "CurrentCity", "CurrentCountry",
        "Biography", "Email", "PhoneNumber", "FatherName", "MotherName",
        "SpouseName", "Generation", "Branch", "CardStyle",
    ]

    def spouse_snapshot(m: dict) -> dict:
        snap = {k: m.get(k, "") for k in SPOUSE_FIELDS}
        snap["_isSpouseRef"] = True
        if m.get("IsPlaceholder"):
            snap["IsPlaceholder"] = True
        return snap

    # Initialize children list; Spouse will be added later
    for m in members:
        m["children"] = []
        m["Spouse"] = None

    # Helper to get string ID from possible list/string field
    def get_sid(field_val):
        if not field_val: return ""
        if isinstance(field_val, list) and len(field_val) > 0:
            return str(field_val[0]).strip()
        return str(field_val).strip()

    # Build a name->member index for smart matching
    name_index = {}
    for m in members:
        fn = (m.get("FullName") or "").strip().lower()
        if fn:
            name_index.setdefault(fn, []).append(m)

    # ── Phase 0: Smart placeholder creation ───────────────────────────────
    # Before creating a placeholder, check:
    #   1. Does a real member with that name already exist? → link directly
    #   2. Is this name the spouse of the other linked parent? → reuse that person
    #   3. Has a placeholder for this name already been created? → reuse
    #   4. Only then create a new placeholder
    placeholders = {}

    def find_or_create_parent(m, name_field, id_field, gender, other_parent_id):
        """Smart parent resolution: tries name matching before creating placeholders."""
        rec_id = get_sid(m.get(id_field))
        if rec_id:
            return  # already linked

        name = (m.get(name_field) or "").strip()
        if not name:
            return  # no name to work with

        name_lower = name.lower()

        # Strategy 1: Check if other parent's spouse matches this name
        if other_parent_id:
            other_parent = lookup.get(other_parent_id)
            if other_parent:
                spouse_name = (other_parent.get("SpouseName") or "").strip().lower()
                spouse_id = get_sid(other_parent.get("SpouseRecordId"))
                # If the other parent has a spouse record, and the spouse's name matches
                if spouse_id and spouse_id in lookup:
                    spouse_member = lookup[spouse_id]
                    sfn = (spouse_member.get("FullName") or "").strip().lower()
                    if sfn == name_lower or name_lower in sfn or sfn in name_lower:
                        m[id_field] = spouse_id
                        return
                # If other parent's SpouseName text matches
                if spouse_name and (spouse_name == name_lower or name_lower in spouse_name or spouse_name in name_lower):
                    if spouse_id and spouse_id in lookup:
                        m[id_field] = spouse_id
                        return

        # Strategy 2: Direct name match against existing members
        matches = name_index.get(name_lower, [])
        for match in matches:
            if match["id"] != m["id"] and match.get("Gender", "") == gender:
                m[id_field] = match["id"]
                return

        # Strategy 3: Fuzzy - check if existing member name contains this name
        for fn_lower, candidates in name_index.items():
            if name_lower in fn_lower or fn_lower in name_lower:
                for match in candidates:
                    if match["id"] != m["id"] and match.get("Gender", "") == gender:
                        m[id_field] = match["id"]
                        return

        # Strategy 4: Reuse existing placeholder
        if name in placeholders:
            m[id_field] = placeholders[name]["id"]
            return

        # Strategy 5: Create new placeholder
        prefix = "f" if gender == "Male" else "m"
        fake_id = f"__ph_{prefix}__{name.replace(' ', '_')}"
        ph = {
            "id": fake_id, "FullName": f"{name} (Unknown)", "Gender": gender,
            "IsAlive": False, "ProfileImageUrl": "", "DateOfBirth": "", "DateOfDeath": "",
            "Generation": max(1, (m.get("Generation") or 2) - 1),
            "FatherRecordId": "", "MotherRecordId": "", "SpouseRecordId": "",
            "FatherName": "", "MotherName": "", "SpouseName": "",
            "CurrentCity": "", "CurrentCountry": "", "Biography": "",
            "children": [], "Spouse": None, "IsPlaceholder": True
        }
        placeholders[name] = ph
        lookup[fake_id] = ph
        m[id_field] = fake_id

    for m in members:
        mo_id = get_sid(m.get("MotherRecordId"))
        # Resolve father (pass mother as other parent for cross-check)
        find_or_create_parent(m, "FatherName", "FatherRecordId", "Male", mo_id)
        # Resolve mother (pass father as other parent for cross-check)
        f_id_after = get_sid(m.get("FatherRecordId"))  # may have been set above
        find_or_create_parent(m, "MotherName", "MotherRecordId", "Female", f_id_after)

    members.extend(placeholders.values())

    # Phase 1: Infer spousal links from shared children
    for m in members:
        father_id = get_sid(m.get("FatherRecordId"))
        mother_id = get_sid(m.get("MotherRecordId"))
        
        if father_id and mother_id and father_id in lookup and mother_id in lookup:
            father = lookup[father_id]
            mother = lookup[mother_id]
            if not father["Spouse"]: father["Spouse"] = spouse_snapshot(mother)
            if not mother["Spouse"]: mother["Spouse"] = spouse_snapshot(father)

    # Phase 2: Spouse linking (Reciprocal & Cross-verified)
    for m in members:
        spouse_id = get_sid(m.get("SpouseRecordId"))
        if spouse_id and spouse_id in lookup:
            if not m.get("Spouse"): m["Spouse"] = spouse_snapshot(lookup[spouse_id])
            target_spouse = lookup[spouse_id]
            if not target_spouse.get("Spouse"):
                target_spouse["Spouse"] = spouse_snapshot(m)

    # Phase 2b: Infer spouse from SpouseName text if no SpouseRecordId
    for m in members:
        if m.get("Spouse"):
            continue
        spouse_name = (m.get("SpouseName") or "").strip().lower()
        if not spouse_name:
            continue
        # Try to find a matching member
        for fn_lower, candidates in name_index.items():
            if spouse_name == fn_lower or spouse_name in fn_lower or fn_lower in spouse_name:
                for candidate in candidates:
                    if candidate["id"] != m["id"] and not candidate.get("Spouse"):
                        m["Spouse"] = spouse_snapshot(candidate)
                        candidate["Spouse"] = spouse_snapshot(m)
                        break
                if m.get("Spouse"):
                    break

    # ── Phase 3: Build parent-child hierarchy ─────────────────────────────
    for m in members:
        father_id = get_sid(m.get("FatherRecordId"))
        mother_id = get_sid(m.get("MotherRecordId"))
        if father_id and father_id in lookup:
            lookup[father_id]["children"].append(m)
        elif mother_id and mother_id in lookup:
            lookup[mother_id]["children"].append(m)

    # ── Phase 4: Merge spouse children into primary member ────────────────
    def merge_spouse_children(node, visited=None):
        if visited is None:
            visited = set()
        if node["id"] in visited:
            return
        visited.add(node["id"])

        if node.get("Spouse"):
            spouse_id = node["Spouse"]["id"]
            if spouse_id in lookup:
                spouse_full = lookup[spouse_id]
                existing_ids = {c["id"] for c in node["children"]}
                for child in spouse_full.get("children", []):
                    if child["id"] not in existing_ids:
                        node["children"].append(child)
        for child in node["children"]:
            merge_spouse_children(child, visited)

    # ── Phase 5: Identify true roots ──────────────────────────────────────
    final_roots = []
    processed_root_ids = set()

    # Pre-mark all spouses of dependent members so they never become roots prematurely
    for m in members:
        father_id = get_sid(m.get("FatherRecordId"))
        mother_id = get_sid(m.get("MotherRecordId"))
        if (father_id and father_id in lookup) or (mother_id and mother_id in lookup):
            if m.get("Spouse"):
                processed_root_ids.add(m["Spouse"]["id"])

    def sort_key(m):
        gen = m.get("Generation", 99)
        has_parents = 0 if (m.get("FatherRecordId") or m.get("MotherRecordId")) else 1
        return (gen, has_parents)

    for m in sorted(members, key=sort_key):
        if m["id"] in processed_root_ids:
            continue
        father_id = get_sid(m.get("FatherRecordId"))
        mother_id = get_sid(m.get("MotherRecordId"))
        is_independent = (not father_id or father_id not in lookup) and \
                         (not mother_id or mother_id not in lookup)
        if is_independent:
            final_roots.append(m)
            processed_root_ids.add(m["id"])
            if m.get("Spouse"):
                processed_root_ids.add(m["Spouse"]["id"])
        else:
            if m.get("Spouse"):
                processed_root_ids.add(m["Spouse"]["id"])

    # Merge spouse children for all nodes in the final tree
    for root in final_roots:
        merge_spouse_children(root)

    return final_roots


CITY_GEODATA = {
    "Karachi": {"lat": 24.8607, "lng": 67.0011, "country": "Pakistan"},
    "Lahore": {"lat": 31.5204, "lng": 74.3587, "country": "Pakistan"},
    "Islamabad": {"lat": 33.6844, "lng": 73.0479, "country": "Pakistan"},
    "Faisalabad": {"lat": 31.4504, "lng": 73.1350, "country": "Pakistan"},
    "Multan": {"lat": 30.1575, "lng": 71.5249, "country": "Pakistan"},
    "London": {"lat": 51.5074, "lng": -0.1278, "country": "UK"},
    "Birmingham": {"lat": 52.4862, "lng": -1.8904, "country": "UK"},
    "Manchester": {"lat": 53.4808, "lng": -2.2426, "country": "UK"},
    "New York": {"lat": 40.7128, "lng": -74.0060, "country": "USA"},
    "Chicago": {"lat": 41.8781, "lng": -87.6298, "country": "USA"},
    "Houston": {"lat": 29.7604, "lng": -95.3698, "country": "USA"},
    "Dubai": {"lat": 25.2048, "lng": 55.2708, "country": "UAE"},
}

@app.get("/api/map-markers")
def get_map_markers():
    """Get all members with location data for the map view, including relationship arcs."""
    members = db.get_all_members()
    markers = []
    arcs = []

    # Helper to get string ID from possible list/string field
    def get_sid(field_val):
        if not field_val: return ""
        if isinstance(field_val, list) and len(field_val) > 0:
            return str(field_val[0]).strip()
        return str(field_val).strip()

    member_lookup = {m["id"]: m for m in members}

    def get_member_coords(member):
        # 1. Direct Coords
        if member.get("Latitude") and member.get("Longitude"):
            return float(member["Latitude"]), float(member["Longitude"])
        
        # 2. City Fallback
        city = member.get("CurrentCity", "").strip().title()
        if city in CITY_GEODATA:
            return CITY_GEODATA[city]["lat"], CITY_GEODATA[city]["lng"]
        
        return None, None

    for m in members:
        mid = m["id"]
        name = m.get("FullName", "")
        lat, lng = get_member_coords(m)

        # Residence marker
        if lat and lng:
            markers.append({
                "id": mid,
                "name": name,
                "type": "residence",
                "lat": lat,
                "lng": lng,
                "city": m.get("CurrentCity", ""),
                "country": m.get("CurrentCountry", ""),
                "gender": m.get("Gender", ""),
                "isAlive": m.get("IsAlive", True),
            })

            # Build arc: connect child to parents if they have coords
            parents = [get_sid(m.get("FatherRecordId")), get_sid(m.get("MotherRecordId"))]
            for pid in parents:
                if pid and pid in member_lookup:
                    parent = member_lookup[pid]
                    p_lat, p_lng = get_member_coords(parent)
                    if p_lat and p_lng:
                        # Only create arc if locations are different (to avoid dots)
                        if abs(lat - p_lat) > 0.05 or abs(lng - p_lng) > 0.05:
                            arcs.append({
                                "startLat": lat,
                                "startLng": lng,
                                "endLat": p_lat,
                                "endLng": p_lng,
                                "label": f"{name} → {parent.get('FullName', '')}",
                                "color": "#c9956c" if parent.get("Gender") == "Male" else "#d9819a",
                            })

        # Burial marker (only if exact coords or city is available)
        b_lat, b_lng = None, None
        if m.get("BurialLatitude") and m.get("BurialLongitude"):
            b_lat, b_lng = float(m["BurialLatitude"]), float(m["BurialLongitude"])
        elif m.get("BurialLocation", "").strip().title() in CITY_GEODATA:
            city = m.get("BurialLocation", "").strip().title()
            b_lat, b_lng = CITY_GEODATA[city]["lat"], CITY_GEODATA[city]["lng"]

        if b_lat and b_lng:
            markers.append({
                "id": mid,
                "name": name,
                "type": "burial",
                "lat": b_lat,
                "lng": b_lng,
                "location": m.get("BurialLocation", ""),
                "gender": m.get("Gender", ""),
                "isAlive": False,
            })

    return {"markers": markers, "arcs": arcs}


# ── Email Verification (public) ──────────────────────────────────

@app.get("/api/verify-email")
def verify_email(email: str = ""):
    """Check if an email is in the approved list."""
    if not email:
        raise HTTPException(status_code=400, detail="Email required")
    approved = db.is_email_approved(email)
    return {"approved": approved, "email": email}


# ── Comments (public read, approved-email write) ──────────────────

@app.get("/api/comments/{member_record_id}")
def get_comments(member_record_id: str):
    """Get all comments for a member."""
    return db.get_comments_for_member(member_record_id)


@app.post("/api/comments")
def post_comment(comment: CommentCreate, _=Depends(require_public_writes)):
    """Post a comment. Only approved emails can comment."""
    if not db.is_email_approved(comment.AuthorEmail):
        raise HTTPException(
            status_code=403,
            detail="Your email is not on the approved family members list. Contact the admin to get access."
        )
    fields = {
        "MemberRecordId": comment.MemberRecordId,
        "MemberName": comment.MemberName,
        "AuthorName": comment.AuthorName,
        "AuthorEmail": comment.AuthorEmail,
        "CommentText": comment.CommentText,
        "CreatedAt": datetime.now(timezone.utc).isoformat(),
    }
    return db.create_comment(fields)


# ── Stories (public read & write) ────────────────────────────────

@app.get("/api/stories")
def get_all_stories():
    """Get all stories."""
    return db.get_all_stories()


@app.get("/api/stories/family")
def get_family_stories():
    """Get stories not tied to a specific member."""
    return db.get_family_stories()


@app.get("/api/stories/member/{member_record_id}")
def get_stories_for_member(member_record_id: str):
    """Get stories for a specific member."""
    return db.get_stories_for_member(member_record_id)


@app.post("/api/stories")
def post_story(story: StoryCreate, _=Depends(require_public_writes)):
    """Submit a new story."""
    fields = {
        "Title": story.Title,
        "Content": story.Content,
        "AuthorName": story.AuthorName,
        "AuthorEmail": story.AuthorEmail,
        "MemberRecordId": story.MemberRecordId,
        "MemberName": story.MemberName,
        "StoryType": story.StoryType,
        "CreatedAt": datetime.now(timezone.utc).isoformat(),
    }
    # Remove empty optional fields
    fields = {k: v for k, v in fields.items() if v not in ("", None)}
    return db.create_story(fields)


# ── Photo Albums (public read) ────────────────────────────────────

@app.get("/api/albums")
def get_all_albums():
    """Get all photo albums."""
    return db.get_all_albums()


@app.get("/api/albums/member/{member_record_id}")
def get_albums_for_member(member_record_id: str):
    """Get albums for a specific member."""
    return db.get_albums_for_member(member_record_id)


@app.post("/api/albums")
def post_album(album: PhotoAlbumCreate, _=Depends(require_public_writes)):
    """Post an album/photo. Uploads to Airtable as an Attachment."""
    fields = album.model_dump(exclude_none=True)
    # Convert string URL into Airtable Attachment array format
    if "ImageUrl" in fields:
        fields["ImageUrl"] = [{"url": fields["ImageUrl"]}]
    fields["CreatedAt"] = datetime.now(timezone.utc).isoformat()
    return db.create_album(fields)


# ══════════════════════════════════════════════════════════════
#   WEBHOOK — Google Form Submissions
# ══════════════════════════════════════════════════════════════

@app.post("/api/webhook/google-form")
async def receive_google_form(payload: GoogleFormWebhook, _=Depends(require_public_writes)):
    """
    Receive data from Google Apps Script, run through AI, store as pending.
    """
    raw_data = {
        "RawFullName": payload.fullName,
        "RawFatherName": payload.fatherName,
        "RawMotherName": payload.motherName,
        "RawSpouseName": payload.spouseName,
        "RawDateOfBirth": payload.dateOfBirth,
        "RawDateOfDeath": payload.dateOfDeath,
        "RawLocation": payload.location,
        "RawBurialLocation": payload.burialLocation,
        "RawBiography": payload.biography,
        "RawGender": payload.gender,
        "RawEmail": payload.email,
        "RawPhoneNumber": payload.phoneNumber,
        "RawProfileImage": payload.profileImage,
        "SubmittedAt": payload.timestamp or datetime.now(timezone.utc).isoformat(),
    }
    result = ai_service.process_and_store_submission(raw_data)
    return {"status": "success", "message": "Submission received and AI-processed", "record": result}


@app.post("/api/submit")
async def direct_submit(payload: DirectSubmission, _=Depends(require_public_writes)):
    """
    Direct submission from the frontend form (same AI pipeline as Google Form).
    Allows submitting without Google Forms.
    """
    raw_data = {
        "RawFullName": payload.fullName,
        "RawFatherName": payload.fatherName,
        "RawMotherName": payload.motherName,
        "RawSpouseName": payload.spouseName,
        "RawDateOfBirth": payload.dateOfBirth,
        "RawDateOfDeath": payload.dateOfDeath,
        "RawLocation": payload.location,
        "RawBurialLocation": payload.burialLocation,
        "RawBiography": payload.biography,
        "RawGender": payload.gender,
        "RawEmail": payload.email,
        "RawPhoneNumber": payload.phoneNumber,
        "RawProfileImage": payload.profileImage,
        "SubmittedAt": datetime.now(timezone.utc).isoformat(),
    }
    try:
        result = ai_service.process_and_store_submission(raw_data)
        return {
            "status": "success",
            "message": "Your submission has been received. Our AI is processing it and an admin will review it shortly.",
            "pendingId": result.get("id"),
        }
    except HTTPError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Airtable Schema Error in PendingSubmissions: {e.response.text}",
        ) from e

@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...), _=Depends(require_public_writes)):
    """
    Publicly accessible image uploader for form submissions and admin edits.
    Uploads precisely to Cloudinary and returns the secure public URL perfectly suited for Airtable.
    """
    try:
        if file.content_type not in ["image/jpeg", "image/png", "image/webp", "image/gif", "image/jpg"]:
            raise HTTPException(status_code=400, detail="Invalid file type. Only standard images are allowed.")
            
        file_content = await file.read()
        res = cloudinary.uploader.upload(file_content, folder="shajra_system")
        return {"url": res.get("secure_url")}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Cloudinary upload failed.") from e


# ══════════════════════════════════════════════════════════════
#   ADMIN ENDPOINTS (JWT Protected)
# ══════════════════════════════════════════════════════════════

@app.post("/api/admin/login")
def admin_login(req: LoginRequest):
    """Admin login — returns JWT token."""
    if not verify_admin(req.username, req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": req.username, "role": "admin"})
    return {"access_token": token, "token_type": "bearer"}


@app.get("/api/admin/pending")
def list_pending(admin=Depends(get_current_admin)):
    """Get all pending submissions."""
    return db.get_all_pending()



# ── Change History & Undo System ──────────────────────────────────────

# In-memory change history (lost on restart — for session undo only)
_change_history: list = []
_MAX_HISTORY = 50


def _snapshot_member(record_id: str) -> dict | None:
    """Take a snapshot of a member before mutation."""
    try:
        return db.get_member_by_id(record_id)
    except Exception:  # noqa: BLE001 - Preserve the v1 missing-member snapshot fallback.
        return None


def _push_history(action: str, record_id: str, before: dict | None, after: dict | None):
    """Push a change to the undo stack."""
    _change_history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "record_id": record_id,
        "before": before,
        "after": after,
    })
    # Trim to max size
    while len(_change_history) > _MAX_HISTORY:
        _change_history.pop(0)


@app.get("/api/admin/pending/status/{status}")
def list_pending_by_status(status: str, admin=Depends(get_current_admin)):
    """Get pending submissions filtered by status."""
    return db.get_pending_by_status(status)


@app.post("/api/admin/approve/{record_id}")
def approve_submission(
    record_id: str,
    admin=Depends(get_current_admin),
    _=Depends(require_relationship_writes),
):
    """
    Approve a pending submission: create an approved member, mark submission as approved.
    """
    pending_records = db.get_all_pending()
    pending = None
    for p in pending_records:
        if p["id"] == record_id:
            pending = p
            break

    if not pending:
        raise HTTPException(status_code=404, detail="Pending submission not found")

    # Create approved member from AI-cleaned data
    member_fields = {
        "FullName": pending.get("CleanFullName", pending.get("RawFullName", "")),
        "FatherName": pending.get("CleanFatherName", ""),
        "MotherName": pending.get("CleanMotherName", ""),
        "SpouseName": pending.get("CleanSpouseName", ""),
        "DateOfBirth": pending.get("CleanDOB", ""),
        "DateOfDeath": pending.get("CleanDOD", ""),
        "CurrentCity": pending.get("CleanCity", ""),
        "CurrentCountry": pending.get("CleanCountry", ""),
        "BurialLocation": pending.get("CleanBurialLocation", ""),
        "Gender": pending.get("CleanGender", ""),
        "Email": pending.get("CleanEmail", pending.get("RawEmail", "")),
        "PhoneNumber": pending.get("CleanPhoneNumber", pending.get("RawPhoneNumber", "")),
        "ProfileImageUrl": pending.get("CleanProfileImage", pending.get("RawProfileImage", "")),
        "Biography": pending.get("RawBiography", ""),
        "FatherRecordId": pending.get("AIMatchedFatherId", ""),
        "MotherRecordId": pending.get("AIMatchedMotherId", ""),
        "SpouseRecordId": pending.get("AIMatchedSpouseId", ""),
    }

    # Determine IsAlive: only mark as dead if CleanDOD contains an actual date-like value
    clean_dod = (pending.get("CleanDOD", "") or "").strip().lower()
    has_real_dod = bool(clean_dod) and clean_dod not in ("", "n/a", "unknown", "none", "na", "-")
    member_fields["IsAlive"] = not has_real_dod

    # Remove None items, keep everything else so we can clear Text fields
    member_fields = {k: v for k, v in member_fields.items() if v is not None}

    new_member = db.create_member(member_fields)
    db.update_pending(record_id, {"Status": "Approved"})

    # Record in history
    _push_history("approve", new_member["id"], None, new_member)

    return {"status": "approved", "member": new_member}


@app.get("/api/admin/integrations")
def admin_integrations(admin=Depends(get_current_admin)):
    settings = get_settings()
    return {
        "groqConfigured": bool(settings.groq_api_key),
        "cloudinaryConfigured": bool(settings.cloudinary_url),
        "coordinationConfigured": False,
    }

@app.post("/api/admin/reject/{record_id}")
def reject_submission(record_id: str, admin=Depends(get_current_admin)):
    """Reject a pending submission."""
    db.update_pending(record_id, {"Status": "Rejected"})
    return {"status": "rejected"}


@app.post("/api/admin/members")
def admin_create_member(
    member: MemberCreate,
    admin=Depends(get_current_admin),
    _=Depends(require_relationship_writes),
):
    """Admin directly creates an approved member."""
    try:
        fields = member.model_dump(exclude_none=True)
        new_member = db.create_member(fields)
        _push_history("create", new_member["id"], None, new_member)
        return new_member
    except HTTPError as e:
        raise HTTPException(status_code=422, detail=f"Airtable Schema Error: {e.response.text}") from e

@app.put("/api/admin/members/{record_id}")
def admin_update_member(
    record_id: str,
    member: MemberUpdate,
    admin=Depends(get_current_admin),
    _=Depends(require_relationship_writes),
):
    """Admin updates an approved member."""
    try:
        before = _snapshot_member(record_id)
        fields = member.model_dump(exclude_none=True)
        result = db.update_member(record_id, fields)
        _push_history("update", record_id, before, result)
        return result
    except HTTPError as e:
        raise HTTPException(status_code=422, detail=f"Airtable Schema Error: {e.response.text}") from e


@app.delete("/api/admin/members/{record_id}")
def admin_delete_member(
    record_id: str,
    admin=Depends(get_current_admin),
    _=Depends(require_relationship_writes),
):
    """Admin deletes an approved member. Snapshots before for undo."""
    before = _snapshot_member(record_id)
    db.delete_member(record_id)
    _push_history("delete", record_id, before, None)
    return {"status": "deleted"}


# ── Undo / Revert Endpoint ────────────────────────────────────────────

@app.get("/api/admin/history")
def get_change_history(admin=Depends(get_current_admin)):
    """Get the change history stack (most recent first)."""
    return list(reversed(_change_history))

@app.post("/api/admin/undo")
def undo_last_change(
    admin=Depends(get_current_admin), _=Depends(require_relationship_writes)
):
    """Undo the most recent admin change."""
    if not _change_history:
        raise HTTPException(status_code=404, detail="No changes to undo")
    
    entry = _change_history.pop()
    action = entry["action"]
    record_id = entry["record_id"]
    before = entry["before"]
    
    try:
        if action == "create":
            # Undo create → delete the record
            db.delete_member(record_id)
            return {"status": "undone", "action": "Deleted created member", "record_id": record_id}
        
        elif action == "update":
            # Undo update → restore previous fields
            if before:
                restore_fields = {k: v for k, v in before.items() if k != "id"}
                db.update_member(record_id, restore_fields)
                return {"status": "undone", "action": f"Restored {before.get('FullName', record_id)} to previous state"}
            raise HTTPException(status_code=500, detail="No snapshot available to restore")
        
        elif action == "delete":
            # Undo delete → recreate the record with original data
            if before:
                restore_fields = {k: v for k, v in before.items() if k != "id"}
                new_rec = db.create_member(restore_fields)
                return {"status": "undone", "action": f"Restored deleted member {before.get('FullName', '')}", "new_id": new_rec["id"]}
            raise HTTPException(status_code=500, detail="No snapshot available to restore")
        
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
    
    except Exception as e:
        # Put entry back if undo failed
        _change_history.append(entry)
        raise HTTPException(status_code=500, detail="Undo failed.") from e


# ── Manual Heal Endpoint ──────────────────────────────────────────────

@app.post("/api/admin/heal")
def manual_heal(admin=Depends(get_current_admin)):
    raise HTTPException(
        status_code=410,
        detail={
            "code": "SELF_HEAL_REMOVED",
            "message": "Automatic graph healing has been removed.",
        },
    )


# ── Admin: Approved Emails ────────────────────────────────────────

@app.get("/api/admin/approved-emails")
def list_approved_emails(admin=Depends(get_current_admin)):
    """List all approved family member emails."""
    return db.get_approved_emails()


@app.post("/api/admin/approved-emails")
def add_approved_email(payload: ApprovedEmailCreate, admin=Depends(get_current_admin)):
    """Add an approved family email."""
    fields = {
        "Email": payload.Email.lower().strip(),
        "Name": payload.Name,
        "Notes": payload.Notes,
        "AddedAt": datetime.now(timezone.utc).isoformat(),
    }
    fields = {k: v for k, v in fields.items() if v not in ("", None)}
    return db.add_approved_email(fields)


@app.delete("/api/admin/approved-emails/{record_id}")
def remove_approved_email(record_id: str, admin=Depends(get_current_admin)):
    """Remove an approved email."""
    db.remove_approved_email(record_id)
    return {"status": "removed"}


# ── Admin: Comments Moderation ─────────────────────────────────────

@app.get("/api/admin/comments")
def list_all_comments(admin=Depends(get_current_admin)):
    """List all comments for moderation."""
    return db.get_all_comments()


@app.delete("/api/admin/comments/{record_id}")
def delete_comment(record_id: str, admin=Depends(get_current_admin)):
    """Admin deletes a comment."""
    db.delete_comment(record_id)
    return {"status": "deleted"}


# ── Admin: Stories Moderation ──────────────────────────────────────

@app.delete("/api/admin/stories/{record_id}")
def delete_story(record_id: str, admin=Depends(get_current_admin)):
    """Admin deletes a story."""
    db.delete_story(record_id)
    return {"status": "deleted"}


# ══════════════════════════════════════════════════════════════
#   RUN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
