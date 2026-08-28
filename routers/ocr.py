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

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

ocr_app = FastAPI(title="Character Recognition API", version="2.0.1")

# Mount OCR-specific static overrides if needed, or point to main static
ocr_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

loaded_models = {}
TARGET_MODELS = ["emnist_byclass_model.keras", "swe_chars_model.keras"]
SWE_MAPPING = {0: 'Å', 1: 'Ä', 2: 'Ö', 3: 'å', 4: 'ä', 5: 'ö', 6: 'null'}

if MODELS_DIR.exists() and MODELS_DIR.is_dir():
    for model_name in TARGET_MODELS:
        model_path = MODELS_DIR / model_name
        if model_path.exists():
            try:
                # Force path to string for cross-version Keras compatibility
                model = load_model(str(model_path))
                
                # Dynamic shape detection for Warm-up
                try:
                    inp_shape = model.input_shape
                    if len(inp_shape) == 2: dummy = np.zeros((1, 784))
                    elif len(inp_shape) == 3: dummy = np.zeros((1, 28, 28))
                    else: dummy = np.zeros((1, 28, 28, 1))
                    model.predict(dummy, verbose=0)
                except Exception as shape_err:
                    print(f"Warmup warning for {model_name}: {shape_err}")

                loaded_models[model_name] = model
                print(f"Successfully loaded: {model_name}")
            except Exception as e:
                print(f"Error loading {model_name}: {e}")

class PredictRequest(BaseModel):
    image: str
    model: str = "Alla modeller"
    lang: str = "SWE"

def emnist_idx_to_char(i: int) -> str:
    if i <= 9: return str(i)
    elif i <= 35: return chr(i - 10 + ord('A'))
    else: return chr(i - 36 + ord('a'))

def predict_single_model(model_name: str, pixels_28x28: np.ndarray):
    model = loaded_models.get(model_name)
    if not model: return []

    model_input = pixels_28x28.astype(np.float32) / 255.0
    
    # Dynamic input reshaping to prevent crash on (784,) vs (28, 28, 1) mismatch
    try:
        input_shape = model.input_shape
        if len(input_shape) == 2 and input_shape[1] == 784:
            model_input = model_input.reshape(1, 784)
        elif len(input_shape) == 3:
            model_input = model_input.reshape(1, 28, 28)
        else:
            model_input = model_input.reshape(1, 28, 28, 1)
    except:
        model_input = model_input.reshape(1, 28, 28, 1)

    probs = model.predict(model_input, verbose=0)[0]
    options = []

    # Swedish Characters Model Parsing
    if "swe_chars" in model_name:
        best_idx = int(np.argmax(probs))
        best_char = SWE_MAPPING.get(best_idx, str(best_idx))
        if best_char.lower() == "null": return []
        
        for idx in np.argsort(probs)[::-1]:
            char = SWE_MAPPING.get(int(idx), str(idx))
            if char.lower() != "null" and probs[idx] >= 0.15:
                options.append(char)
        if not options and best_char.lower() != "null": options.append(best_char)
        return options[:3]

    # Standard EMNIST Parsing
    top_3_indices = np.argsort(probs)[-3:][::-1]
    for i in top_3_indices:
        if probs[i] >= 0.10: options.append(emnist_idx_to_char(int(i)))
    if not options: options.append(emnist_idx_to_char(int(top_3_indices[0])))
    return options[:3]

def predict_all_models(pixels_28x28: np.ndarray):
    # Try Swedish characters first
    if "swe_chars_model.keras" in loaded_models:
        swe_options = predict_single_model("swe_chars_model.keras", pixels_28x28)
        if swe_options: return swe_options
    
    # Fallback to EMNIST
    if "emnist_byclass_model.keras" in loaded_models:
        return predict_single_model("emnist_byclass_model.keras", pixels_28x28)
    return []

@ocr_app.get("/")
async def serve_index():
    index_file = TEMPLATES_DIR / "ocr.html"
    if not index_file.exists(): raise HTTPException(status_code=404, detail="OCR App UI not found.")
    return FileResponse(str(index_file), media_type="text/html")

@ocr_app.get("/api/models")
async def get_models():
    models_list = [m.replace(".keras", "").replace("_", " ").title() for m in loaded_models.keys()]
    models_list.append("Alla modeller")
    return {"models": models_list}

@ocr_app.post("/api/predict")
async def predict_character(req: PredictRequest):
    if not req.image: 
        return {"status": "error", "message": "Ingen bild mottogs / No image provided."}
    
    try:
        raw_b64 = req.image.split(",", 1)[1] if "," in req.image else req.image
        img_bytes = base64.b64decode(raw_b64)
        img = Image.open(BytesIO(img_bytes)).convert("L")
    except Exception as e: 
        return {"status": "error", "message": f"Kunde inte tolka bilden: {e}"}

    try:
        arr = np.array(img)
        if arr.mean() > 127: arr = 255 - arr
        
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
        y_offset, x_offset = (size - h) // 2, (size - w) // 2
        square[y_offset:y_offset + h, x_offset:x_offset + w] = cropped

        square_img = Image.fromarray(square)
        square_img.thumbnail((20, 20), Image.Resampling.LANCZOS)
        img_28 = Image.new("L", (28, 28), 0)
        img_28.paste(square_img, ((28 - square_img.width) // 2, (28 - square_img.height) // 2))

        buffered = BytesIO()
        img_28.save(buffered, format="PNG")
        preview_b64 = "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode("utf-8")

        real_model_name = req.model
        if real_model_name not in ["Alla modeller", "All models"]:
            for m in loaded_models.keys():
                if req.model.lower() == m.replace(".keras", "").replace("_", " ").title().lower():
                    real_model_name = m
                    break

        pixels = np.array(img_28)
        
        if real_model_name in ["Alla modeller", "All models"]:
            candidates = predict_all_models(pixels)
        else:
            candidates = predict_single_model(real_model_name, pixels)
        
        # If models loaded successfully but both returned exactly empty lists (out-of-distribution garbage prediction)
        if not candidates:
            return {"status": "error", "message": "Inget tecken hittades / No character detected."}
        
        return {"status": "ok", "preview": preview_b64, "candidates": candidates}
        
    except Exception as server_e:
        # Prevents a 500 error from triggering the generic frontend message
        return {"status": "error", "message": f"Server/Model Processing Error: {str(server_e)}"}