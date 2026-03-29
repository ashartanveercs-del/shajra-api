"""
Shajra System — Main FastAPI Application v2
"""
import os
import json
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Depends, Header, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from requests.exceptions import HTTPError
import cloudinary
import cloudinary.uploader

cloudinary.config(secure=True)

import airtable_client as db
import ai_service
from settings_manager import get_groq_api_key, set_groq_api_key
from auth import verify_admin, create_access_token, decode_access_token

app = FastAPI(
    title="Shajra System API",
    description="Family Genealogy Backend — Tree, Map, Comments, Stories, AI Processing",
    version="2.0.0",
)

# CORS — allow frontend to call backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
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

class SettingsUpdate(BaseModel):
    GROQ_API_KEY: str


# ══════════════════════════════════════════════════════════════
#   PUBLIC ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.get("/")
def root():
    return {"message": "Shajra System API v2 is running", "version": "2.0.0"}


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
    """Build hierarchical tree data for visualization."""
    members = db.get_all_members()
    lookup = {m["id"]: m for m in members}
    for m in members:
        m["children"] = []

    roots = []
    for m in members:
        father_id = m.get("FatherRecordId", "")
        if father_id and father_id in lookup:
            lookup[father_id]["children"].append(m)
        else:
            roots.append(m)

    return roots


@app.get("/api/map-markers")
def get_map_markers():
    """Get all members with location data for the map view, including relationship arcs."""
    members = db.get_all_members()
    markers = []
    arcs = []

    member_lookup = {m["id"]: m for m in members}

    for m in members:
        mid = m["id"]
        name = m.get("FullName", "")

        # Residence marker
        if m.get("Latitude") and m.get("Longitude"):
            markers.append({
                "id": mid,
                "name": name,
                "type": "residence",
                "lat": m["Latitude"],
                "lng": m["Longitude"],
                "city": m.get("CurrentCity", ""),
                "country": m.get("CurrentCountry", ""),
                "gender": m.get("Gender", ""),
                "isAlive": m.get("IsAlive", True),
            })

            # Build arc: connect child to father if both have coords
            father_id = m.get("FatherRecordId", "")
            if father_id and father_id in member_lookup:
                father = member_lookup[father_id]
                if father.get("Latitude") and father.get("Longitude"):
                    arcs.append({
                        "startLat": m["Latitude"],
                        "startLng": m["Longitude"],
                        "endLat": father["Latitude"],
                        "endLng": father["Longitude"],
                        "label": f"{name} → {father.get('FullName', '')}",
                        "color": "#c9956c",
                    })

        # Burial marker
        if m.get("BurialLatitude") and m.get("BurialLongitude"):
            markers.append({
                "id": mid,
                "name": name,
                "type": "burial",
                "lat": m["BurialLatitude"],
                "lng": m["BurialLongitude"],
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
def post_comment(comment: CommentCreate):
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
def post_story(story: StoryCreate):
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
def post_album(album: PhotoAlbumCreate):
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
async def receive_google_form(payload: GoogleFormWebhook):
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
async def direct_submit(payload: DirectSubmission):
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
        raise HTTPException(status_code=422, detail=f"Airtable Schema Error in PendingSubmissions: {e.response.text}")

@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...)):
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
        raise HTTPException(status_code=500, detail=f"Cloudinary Upload Failed: {str(e)}")


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


@app.get("/api/admin/pending/status/{status}")
def list_pending_by_status(status: str, admin=Depends(get_current_admin)):
    """Get pending submissions filtered by status."""
    return db.get_pending_by_status(status)


@app.post("/api/admin/approve/{record_id}")
def approve_submission(record_id: str, admin=Depends(get_current_admin)):
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
        "IsAlive": not bool(pending.get("CleanDOD", "")),
    }

    # Remove None items, keep everything else so we can clear Text fields
    member_fields = {k: v for k, v in member_fields.items() if v is not None}

    new_member = db.create_member(member_fields)
    db.update_pending(record_id, {"Status": "Approved"})
    return {"status": "approved", "member": new_member}


@app.get("/api/admin/settings")
def get_admin_settings(admin=Depends(get_current_admin)):
    """Retrieve dynamic settings like GROQ_API_KEY."""
    # Never return the full key for security, just preview or dummy string
    # Actually wait, admins might want to see the key they set.
    return {"GROQ_API_KEY": get_groq_api_key()}

@app.post("/api/admin/settings")
def update_admin_settings(payload: SettingsUpdate, admin=Depends(get_current_admin)):
    """Update dynamic settings."""
    set_groq_api_key(payload.GROQ_API_KEY)
    return {"status": "success", "message": "Settings updated"}

@app.post("/api/admin/reject/{record_id}")
def reject_submission(record_id: str, admin=Depends(get_current_admin)):
    """Reject a pending submission."""
    db.update_pending(record_id, {"Status": "Rejected"})
    return {"status": "rejected"}


@app.post("/api/admin/members")
def admin_create_member(member: MemberCreate, admin=Depends(get_current_admin)):
    """Admin directly creates an approved member."""
    try:
        fields = member.model_dump(exclude_none=True)
        return db.create_member(fields)
    except HTTPError as e:
        raise HTTPException(status_code=422, detail=f"Airtable Schema Error: {e.response.text}")

@app.put("/api/admin/members/{record_id}")
def admin_update_member(record_id: str, member: MemberUpdate, admin=Depends(get_current_admin)):
    """Admin updates an approved member."""
    try:
        fields = member.model_dump(exclude_none=True)
        return db.update_member(record_id, fields)
    except HTTPError as e:
        raise HTTPException(status_code=422, detail=f"Airtable Schema Error: {e.response.text}")


@app.delete("/api/admin/members/{record_id}")
def admin_delete_member(record_id: str, admin=Depends(get_current_admin)):
    """Admin deletes an approved member."""
    db.delete_member(record_id)
    return {"status": "deleted"}


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
