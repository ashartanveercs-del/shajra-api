"""
Shajra System — Main FastAPI Application v2
"""
import os
from datetime import datetime, timezone
from io import BytesIO
from ipaddress import ip_address
from math import ceil
from typing import Literal, NoReturn, Optional
import warnings

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Load .env only if it exists (local dev). On Vercel, env vars come from dashboard.
_env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(_env_path):
    load_dotenv(_env_path)

import cloudinary  # type: ignore[import-untyped]
import cloudinary.uploader  # type: ignore[import-untyped]
from requests.exceptions import HTTPError

cloudinary.config(secure=True)

import ai_service
import airtable_client as db
import relationship_writes
import runtime_coordination
from auth import create_access_token, decode_access_token, verify_admin
from change_history import ChangeHistoryStore, InMemoryChangeHistoryStore
from config import get_settings
from coordination import (
    CoordinationError,
    IpRateLimitSubject,
    LeaseManager,
    RateLimiter,
    RateLimitPolicyId,
    new_acquisition_id,
)
from cors_policy import configure_cors
from public_data import normalize_name as normalize_person_name
from public_data import redact_public
from public_data import unique_member_by_name
from write_gates import require_public_writes, require_relationship_writes

app = FastAPI(
    title="Shajra System API",
    description="Family Genealogy Backend — Tree, Map, Comments, Stories, AI Processing",
    version="2.0.0",
)

MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_REQUEST_BYTES = MAX_IMAGE_UPLOAD_BYTES + 64 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_FRAMES = 200
MAX_IMAGE_TOTAL_PIXELS = 40_000_000
ALLOWED_IMAGE_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)
IMAGE_FORMATS_BY_CONTENT_TYPE = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/gif": "GIF",
}


class _UploadRequestTooLarge(Exception):
    pass


class UploadRequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, maximum_bytes: int) -> None:
        self.app = app
        self.maximum_bytes = maximum_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or scope.get("path") != "/api/upload-image":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_content_length = headers.get(b"content-length", b"")
        try:
            content_length = int(raw_content_length) if raw_content_length else None
        except ValueError:
            content_length = None
        if content_length is not None and content_length > self.maximum_bytes:
            await self._reject(scope, receive, send)
            return

        received_bytes = 0
        pending_messages: list[Message] = []

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.maximum_bytes:
                    raise _UploadRequestTooLarge
            return message

        async def buffered_send(message: Message) -> None:
            pending_messages.append(message)

        try:
            await self.app(scope, limited_receive, buffered_send)
        except _UploadRequestTooLarge:
            pass
        if received_bytes > self.maximum_bytes:
            await self._reject(scope, receive, send)
            return
        for message in pending_messages:
            await send(message)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "Image exceeds the 10 MB upload limit."},
        )
        await response(scope, receive, send)

_change_history: list[dict[str, object]] = []
_memory_change_history_store = InMemoryChangeHistoryStore(_change_history)
_HISTORY_RECONCILIATION_CONFIRMATION = "I_HAVE_VERIFIED_THE_DATASTORE"

# CORS — allow frontend to call backend
app.add_middleware(
    UploadRequestSizeLimitMiddleware,
    maximum_bytes=MAX_IMAGE_REQUEST_BYTES,
)
configure_cors(app, get_settings())


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


_COORDINATION_ERROR_MESSAGES = {
    "COORDINATION_UNINITIALIZED": "Relationship coordination is not initialized.",
    "COORDINATION_UNAVAILABLE": "Relationship coordination is temporarily unavailable.",
    "LOCK_UNAVAILABLE": "Another relationship update is in progress.",
    "LEASE_LOST": "Relationship coordination was lost. Please retry.",
    "COORDINATION_STATE_CORRUPT": "Relationship coordination is temporarily unavailable.",
    "UNDO_HISTORY_RESTORE_FAILED": "Undo failed and its history could not be restored.",
    "UNDO_IN_PROGRESS": "An undo is awaiting recovery before more changes can be saved.",
    "UNDO_HISTORY_GAP": "Undo and relationship edits are paused until change history is recovered.",
}


def _raise_coordination_error(error: CoordinationError) -> NoReturn:
    code = (
        error.code
        if error.code in _COORDINATION_ERROR_MESSAGES
        else "COORDINATION_UNAVAILABLE"
    )
    raise HTTPException(
        status_code=503,
        detail={"code": code, "message": _COORDINATION_ERROR_MESSAGES[code]},
    ) from error


def get_relationship_lease_manager() -> LeaseManager:
    try:
        return runtime_coordination.build_lease_manager(get_settings())
    except CoordinationError as error:
        _raise_coordination_error(error)


