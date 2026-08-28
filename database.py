import os
import json
import secrets
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

DB_NAME = os.getenv("DB_NAME", "portfolio")
DB_FILENAME = f"{DB_NAME}.db"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / DB_FILENAME
ROOT_DB_PATH = BASE_DIR / DB_FILENAME

DEFAULT_SITE_CONTENT = {
    "profile_name": "Name Surname",
    "profile_role_sv": "Example text",
    "profile_role_en": "Example text",
    "profile_location_sv": "Example, text",
    "profile_location_en": "Example, text",
    "profile_exp_sv": "Example, text",
    "profile_exp_en": "Example, text",
    "profile_email": "name.surname@example.com",
    "profile_bio_sv": "Example text",
    "profile_bio_en": "Example text",
    "hero_contact_btn_sv": "Kontakta Mig",
    "hero_contact_btn_en": "Contact Me",
    "hero_contact_url": "mailto:name.surname@example.com",
    "profile_github": "https://github.com",
    "profile_linkedin": "https://linkedin.com",
    "projects_title_sv": "Utvalda Projekt",
    "projects_title_en": "Featured Projects",
    "footer_text_sv": "Alla rättigheter förbehållna.",
    "footer_text_en": "All rights reserved.",
    "projects": [
        {
            "id": "word-matrix",
            "title_sv": "Word Matrix - Bot Solver & Wordle Game",
            "title_en": "Word Matrix - Bot Solver & Wordle Game",
            "desc_sv": "En klon av Wordle med en integrerad AI-bot som kan lösa pussel optimalt.",
            "desc_en": "A Wordle clone with an integrated AI bot that can solve puzzles optimally.",
            "tags": ["FastAPI", "Python", "Tailwind CSS"],
            "image": "https://images.unsplash.com/photo-1647413669642-83b632598379?auto=format&fit=crop&q=80&w=800",
            "demo_url": "/wordle/",
            "github_url": "https://github.com"
        },
        {
            "id": "ai-ocr",
            "title_sv": "AI OCR - Teckenigenkänning",
            "title_en": "AI OCR - Character Recognition",
            "desc_sv": "Applikation för teckenigenkänning av handskrivna svenska bokstäver med hjälp av AI.",
            "desc_en": "Character recognition application for handwritten Swedish letters using AI.",
            "tags": ["FastAPI", "TensorFlow", "Keras"],
            "image": "https://images.unsplash.com/photo-1555949963-aa79dcee981c?auto=format&fit=crop&q=80&w=800",
            "demo_url": "/ocr/",
            "github_url": "https://github.com"
        }
    ]
}

PORTFOLIO_DATA = {
    "metrics": [
        {"id": "years", "value": "8+", "label_sv": "År i Branschen", "label_en": "Years Experience"},
        {"id": "projects", "value": "45+", "label_sv": "Slutförda Projekt", "label_en": "Projects Completed"},
    ],
    "skills": {
        "frontend": [{"name": "React & Next.js", "level": 95}, {"name": "TypeScript", "level": 90}],
        "backend": [{"name": "FastAPI & Python", "level": 92}, {"name": "SQLite & PostgreSQL", "level": 90}]
    }
}

def hash_password(password: str, salt: Optional[str] = None) -> str:
    if salt is None: salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${hashed}"

def verify_password(plain_password: str, stored_hash: str) -> bool:
    if not stored_hash: return False
    if "$" in stored_hash:
        try:
            salt, hash_val = stored_hash.split("$", 1)
            recalculated = hashlib.sha256((salt + plain_password).encode("utf-8")).hexdigest()
            return secrets.compare_digest(recalculated, hash_val)
        except Exception: return False
    return plain_password == stored_hash

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db(force_recreate: bool = False) -> Dict[str, Any]:
    if force_recreate and DB_PATH.exists():
        try: DB_PATH.unlink()
        except Exception: pass

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS sys_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'admin', created_at TEXT NOT NULL)")
        cursor.execute("CREATE TABLE IF NOT EXISTS contacts (id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL, message TEXT NOT NULL, received_at TEXT NOT NULL)")
        cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.commit()
    return get_db_info()

def verify_user(identifier: str, password: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, email, password_hash, name, role FROM users WHERE username = ? OR email = ?", (identifier.strip(), identifier.strip()))
        user = cursor.fetchone()
        if user and verify_password(password, user["password_hash"]):
            return {"id": user["id"], "username": user["username"], "email": user["email"], "name": user["name"], "role": user["role"]}
        return None

def add_contact(name: str, email: str, message: str, entry_id: Optional[str] = None) -> Dict[str, Any]:
    entry_id = entry_id or secrets.token_hex(6)
    received_at = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as conn:
        conn.cursor().execute("INSERT INTO contacts (id, name, email, message, received_at) VALUES (?, ?, ?, ?, ?)", (entry_id, name, email, message, received_at))
        conn.commit()
    return {"id": entry_id, "name": name, "email": email, "message": message, "received_at": received_at}

def get_all_contacts() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(row) for row in conn.cursor().execute("SELECT * FROM contacts ORDER BY received_at DESC").fetchall()]

def get_setting(key: str, default: Any = None) -> Any:
    with get_db_connection() as conn:
        row = conn.cursor().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as conn:
        conn.cursor().execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at", (key, value, now))
        conn.commit()

def get_site_content() -> Dict[str, Any]:
    raw_content = get_setting("site_content")
    content = dict(DEFAULT_SITE_CONTENT)
    if raw_content:
        try: content.update(json.loads(raw_content))
        except Exception: pass
    for key in list(DEFAULT_SITE_CONTENT.keys()):
        if key != "projects":
            val = get_setting(key)
            if val: content[key] = val
    return content

def save_site_content(updated_content: Dict[str, Any]) -> Dict[str, Any]:
    current = get_site_content()
    current.update(updated_content)
    for key, val in updated_content.items():
        if isinstance(val, (str, int, float, bool)):
            set_setting(key, str(val))
    set_setting("site_content", json.dumps(current, ensure_ascii=False))
    return current

def get_db_info() -> Dict[str, Any]:
    try:
        with get_db_connection() as conn:
            user_count = conn.cursor().execute("SELECT COUNT(*) as count FROM users").fetchone()["count"]
            contact_count = conn.cursor().execute("SELECT COUNT(*) as count FROM contacts").fetchone()["count"]
            return {"status": "connected", "database_file": DB_FILENAME, "db_path": str(DB_PATH), "user_count": user_count, "contact_count": contact_count}
    except Exception as e:
        return {"status": "error", "error": str(e)}