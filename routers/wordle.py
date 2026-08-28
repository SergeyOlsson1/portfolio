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

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TARGETS_FILE = DATA_DIR / "wordle_targets_2k.json"
DICTIONARY_FILE = DATA_DIR / "wordle_dictionary_10k.json"
LEADERBOARD_FILE = BASE_DIR / "leaderboard.json"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

wordle_app = FastAPI(title="Word Matrix - Bot Solver", version="2.1.0")
wordle_app.add_middleware(SessionMiddleware, secret_key="dev_secret_key", max_age=30 * 24 * 60 * 60)
wordle_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Load Datasets
def load_json_dataset(filepath, default_desc=""):
    if filepath.exists():
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {"total": len(data), "description": default_desc, "words": data}
        except Exception: pass
    return {"total": 0, "description": default_desc, "words": []}

targets_data = load_json_dataset(TARGETS_FILE, "Standard Targets")
dictionary_data = load_json_dataset(DICTIONARY_FILE, "Valid Guesses")
WORDLIST = targets_data.get("words", []) if targets_data.get("words") else ["cigar", "rebut", "sissy"]
WORDLIST_SET = set(WORDLIST).union(set(dictionary_data.get("words", [])))

class CheckWordRequest(BaseModel): word: str = ""
class GetHintRequest(BaseModel): rows: List[List[str]] = Field(default_factory=list)
class SaveScoreRequest(BaseModel): name: str = "Anonymous"; wins: int = 0; points: int = 0
class WordValidationRequest(BaseModel): word: str = Field(..., min_length=5, max_length=5)

@wordle_app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("wordle.html", {"request": request})

@wordle_app.post("/check-word")
async def check_word(request: Request, payload: CheckWordRequest):
    secret = request.session.get("secret_word", random.choice(WORDLIST))
    request.session["secret_word"] = secret
    word = payload.word.strip().lower()

    if len(word) != 5: return {"valid": False, "reason": "Word must be 5 letters"}
    if word not in WORDLIST_SET: return {"valid": False, "reason": "Not in wordlist"}

    evaluation = [0]*5
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
    if win: request.session["wins"] = request.session.get("wins", 0) + 1
    
    return {"valid": True, "evaluation": evaluation, "win": win, "wins": request.session.get("wins", 0)}

@wordle_app.post("/get-hint")
async def get_hint(request: Request, payload: GetHintRequest):
    secret = request.session.get("secret_word", random.choice(WORDLIST))
    available_cols = [i for i in range(5) if not any(len(r)>i and r[i]=="correct" for r in payload.rows)]
    if not available_cols: return {"success": False, "message": "All letters guessed"}
    
    col = random.choice(available_cols)
    return {"success": True, "col": col, "letter": secret[col].upper()}

@wordle_app.get("/api/wordle/targets")
async def get_wordle_targets():
    return JSONResponse(content=targets_data)

@wordle_app.get("/api/wordle/dictionary")
async def get_wordle_dictionary():
    return JSONResponse(content=dictionary_data)

@wordle_app.post("/save-score")
async def save_score(payload: SaveScoreRequest):
    data = []
    if LEADERBOARD_FILE.exists():
        try:
            with open(LEADERBOARD_FILE, "r") as f: data = json.load(f)
        except Exception: pass
    data.append({"name": payload.name[:20], "wins": payload.wins, "points": payload.points})
    data.sort(key=lambda x: (x.get("points",0), x.get("wins",0)), reverse=True)
    with open(LEADERBOARD_FILE, "w") as f: json.dump(data[:10], f)
    return {"success": True}

@wordle_app.get("/leaderboard")
async def leaderboard():
    data = []
    if LEADERBOARD_FILE.exists():
        try:
            with open(LEADERBOARD_FILE, "r") as f: data = json.load(f)
        except Exception: pass
    return {"leaderboard": data}

@wordle_app.post("/restart")
async def restart(request: Request):
    request.session["secret_word"] = random.choice(WORDLIST)
    return {"success": True}