def get_change_history_store() -> ChangeHistoryStore:
    settings = get_settings()
    if settings.runtime_environment in {"development", "test"}:
        return _memory_change_history_store
    try:
        return runtime_coordination.build_change_history_store(settings)
    except CoordinationError as error:
        _raise_coordination_error(error)


def get_upload_rate_limiter() -> RateLimiter:
    try:
        return runtime_coordination.build_rate_limiter(get_settings())
    except CoordinationError as error:
        _raise_upload_protection_error(error)


def get_login_rate_limiter() -> RateLimiter:
    try:
        return runtime_coordination.build_rate_limiter(get_settings())
    except CoordinationError as error:
        _raise_login_protection_error(error)


def _raise_upload_protection_error(error: CoordinationError) -> NoReturn:
    code = (
        error.code
        if error.code in _COORDINATION_ERROR_MESSAGES
        else "COORDINATION_UNAVAILABLE"
    )
    raise HTTPException(
        status_code=503,
        detail={
            "code": code,
            "message": "Upload protection is temporarily unavailable.",
        },
    ) from error


def _raise_login_protection_error(error: CoordinationError) -> NoReturn:
    code = (
        error.code
        if error.code in _COORDINATION_ERROR_MESSAGES
        else "COORDINATION_UNAVAILABLE"
    )
    raise HTTPException(
        status_code=503,
        detail={
            "code": code,
            "message": "Login protection is temporarily unavailable.",
        },
    ) from error


def _request_ip(request: Request) -> str:
    forwarded = ""
    if os.getenv("VERCEL") == "1":
        forwarded = (
            request.headers.get("x-forwarded-for", "")
            .split(",", 1)[0]
            .strip()
        )
    candidate = forwarded or (request.client.host if request.client else "")
    try:
        return str(ip_address(candidate))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_CLIENT_IP", "message": "Client address is invalid."},
        ) from None


def require_upload_rate_limit(
    request: Request,
    limiter: RateLimiter = Depends(get_upload_rate_limiter),
) -> None:
    try:
        result = limiter.consume(
            RateLimitPolicyId.UPLOAD,
            IpRateLimitSubject("IP", _request_ip(request)),
            new_acquisition_id(),
        )
    except CoordinationError as error:
        _raise_upload_protection_error(error)
    if not result.allowed:
        retry_after = max(1, ceil(result.retry_after_ms / 1000))
        raise HTTPException(
            status_code=429,
            detail={
                "code": "RATE_LIMITED",
                "message": "Too many image uploads. Please try again later.",
            },
            headers={"Retry-After": str(retry_after)},
        )


def require_login_rate_limit(
    request: Request,
    limiter: RateLimiter = Depends(get_login_rate_limiter),
) -> None:
    try:
        result = limiter.consume(
            RateLimitPolicyId.LOGIN,
            IpRateLimitSubject("IP", _request_ip(request)),
            new_acquisition_id(),
        )
    except CoordinationError as error:
        _raise_login_protection_error(error)
    if not result.allowed:
        retry_after = max(1, ceil(result.retry_after_ms / 1000))
        raise HTTPException(
            status_code=429,
            detail={
                "code": "RATE_LIMITED",
                "message": "Too many login attempts. Please try again later.",
            },
            headers={"Retry-After": str(retry_after)},
        )


# ── Pydantic Models ─────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class HistoryEntryPayload(BaseModel):
    timestamp: str
    action: Literal["approve", "create", "delete", "update"]
    record_id: str
    before: dict[str, object] | None = None
    after: dict[str, object] | None = None


class HistoryReconcileRequest(BaseModel):
    idempotencyKey: str
    resolution: Literal["abort", "commit"]
    confirmation: str
    entry: HistoryEntryPayload | None = None

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
        "environment": settings.runtime_environment,
        "environmentMismatch": settings.environment_mismatch,
        "configured": {
            "airtable": bool(settings.airtable_pat and settings.airtable_base_id),
            "groq": bool(settings.groq_api_key),
            "cloudinary": bool(settings.cloudinary_url),
            "coordination": runtime_coordination.coordination_configured(settings),
        },
        "writes": {
            "public": settings.effective_public_writes_enabled,
            "relationships": settings.effective_relationship_writes_enabled,
            "datastore": db.legacy_mutations_enabled(settings),
        },
        "normalizedReads": settings.effective_normalized_reads_enabled,
    }


@app.get("/api/members")
def list_members():
    """Get all approved family members (for tree & map)."""
    return redact_public(db.get_all_members())


