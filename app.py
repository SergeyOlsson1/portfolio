from PIL import Image
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from io import BytesIO
from pathlib import Path
from pydantic import BaseModel, EmailStr, Field
from starlette.middleware.sessions import SessionMiddleware
from tensorflow.keras.models import load_model
from typing import Any, Dict, List, Optional, Tuple
import base64
import hashlib
import json
import numpy as np
import os
import random
import re
import requests
import secrets
import sqlite3
import sys
import uvicorn

########################################
# DATABASE LOGIC
########################################
"""
SQLite Database Module for Portfolio Website.
Standard, discreet local database configuration for application data persistence.
"""


# Configuration Constants - Discreet, standard naming
DB_NAME = os.getenv("DB_NAME", "portfolio")
DB_FILENAME = f"{DB_NAME}.db"

# Paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Main database file in data directory
DB_PATH = DATA_DIR / DB_FILENAME
# Root level copy for direct local access
ROOT_DB_PATH = BASE_DIR / DB_FILENAME


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Generate salted SHA-256 password hash."""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${hashed}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """Verify password against stored hash."""
    if not stored_hash:
        return False
    if "$" in stored_hash:
        try:
            salt, hash_val = stored_hash.split("$", 1)
            recalculated = hashlib.sha256((salt + plain_password).encode("utf-8")).hexdigest()
            return hmac_equal(recalculated, hash_val)
        except Exception:
            return False
    # Fallback direct string match if unhashed
    return plain_password == stored_hash


def hmac_equal(a: str, b: str) -> bool:
    """Constant-time string comparison."""
    return secrets.compare_digest(a, b)


def get_db_connection() -> sqlite3.Connection:
    """Create and return a configured SQLite connection with dict-like row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(force_recreate: bool = False) -> Dict[str, Any]:
    """
    Initialize SQLite database schema and seed administrator account.
    """
    if force_recreate and DB_PATH.exists():
        try:
            DB_PATH.unlink()
        except Exception:
            pass

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. System Metadata Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sys_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        now = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
            INSERT INTO sys_metadata (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, ("app_name", "Portfolio CMS", now))

        cursor.execute("""
            INSERT INTO sys_metadata (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, ("db_version", "1.0.0", now))

        cursor.execute("""
            INSERT INTO sys_metadata (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, ("initialized_at", now, now))

        # 2. Users / Auth Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL
            )
        """)

        # 3. Contacts Messages Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                message TEXT NOT NULL,
                received_at TEXT NOT NULL
            )
        """)

        # 4. Settings Table (Avatar, Customizations)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Migrate existing JSON files if found
        contacts_json = DATA_DIR / "contacts.json"
        if contacts_json.exists():
            try:
                with open(contacts_json, "r", encoding="utf-8") as f:
                    contacts_list = json.load(f)
                    if isinstance(contacts_list, list):
                        for item in contacts_list:
                            cursor.execute("""
                                INSERT OR IGNORE INTO contacts (id, name, email, message, received_at)
                                VALUES (?, ?, ?, ?, ?)
                            """, (
                                item.get("id", secrets.token_hex(6)),
                                item.get("name", ""),
                                item.get("email", ""),
                                item.get("message", ""),
                                item.get("received_at", now)
                            ))
            except Exception:
                pass

        avatar_json = DATA_DIR / "avatar.json"
        if avatar_json.exists():
            try:
                with open(avatar_json, "r", encoding="utf-8") as f:
                    avatar_data = json.load(f)
                    if isinstance(avatar_data, dict) and "avatar_url" in avatar_data:
                        cursor.execute("""
                            INSERT INTO settings (key, value, updated_at)
                            VALUES (?, ?, ?)
                            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                        """, ("avatar_url", avatar_data["avatar_url"], avatar_data.get("updated_at", now)))
            except Exception:
                pass

        conn.commit()

    # Create root level copy for convenience
    try:
        if DB_PATH.exists():
            import shutil
            shutil.copy2(DB_PATH, ROOT_DB_PATH)
    except Exception:
        pass

    return get_db_info()


