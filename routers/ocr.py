from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from PIL import Image, ImageOps, UnidentifiedImageError
from io import BytesIO
from pathlib import Path
from scipy.ndimage import label
import base64
import binascii
import logging
import threading
import numpy as np

router = APIRouter()
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- Model configuration ---
EMNIST_MODEL = "emnist_byclass_model.keras"
SWE_MODEL = "swe_chars_model.keras"
TARGET_MODELS = [EMNIST_MODEL, SWE_MODEL]
ALL_MODELS_ALIASES = {"alla modeller", "all models", "all"}

SWE_MAPPING = {0: 'Å', 1: 'Ä', 2: 'Ö', 3: 'å', 4: 'ä', 5: 'ö', 6: 'null'}
SWE_NULL_CLASS = 'null'
SWE_MIN_PROB = 0.15      # Minimum confidence for a Swedish character
EMNIST_MIN_PROB = 0.10   # Minimum confidence for an EMNIST alternative
MAX_CANDIDATES = 4

# --- Input limits (protects the server from huge uploads) ---
MAX_IMAGE_BYTES = 10 * 1024 * 1024       # 10 MB decoded
MAX_IMAGE_PIXELS = 40_000_000            # e.g. ~8000 x 5000
MAX_WORKING_SIDE = 1024                  # larger images are downscaled first

# --- Localized error messages ---
MESSAGES = {
    "no_image":        {"SWE": "Ingen bild mottogs.",                 "ENG": "No image provided."},
    "too_large":       {"SWE": "Bilden är för stor.",                 "ENG": "The image is too large."},
    "bad_image":       {"SWE": "Kunde inte läsa bilden.",             "ENG": "Could not read the image."},
    "no_character":    {"SWE": "Inget tecken hittades.",              "ENG": "No character detected."},
    "models_missing":  {"SWE": "AI-modellerna kunde inte laddas.",    "ENG": "The AI models could not be loaded."},
    "unknown_model":   {"SWE": "Okänd modell.",                       "ENG": "Unknown model."},
}


def msg(key: str, lang: str) -> str:
    return MESSAGES[key].get(lang, MESSAGES[key]["ENG"])


def error_response(key: str, lang: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"status": "error", "code": key, "message": msg(key, lang)})


# =====================================================================
# Model loading
# =====================================================================
loaded_models = {}
models_initialized = False
model_load_progress = 0
model_load_status = {"sv": "Väntar...", "en": "Waiting..."}
model_load_error = None

_load_lock = threading.Lock()      # only one thread loads the models
_predict_lock = threading.Lock()   # Keras models are not guaranteed thread-safe
_loader_thread = None


def _set_progress(progress: int, sv: str, en: str):
    global model_load_progress, model_load_status
    model_load_progress = progress
    model_load_status = {"sv": sv, "en": en}


def init_models():
    """Loads all models once. Safe to call from several threads."""
    global models_initialized, model_load_error

    if models_initialized:
        return

    with _load_lock:
        if models_initialized:
            return

        _set_progress(5, "Initierar AI-motor...", "Initializing AI engine...")

        try:
            from tensorflow.keras.models import load_model
        except ImportError as e:
            logger.error("TensorFlow could not be imported: %s", e)
            model_load_error = "TensorFlow is not installed"
            load_model = None

        if load_model is not None:
            total = len(TARGET_MODELS)
            for i, model_name in enumerate(TARGET_MODELS):
                _set_progress(10 + int(80 * i / total),
                              f"Laddar modell {i + 1} av {total}...",
                              f"Loading model {i + 1} of {total}...")

                model_path = MODELS_DIR / model_name
                if not model_path.is_file():
                    logger.warning("Model file not found: %s", model_path)
                    continue
                try:
                    # compile=False: we only run inference, so optimizer state is not needed (faster load)
                    model = load_model(model_path, compile=False)
                    # Warm-up call so the first real prediction is fast
                    model(np.zeros((1, 28, 28, 1), dtype=np.float32), training=False)
                    loaded_models[model_name] = model
                    logger.info("Loaded model %s", model_name)
                except Exception:
                    logger.exception("Failed to load model %s", model_name)

            if not loaded_models and model_load_error is None:
                model_load_error = "No models could be loaded"

        _set_progress(100, "Klar", "Ready")
        models_initialized = True


