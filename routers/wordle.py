from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from typing import List
from pathlib import Path
import json
import random
import requests
import os

# Adjusted base dir to step out of routers/
BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
TARGETS_FILE = DATA_DIR / "wordle_targets_2k.json"
DICTIONARY_FILE = DATA_DIR / "wordle_dictionary_10k.json"
LEADERBOARD_FILE = BASE_DIR / "leaderboard.json"

WORD_LENGTH = 5
FALLBACK_WORDS = ["cigar", "rebut", "sissy", "humph", "awake", "blush", "focal"]

def fetch_word_from_api() -> list[str]:
    url = "https://gist.githubusercontent.com/mrhead/f0ced2726394588e8d9863e0568b6473/raw/wordle.json"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            words_file = DATA_DIR / "words.json"
            words_file.parent.mkdir(exist_ok=True)
            with open(words_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return data
    except Exception: pass
    return FALLBACK_WORDS

def load_wordlist() -> list[str]:
    if TARGETS_FILE.exists():
        try:
            with open(TARGETS_FILE, encoding="utf-8") as f:
                data = json.load(f)
                words = data.get("words", []) if isinstance(data, dict) else data
                if isinstance(words, list) and len(words) > 0:
                    return [w.strip().lower() for w in words if isinstance(w, str) and len(w.strip()) == 5]
        except Exception: pass
    return fetch_word_from_api()

def load_json_dataset(filepath, default_desc=""):
    if filepath.exists():
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict): return data
                if isinstance(data, list): return {"total": len(data), "description": default_desc, "words": data}
        except Exception as e: print(f"Error loading dataset: {e}")
    return {"total": 0, "description": default_desc, "words": []}

targets_data = load_json_dataset(TARGETS_FILE, "Standard Wordle Target Answer List")
dictionary_data = load_json_dataset(DICTIONARY_FILE, "Extended Wordle Valid Guesses List")

WORDLIST = load_wordlist()
WORDLIST_SET = set(WORDLIST).union(
    set(w.strip().lower() for w in targets_data.get("words", []) if isinstance(w, str))
).union(
    set(w.strip().lower() for w in dictionary_data.get("words", []) if isinstance(w, str))
)

wordle_app = FastAPI(title="Word Matrix - Bot Solver", version="2.1.0")
wordle_app.add_middleware(SessionMiddleware, secret_key="dev_secret_key", max_age=30 * 24 * 60 * 60)
wordle_app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

class SaveScoreRequest(BaseModel):
    name: str = "Anonymous"
    wins: int = 0
    points: int = 0

class CheckWordRequest(BaseModel):
    word: str = ""

class GetHintRequest(BaseModel):
    rows: List[List[str]] = Field(default_factory=list)

class WordValidationRequest(BaseModel):
    word: str = Field(..., min_length=5, max_length=5)

def get_score(player_entry: dict):
    return (player_entry.get("points", 0), player_entry.get("wins", 0))

def save_to_json(name: str, win_count: int, point_count: int):
    data = []
    if LEADERBOARD_FILE.exists():
        try:
            with open(LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, list): data = []
        except Exception: data = []

    clean_name = name.strip()[:20] if name and name.strip() else "Anonymous"
    data.append({"name": clean_name, "wins": max(0, int(win_count)), "points": max(0, int(point_count))})
    data.sort(key=get_score, reverse=True)
    
    with open(LEADERBOARD_FILE, "w", encoding="utf-8") as f:
        json.dump(data[:10], f, indent=2)

@wordle_app.get("/", response_class=HTMLResponse)
@wordle_app.get("/wordle", response_class=HTMLResponse)
@wordle_app.get("/bot", response_class=HTMLResponse)
@wordle_app.get("/matrix", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="wordle.html")

