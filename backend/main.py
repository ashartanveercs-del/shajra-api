"""
Shajra System — Main FastAPI Application v2
"""
import os
import re
import unicodedata
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
def search_members(q: str = "", city: str = "", branch: str = "", generation: str = ""):
    """Search members by name, with optional city/branch/generation filters.

    Name search is delegated to Airtable when a query is supplied; otherwise all
    members are loaded and filtered server-side.
    """
    q = (q or "").strip()
    city = (city or "").strip()
    branch = (branch or "").strip()
    generation = (generation or "").strip()

    if len(q) >= 2:
        members = db.search_members(q)
    else:
        members = db.get_all_members()

    results = []
    for m in members:
        if city and (m.get("CurrentCity") or "").strip() != city:
            continue
        if branch and (m.get("Branch") or "").strip() != branch:
            continue
        if generation and str(m.get("Generation") or "") != generation:
            continue
        results.append(m)

    return results


@app.get("/api/tree")
def get_tree():
    """Build hierarchical tree data with Marital Grouping (no duplicates).

    v4: Normalized name matching (casefold + diacritics + trailing-word strip,
        never first-name-only), spouse nodes created from name text with their
        exact name (no '(Unknown)' phantom placeholders), and always-reciprocal
        spouse links.
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
        if not field_val:
            return ""
        if isinstance(field_val, list) and len(field_val) > 0:
            return str(field_val[0]).strip()
        return str(field_val).strip()

    # ── Name normalization ────────────────────────────────────────────
    def normalize_name(name) -> str:
        """Casefold, strip diacritics, collapse whitespace."""
        if not name:
            return ""
        s = unicodedata.normalize("NFKD", str(name))
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", s.casefold().strip())

    def name_keys(name) -> list:
        """Matching keys from most-specific to least-specific.

        Progressively strips trailing words but never drops below two words,
        so a bare first name can never match (avoids false positives).
        Single-word names only ever match on the full name.
        """
        n = normalize_name(name)
        if not n:
            return []
        words = n.split()
        if len(words) <= 2:
            return [n]
        keys = []
        while len(words) >= 2:
            keys.append(" ".join(words))
            words = words[:-1]
        return keys

    # Build a normalized name -> member index for smart matching
    name_index: dict[str, list] = {}
    for m in members:
        for key in name_keys(m.get("FullName")):
            name_index.setdefault(key, []).append(m)

    # Registry of name-derived nodes (created from name text), keyed by
    # normalized name so the same person referenced from multiple places
    # resolves to a single node.
    name_nodes: dict[str, dict] = {}

    def find_member_by_name(name, exclude_id=None, gender=None):
        # Iterate most-specific → least-specific keys. Only link when a key
        # matches EXACTLY ONE candidate; multiple candidates is ambiguous and
        # must never be guessed (avoids attaching the wrong same-name relative).
        for key in name_keys(name):
            candidates = [
                c for c in name_index.get(key, [])
                if not (exclude_id and c["id"] == exclude_id)
                and not (gender and c.get("Gender") and c.get("Gender") != gender)
            ]
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                return None
        return None

    def make_name_node(name, gender) -> dict:
        """Create (or reuse) a node for a person known only by name text.

        The node carries the exact name — never an '(Unknown)' suffix — so a
        spouse/parent referenced by name renders with their real name.
        """
        n_key = normalize_name(name)
        if n_key in name_nodes:
            return name_nodes[n_key]
        fake_id = f"__name__{n_key.replace(' ', '_')}"
        node = {
            "id": fake_id, "FullName": name.strip(), "Gender": gender,
            "IsAlive": False, "ProfileImageUrl": "", "DateOfBirth": "",
            "DateOfDeath": "", "Generation": 99,
            "FatherRecordId": "", "MotherRecordId": "", "SpouseRecordId": "",
            "FatherName": "", "MotherName": "", "SpouseName": "",
            "CurrentCity": "", "CurrentCountry": "", "Biography": "",
            "children": [], "Spouse": None, "IsPlaceholder": True,
        }
        name_nodes[n_key] = node
        lookup[fake_id] = node
        return node

    def resolve_by_name(name, gender, exclude_id=None):
        if not name or not name.strip():
            return None
        m = find_member_by_name(name, exclude_id=exclude_id, gender=gender)
        if m:
            return m
        return make_name_node(name, gender)

    def opposite_gender(gender):
        if gender == "Male":
            return "Female"
        if gender == "Female":
            return "Male"
        return ""

    def link_spouses(a, b):
        """Reciprocally link two people as spouses (never one-sided)."""
        if not a.get("Spouse"):
            a["Spouse"] = spouse_snapshot(b)
        if not b.get("Spouse"):
            b["Spouse"] = spouse_snapshot(a)

    # ── Phase 0: Spouse linking (record id, then name text) ───────────
    # Resolved first so parent resolution can cross-reference spouses.
    for m in members:
        if m.get("Spouse"):
            continue
        spouse_id = get_sid(m.get("SpouseRecordId"))
        if spouse_id and spouse_id in lookup:
            link_spouses(m, lookup[spouse_id])
            continue
        spouse_name = (m.get("SpouseName") or "").strip()
        if not spouse_name:
            continue
        spouse_node = resolve_by_name(
            spouse_name, opposite_gender(m.get("Gender", "")), exclude_id=m["id"]
        )
        if spouse_node:
            link_spouses(m, spouse_node)

    # Add name-derived nodes to the member set for hierarchy building
    members.extend(name_nodes.values())

    # ── Phase 1: Parent resolution (name match + spouse cross-check) ──
    def resolve_parent(m, name_field, id_field, gender, other_parent_id):
        rec_id = get_sid(m.get(id_field))
        if rec_id and rec_id in lookup:
            return rec_id
        name = (m.get(name_field) or "").strip()
        if not name:
            return rec_id or ""
        # Cross-check: if the other parent's spouse matches this name, reuse it
        if other_parent_id and other_parent_id in lookup:
            other = lookup[other_parent_id]
            osp = other.get("Spouse")
            if osp:
                osid = get_sid(osp.get("id"))
                osname = (osp.get("FullName") or "").strip()
                if osid and osname and name_keys(name) and (
                    set(name_keys(name)) & set(name_keys(osname))
                ):
                    return osid
        node = resolve_by_name(name, gender, exclude_id=m["id"])
        if node:
            return node["id"]
        return rec_id or ""

    for m in members:
        mo_id = get_sid(m.get("MotherRecordId"))
        m["FatherRecordId"] = resolve_parent(m, "FatherName", "FatherRecordId", "Male", mo_id)
        fa_id = get_sid(m.get("FatherRecordId"))
        m["MotherRecordId"] = resolve_parent(m, "MotherName", "MotherRecordId", "Female", fa_id)

    # Any name nodes created during parent resolution need to be included too
    existing_ids = {m["id"] for m in members}
    for node in name_nodes.values():
        if node["id"] not in existing_ids:
            members.append(node)

    # ── Phase 2: Infer spousal links from shared children ─────────────
    for m in members:
        father_id = get_sid(m.get("FatherRecordId"))
        mother_id = get_sid(m.get("MotherRecordId"))
        if father_id and mother_id and father_id in lookup and mother_id in lookup:
            link_spouses(lookup[father_id], lookup[mother_id])

    # ── Phase 2.5: Break parent-link cycles ─────────────────────────────
    # A parent-link cycle (e.g. A.FatherRecordId=B and B.FatherRecordId=A) would
    # otherwise make every member in the cycle "dependent", so phase 5 silently
    # drops them all. Break the back-edge so no member ever vanishes: the
    # cycle-member becomes a root (or stays under a real ancestor) instead.
    def _parent_record_ids(m):
        pids = []
        for pid in (get_sid(m.get("FatherRecordId")), get_sid(m.get("MotherRecordId"))):
            if pid and pid in lookup:
                pids.append(pid)
        return pids

    def _in_parent_cycle(mid):
        # Is `mid` reachable from itself by walking up parent record links?
        seen = set()
        stack = [p for p in _parent_record_ids(lookup.get(mid) or {})]
        while stack:
            cur = stack.pop()
            if cur == mid:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(_parent_record_ids(lookup.get(cur) or {}))
        return False

    for m in members:
        if _in_parent_cycle(m["id"]):
            m["FatherRecordId"] = ""
            m["MotherRecordId"] = ""

    # ── Phase 3: Build parent-child hierarchy ─────────────────────────
    for m in members:
        father_id = get_sid(m.get("FatherRecordId"))
        mother_id = get_sid(m.get("MotherRecordId"))
        if father_id and father_id in lookup:
            lookup[father_id]["children"].append(m)
        elif mother_id and mother_id in lookup:
            lookup[mother_id]["children"].append(m)

    # ── Phase 4: Merge spouse children into primary member ────────────
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
                existing = {c["id"] for c in node["children"]}
                for child in spouse_full.get("children", []):
                    if child["id"] not in existing:
                        node["children"].append(child)
        for child in node["children"]:
            merge_spouse_children(child, visited)

    # ── Phase 5: Identify true roots ──────────────────────────────────
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
        gen_val = m.get("Generation")
        gen = gen_val if isinstance(gen_val, (int, float)) else 99
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
        if not field_val:
            return ""
        if isinstance(field_val, list) and len(field_val) > 0:
            return str(field_val[0]).strip()
        return str(field_val).strip()

    def normalize_name(name) -> str:
        """Casefold, strip diacritics, collapse whitespace for name matching."""
        if not name:
            return ""
        s = unicodedata.normalize("NFKD", str(name))
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", s.casefold().strip())

    def normalize_city(value) -> str:
        """Strip everything after the first comma, e.g. 'Karachi, Pakistan' -> 'Karachi'."""
        if not value:
            return ""
        return str(value).split(",")[0].strip().title()

    member_lookup = {m["id"]: m for m in members}

    # Name -> member index used to resolve parents by name when record IDs are missing.
    name_index = {}
    for m in members:
        key = normalize_name(m.get("FullName"))
        if key:
            name_index.setdefault(key, []).append(m)

    def city_coords(city_value):
        city = normalize_city(city_value)
        if city in CITY_GEODATA:
            return CITY_GEODATA[city]["lat"], CITY_GEODATA[city]["lng"]
        return None

    def get_burial_coords(member):
        if member.get("BurialLatitude") and member.get("BurialLongitude"):
            return float(member["BurialLatitude"]), float(member["BurialLongitude"])
        return city_coords(member.get("BurialLocation")) or (None, None)

    def get_member_coords(member):
        # 1. Direct coordinates
        if member.get("Latitude") and member.get("Longitude"):
            return float(member["Latitude"]), float(member["Longitude"])
        # 2. Residence city (normalized) fallback
        coords = city_coords(member.get("CurrentCity"))
        if coords:
            return coords
        # 3. Burial coordinates fallback (deceased member's resting place)
        if member.get("BurialLatitude") and member.get("BurialLongitude"):
            return float(member["BurialLatitude"]), float(member["BurialLongitude"])
        return city_coords(member.get("BurialLocation")) or (None, None)

    def resolve_parent(m, name_field, id_field, gender):
        """Resolve a parent by record ID, falling back to a name match against FullName."""
        pid = get_sid(m.get(id_field))
        if pid and pid in member_lookup:
            return member_lookup[pid]
        name = normalize_name(m.get(name_field))
        if not name:
            return None
        # Exact normalized name match
        for cand in name_index.get(name, []):
            if cand["id"] != m["id"] and (not gender or cand.get("Gender", "") == gender):
                return cand
        # Partial / contained match (e.g. missing middle name)
        for key, cands in name_index.items():
            if name in key or key in name:
                for cand in cands:
                    if cand["id"] != m["id"] and (not gender or cand.get("Gender", "") == gender):
                        return cand
        return None

    for m in members:
        mid = m["id"]
        name = m.get("FullName", "")

        # Residence coordinates (direct -> normalized city)
        res_lat = res_lng = None
        if m.get("Latitude") and m.get("Longitude"):
            res_lat, res_lng = float(m["Latitude"]), float(m["Longitude"])
        else:
            rc = city_coords(m.get("CurrentCity"))
            if rc:
                res_lat, res_lng = rc

        # Burial coordinates (direct -> normalized city)
        bur_lat, bur_lng = get_burial_coords(m)

        # Coordinates used for relationship arcs: residence first, burial fallback.
        arc_lat, arc_lng = (res_lat, res_lng) if (res_lat and res_lng) else (bur_lat, bur_lng)

        # Residence marker
        if res_lat and res_lng:
            markers.append({
                "id": mid,
                "name": name,
                "type": "residence",
                "lat": res_lat,
                "lng": res_lng,
                "city": m.get("CurrentCity", ""),
                "country": m.get("CurrentCountry", ""),
                "gender": m.get("Gender", ""),
                "isAlive": m.get("IsAlive", True),
            })

        # Burial marker
        if bur_lat and bur_lng:
            markers.append({
                "id": mid,
                "name": name,
                "type": "burial",
                "lat": bur_lat,
                "lng": bur_lng,
                "location": m.get("BurialLocation", ""),
                "gender": m.get("Gender", ""),
                "isAlive": False,
            })

        # Build arcs: connect child to resolved parents (ID or name) if both have coords.
        if arc_lat and arc_lng:
            seen_parents = set()
            for id_field, name_field, gender in (
                ("FatherRecordId", "FatherName", "Male"),
                ("MotherRecordId", "MotherName", "Female"),
            ):
                parent = resolve_parent(m, name_field, id_field, gender)
                if not parent or parent["id"] == mid or parent["id"] in seen_parents:
                    continue
                seen_parents.add(parent["id"])
                p_lat, p_lng = get_member_coords(parent)
                if p_lat and p_lng:
                    # Only create arc if locations are different (to avoid dots)
                    if abs(arc_lat - p_lat) > 0.05 or abs(arc_lng - p_lng) > 0.05:
                        arcs.append({
                            "startLat": arc_lat,
                            "startLng": arc_lng,
                            "endLat": p_lat,
                            "endLng": p_lng,
                            "label": f"{name} → {parent.get('FullName', '')}",
                            "color": "#c9956c" if parent.get("Gender") == "Male" else "#d9819a",
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


def _link_spouse_reciprocal(member: dict) -> None:
    """Store the marriage on BOTH sides of the relationship.

    When a member is created/approved with a SpouseRecordId, update that spouse's
    SpouseRecordId to point back at this member. Best-effort: a failure here
    (e.g. the spouse is a name-only placeholder with no real record) must never
    fail the whole create/approve operation — get_tree() also links spouses at
    read time, so this only improves data integrity.
    """
    spouse_id = (member.get("SpouseRecordId") or "").strip()
    member_id = member.get("id")
    if not spouse_id or not member_id or spouse_id == member_id:
        return
    try:
        db.update_member(spouse_id, {"SpouseRecordId": member_id})
    except Exception:  # noqa: BLE001 - best-effort reciprocal link.
        pass


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

    # Reciprocally link the spouse so the marriage unit is stored on BOTH
    # sides of the relationship (not just the new member -> spouse).
    _link_spouse_reciprocal(new_member)

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
        _link_spouse_reciprocal(new_member)
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