def start_background_loading():
    """Starts loading the models in a background thread (no-op if already started)."""
    global _loader_thread
    if models_initialized or (_loader_thread is not None and _loader_thread.is_alive()):
        return
    _loader_thread = threading.Thread(target=init_models, name="ocr-model-loader", daemon=True)
    _loader_thread.start()


# =====================================================================
# Image preprocessing
# =====================================================================
class ImageTooLargeError(ValueError):
    pass


def decode_image(data_url: str) -> Image.Image:
    """Decodes a base64 string / data URL to a grayscale PIL image.
    Transparent pixels are composited onto a white background."""
    raw_b64 = data_url.split(",", 1)[1] if "," in data_url else data_url

    # Cheap size check before decoding (base64 is ~4/3 of the binary size)
    if len(raw_b64) * 3 // 4 > MAX_IMAGE_BYTES:
        raise ImageTooLargeError()

    img_bytes = base64.b64decode(raw_b64)
    img = Image.open(BytesIO(img_bytes))

    if img.width * img.height > MAX_IMAGE_PIXELS:
        raise ImageTooLargeError()

    # Respect camera rotation for photos pasted from a phone
    img = ImageOps.exif_transpose(img)

    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        rgba = img.convert('RGBA')
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg.convert("L")
    else:
        img = img.convert("L")

    # Very large photos are downscaled for speed; the final input is only 28x28 anyway
    if max(img.size) > MAX_WORKING_SIDE:
        img.thumbnail((MAX_WORKING_SIDE, MAX_WORKING_SIDE), Image.Resampling.LANCZOS)

    return img