@app.get("/api/members/{record_id}")
def get_member(record_id: str):
    """Get a single member by ID."""
    member = db.get_member_by_id(record_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    return redact_public(member)


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

    has_filters = any((city, branch, generation))
    if len(q) < 2 and not has_filters:
        return []
    if len(q) >= 2:
        members = db.search_members(q)
    else:
        members = db.get_all_members()

    results = []
    for m in members:
        if city and str(m.get("CurrentCity") or "").strip() != city:
            continue
        if branch and str(m.get("Branch") or "").strip() != branch:
            continue
        if generation and str(m.get("Generation") or "") != generation:
            continue
        results.append(m)

    return redact_public(results)


@app.get("/api/tree")
def get_tree():
    """Build hierarchical tree data with Marital Grouping (no duplicates).

    v4: Exact normalized name matching (Unicode, case, and whitespace), spouse
        nodes created from name text with their exact name (no '(Unknown)'
        phantom placeholders), and always-reciprocal spouse links.
    """
    members = sorted(
        (dict(member) for member in db.get_all_members()),
        key=lambda member: (
            str(member.get("id", "")),
            normalize_person_name(member.get("FullName")),
        ),
    )
    indexed_members = list(members)
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
        return normalize_person_name(name)

    def name_keys(name) -> list:
        """Matching keys from most-specific to least-specific.

        Matching is exact after Unicode, case, and whitespace normalization.
        """
        n = normalize_name(name)
        if not n:
            return []
        return [n]

    # Registry of name-derived nodes (created from name text), keyed by
    # normalized name so the same person referenced from multiple places
    # resolves to a single node.
    name_nodes: dict[tuple[str, str, str, str], dict] = {}

    def find_member_by_name(name, exclude_id=None, gender=None):
        return unique_member_by_name(
            name,
            indexed_members,
            exclude_id=exclude_id,
            gender=gender,
        )

    def make_name_node(name, gender, owner_id, relationship) -> dict:
        """Create (or reuse) a node for a person known only by name text.

        The node carries the exact name — never an '(Unknown)' suffix — so a
        spouse/parent referenced by name renders with their real name.
        """
        n_key = normalize_name(name)
        node_key = (n_key, str(gender), str(owner_id), str(relationship))
        if node_key in name_nodes:
            return name_nodes[node_key]
        fake_id = f"__name__{owner_id}__{relationship}"
        node = {
            "id": fake_id, "FullName": name.strip(), "Gender": gender,
            "IsAlive": False, "ProfileImageUrl": "", "DateOfBirth": "",
            "DateOfDeath": "", "Generation": 99,
            "FatherRecordId": "", "MotherRecordId": "", "SpouseRecordId": "",
            "FatherName": "", "MotherName": "", "SpouseName": "",
            "CurrentCity": "", "CurrentCountry": "", "Biography": "",
            "children": [], "Spouse": None, "IsPlaceholder": True,
        }
        name_nodes[node_key] = node
        lookup[fake_id] = node
        return node

    def resolve_by_name(name, gender, exclude_id=None, relationship="unknown"):
        if not name or not name.strip():
            return None
        m = find_member_by_name(name, exclude_id=exclude_id, gender=gender)
        if m:
            return m
        return make_name_node(name, gender, exclude_id or "unknown", relationship)

    def opposite_gender(gender):
        if gender == "Male":
            return "Female"
        if gender == "Female":
            return "Male"
        return ""

    def link_spouses(a, b):
        """Reciprocally link two people as spouses (never one-sided)."""
        a_id = get_sid(a.get("id"))
        b_id = get_sid(b.get("id"))
        if not a_id or not b_id or a_id == b_id:
            return False
        a_declared = get_sid(a.get("SpouseRecordId"))
        b_declared = get_sid(b.get("SpouseRecordId"))
        a_linked = get_sid((a.get("Spouse") or {}).get("id"))
        b_linked = get_sid((b.get("Spouse") or {}).get("id"))
        if (
            (a_declared and a_declared != b_id)
            or (b_declared and b_declared != a_id)
            or (a_linked and a_linked != b_id)
            or (b_linked and b_linked != a_id)
        ):
            return False
        a["Spouse"] = spouse_snapshot(b)
        b["Spouse"] = spouse_snapshot(a)
        return True

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
            spouse_name,
            opposite_gender(m.get("Gender", "")),
            exclude_id=m["id"],
            relationship="spouse",
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
        node = resolve_by_name(
            name,
            gender,
            exclude_id=m["id"],
            relationship=id_field.casefold(),
        )
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

    # ── Phase 2: Deterministically project ancestry to a DAG ──────────
    # Process stable parent edges and discard only the edge that closes a cycle.
    # A valid second parent survives, unlike the previous all-or-nothing repair.
    ancestry_children: dict[str, set[str]] = {}

    def closes_cycle(parent_id: str, child_id: str) -> bool:
        stack = [child_id]
        seen = set()
        while stack:
            current = stack.pop()
            if current == parent_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(sorted(ancestry_children.get(current, ()), reverse=True))
        return False

    for m in sorted(members, key=lambda member: str(member["id"])):
        for field in ("FatherRecordId", "MotherRecordId"):
            parent_id = get_sid(m.get(field))
            if not parent_id or parent_id not in lookup:
                continue
            if parent_id == m["id"] or closes_cycle(parent_id, m["id"]):
                m[field] = ""
                continue
            ancestry_children.setdefault(parent_id, set()).add(m["id"])

    # ── Phase 2.5: Infer spouses only from repaired parent pairs ──────
    for m in members:
        father_id = get_sid(m.get("FatherRecordId"))
        mother_id = get_sid(m.get("MotherRecordId"))
        if father_id and mother_id and father_id in lookup and mother_id in lookup:
            link_spouses(lookup[father_id], lookup[mother_id])

    # ── Phase 3: Build parent-child hierarchy ─────────────────────────
    for m in members:
        father_id = get_sid(m.get("FatherRecordId"))
        mother_id = get_sid(m.get("MotherRecordId"))
        if father_id and father_id in lookup:
            lookup[father_id]["children"].append(m)
        elif mother_id and mother_id in lookup:
            lookup[mother_id]["children"].append(m)

    # ── Phase 4: Merge spouse children into primary member ────────────
    def child_reaches(candidate, target_id):
        stack = [candidate]
        seen = set()
        while stack:
            current = stack.pop()
            current_id = current["id"]
            if current_id == target_id:
                return True
            if current_id in seen:
                continue
            seen.add(current_id)
            stack.extend(
                sorted(
                    current.get("children", []),
                    key=lambda child: str(child["id"]),
                    reverse=True,
                )
            )
        return False

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
                    if (
                        child["id"] not in existing
                        and child["id"] != node["id"]
                        and not child_reaches(child, node["id"])
                    ):
                        node["children"].append(child)
                        existing.add(child["id"])
        for child in node["children"]:
            merge_spouse_children(child, visited)

    # ── Phase 5: Identify true roots ──────────────────────────────────
    final_roots = []
    processed_root_ids = set()

    def sort_key(m):
        gen_val = m.get("Generation")
        gen = gen_val if isinstance(gen_val, (int, float)) else 99
        has_parents = 1 if (m.get("FatherRecordId") or m.get("MotherRecordId")) else 0
        return (gen, has_parents, normalize_name(m.get("FullName")), str(m["id"]))

    independent_members = []
    independent_ids = set()
    for m in members:
        father_id = get_sid(m.get("FatherRecordId"))
        mother_id = get_sid(m.get("MotherRecordId"))
        if (not father_id or father_id not in lookup) and (
            not mother_id or mother_id not in lookup
        ):
            independent_members.append(m)
            independent_ids.add(m["id"])

    # A dependent member's independent spouse is represented on that member's
    # card, unless the supposed spouse is also an actual ancestor. Malformed
    # data must never suppress the only ancestry root for a component.
    suppressed_spouse_roots = set()
    for m in members:
        father_id = get_sid(m.get("FatherRecordId"))
        mother_id = get_sid(m.get("MotherRecordId"))
        has_parent = (father_id and father_id in lookup) or (
            mother_id and mother_id in lookup
        )
        spouse_id = m.get("Spouse", {}).get("id") if m.get("Spouse") else ""
        if (
            has_parent
            and spouse_id in independent_ids
            and not child_reaches(lookup[spouse_id], m["id"])
        ):
            suppressed_spouse_roots.add(spouse_id)

    root_candidates = [
        member
        for member in independent_members
        if member["id"] not in suppressed_spouse_roots
    ]
    if not root_candidates:
        root_candidates = independent_members

    for m in sorted(root_candidates, key=sort_key):
        if m["id"] in processed_root_ids:
            continue
        final_roots.append(m)
        processed_root_ids.add(m["id"])
        spouse_id = m.get("Spouse", {}).get("id") if m.get("Spouse") else ""
        if spouse_id in independent_ids:
            processed_root_ids.add(spouse_id)

    # Merge spouse children for all nodes in the final tree
    for root in final_roots:
        merge_spouse_children(root)

    def sort_descendants(node, visited=None):
        if visited is None:
            visited = set()
        if node["id"] in visited:
            return
        visited.add(node["id"])
        node["children"].sort(key=sort_key)
        for child in node["children"]:
            sort_descendants(child, visited)

    final_roots.sort(key=sort_key)
    for root in final_roots:
        sort_descendants(root)

    return redact_public(final_roots)


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
        return normalize_person_name(name)

    def normalize_city(value) -> str:
        """Strip everything after the first comma, e.g. 'Karachi, Pakistan' -> 'Karachi'."""
        if not value:
            return ""
        return str(value).split(",")[0].strip().title()

    member_lookup = {m["id"]: m for m in members}

    def city_coords(city_value):
        city = normalize_city(city_value)
        if city in CITY_GEODATA:
            return CITY_GEODATA[city]["lat"], CITY_GEODATA[city]["lng"]
        return None

    def get_burial_coords(member):
        if member.get("IsAlive", True):
            return None, None
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
        return get_burial_coords(member)

    def resolve_parent(m, name_field, id_field, gender):
        """Resolve a parent by record ID, falling back to a name match against FullName."""
        pid = get_sid(m.get(id_field))
        if pid and pid in member_lookup:
            return member_lookup[pid]
        return unique_member_by_name(
            m.get(name_field),
            members,
            exclude_id=str(m["id"]),
            gender=gender,
        )

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
    return redact_public(db.get_comments_for_member(member_record_id))


@app.post("/api/comments")
def post_comment(comment: CommentCreate, _=Depends(require_public_writes)):
    """Post a comment. Only approved emails can comment."""
    if not db.is_email_approved(comment.AuthorEmail):
        raise HTTPException(
            status_code=403,
            detail="Your email is not on the approved family members list. Contact the admin to get access."
        )
    fields: dict[str, object] = {
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
    return redact_public(db.get_all_stories())


@app.get("/api/stories/family")
def get_family_stories():
    """Get stories not tied to a specific member."""
    return redact_public(db.get_family_stories())


@app.get("/api/stories/member/{member_record_id}")
def get_stories_for_member(member_record_id: str):
    """Get stories for a specific member."""
    return redact_public(db.get_stories_for_member(member_record_id))


@app.post("/api/stories")
def post_story(story: StoryCreate, _=Depends(require_public_writes)):
    """Submit a new story."""
    fields: dict[str, object] = {
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
            detail=f"Airtable Schema Error in PendingSubmissions: {_airtable_error_text(e)}",
        ) from e

def _validate_image_content(content: bytes, content_type: str) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                detected_format = str(image.format or "").upper()
                width, height = image.size
                frames = int(getattr(image, "n_frames", 1))
                if detected_format != IMAGE_FORMATS_BY_CONTENT_TYPE[content_type]:
                    raise ValueError("declared and detected image formats differ")
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("image dimensions exceed policy")
                if frames <= 0 or frames > MAX_IMAGE_FRAMES:
                    raise ValueError("image frame count exceeds policy")
                if width * height * frames > MAX_IMAGE_TOTAL_PIXELS:
                    raise ValueError("aggregate image pixels exceed policy")
                image.verify()
            if frames > 1:
                decoded_pixels = 0
                with Image.open(BytesIO(content)) as image:
                    for frame_index in range(frames):
                        image.seek(frame_index)
                        frame_width, frame_height = image.size
                        if frame_width <= 0 or frame_height <= 0:
                            raise ValueError("image frame dimensions are invalid")
                        decoded_pixels += frame_width * frame_height
                        if decoded_pixels > MAX_IMAGE_TOTAL_PIXELS:
                            raise ValueError("aggregate image pixels exceed policy")
                        image.load()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not a valid image.",
        ) from None


@app.post("/api/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    _=Depends(require_public_writes),
    __=Depends(require_upload_rate_limit),
):
    """
    Publicly accessible image uploader for form submissions and admin edits.
    Uploads precisely to Cloudinary and returns the secure public URL perfectly suited for Airtable.
    """
    if file.content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Only standard images are allowed.",
        )

    file_content = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
    if len(file_content) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Image exceeds the 10 MB upload limit.",
        )
    if not file_content:
        raise HTTPException(status_code=400, detail="Image file is empty.")

    _validate_image_content(file_content, file.content_type)

    try:
        res = cloudinary.uploader.upload(file_content, folder="shajra_system")
        secure_url = res.get("secure_url")
        if not secure_url:
            raise RuntimeError("Cloudinary response omitted secure_url")
        return {"url": secure_url}
    except Exception as e:
        raise HTTPException(status_code=502, detail="Cloudinary upload failed.") from e


