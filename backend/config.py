"""
Shajra System — Configuration & Environment Variables
"""
import os
from dotenv import load_dotenv

# Load .env only if it exists (local dev). On Vercel, env vars come from dashboard.
_env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(_env_path):
    load_dotenv(_env_path)

AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "shajrasecure123")

# JWT Settings
JWT_SECRET = os.getenv("JWT_SECRET", "shajra-jwt-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 60 * 24  # 24 hours

# Airtable Table Names
APPROVED_MEMBERS_TABLE = "ApprovedMembers"
PENDING_SUBMISSIONS_TABLE = "PendingSubmissions"
APPROVED_EMAILS_TABLE = "ApprovedEmails"
ALBUMS_TABLE = "Albums"
PHOTOS_TABLE = "Photos"
