import os
import platform
import subprocess
import socket
import time
import logging

# Suppress TensorFlow logs before importing anything that might trigger TF
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
logging.getLogger('tensorflow').setLevel(logging.FATAL)

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
import uvicorn

# Import our modularized routers
from routers.portfolio import router as portfolio_router
from routers.ocr import router as ocr_router
from routers.wordle import router as wordle_router

app = FastAPI(title="Portfolio SPA Architecture")

# Mount the static directory so images like rfr_1.png load correctly
app.mount("/static", StaticFiles(directory="static"), name="static")

# Middleware to prevent caching on all dynamic HTML responses
@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    
    # Exclude static assets from no-cache rules so they stay optimized
    if not request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        
    return response

# Register the routes
app.include_router(portfolio_router)
app.include_router(ocr_router)
app.include_router(wordle_router)

def kill_port(port: int):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(('127.0.0.1', port)) != 0:
            return

    try:
        if platform.system() == "Windows":
            result = subprocess.run(f"netstat -ano | findstr :{port} | findstr LISTENING", shell=True, capture_output=True, text=True)
            for line in result.stdout.strip().split('\n'):
                if line:
                    pid = line.strip().split()[-1]
                    subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
        else:
            subprocess.run(f"lsof -t -i:{port} | xargs kill -9", shell=True, capture_output=True)
        time.sleep(1)
    except Exception:
        pass

if __name__ == "__main__":
    PORT = 3001
    
    kill_port(PORT)
    
    print("\n[Application Architecture Loaded Successfully]")
    print(f"http://127.0.0.1:{PORT}\n")
    
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")