# ══════════════════════════════════════════════════════════════
#   ADMIN ENDPOINTS (JWT Protected)
# ══════════════════════════════════════════════════════════════

@app.post("/api/admin/login")
def admin_login(req: LoginRequest, _=Depends(require_login_rate_limit)):
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

# Development/test uses the module list for compatibility. Preview and
# production resolve the same interface to durable Upstash history.


def _push_history(
    history_store: ChangeHistoryStore,
    request_nonce: str,
    action: str,
    record_id: str,
    before: dict | None,
    after: dict | None,
) -> bool:
    """Commit guarded history without making an uncertain write retryable."""
    entry: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "record_id": record_id,
        "before": before,
        "after": after,
    }
    try:
        history_store.bind_write_target(
            request_nonce,
            {"action": action, "record_id": record_id},
        )
        history_store.commit_write(request_nonce, entry)
    except CoordinationError:
        return False
    return True


def _undo_metadata(available: bool) -> dict[str, object]:
    metadata: dict[str, object] = {"undoAvailable": available}
    if not available:
        metadata["undoWarning"] = (
            "The change was saved but cannot be undone because durable history "
            "is temporarily unavailable."
        )
    return metadata


@app.get("/api/admin/pending/status/{status}")
def list_pending_by_status(status: str, admin=Depends(get_current_admin)):
    """Get pending submissions filtered by status."""
    return db.get_pending_by_status(status)