@wordle_app.get("/leaderboard")
async def leaderboard():
    data = []
    if LEADERBOARD_FILE.exists():
        try:
            with open(LEADERBOARD_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        except Exception: pass
    return {"leaderboard": data}

@wordle_app.post("/save-score")
async def save_score(payload: SaveScoreRequest):
    save_to_json(payload.name, payload.wins, payload.points)
    return {"success": True}

@wordle_app.post("/check-word")
async def check_word(request: Request, payload: CheckWordRequest):
    secret = request.session.get("secret_word")
    if not secret:
        secret = random.choice(WORDLIST).strip().lower()
        request.session["secret_word"] = secret
        request.session["game_hint_used"] = False

    word = payload.word.strip().lower()
    if len(word) != 5: return {"valid": False, "reason": "Word must be 5 letters"}
    if word not in WORDLIST_SET: return {"valid": False, "reason": "Not in wordlist"}

    evaluation = [0] * 5
    letter_counts = {char: secret.count(char) for char in secret}

    for i in range(5):
        if word[i] == secret[i]:
            evaluation[i] = 2
            letter_counts[word[i]] -= 1

    for i in range(5):
        if evaluation[i] != 2 and letter_counts.get(word[i], 0) > 0:
            evaluation[i] = 1
            letter_counts[word[i]] -= 1

    win = (word == secret)
    if win:
        current_wins = request.session.get("wins", 0) + 1
        request.session["wins"] = current_wins
        request.session["secret_word"] = random.choice(WORDLIST).strip().lower()
        request.session["game_hint_used"] = False
        request.session.pop("hint_col", None)
        request.session.pop("hint_letter", None)
    else:
        current_wins = request.session.get("wins", 0)

    return {"valid": True, "evaluation": evaluation, "win": win, "wins": current_wins}

@wordle_app.post("/get-hint")
async def get_hint(request: Request, payload: GetHintRequest):
    secret = request.session.get("secret_word")
    if not secret:
        secret = random.choice(WORDLIST).strip().lower()
        request.session["secret_word"] = secret

    if request.session.get("game_hint_used", False):
        return {"success": True, "col": request.session.get("hint_col"), "letter": request.session.get("hint_letter")}

    available_cols = [i for i in range(5) if not any(len(row)>i and row[i]=="correct" for row in payload.rows)]
    if not available_cols: return {"success": False, "message": "All letters already guessed"}

    col = random.choice(available_cols)
    letter = secret[col].upper()
    request.session["game_hint_used"] = True
    request.session["hint_col"] = col
    request.session["hint_letter"] = letter
    return {"success": True, "col": col, "letter": letter}

@wordle_app.post("/restart")
async def restart(request: Request):
    request.session["secret_word"] = random.choice(WORDLIST).strip().lower()
    request.session["game_hint_used"] = False
    request.session.pop("hint_col", None)
    request.session.pop("hint_letter", None)
    request.session["wins"] = 0
    return {"success": True, "secret": request.session["secret_word"]}

@wordle_app.get("/api/health")
async def health_check():
    return JSONResponse(content={"status": "healthy", "targets_count": len(targets_data.get("words", []))})

@wordle_app.get("/api/wordle/targets")
async def get_wordle_targets():
    return JSONResponse(content=targets_data)

@wordle_app.get("/api/wordle/dictionary")
async def get_wordle_dictionary():
    return JSONResponse(content=dictionary_data)

@wordle_app.get("/api/wordle/random")
async def get_random_target():
    words = targets_data.get("words", []) or dictionary_data.get("words", [])
    if not words: raise HTTPException(status_code=404, detail="No words available.")
    return JSONResponse(content={"word": random.choice(words)})

@wordle_app.post("/api/wordle/validate")
async def validate_word(payload: WordValidationRequest):
    w = payload.word.strip().upper()
    is_target = w in targets_data.get("words", [])
    is_valid = w in dictionary_data.get("words", []) or is_target
    return JSONResponse(content={"word": w, "is_valid": is_valid, "is_target": is_target, "is_dictionary_word": w in dictionary_data.get("words", [])})