def verify_user(identifier: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Authenticate user by username or email with securely hashed password verification.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, username, email, password_hash, name, role
            FROM users
            WHERE username = ? OR email = ?
        """, (identifier.strip(), identifier.strip()))
        user = cursor.fetchone()

        if not user:
            return None

        if verify_password(password, user["password_hash"]):
            return {
                "id": user["id"],
                "username": user["username"],
                "email": user["email"],
                "name": user["name"],
                "role": user["role"]
            }
        return None


def add_contact(name: str, email: str, message: str, entry_id: Optional[str] = None) -> Dict[str, Any]:
    """Insert a new contact form message into database."""
    entry_id = entry_id or secrets.token_hex(6)
    received_at = datetime.now(timezone.utc).isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO contacts (id, name, email, message, received_at)
            VALUES (?, ?, ?, ?, ?)
        """, (entry_id, name, email, message, received_at))
        conn.commit()

    return {
        "id": entry_id,
        "name": name,
        "email": email,
        "message": message,
        "received_at": received_at
    }


def get_all_contacts() -> List[Dict[str, Any]]:
    """Retrieve all contact messages ordered from newest to oldest."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, email, message, received_at
            FROM contacts
            ORDER BY received_at DESC
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_setting(key: str, default: Any = None) -> Any:
    """Retrieve a setting value from settings table."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Store or update a setting in settings table."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, (key, value, now))
        conn.commit()


# Default site content schema for all text, links, buttons, and projects
DEFAULT_SITE_CONTENT: Dict[str, Any] = {
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
            "desc_sv": "En klon av Wordle med en integrerad AI-bot som kan lösa pussel optimalt. Inkluderar mörkt läge och global leaderboard.",
            "desc_en": "A Wordle clone with an integrated AI bot that can solve puzzles optimally. Includes dark mode and global leaderboard.",
            "tags": ["FastAPI", "Python", "Tailwind CSS"],
            "image": "https://images.unsplash.com/photo-1647413669642-83b632598379?auto=format&fit=crop&q=80&w=800",
            "demo_url": "/wordle/",
            "demo_label_sv": "Live Demo",
            "demo_label_en": "Live Demo",
            "github_url": "https://github.com",
            "github_label_sv": "Källkod",
            "github_label_en": "Source Code"
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
            "demo_label_sv": "Live Demo",
            "demo_label_en": "Live Demo",
            "github_url": "https://github.com",
            "github_label_sv": "Källkod",
            "github_label_en": "Source Code"
        },
        {
            "id": "roll-for-roleplay",
            "title_sv": "Roll For Roleplay",
            "title_en": "Roll For Roleplay",
            "desc_sv": "Plattform för bordsrollspel.",
            "desc_en": "Tabletop roleplaying platform.",
            "tags": ["Roleplay", "TTRPG"],
            "image": "/static/rfr_1.png",
            "demo_url": "https://www.rollforroleplay.com/",
            "demo_label_sv": "Besök Hemsidan",
            "demo_label_en": "Visit Website",
            "github_url": "",
            "github_label_sv": "",
            "github_label_en": ""
        }
    ]
}


def get_site_content() -> Dict[str, Any]:
    """Retrieve full customizable site content with defaults fallback."""
    raw_content = get_setting("site_content")
    content = dict(DEFAULT_SITE_CONTENT)

    if raw_content:
        try:
            stored = json.loads(raw_content)
            if isinstance(stored, dict):
                content.update(stored)
        except Exception:
            pass

    # Also overlay individual profile settings if they were set separately
    for key in list(DEFAULT_SITE_CONTENT.keys()):
        if key != "projects":
            val = get_setting(key)
            if val:
                content[key] = val

    return content


def save_site_content(updated_content: Dict[str, Any]) -> Dict[str, Any]:
    """Store or update full site content in SQLite settings table."""
    current = get_site_content()
    current.update(updated_content)

    # Sync individual keys in settings table
    for key, val in updated_content.items():
        if isinstance(val, (str, int, float, bool)):
            set_setting(key, str(val))

    # Store full JSON in site_content key
    set_setting("site_content", json.dumps(current, ensure_ascii=False))
    return current