def preprocess(img: Image.Image):
    """Converts a grayscale image to a 28x28 EMNIST-style array (white character on black).
    Returns None if no character is found."""
    arr = np.array(img, dtype=np.uint8)

    # Models expect a light character on a dark background
    if arr.mean() > 127:
        arr = 255 - arr

    # Remove faint background noise
    threshold = min(30, np.percentile(arr, 95) * 0.4) if arr.max() > 0 else 30
    arr[arr < threshold] = 0

    # Remove small specks (connected components much smaller than the main stroke)
    labeled_array, num_features = label(arr > 0)
    if num_features > 0:
        sizes = np.bincount(labeled_array.ravel())
        sizes[0] = 0
        min_size = max(15, sizes.max() * 0.005)
        arr[sizes[labeled_array] < min_size] = 0  # vectorized: one pass instead of one per component

    coords = np.argwhere(arr > 0)
    if coords.size == 0:
        return None

    # Crop to the character and pad it to a square
    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0)
    cropped = arr[y0:y1 + 1, x0:x1 + 1]

    h, w = cropped.shape
    size = max(h, w)
    square = np.zeros((size, size), dtype=np.uint8)
    y_off = (size - h) // 2
    x_off = (size - w) // 2
    square[y_off:y_off + h, x_off:x_off + w] = cropped

    # Fit into a 20x20 box and center it in a 28x28 frame (same as MNIST/EMNIST)
    square_img = Image.fromarray(square)
    square_img.thumbnail((20, 20), Image.Resampling.LANCZOS)

    img_28 = Image.new("L", (28, 28), 0)
    img_28.paste(square_img, ((28 - square_img.width) // 2, (28 - square_img.height) // 2))
    return np.array(img_28)


# =====================================================================
# Prediction
# =====================================================================
def emnist_idx_to_char(i: int) -> str:
    if i <= 9:
        return str(i)
    if i <= 35:
        return chr(i - 10 + ord('A'))
    return chr(i - 36 + ord('a'))


def run_model(model_name: str, pixels_28x28: np.ndarray):
    """Returns the model's probability vector, or None if the model is not loaded."""
    model = loaded_models.get(model_name)
    if model is None:
        return None
    x = (pixels_28x28.reshape(1, 28, 28, 1) / 255.0).astype(np.float32)
    # Calling the model directly is much faster than model.predict() for a single image
    with _predict_lock:
        probs = model(x, training=False)
    return np.asarray(probs)[0]


def predict_single_model(model_name: str, pixels_28x28: np.ndarray):
    """Returns up to 3 (char, probability) tuples, best first."""
    probs = run_model(model_name, pixels_28x28)
    if probs is None:
        return []

    order = np.argsort(probs)[::-1]

    if model_name == SWE_MODEL:
        # If the model's best guess is "not a Swedish letter", trust that
        if SWE_MAPPING.get(int(order[0])) == SWE_NULL_CLASS:
            return []
        options = []
        for idx in order:
            char = SWE_MAPPING.get(int(idx), str(idx))
            if char != SWE_NULL_CLASS and probs[idx] >= SWE_MIN_PROB:
                options.append((char, float(probs[idx])))
        return options[:3]

    options = [(emnist_idx_to_char(int(i)), float(probs[i])) for i in order[:3] if probs[i] >= EMNIST_MIN_PROB]
    if not options:
        options.append((emnist_idx_to_char(int(order[0])), float(probs[order[0]])))
    return options


def predict_all_models(pixels_28x28: np.ndarray):
    """Swedish letters (Å, Ä, Ö) take priority; EMNIST results are added as alternatives."""
    swe_options = predict_single_model(SWE_MODEL, pixels_28x28)
    emnist_options = predict_single_model(EMNIST_MODEL, pixels_28x28)

    candidates, seen = [], set()
    for char, _ in swe_options[:1] + emnist_options:
        if char.lower() not in seen:
            candidates.append(char)
            seen.add(char.lower())
        if len(candidates) >= MAX_CANDIDATES:
            break
    return candidates


def resolve_model_filename(choice: str):
    """Maps a user-facing model name to a filename. Returns None for 'all models'."""
    if not choice or choice.strip().lower() in ALL_MODELS_ALIASES:
        return None
    for m in TARGET_MODELS:
        pretty = m.replace(".keras", "").replace("_", " ")
        if choice == m or choice.lower() == pretty.lower():
            return m
    return choice


# =====================================================================
# Routes
# =====================================================================
class PredictRequest(BaseModel):
    image: str
    model: str = "Alla modeller"
    lang: str = "SWE"


@router.get("/ocr")
async def serve_ocr(request: Request):
    # Start loading the AI models as soon as someone opens the page,
    # so they are usually ready by the time the first character is drawn.
    start_background_loading()
    return templates.TemplateResponse(
        request=request,
        name="ocr.html",
        context={"page_title": "Teckenigenkänning / AI OCR", "back_url": "/"}
    )


@router.get("/api/ocr/status")
async def get_ocr_status():
    """Returns the current loading progress of the AI models."""
    return {
        "initialized": models_initialized,
        "progress": model_load_progress,
        "status": model_load_status,
        "models": list(loaded_models.keys()),
        "error": model_load_error,
    }


@router.post("/api/predict")
def predict_character(req: PredictRequest):
    lang = "SWE" if req.lang.upper() in ("SWE", "SV") else "ENG"

    if not req.image:
        return error_response("no_image", lang, 400)

    try:
        img = decode_image(req.image)
    except ImageTooLargeError:
        return error_response("too_large", lang, 413)
    except (binascii.Error, UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return error_response("bad_image", lang, 400)

    pixels = preprocess(img)
    if pixels is None:
        return error_response("no_character", lang, 422)

    init_models()  # returns immediately once models are loaded
    if not loaded_models:
        return error_response("models_missing", lang, 503)

    model_name = resolve_model_filename(req.model)
    if model_name is None:
        candidates = predict_all_models(pixels)
    elif model_name in loaded_models:
        candidates = [c for c, _ in predict_single_model(model_name, pixels)]
    else:
        return error_response("unknown_model", lang, 400)

    if not candidates:
        return error_response("no_character", lang, 422)
    return {"status": "ok", "candidates": candidates}
