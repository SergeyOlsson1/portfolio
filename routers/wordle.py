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

# Check if running on Azure, route to persistent storage if true
if os.environ.get("WEBSITE_SITE_NAME"):
    DATA_DIR = Path("/home/data")
else:
    DATA_DIR = BASE_DIR / "data"

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "layout.db"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

class LayoutPayload(BaseModel):
    app_id: str
    data: str

@router.get("/wordle")
async def serve_wordle(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="wordle.html", 
        context={"page_title": "Word Matrix Bot", "back_url": "/"}
    )

@router.get("/api/wordle-dict")
async def get_wordle_dict():
    """Serves the Wordle JSON dictionaries to the frontend."""
    # Data directory for JSON dicts remains in the repo structure
    repo_data_path = BASE_DIR / "data"
    guesses_path = repo_data_path / "wordle_dictionary_10k.json"
    targets_path = repo_data_path / "wordle_targets_2k.json"
    
    guesses = []
    targets = []
    
    try:
        if guesses_path.exists():
            with open(guesses_path, "r", encoding="utf-8") as f:
                guesses = json.load(f).get("words", [])
        if targets_path.exists():
            with open(targets_path, "r", encoding="utf-8") as f:
                targets = json.load(f).get("words", [])
    except Exception as e:
        print(f"Error reading dicts: {e}")
        
    return {"guesses": guesses, "targets": targets}

@router.post("/api/save_layout")
async def save_layout(req: LayoutPayload):
    try:
        with sqlite3.connect(DB_PATH) as conn:
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
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS layouts (app_id TEXT PRIMARY KEY, layout_data TEXT)''')
            c.execute('''SELECT layout_data FROM layouts WHERE app_id = ?''', (app_id,))
            row = c.fetchone()
            if row:
                return {"status": "ok", "data": row[0]}
            return {"status": "not_found"}
    except Exception as e:
        return {"status": "error", "message": str(e)}