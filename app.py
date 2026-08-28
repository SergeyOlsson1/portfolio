from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import uvicorn

from database import init_db
from routers.portfolio import router as portfolio_router
from routers.wordle import wordle_app
from routers.ocr import ocr_app

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database
    db_info = init_db()
    print(f"[Lifespan] Database '{db_info.get('database_file')}' initialized at {db_info.get('db_path')}")
    yield

app = FastAPI(
    title="Name Surname Portfolio API",
    description="Modular FastAPI backend for portfolio assets, OCR, and Wordle.",
    version="1.0.0",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Static Files (CSS, JS, Assets)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Mount Routers and Sub-Applications
app.include_router(portfolio_router)
app.mount("/wordle", wordle_app)
app.mount("/ocr", ocr_app)

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=3000, reload=True)