from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from pathlib import Path
from datetime import datetime, timezone
import secrets

from database import (
    add_contact, get_all_contacts, verify_user, get_setting,
    set_setting, get_site_content, save_site_content, get_db_info,
    DB_FILENAME, PORTFOLIO_DATA
)

# Adjusted base dir to step out of routers/
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

router = APIRouter()

class ContactMessage(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., min_length=3, max_length=150)
    message: str = Field(..., min_length=5, max_length=5000)

class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=1, max_length=150)

class AvatarRequest(BaseModel):
    avatar_url: str

PROFILE_SETTING_KEYS = [
    "profile_name", "profile_role_sv", "profile_role_en",
    "profile_location_sv", "profile_location_en",
    "profile_exp_sv", "profile_exp_en",
    "profile_email", "profile_bio_sv", "profile_bio_en",
    "profile_github", "profile_linkedin"
]

class ProfileSettingsRequest(BaseModel):
    profile_name: Optional[str] = None
    profile_role_sv: Optional[str] = None
    profile_role_en: Optional[str] = None
    profile_location_sv: Optional[str] = None
    profile_location_en: Optional[str] = None
    profile_exp_sv: Optional[str] = None
    profile_exp_en: Optional[str] = None
    profile_email: Optional[str] = None
    profile_bio_sv: Optional[str] = None
    profile_bio_en: Optional[str] = None
    profile_github: Optional[str] = None
    profile_linkedin: Optional[str] = None

@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/", summary="Serve Portfolio Homepage")
async def read_index():
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html template not found")
    return FileResponse(str(index_file), media_type="text/html")

@router.get("/api/health", summary="Health check endpoint")
async def health_check():
    db_info = get_db_info()
    return {
        "status": "healthy",
        "service": "FastAPI Portfolio Web Application",
        "database": {
            "file": DB_FILENAME,
            "status": db_info.get("status"),
            "path": db_info.get("db_path"),
            "contacts_stored": db_info.get("contact_count", 0),
            "users_count": db_info.get("user_count", 0)
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0"
    }

@router.get("/api/db-status", summary="SQLite Database Status and Information")
async def db_status():
    return get_db_info()

@router.get("/api/portfolio", summary="Get portfolio structured data")
async def get_portfolio_data():
    return PORTFOLIO_DATA

@router.post("/api/contact", summary="Submit contact form message")
async def submit_contact(message: ContactMessage):
    entry = add_contact(name=message.name, email=message.email, message=message.message)
    return {
        "success": True,
        "message": "Tack för ditt meddelande! Vi återkommer inom kort.",
        "id": entry["id"]
    }

@router.get("/api/contacts", summary="Get all received contact messages from SQLite (Admin)")
async def get_contacts():
    contacts = get_all_contacts()
    return {
        "total": len(contacts),
        "database": DB_FILENAME,
        "contacts": contacts
    }

@router.post("/api/login", summary="Admin Login via SQLite Authentication")
async def admin_login(creds: LoginRequest):
    user = verify_user(creds.email, creds.password)
    if user:
        token = secrets.token_urlsafe(32)
        return {"success": True, "token": token, "database": DB_FILENAME, "user": user}
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Felaktigt användarnamn eller lösenord.")

@router.get("/api/avatar", summary="Get custom avatar from SQLite database")
async def get_avatar():
    return {"avatar_url": get_setting("avatar_url", "")}

@router.post("/api/avatar", summary="Save custom avatar to SQLite database")
async def update_avatar(payload: AvatarRequest):
    set_setting("avatar_url", payload.avatar_url)
    return {"success": True, "database": DB_FILENAME, "message": "Avatar sparad framgångsrikt i SQLite-databasen."}

@router.get("/api/profile-settings", summary="Get profile settings from SQLite")
async def get_profile_settings():
    settings = {}
    for key in PROFILE_SETTING_KEYS:
        val = get_setting(key, "")
        if val: settings[key] = val
    return settings

@router.post("/api/profile-settings", summary="Save profile settings to SQLite")
async def save_profile_settings(payload: ProfileSettingsRequest):
    data = payload.model_dump(exclude_none=True)
    for key, value in data.items():
        if key in PROFILE_SETTING_KEYS and value is not None:
            set_setting(key, value)
    return {"success": True, "database": DB_FILENAME, "saved_keys": list(data.keys()), "message": "Profilinställningar sparade."}

@router.get("/api/site-content", summary="Get all customizable site content, links, and projects from SQLite")
async def get_site_content_endpoint():
    return get_site_content()

@router.post("/api/site-content", summary="Save customizable site content, links, and projects to SQLite")
async def save_site_content_endpoint(payload: Dict[str, Any]):
    updated = save_site_content(payload)
    return {"success": True, "database": DB_FILENAME, "content": updated, "message": "Webbplatsinnehåll och länkar sparade i SQLite-databasen."}