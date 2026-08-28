from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image
from io import BytesIO
from pathlib import Path
from tensorflow.keras.models import load_model
import base64
import numpy as np

# Adjust base dir to step out of 'routers/'[cite: 1]
BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

ocr_app = FastAPI(title="Character Recognition API", version="2.0.0")

# Mount static directory for HTML, CSS, and JS[cite: 1]
ocr_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
                # Kept exact original load_model & warmup[cite: 1]
                model = load_model(model_path)
                loaded_models[model_name] = model
                # Warm-up run to prevent delay on first user click
                model.predict(np.zeros((1, 28, 28, 1)), verbose=0)
                print(f"Loaded model: {model_name}")
            except Exception as e:
                print(f"Error loading {model_name}: {e}")

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

def predict_single_model(model_name: str, pixels_28x28: np.ndarray):
    model = loaded_models.get(model_name)
    if model is None:
        return []

    model_input = pixels_28x28.reshape(1, 28, 28, 1) / 255.0
    probs = model.predict(model_input, verbose=0)[0]
    options = []

    # Swedish Characters Model[cite: 1]
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

    # EMNIST Byclass Model (62 classes)[cite: 1]
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

class PredictRequest(BaseModel):
    image: str
    model: str = "Alla modeller"
    lang: str = "SWE"

@ocr_app.get("/")
async def serve_index():
    index_file = TEMPLATES_DIR / "ocr.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="OCR App UI not found.")
    return FileResponse(str(index_file), media_type="text/html")

@ocr_app.get("/api/models")
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

@ocr_app.post("/api/predict")
async def predict_character(req: PredictRequest):
    if not req.image:
        return {"status": "error", "message": "Ingen bild mottogs / No image provided."}

    # Decode base64 image[cite: 1]
    try:
        raw_b64 = req.image
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(raw_b64)
        img = Image.open(BytesIO(img_bytes)).convert("L")
    except Exception as e:
        return {"status": "error", "message": f"Kunde inte tolka bilden: {e}"}

    arr = np.array(img)

    # Invert if light background[cite: 1]
    if arr.mean() > 127:
        arr = 255 - arr

    # Filter background noise[cite: 1]
    threshold = min(30, np.percentile(arr, 95) * 0.4) if arr.max() > 0 else 30
    arr[arr < threshold] = 0

    coords = np.argwhere(arr > 0)
    if coords.size == 0:
        return {"status": "error", "message": "Inget tecken hittades / No character detected."}

    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0)
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

    # Generate 28x28 base64 preview[cite: 1]
    buffered = BytesIO()
    img_28.save(buffered, format="PNG")
    preview_b64 = "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode("utf-8")

    # Run Model Inference[cite: 1]
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