from fastapi import APIRouter, HTTPException, status, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from pathlib import Path
import secrets
from datetime import datetime, timezone

from database import (
    add_contact, get_all_contacts, verify_user, get_setting,
    set_setting, get_site_content, save_site_content,
    get_db_info, DB_FILENAME, PORTFOLIO_DATA
)

router = APIRouter()
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

class ContactMessage(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., min_length=3, max_length=150)
    message: str = Field(..., min_length=5, max_length=5000)

class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=1, max_length=150)

class AvatarRequest(BaseModel):
    avatar_url: str

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
        index_file = BASE_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html template not found")
    return FileResponse(str(index_file), media_type="text/html")

@router.get("/api/health")
async def health_check():
    db_info = get_db_info()
    return {
        "status": "healthy",
        "service": "FastAPI Portfolio Web Application",
        "database": {
            "file": DB_FILENAME,
            "status": db_info.get("status"),
            "contacts_stored": db_info.get("contact_count", 0),
            "users_count": db_info.get("user_count", 0)
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0"
    }

@router.get("/api/db-status")
async def db_status():
    return get_db_info()

@router.get("/api/portfolio")
async def get_portfolio_data():
    return PORTFOLIO_DATA

@router.post("/api/contact")
async def submit_contact(message: ContactMessage):
    entry = add_contact(name=message.name, email=message.email, message=message.message)
    return {"success": True, "message": "Tack för ditt meddelande! Vi återkommer inom kort.", "id": entry["id"]}

@router.get("/api/contacts")
async def get_contacts():
    return {"total": len(get_all_contacts()), "database": DB_FILENAME, "contacts": get_all_contacts()}

@router.post("/api/login")
async def admin_login(creds: LoginRequest):
    user = verify_user(creds.email, creds.password)
    if user:
        token = secrets.token_urlsafe(32)
        return {"success": True, "token": token, "user": user}
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Felaktigt användarnamn eller lösenord.")

@router.get("/api/avatar")
async def get_avatar():
    return {"avatar_url": get_setting("avatar_url", "")}

@router.post("/api/avatar")
async def update_avatar(payload: AvatarRequest):
    set_setting("avatar_url", payload.avatar_url)
    return {"success": True, "message": "Avatar sparad framgångsrikt."}

@router.get("/api/profile-settings")
async def get_profile_settings():
    settings = {}
    for key in ProfileSettingsRequest.model_fields.keys():
        val = get_setting(key, "")
        if val: settings[key] = val
    return settings

@router.post("/api/profile-settings")
async def save_profile_settings(payload: ProfileSettingsRequest):
    data = payload.model_dump(exclude_none=True)
    for key, value in data.items():
        set_setting(key, value)
    return {"success": True, "saved_keys": list(data.keys()), "message": "Profilinställningar sparade."}

@router.get("/api/site-content")
async def get_site_content_endpoint():
    return get_site_content()

@router.post("/api/site-content")
async def save_site_content_endpoint(payload: Dict[str, Any]):
    return {"success": True, "content": save_site_content(payload), "message": "Webbplatsinnehåll sparat."}