def _raise_relationship_error(error: Exception) -> None:
    if isinstance(error, CoordinationError):
        _raise_coordination_error(error)
    if isinstance(error, relationship_writes.RelationshipConflict):
        status_code = (
            404
            if error.code in {"MEMBER_NOT_FOUND", "PENDING_NOT_FOUND", "UNDO_EMPTY"}
            else 409
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error
    if isinstance(error, relationship_writes.RelationshipPersistenceError):
        code = (
            "RELATIONSHIP_ROLLBACK_INCOMPLETE"
            if error.rollback_incomplete
            else "RELATIONSHIP_WRITE_FAILED"
        )
        message = (
            "The relationship update failed and could not be fully restored."
            if error.rollback_incomplete
            else "The relationship update failed and no changes were kept."
        )
        raise HTTPException(
            status_code=500 if error.rollback_incomplete else 502,
            detail={"code": code, "message": message},
        ) from error
    raise error


def _airtable_error_text(error: HTTPError) -> str:
    response = error.response
    return response.text if response is not None else "Unknown Airtable error"


@app.post("/api/admin/approve/{record_id}")
def approve_submission(
    record_id: str,
    admin=Depends(get_current_admin),
    _=Depends(require_relationship_writes),
    lease_manager: LeaseManager = Depends(get_relationship_lease_manager),
    history_store: ChangeHistoryStore = Depends(get_change_history_store),
):
    """
    Approve a pending submission: create an approved member, mark submission as approved.
    """
    undo_available = True
    history_nonce = new_acquisition_id()
    history_operation: dict[str, object] = {
        "action": "approve",
        "record_id": record_id,
    }

    def record_history(new_member: dict[str, object]) -> None:
        nonlocal undo_available
        undo_available = _push_history(
            history_store,
            history_nonce,
            "approve",
            str(new_member["id"]),
            {"pending_id": record_id},
            new_member,
        )

    try:
        new_member = relationship_writes.approve_member(
            db,
            record_id,
            lease_manager=lease_manager,
            history_preflight=lambda: history_store.begin_write(
                history_nonce, history_operation
            ),
            history_started=lambda: history_store.mark_write_started(
                history_nonce
            ),
            history_recorder=record_history,
            history_abort=lambda: history_store.abort_write(history_nonce),
        )
    except (
        CoordinationError,
        relationship_writes.RelationshipConflict,
        relationship_writes.RelationshipPersistenceError,
    ) as error:
        _raise_relationship_error(error)

    return {
        "status": "approved",
        "member": new_member,
        **_undo_metadata(undo_available),
    }


@app.get("/api/admin/integrations")
def admin_integrations(admin=Depends(get_current_admin)):
    settings = get_settings()
    return {
        "groqConfigured": bool(settings.groq_api_key),
        "cloudinaryConfigured": bool(settings.cloudinary_url),
        "coordinationConfigured": runtime_coordination.coordination_configured(settings),
        "datastoreMutationsEnabled": db.legacy_mutations_enabled(settings),
    }

@app.post("/api/admin/reject/{record_id}")
def reject_submission(
    record_id: str,
    admin=Depends(get_current_admin),
    _=Depends(require_relationship_writes),
    lease_manager: LeaseManager = Depends(get_relationship_lease_manager),
):
    """Reject a pending submission."""
    try:
        relationship_writes.reject_pending(
            db, record_id, lease_manager=lease_manager
        )
        return {"status": "rejected"}
    except (
        CoordinationError,
        relationship_writes.RelationshipConflict,
        relationship_writes.RelationshipPersistenceError,
    ) as error:
        _raise_relationship_error(error)


@app.post("/api/admin/members")
def admin_create_member(
    member: MemberCreate,
    admin=Depends(get_current_admin),
    _=Depends(require_relationship_writes),
    lease_manager: LeaseManager = Depends(get_relationship_lease_manager),
    history_store: ChangeHistoryStore = Depends(get_change_history_store),
):
    """Admin directly creates an approved member."""
    undo_available = True
    history_nonce = new_acquisition_id()
    history_operation: dict[str, object] = {
        "action": "create",
        "record_id": "",
    }

    def record_history(new_member: dict[str, object]) -> None:
        nonlocal undo_available
        undo_available = _push_history(
            history_store,
            history_nonce,
            "create",
            str(new_member["id"]),
            None,
            new_member,
        )

    try:
        fields = member.model_dump(exclude_none=True)
        new_member = relationship_writes.create_member(
            db,
            fields,
            lease_manager=lease_manager,
            history_preflight=lambda: history_store.begin_write(
                history_nonce, history_operation
            ),
            history_started=lambda: history_store.mark_write_started(
                history_nonce
            ),
            history_recorder=record_history,
            history_abort=lambda: history_store.abort_write(history_nonce),
        )
        return {**new_member, **_undo_metadata(undo_available)}
    except (
        CoordinationError,
        relationship_writes.RelationshipConflict,
        relationship_writes.RelationshipPersistenceError,
    ) as error:
        _raise_relationship_error(error)
    except HTTPError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Airtable Schema Error: {_airtable_error_text(e)}",
        ) from e

