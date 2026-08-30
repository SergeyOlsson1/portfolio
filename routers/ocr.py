from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from PIL import Image
from io import BytesIO
from pathlib import Path
from tensorflow.keras.models import load_model
from scipy.ndimage import label
import base64
import numpy as np

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
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
                model.predict(np.zeros((1, 28, 28, 1)), verbose=0)
            except Exception:
                pass

SWE_MAPPING = {0: 'Å', 1: 'Ä', 2: 'Ö', 3: 'å', 4: 'ä', 5: 'ö', 6: 'null'}

def emnist_idx_to_char(i: int) -> str:
    if i <= 9: return str(i)
    elif i <= 35: return chr(i - 10 + ord('A'))
    else: return chr(i - 36 + ord('a'))

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
    if model is None: return []

    model_input = pixels_28x28.reshape(1, 28, 28, 1) / 255.0
    probs = model.predict(model_input, verbose=0)[0]
    options = []

    if "swe_chars" in model_name:
        top_indices = np.argsort(probs)[::-1]
        for idx in top_indices:
            char = SWE_MAPPING.get(int(idx), str(idx))
            if char.lower() != "null" and probs[idx] >= 0.15:
                options.append((char, float(probs[idx])))
        return options[:3]

    top_indices = np.argsort(probs)[-3:][::-1]
    for i in top_indices:
        if probs[i] >= 0.10: 
            options.append((emnist_idx_to_char(int(i)), float(probs[i])))
    
    if not options: 
        options.append((emnist_idx_to_char(int(top_indices[0])), float(probs[top_indices[0]])))
        
    return options[:3]

def predict_all_models(pixels_28x28: np.ndarray):
    swe_options = []
    emnist_options = []
    
    if "swe_chars_model.keras" in loaded_models:
        swe_options = predict_single_model("swe_chars_model.keras", pixels_28x28)
        
    if swe_options:
        return [swe_options[0][0]]
        
    if "emnist_byclass_model.keras" in loaded_models:
        emnist_options = predict_single_model("emnist_byclass_model.keras", pixels_28x28)
        
    candidates = []
    seen = set()
        
    for char, prob in emnist_options:
        if char.lower() not in seen and len(candidates) < 4:
            candidates.append(char)
            seen.add(char.lower())
            
    return candidates

class PredictRequest(BaseModel):
    image: str
    model: str = "Alla modeller"
    lang: str = "SWE"

@router.get("/ocr")
async def serve_ocr(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="ocr.html", 
        context={"page_title": "Teckenigenkänning / AI OCR", "back_url": "/"}
    )

@router.post("/api/predict")
async def predict_character(req: PredictRequest):
    if not req.image:
        return {"status": "error", "message": "Ingen bild mottogs / No image provided."}

    try:
        raw_b64 = req.image
        if "," in raw_b64: raw_b64 = raw_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(raw_b64)
        img = Image.open(BytesIO(img_bytes)).convert("L")
    except Exception as e:
        return {"status": "error", "message": f"Kunde inte tolka bilden: {e}"}

    arr = np.array(img)
    if arr.mean() > 127: arr = 255 - arr

    threshold = min(30, np.percentile(arr, 95) * 0.4) if arr.max() > 0 else 30
    arr[arr < threshold] = 0

    labeled_array, num_features = label(arr > 0)
    if num_features > 0:
        sizes = np.bincount(labeled_array.ravel())
        sizes[0] = 0 
        largest_size = sizes.max()
        min_size = max(15, largest_size * 0.005) 
        
        for i in range(1, num_features + 1):
            if sizes[i] < min_size:
                arr[labeled_array == i] = 0

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

    pixels = np.array(img_28)
    real_model_name = resolve_model_filename(req.model)

    if real_model_name == "Alla modeller":
        candidates = predict_all_models(pixels)
    elif real_model_name in loaded_models:
        raw_candidates = predict_single_model(real_model_name, pixels)
        candidates = [c[0] for c in raw_candidates]
    else:
        candidates = []

    return {"status": "ok", "candidates": candidates}