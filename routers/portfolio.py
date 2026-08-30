from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pathlib import Path
import sqlite3
import bcrypt

router = APIRouter()

# Go up two levels to reach the project root
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
AUTH_DB_PATH = BASE_DIR / "data" / "database.db"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

class LoginRequest(BaseModel):
    username: str
    password: str

@router.get("/")
async def serve_portfolio(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="portfolio.html", 
        context={"page_title": "Portfolio SPA", "back_url": "/"}
    )

@router.post("/api/login")
def login_admin(req: LoginRequest):
    with sqlite3.connect(AUTH_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT password_hash FROM admin_users WHERE username=?', (req.username,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        stored_hash = row[0]
        if bcrypt.checkpw(req.password.encode('utf-8'), stored_hash.encode('utf-8')):
            return {"status": "ok", "token": "secret-admin-token"}
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")