@app.put("/api/admin/members/{record_id}")
def admin_update_member(
    record_id: str,
    member: MemberUpdate,
    admin=Depends(get_current_admin),
    _=Depends(require_relationship_writes),
    lease_manager: LeaseManager = Depends(get_relationship_lease_manager),
    history_store: ChangeHistoryStore = Depends(get_change_history_store),
):
    """Admin updates an approved member."""
    undo_available = True
    history_nonce = new_acquisition_id()
    history_operation: dict[str, object] = {
        "action": "update",
        "record_id": record_id,
    }

    def record_history(
        before: dict[str, object], after: dict[str, object]
    ) -> None:
        nonlocal undo_available
        undo_available = _push_history(
            history_store,
            history_nonce,
            "update",
            record_id,
            before,
            after,
        )

    try:
        fields = member.model_dump(exclude_none=True)
        result = relationship_writes.update_member(
            db,
            record_id,
            fields,
            lease_manager=lease_manager,
            history_preflight=lambda: history_store.begin_write(
                history_nonce, history_operation
            ),
            history_started=lambda: history_store.mark_write_started(
                history_nonce
            ),
            history_recorder=record_history,
            history_abort=lambda: history_store.abort_write(history_nonce),
        )
        return {**result, **_undo_metadata(undo_available)}
    except (
        CoordinationError,
        relationship_writes.RelationshipConflict,
        relationship_writes.RelationshipPersistenceError,
    ) as error:
        _raise_relationship_error(error)
    except HTTPError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Airtable Schema Error: {_airtable_error_text(e)}",
        ) from e


