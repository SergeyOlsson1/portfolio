from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pathlib import Path
import sqlite3
import json
import os

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

# Persistent storage on Azure App Service vs local workspace
if os.environ.get("WEBSITE_SITE_NAME"):
    DATA_DIR = Path("/home/data")
else:
    DATA_DIR = BASE_DIR / "data"

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "layout.db"
REPO_LAYOUT_PATH = BASE_DIR / "data" / "layout.db"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

class LayoutPayload(BaseModel):
    app_id: str
    data: str

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS layouts (app_id TEXT PRIMARY KEY, layout_data TEXT)''')
        conn.commit()

        # Migrate layout state if persistent database has no saved records
        if REPO_LAYOUT_PATH.exists() and REPO_LAYOUT_PATH != DB_PATH:
            with sqlite3.connect(REPO_LAYOUT_PATH) as repo_conn:
                repo_c = repo_conn.cursor()
                try:
                    c.execute('SELECT COUNT(*) FROM layouts')
                    if c.fetchone()[0] == 0:
                        repo_c.execute('SELECT app_id, layout_data FROM layouts')
                        rows = repo_c.fetchall()
                        c.executemany('INSERT OR IGNORE INTO layouts (app_id, layout_data) VALUES (?, ?)', rows)
                        conn.commit()
                except sqlite3.OperationalError:
                    pass

init_db()

def parse_words_file(*filenames: str) -> list[str]:
    """Finds and parses word lists from repo data or persistent Azure storage."""
    search_directories = [BASE_DIR / "data", DATA_DIR]
    
    for filename in filenames:
        for directory in search_directories:
            file_path = directory / filename
            if file_path.exists():
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = json.load(f)
                        if isinstance(content, list):
                            raw_words = content
                        elif isinstance(content, dict):
                            raw_words = content.get("words", [])
                        else:
                            raw_words = []
                        
                        words = [
                            str(w).strip().upper() 
                            for w in raw_words 
                            if len(str(w).strip()) == 5 and str(w).strip().isalpha()
                        ]
                        if words:
                            return words
                except Exception as e:
                    print(f"Error parsing {file_path}: {e}")
    return []

@router.get("/wordle")
async def serve_wordle(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="wordle.html", 
        context={"page_title": "Word Matrix Bot", "back_url": "/"}
    )

@router.get("/api/wordle-dict")
async def get_wordle_dict():
    guesses = parse_words_file("wordle_dictionary_10k.json")
    targets = parse_words_file("wordle_targets_2k.json", "wordle_targets.json")
    
    # Built-in fallback to ensure gameplay never breaks
    if not targets:
        targets = ["CRANE", "TRACE", "SLATE", "LIONS", "AUDIO", "HOUSE", "WORLD"]
    if not guesses:
        guesses = targets.copy()

    return {"guesses": guesses, "targets": targets}

@router.post("/api/save_layout")
async def save_layout(req: LayoutPayload):
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS layouts (app_id TEXT PRIMARY KEY, layout_data TEXT)''')
            c.execute('''INSERT OR REPLACE INTO layouts (app_id, layout_data) VALUES (?, ?)''', (req.app_id, req.data))
            conn.commit()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/api/load_layout/{app_id}")
async def load_layout(app_id: str):
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS layouts (app_id TEXT PRIMARY KEY, layout_data TEXT)''')
            c.execute('''SELECT layout_data FROM layouts WHERE app_id = ?''', (app_id,))
            row = c.fetchone()
            if row:
                return {"status": "ok", "data": row[0]}
            return {"status": "not_found"}
    except Exception as e:
        return {"status": "error", "message": str(e)}