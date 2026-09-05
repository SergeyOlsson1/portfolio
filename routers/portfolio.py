from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pathlib import Path
import sqlite3
import bcrypt
import json
import os

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
REPO_DB_PATH = BASE_DIR / "data" / "database.db"

# Check if running on Azure, route to persistent storage if true
if os.environ.get("WEBSITE_SITE_NAME"):
    DATA_DIR = Path("/home/data")
else:
    DATA_DIR = BASE_DIR / "data"

DATA_DIR.mkdir(parents=True, exist_ok=True)
AUTH_DB_PATH = DATA_DIR / "database.db"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

class LoginRequest(BaseModel):
    username: str
    password: str

class PortfolioState(BaseModel):
    data: str

def init_db():
    with sqlite3.connect(AUTH_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS admin_users (username TEXT PRIMARY KEY, password_hash TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS portfolio_state (id INTEGER PRIMARY KEY, state_data TEXT)''')
        conn.commit()

        if REPO_DB_PATH.exists():
            with sqlite3.connect(REPO_DB_PATH) as repo_conn:
                repo_c = repo_conn.cursor()
                
                # Migrate admin credentials if persistent database is empty
                try:
                    c.execute('SELECT COUNT(*) FROM admin_users')
                    if c.fetchone()[0] == 0:
                        repo_c.execute('SELECT username, password_hash FROM admin_users')
                        users = repo_c.fetchall()
                        c.executemany('INSERT OR IGNORE INTO admin_users (username, password_hash) VALUES (?, ?)', users)
                except sqlite3.OperationalError:
                    pass

                # Migrate portfolio state if persistent database has no state saved
                try:
                    c.execute('SELECT COUNT(*) FROM portfolio_state')
                    if c.fetchone()[0] == 0:
                        repo_c.execute('SELECT id, state_data FROM portfolio_state WHERE id = 1')
                        row = repo_c.fetchone()
                        if row:
                            c.execute('INSERT OR REPLACE INTO portfolio_state (id, state_data) VALUES (?, ?)', row)
                except sqlite3.OperationalError:
                    pass

        conn.commit()

init_db()

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

@router.get("/api/portfolio_data")
def get_portfolio_data():
    with sqlite3.connect(AUTH_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT state_data FROM portfolio_state WHERE id = 1')
        row = c.fetchone()
        if row and row[0]:
            return {"status": "ok", "data": json.loads(row[0])}
        return {"status": "empty"}

@router.post("/api/portfolio_data")
def save_portfolio_data(payload: PortfolioState):
    if not payload.data or payload.data.strip() == "{}":
        raise HTTPException(status_code=400, detail="Refusing to overwrite with empty state")
    
    try:
        json.loads(payload.data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    with sqlite3.connect(AUTH_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO portfolio_state (id, state_data) VALUES (1, ?)', (payload.data,))
        conn.commit()
    return {"status": "ok"}