@app.delete("/api/admin/members/{record_id}")
def admin_delete_member(
    record_id: str,
    admin=Depends(get_current_admin),
    _=Depends(require_relationship_writes),
    lease_manager: LeaseManager = Depends(get_relationship_lease_manager),
    history_store: ChangeHistoryStore = Depends(get_change_history_store),
):
    """Admin deletes an approved member. Snapshots before for undo."""
    undo_available = True
    history_nonce = new_acquisition_id()
    history_operation: dict[str, object] = {
        "action": "delete",
        "record_id": record_id,
    }

    def record_history(snapshot: dict[str, object]) -> None:
        nonlocal undo_available
        undo_available = _push_history(
            history_store,
            history_nonce,
            "delete",
            record_id,
            snapshot,
            None,
        )

    try:
        relationship_writes.delete_member_with_snapshot(
            db,
            record_id,
            lease_manager=lease_manager,
            history_preflight=lambda: history_store.begin_write(
                history_nonce, history_operation
            ),
            history_started=lambda: history_store.mark_write_started(
                history_nonce
            ),
            history_recorder=record_history,
            history_abort=lambda: history_store.abort_write(history_nonce),
        )
        return {"status": "deleted", **_undo_metadata(undo_available)}
    except (
        CoordinationError,
        relationship_writes.RelationshipConflict,
        relationship_writes.RelationshipPersistenceError,
    ) as error:
        _raise_relationship_error(error)


# ── Undo / Revert Endpoint ────────────────────────────────────────────

@app.get("/api/admin/history")
def get_change_history(
    admin=Depends(get_current_admin),
    history_store: ChangeHistoryStore = Depends(get_change_history_store),
):
    """Get the change history stack (most recent first)."""
    try:
        return history_store.list()
    except CoordinationError as error:
        _raise_coordination_error(error)


@app.get("/api/admin/history/write-status")
def get_history_write_status(
    admin=Depends(get_current_admin),
    history_store: ChangeHistoryStore = Depends(get_change_history_store),
):
    """Expose enough durable metadata to reconcile an interrupted write."""
    try:
        status = history_store.write_status()
    except CoordinationError as error:
        _raise_coordination_error(error)
    if status is None:
        return {"active": False}
    return {
        "active": True,
        "idempotencyKey": status.nonce,
        "phase": status.phase,
        "operation": status.operation,
        "preparedAt": status.prepared_at,
        "startedAt": status.started_at,
        "boundAt": status.bound_at,
    }