def get_db_info() -> Dict[str, Any]:
    """Get database summary information."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM users")
            user_count = cursor.fetchone()["count"]

            cursor.execute("SELECT COUNT(*) as count FROM contacts")
            contact_count = cursor.fetchone()["count"]

            cursor.execute("SELECT key, value FROM sys_metadata")
            metadata = {row["key"]: row["value"] for row in cursor.fetchall()}

            return {
                "status": "connected",
                "database_file": DB_FILENAME,
                "db_path": str(DB_PATH),
                "user_count": user_count,
                "contact_count": contact_count,
                "metadata": metadata
            }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "database_file": DB_FILENAME,
            "db_path": str(DB_PATH)
        }




########################################
# WORDLE LOGIC
########################################

LETTER_NOT_IN_WORD = 0
LETTER_CORRECT_WRONG_POS = 1
LETTER_PERFECT = 2

BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)

FALLBACK_WORDS = [
    "cigar", "rebut", "sissy", "humph", "awake", "blush", "focal", "evade",
    "naval", "serve", "heath", "dwarf", "model", "karma", "stink", "grade",
    "quiet", "bench", "abate", "feign", "major", "death", "fresh", "crust",
    "stool", "colon", "abase", "marry", "react", "batty", "pride", "floss",
    "helix", "croak", "staff", "paper", "unfed", "whelp", "trawl", "outdo",
    "adobe", "crazy", "sower", "repay", "digit", "crate", "cluck", "spike",
    "mimic", "pound", "maxim", "linen", "unmet", "flesh", "booby", "forth",
    "first", "stand", "belly", "ivory", "seedy", "print", "yearn", "drain",
    "apple", "crane", "slate", "sound", "plant", "trace", "house", "light"
]


def fetch_word_from_api() -> list[str]:
    url = "https://gist.githubusercontent.com/mrhead/f0ced2726394588e8d9863e0568b6473/raw/wordle.json"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            words_file = os.path.join(BASE_DIR, "data", "words.json")
            os.makedirs(os.path.dirname(words_file), exist_ok=True)
            with open(words_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return data
    except Exception:
        pass
    return FALLBACK_WORDS


def generate_word(words: list[str]) -> str:
    if not words:
        words = FALLBACK_WORDS
    return random.choice(words).strip().lower()


def win_validation(guessed_word: str, generated_word: str) -> bool:
    if not isinstance(guessed_word, str) or not isinstance(generated_word, str):
        return False
    return guessed_word.strip().lower() == generated_word.strip().lower()


def try_again(yes: bool) -> bool:
    return bool(yes)


def validate_word_length(guessed_word: str) -> bool:
    return isinstance(guessed_word, str) and len(guessed_word.strip()) == 5


def letter_check(generated_word: str, guessed_word: str) -> list[int]:
    if not isinstance(generated_word, str) or not isinstance(guessed_word, str):
        raise TypeError("Both generated_word and guessed_word must be strings")

    if len(guessed_word) != len(generated_word):
        raise IndexError("Guessed word length must match generated word length")

    gen = generated_word.lower()
    guess = guessed_word.lower()
    n = len(gen)

    result = [LETTER_NOT_IN_WORD] * n

    letter_counts: dict[str, int] = {}
    for char in gen:
        letter_counts[char] = letter_counts.get(char, 0) + 1

    for i in range(n):
        if guess[i] == gen[i]:
            result[i] = LETTER_PERFECT
            letter_counts[guess[i]] -= 1

    for i in range(n):
        if result[i] != LETTER_PERFECT:
            char = guess[i]
            if letter_counts.get(char, 0) > 0:
                result[i] = LETTER_CORRECT_WRONG_POS
                letter_counts[char] -= 1

    return result


def load_wordlist() -> list[str]:
    data_path = os.path.join(BASE_DIR, "data", "wordle_targets_2k.json")
    if os.path.exists(data_path):
        try:
            with open(data_path, encoding="utf-8") as f:
                data = json.load(f)
                words = data.get("words", []) if isinstance(data, dict) else data
                if isinstance(words, list) and len(words) > 0:
                    return [w.strip().lower() for w in words if isinstance(w, str) and len(w.strip()) == 5]
        except Exception:
            pass

    path = os.path.join(BASE_DIR, "data", "words.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                words = json.load(f)
                if isinstance(words, list) and len(words) > 0:
                    return [w.strip().lower() for w in words if isinstance(w, str) and len(w.strip()) == 5]
        except Exception:
            pass

    return fetch_word_from_api()

########################################
# WORDLE APP
########################################


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TARGETS_FILE = os.path.join(DATA_DIR, "wordle_targets_2k.json")
DICTIONARY_FILE = os.path.join(DATA_DIR, "wordle_dictionary_10k.json")
LEADERBOARD_FILE = os.path.join(BASE_DIR, "leaderboard.json")

def load_json_dataset(filepath: str, default_desc: str = ""):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
                if isinstance(data, list):
                    return {"total": len(data), "description": default_desc, "words": data}
        except Exception as e:
            print(f"Error loading dataset from {filepath}: {e}")
    return {"total": 0, "description": default_desc, "words": []}

targets_data = load_json_dataset(TARGETS_FILE, "Standard Wordle Target Answer List")
dictionary_data = load_json_dataset(DICTIONARY_FILE, "Extended Wordle Valid Guesses List")

WORDLIST = load_wordlist()
WORDLIST_SET = set(WORDLIST).union(
    set(w.strip().lower() for w in targets_data.get("words", []) if isinstance(w, str))
).union(
    set(w.strip().lower() for w in dictionary_data.get("words", []) if isinstance(w, str))
)
WORD_LENGTH = 5

project_1_app = FastAPI(
    title="Word Matrix - Bot Solver & Wordle Game",
    description="Unified Word Matrix application featuring smart constraint solving, letter hints, timer challenge, dark mode, and global leaderboard.",
    version="2.1.0"
)

project_1_app.add_middleware(
    SessionMiddleware,
    secret_key="dev_secret_key",
    max_age=30 * 24 * 60 * 60
)

project_1_app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


class SaveScoreRequest(BaseModel):
    name: str = "Anonymous"
    wins: int = 0
    points: int = 0


class CheckWordRequest(BaseModel):
    word: str = ""


class GetHintRequest(BaseModel):
    rows: List[List[str]] = Field(default_factory=list)


class WordValidationRequest(BaseModel):
    word: str = Field(..., min_length=5, max_length=5, description="5-letter uppercase word to validate")


def get_score(player_entry: dict):
    return (player_entry.get("points", 0), player_entry.get("wins", 0))


def save_to_json(name: str, win_count: int, point_count: int):
    data = []
    if os.path.exists(LEADERBOARD_FILE):
        try:
            with open(LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, list):
                    data = []
        except Exception:
            data = []

    clean_name = name.strip()[:20] if name and name.strip() else "Anonymous"
    new_entry = {
        "name": clean_name,
        "wins": max(0, int(win_count)),
        "points": max(0, int(point_count))
    }
    data.append(new_entry)
    data.sort(key=get_score, reverse=True)
    top_ten = data[:10]

    with open(LEADERBOARD_FILE, "w", encoding="utf-8") as f:
        json.dump(top_ten, f, indent=2)


# ==========================================
# Web Routes - Word Matrix Primary Game
# ==========================================

@project_1_app.get("/", response_class=HTMLResponse)
@project_1_app.get("/wordle", response_class=HTMLResponse)
@project_1_app.get("/bot", response_class=HTMLResponse)
@project_1_app.get("/matrix", response_class=HTMLResponse)
async def home(request: Request):
    """Renders the Unified Word Matrix Bot Solver Game."""
    return templates.TemplateResponse(
        request=request,
        name="wordle.html"
    )


# ==========================================
# Leaderboard & Score Endpoints
# ==========================================

@project_1_app.get("/leaderboard")
async def leaderboard():
    """Retrieves top 10 leaderboard rankings."""
    data = []
    if os.path.exists(LEADERBOARD_FILE):
        try:
            with open(LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, list):
                    data = []
        except Exception:
            data = []
    return {"leaderboard": data}


@project_1_app.post("/save-score")
async def save_score(payload: SaveScoreRequest):
    """Saves player score to leaderboard."""
    save_to_json(payload.name, payload.wins, payload.points)
    return {"success": True}


# ==========================================
# Session & Logic Support Endpoints
# ==========================================

@project_1_app.post("/check-word")
async def check_word(request: Request, payload: CheckWordRequest):
    secret = request.session.get("secret_word")
    if not secret:
        secret = generate_word(WORDLIST)
        request.session["secret_word"] = secret
        request.session["game_hint_used"] = False

    word = payload.word.strip().lower()

    if not validate_word_length(word):
        return {"valid": False, "reason": "Word must be 5 letters"}

    if word not in WORDLIST_SET:
        return {"valid": False, "reason": "Not in wordlist"}

    evaluation = letter_check(secret, word)
    win = win_validation(word, secret)

    if win:
        current_wins = request.session.get("wins", 0) + 1
        request.session["wins"] = current_wins
        request.session["secret_word"] = generate_word(WORDLIST)
        request.session["game_hint_used"] = False
        request.session.pop("hint_col", None)
        request.session.pop("hint_letter", None)
    else:
        current_wins = request.session.get("wins", 0)

    return {
        "valid": True,
        "evaluation": evaluation,
        "win": win,
        "wins": current_wins
    }


@project_1_app.post("/get-hint")
async def get_hint(request: Request, payload: GetHintRequest):
    secret = request.session.get("secret_word")
    if not secret:
        secret = generate_word(WORDLIST)
        request.session["secret_word"] = secret
        request.session["game_hint_used"] = False
        request.session.pop("hint_col", None)
        request.session.pop("hint_letter", None)

    if request.session.get("game_hint_used", False):
        col = request.session.get("hint_col")
        letter = request.session.get("hint_letter")
        if col is not None and letter is not None:
            return {"success": True, "col": col, "letter": letter}
        return {"success": False, "message": "Hint already used"}

    guessed_rows = payload.rows

    available_cols = []
    for i in range(WORD_LENGTH):
        col_correct = any(len(row) > i and row[i] == "correct" for row in guessed_rows)
        if not col_correct:
            available_cols.append(i)

    if not available_cols:
        return {"success": False, "message": "All letters already guessed"}

    col = random.choice(available_cols)
    letter = secret[col].upper()

    request.session["game_hint_used"] = True
    request.session["hint_col"] = col
    request.session["hint_letter"] = letter

    return {"success": True, "col": col, "letter": letter}


@project_1_app.post("/restart")
async def restart(request: Request):
    request.session["secret_word"] = generate_word(WORDLIST)
    request.session["game_hint_used"] = False
    request.session.pop("hint_col", None)
    request.session.pop("hint_letter", None)
    request.session["wins"] = 0
    return {"success": True, "secret": request.session["secret_word"]}


# ==========================================
# Word Matrix & Bot Solver Dataset Endpoints
# ==========================================

@project_1_app.get("/api/health")
async def health_check():
    """Health check and dataset statistics."""
    return JSONResponse(content={
        "status": "healthy",
        "app": "Word Matrix - Wordle Game & Bot Engine",
        "targets_count": len(targets_data.get("words", [])),
        "dictionary_count": len(dictionary_data.get("words", []))
    })


@project_1_app.get("/api/wordle/targets")
async def get_wordle_targets():
    """Returns official standard Wordle target words (~2.3k dataset)."""
    if targets_data.get("words"):
        return JSONResponse(content=targets_data)
    raise HTTPException(status_code=404, detail="Target word list dataset not found.")


@project_1_app.get("/api/wordle/dictionary")
async def get_wordle_dictionary():
    """Returns full extended Wordle valid guesses dictionary (~12.9k dataset)."""
    if dictionary_data.get("words"):
        return JSONResponse(content=dictionary_data)
    raise HTTPException(status_code=404, detail="Extended dictionary dataset not found.")


@project_1_app.get("/api/wordle/random")
async def get_random_target():
    """Returns a single randomly selected target word."""
    words = targets_data.get("words", [])
    if not words:
        words = dictionary_data.get("words", [])
    if not words:
        raise HTTPException(status_code=404, detail="No words available in dataset.")
    random_word = random.choice(words)
    return JSONResponse(content={"word": random_word})


@project_1_app.post("/api/wordle/validate")
async def validate_word(payload: WordValidationRequest):
    """Validates if a 5-letter word is in the dictionary or target dataset."""
    w = payload.word.strip().upper()
    if len(w) != 5:
        raise HTTPException(status_code=400, detail="Word must be exactly 5 letters.")

    is_valid_guess = w in dictionary_data.get("words", [])
    is_target_word = w in targets_data.get("words", [])

    return JSONResponse(content={
        "word": w,
        "is_valid": is_valid_guess or is_target_word,
        "is_target": is_target_word,
        "is_dictionary_word": is_valid_guess
    })




########################################
# OCR APP
########################################



# ==============================================================================
# Model Loading & Configuration
# ==============================================================================

BASE_DIR = Path(__file__).resolve().parent

MODELS_DIR = BASE_DIR / "models"
loaded_models = {}

TARGET_MODELS = [
    "emnist_byclass_model.keras",
    "swe_chars_model.keras",
]

if MODELS_DIR.exists() and MODELS_DIR.is_dir():
    for model_name in TARGET_MODELS:
        model_path = MODELS_DIR / model_name
        if model_path.exists() and model_path.is_file():
            try:
                model = load_model(model_path)
                loaded_models[model_name] = model
                # Warm-up run to prevent delay on first user click
                model.predict(np.zeros((1, 28, 28, 1)), verbose=0)
                print(f"Loaded model: {model_name}")
            except Exception as e:
                print(f"Error loading {model_name}: {e}")

MODEL_ACCURACY = {
    "emnist_byclass_model.keras": 95.0,
    "swe_chars_model.keras": 90.0,
}

SWE_MAPPING = {0: 'Å', 1: 'Ä', 2: 'Ö', 3: 'å', 4: 'ä', 5: 'ö', 6: 'null'}


def emnist_idx_to_char(i: int) -> str:
    """Map EMNIST ByClass index (0-61) to corresponding character."""
    if i <= 9:
        return str(i)
    elif i <= 35:
        return chr(i - 10 + ord('A'))
    else:
        return chr(i - 36 + ord('a'))


def resolve_model_filename(choice: str) -> str:
    if choice in ["Alla modeller", "All models"]:
        return "Alla modeller"
    for m in loaded_models.keys():
        clean_name = m.replace(".keras", "").replace("_", " ").title()
        if choice.lower() == clean_name.lower() or choice == m:
            return m
    return choice


# ==============================================================================
# Inference Logic
# ==============================================================================

def predict_single_model(model_name: str, pixels_28x28: np.ndarray):
    model = loaded_models.get(model_name)
    if model is None:
        return []

    model_input = pixels_28x28.reshape(1, 28, 28, 1) / 255.0
    probs = model.predict(model_input, verbose=0)[0]
    options = []

    # Swedish Characters Model
    if "swe_chars" in model_name:
        best_idx = int(np.argmax(probs))
        best_char = SWE_MAPPING.get(best_idx, str(best_idx))
        if best_char.lower() == "null":
            return []

        top_indices = np.argsort(probs)[::-1]
        for idx in top_indices:
            char = SWE_MAPPING.get(int(idx), str(idx))
            if char.lower() != "null" and probs[idx] >= 0.15:
                options.append(char)

        if not options and best_char.lower() != "null":
            options.append(best_char)

        return options[:3]

    # EMNIST Byclass Model (62 classes)
    top_3_indices = np.argsort(probs)[-3:][::-1]
    for i in top_3_indices:
        if probs[i] >= 0.10:
            options.append(emnist_idx_to_char(int(i)))

    if not options:
        options.append(emnist_idx_to_char(int(top_3_indices[0])))

    return options[:3]


def predict_all_models(pixels_28x28: np.ndarray):
    """
    Combined mode:
    Checks if Swe Chars model detects a Swedish letter (Å, Ä, Ö, å, ä, ö).
    If so, returns Swedish candidate recommendations.
    Otherwise, returns top EMNIST candidates.
    """
    if "swe_chars_model.keras" in loaded_models:
        swe_options = predict_single_model("swe_chars_model.keras", pixels_28x28)
        if swe_options:
            return swe_options

    if "emnist_byclass_model.keras" in loaded_models:
        return predict_single_model("emnist_byclass_model.keras", pixels_28x28)

    return []


# ==============================================================================
# FastAPI Application
# ==============================================================================

project_2_app = FastAPI(title="Character Recognition API", version="2.0.0")

# Mount static directory for HTML, CSS, and JS
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
project_2_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class PredictRequest(BaseModel):
    image: str
    model: str = "Alla modeller"
    lang: str = "SWE"


@project_2_app.get("/api/models")
async def get_models():
    models_list = []
    for model_name in loaded_models.keys():
        clean_name = (
            model_name
            .replace(".keras", "")
            .replace("_", " ")
            .title()
        )
        models_list.append(clean_name)
    models_list.append("Alla modeller")
    return {"models": models_list}


@project_2_app.post("/api/predict")
async def predict_character(req: PredictRequest):
    if not req.image:
        return {"status": "error", "message": "Ingen bild mottogs / No image provided."}

    # Decode base64 image
    try:
        raw_b64 = req.image
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(raw_b64)
        img = Image.open(BytesIO(img_bytes)).convert("L")
    except Exception as e:
        return {"status": "error", "message": f"Kunde inte tolka bilden: {e}"}

    arr = np.array(img)

    # Invert if light background
    if arr.mean() > 127:
        arr = 255 - arr

    # Filter background noise
    threshold = min(30, np.percentile(arr, 95) * 0.4) if arr.max() > 0 else 30
    arr[arr < threshold] = 0

    # Handle image bounds & filter out stray ink/lines relative to the densest parts
    row_sums = arr.sum(axis=1)
    col_sums = arr.sum(axis=0)

    if row_sums.max() == 0 or col_sums.max() == 0:
        return {"status": "error", "message": "Inget tecken hittades / No character detected."}

    # Thresholding at 5% of the max density removes disjointed thin strokes like the "ink at the top" artifact
    row_thresh = row_sums.max() * 0.05
    col_thresh = col_sums.max() * 0.05

    valid_rows = np.argwhere(row_sums > row_thresh)
    valid_cols = np.argwhere(col_sums > col_thresh)

    if valid_rows.size == 0 or valid_cols.size == 0:
        return {"status": "error", "message": "Inget tydligt tecken hittades / No clear character detected."}

    y0, y1 = int(valid_rows.min()), int(valid_rows.max())
    x0, x1 = int(valid_cols.min()), int(valid_cols.max())
    
    cropped = arr[y0:y1 + 1, x0:x1 + 1]

    h, w = cropped.shape
    size = max(h, w)
    square = np.zeros((size, size), dtype=np.uint8)

    y_offset = (size - h) // 2
    x_offset = (size - w) // 2
    square[y_offset:y_offset + h, x_offset:x_offset + w] = cropped

    square_img = Image.fromarray(square)
    square_img.thumbnail((20, 20), Image.Resampling.LANCZOS)

    img_28 = Image.new("L", (28, 28), 0)
    x = (28 - square_img.width) // 2
    y = (28 - square_img.height) // 2
    img_28.paste(square_img, (x, y))

    # Generate 28x28 base64 preview
    buffered = BytesIO()
    img_28.save(buffered, format="PNG")
    preview_b64 = "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode("utf-8")

    # Run Model Inference
    pixels = np.array(img_28)
    real_model_name = resolve_model_filename(req.model)

    if real_model_name == "Alla modeller":
        candidates = predict_all_models(pixels)
    elif real_model_name in loaded_models:
        candidates = predict_single_model(real_model_name, pixels)
    else:
        candidates = []

    return {
        "status": "ok",
        "preview": preview_b64,
        "candidates": candidates
    }


########################################
# MAIN APP
########################################



# Base Directory Setup
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
DATA_DIR = BASE_DIR / "data"

# Ensure data directory exists and SQLite database is initialized
DATA_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database
    db_info = init_db()
    print(f"[Lifespan] Database '{db_info.get('database_file')}' initialized successfully at {db_info.get('db_path')}")
    yield


# Initialize FastAPI App with Lifespan
app = FastAPI(
    title="Name Surname Portfolio API",
    description="FastAPI backend powered by local SQLite database serving portfolio assets, contact messages, admin auth, and avatar persistence.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for local development and external integrations
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Static Files (CSS, JS, Assets)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# Pydantic Schemas
class ContactMessage(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., min_length=3, max_length=150)
    message: str = Field(..., min_length=5, max_length=5000)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=150)  # Can be username or email
    password: str = Field(..., min_length=1, max_length=150)


class AvatarRequest(BaseModel):
    avatar_url: str


# Portfolio Data Definition
PORTFOLIO_DATA = {
    "profile": {
        "name": "Name Surname",
        "role_sv": "Senior Full-Stack Utvecklare & UX-Arkitekt",
        "role_en": "Senior Full-Stack Developer & UX Architect",
        "location_sv": "Stockholm, Sverige",
        "location_en": "Stockholm, Sweden",
        "email": "name.surname@example.com",
        "experience_years": "8+",
        "bio_sv": "Passionerad mjukvaruarkitekt med fokus på skalbara webbapplikationer, intuitiv användarupplevelse och modern molninfrastruktur.",
        "bio_en": "Passionate software architect focused on scalable web applications, intuitive user experiences, and modern cloud infrastructure."
    },
    "metrics": [
        {"id": "years", "value": "8+", "label_sv": "År i Branschen", "label_en": "Years Experience"},
        {"id": "projects", "value": "45+", "label_sv": "Slutförda Projekt", "label_en": "Projects Completed"},
        {"id": "clients", "value": "12", "label_sv": "Glada Klienter", "label_en": "Happy Clients"},
        {"id": "uptime", "value": "99.9%", "label_sv": "Kodkvalitet & Tillgänglighet", "label_en": "Code Quality & Availability"}
    ],
    "skills": {
        "frontend": [
            {"name": "React & Next.js", "level": 95},
            {"name": "TypeScript / JavaScript", "level": 90},
            {"name": "CSS3 & Modern Design Systems", "level": 98},
            {"name": "Vue.js & Nuxt", "level": 80}
        ],
        "backend": [
            {"name": "FastAPI & Python", "level": 92},
            {"name": "SQLite & PostgreSQL", "level": 90},
            {"name": "Node.js & Express", "level": 88},
            {"name": "AWS, Docker & CI/CD", "level": 80}
        ]
    },
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
        },
        {
            "id": "roll-for-roleplay",
            "title_sv": "Roll For Roleplay",
            "title_en": "Roll For Roleplay",
            "desc_sv": "Plattform för bordsrollspel.",
            "desc_en": "Tabletop roleplaying platform.",
            "tags": ["Roleplay", "TTRPG"],
            "image": "/static/rfr_1.png",
            "demo_url": "https://www.rollforroleplay.com/",
            "github_url": ""
        }
    ]
}

# Routes

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.get("/", summary="Serve Portfolio Homepage")
async def read_index():
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        index_file = BASE_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html template not found")
    return FileResponse(str(index_file), media_type="text/html")

@project_2_app.get("/", summary="Serve OCR Web App")
async def serve_index():
    index_file = BASE_DIR / "templates" / "ocr.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="OCR App UI not found.")
    return FileResponse(str(index_file), media_type="text/html")


@app.get("/api/health", summary="Health check endpoint")
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


@app.get("/api/db-status", summary="SQLite Database Status and Information")
async def db_status():
    return get_db_info()


@app.get("/api/portfolio", summary="Get portfolio structured data")
async def get_portfolio_data():
    return PORTFOLIO_DATA


@app.post("/api/contact", summary="Submit contact form message")
async def submit_contact(message: ContactMessage):
    # Save directly to SQLite database
    entry = add_contact(
        name=message.name,
        email=message.email,
        message=message.message
    )
    
    return {
        "success": True,
        "message": "Tack för ditt meddelande! Vi återkommer inom kort.",
        "id": entry["id"]
    }


@app.get("/api/contacts", summary="Get all received contact messages from SQLite (Admin)")
async def get_contacts():
    contacts = get_all_contacts()
    return {
        "total": len(contacts),
        "database": DB_FILENAME,
        "contacts": contacts
    }


@app.post("/api/login", summary="Admin Login via SQLite Authentication")
async def admin_login(creds: LoginRequest):
    # Authenticate against SQLite database
    user = verify_user(creds.email, creds.password)
    
    if user:
        token = secrets.token_urlsafe(32)
        return {
            "success": True,
            "token": token,
            "database": DB_FILENAME,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "email": user["email"],
                "name": user["name"],
                "role": user["role"]
            }
        }
        
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Felaktigt användarnamn eller lösenord."
    )


@app.get("/api/avatar", summary="Get custom avatar from SQLite database")
async def get_avatar():
    avatar_url = get_setting("avatar_url", "")
    return {
        "avatar_url": avatar_url or ""
    }


@app.post("/api/avatar", summary="Save custom avatar to SQLite database")
async def update_avatar(payload: AvatarRequest):
    set_setting("avatar_url", payload.avatar_url)
    return {
        "success": True,
        "database": DB_FILENAME,
        "message": "Avatar sparad framgångsrikt i SQLite-databasen."
    }


# Profile Settings Keys
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


@app.get("/api/profile-settings", summary="Get profile settings from SQLite")
async def get_profile_settings():
    settings = {}
    for key in PROFILE_SETTING_KEYS:
        val = get_setting(key, "")
        if val:
            settings[key] = val
    return settings


@app.post("/api/profile-settings", summary="Save profile settings to SQLite")
async def save_profile_settings(payload: ProfileSettingsRequest):
    data = payload.model_dump(exclude_none=True)
    for key, value in data.items():
        if key in PROFILE_SETTING_KEYS and value is not None:
            set_setting(key, value)
    return {
        "success": True,
        "database": DB_FILENAME,
        "saved_keys": list(data.keys()),
        "message": "Profilinställningar sparade."
    }


@app.get("/api/site-content", summary="Get all customizable site content, links, and projects from SQLite")
async def get_site_content_endpoint():
    return get_site_content()


@app.post("/api/site-content", summary="Save customizable site content, links, and projects to SQLite")
async def save_site_content_endpoint(payload: Dict[str, Any]):
    updated = save_site_content(payload)
    return {
        "success": True,
        "database": DB_FILENAME,
        "content": updated,
        "message": "Webbplatsinnehåll och länkar sparade i SQLite-databasen."
    }



# ==========================================
# Mount Sub-Projects
# ==========================================
app.mount("/wordle", project_1_app)
app.mount("/ocr", project_2_app)

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=3000, reload=True)