@app.post("/api/admin/history/reconcile")
def reconcile_history_write(
    request: HistoryReconcileRequest,
    admin=Depends(get_current_admin),
    _=Depends(require_relationship_writes),
    lease_manager: LeaseManager = Depends(get_relationship_lease_manager),
    history_store: ChangeHistoryStore = Depends(get_change_history_store),
):
    """Resolve an interrupted write after an administrator verifies Airtable."""
    nonce = request.idempotencyKey.strip()
    if not nonce or len(nonce) > 128 or "\x00" in nonce:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "IDEMPOTENCY_KEY_REQUIRED",
                "message": "A valid interrupted-write idempotency key is required.",
            },
        )
    if request.confirmation != _HISTORY_RECONCILIATION_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "DATASTORE_VERIFICATION_REQUIRED",
                "message": "Verify the datastore before reconciling change history.",
            },
        )
    if request.resolution == "commit" and request.entry is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "HISTORY_ENTRY_REQUIRED",
                "message": "A verified history entry is required to commit recovery.",
            },
        )
    if request.resolution == "abort" and request.entry is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "HISTORY_ENTRY_NOT_ALLOWED",
                "message": "Abort recovery must not include a history entry.",
            },
        )
    entry = request.entry.model_dump() if request.entry is not None else None
    try:
        relationship_writes.reconcile_history_write(
            db,
            history_store,
            nonce,
            entry,
            lease_manager=lease_manager,
        )
    except CoordinationError as error:
        _raise_coordination_error(error)
    return {"status": "resolved", "resolution": request.resolution}


@app.get("/api/admin/undo/status")
def get_undo_status(
    admin=Depends(get_current_admin),
    history_store: ChangeHistoryStore = Depends(get_change_history_store),
):
    """Return the durable nonce needed to resume an interrupted undo."""
    try:
        active_nonce = history_store.active_nonce()
    except CoordinationError as error:
        _raise_coordination_error(error)
    return {
        "active": active_nonce is not None,
        "idempotencyKey": active_nonce,
    }

@app.post("/api/admin/undo")
def undo_last_change(
    admin=Depends(get_current_admin),
    _=Depends(require_relationship_writes),
    lease_manager: LeaseManager = Depends(get_relationship_lease_manager),
    history_store: ChangeHistoryStore = Depends(get_change_history_store),
    idempotency_key: str | None = Header(
        default=None, alias="X-Idempotency-Key"
    ),
):
    """Undo the most recent admin change."""
    request_nonce = str(idempotency_key or "").strip()
    if not request_nonce or len(request_nonce) > 128 or "\x00" in request_nonce:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "IDEMPOTENCY_KEY_REQUIRED",
                "message": "A valid X-Idempotency-Key header is required.",
            },
        )
    try:
        return relationship_writes.undo_last_change(
            db,
            history_store,
            request_nonce,
            lease_manager=lease_manager,
        )
    except (
        CoordinationError,
        relationship_writes.RelationshipConflict,
        relationship_writes.RelationshipPersistenceError,
    ) as error:
        _raise_relationship_error(error)


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
def add_approved_email(
    payload: ApprovedEmailCreate,
    admin=Depends(get_current_admin),
    _=Depends(require_public_writes),
):
    """Add an approved family email."""
    fields: dict[str, object] = {
        "Email": payload.Email.lower().strip(),
        "Name": payload.Name,
        "Notes": payload.Notes,
        "AddedAt": datetime.now(timezone.utc).isoformat(),
    }
    fields = {k: v for k, v in fields.items() if v not in ("", None)}
    return db.add_approved_email(fields)


@app.delete("/api/admin/approved-emails/{record_id}")
def remove_approved_email(
    record_id: str,
    admin=Depends(get_current_admin),
    _=Depends(require_public_writes),
):
    """Remove an approved email."""
    db.remove_approved_email(record_id)
    return {"status": "removed"}


# ── Admin: Comments Moderation ─────────────────────────────────────

@app.get("/api/admin/comments")
def list_all_comments(admin=Depends(get_current_admin)):
    """List all comments for moderation."""
    return db.get_all_comments()


@app.delete("/api/admin/comments/{record_id}")
def delete_comment(
    record_id: str,
    admin=Depends(get_current_admin),
    _=Depends(require_public_writes),
):
    """Admin deletes a comment."""
    db.delete_comment(record_id)
    return {"status": "deleted"}


# ── Admin: Stories Moderation ──────────────────────────────────────

@app.delete("/api/admin/stories/{record_id}")
def delete_story(
    record_id: str,
    admin=Depends(get_current_admin),
    _=Depends(require_public_writes),
):
    """Admin deletes a story."""
    db.delete_story(record_id)
    return {"status": "deleted"}


# ══════════════════════════════════════════════════════════════
#   RUN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
