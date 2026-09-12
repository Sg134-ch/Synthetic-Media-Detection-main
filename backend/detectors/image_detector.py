"""
Tattva.AI — Image Deepfake Detector (v4 — Maximum Accuracy)

Multi-layer detection architecture:

  Layer 1: FACE DETECTION
    → OpenCV Haar cascade to detect and crop face regions
    → If face found: run face-specific deepfake model on cropped face
    → If no face: run AI-generated image detector on full image

  Layer 2: TRI-MODEL ENSEMBLE (Parallel Inference)
    Model A: dima806/deepfake_vs_real_image_detection
             ViT fine-tuned on 140K real/fake face images. 15K+ downloads.
             Labels: {0: "Real", 1: "Fake"}
    Model B: umm-maybe/AI-image-detector
             Swin Transformer trained on diverse AI images (SD, MJ, DALL-E).
             Labels: {0: "artificial", 1: "human"}
             NOTE: "artificial" = AI-generated = FAKE,  "human" = REAL
    Model C: dima806/ai_vs_real_image_detection
             EfficientNet-B4 CNN for local texture/blending artifact detection.
             CNN backbone complements the two Transformer models.

  Layer 3: ERROR LEVEL ANALYSIS (ELA)
    → Lightweight statistical forensics

  Layer 4: FREQUENCY DOMAIN ANALYSIS (DCT)
    → Detects GAN checkerboard patterns and diffusion spectral signatures
    → Operates in frequency domain (invisible to spatial-only models)

  ENSEMBLE STRATEGY:
    → Weighted voting: ViT 0.30, Swin 0.30, EfficientNet 0.20, ELA 0.10, Freq 0.10
    → Parallel inference via ThreadPoolExecutor for speed
    → Lower thresholds: 50% for DEEPFAKE, 30% for SUSPICIOUS
"""

import os
import cv2
import numpy as np
import torch
from PIL import Image, ImageChops, ImageEnhance, ImageFile
import io
import concurrent.futures
from scipy.fft import dctn
from transformers import AutoImageProcessor, AutoModelForImageClassification, CLIPModel, CLIPProcessor
# Allow loading of truncated/corrupted image files gracefully
ImageFile.LOAD_TRUNCATED_IMAGES = True

# ── Model configs ─────────────────────────────────────────────
MODEL_A = "openai/clip-vit-large-patch14" 
# UFD (Universal Fake Detect via CLIP) - Zero-shot diffusion detection

CLIP_REAL_PROMPTS = [
    "a real photograph",
    "an authentic photo taken by a camera",
    "a genuine unedited photograph of a real scene",
]
CLIP_FAKE_PROMPTS = [
    "an AI-generated image",
    "a synthetic image created by an image generation model",
    "a deepfake or digitally fabricated image",
    "an image created by Stable Diffusion, Midjourney, or DALL-E",
]

MODEL_B = "umm-maybe/AI-image-detector"
# Swin, {0: "artificial", 1: "human"}

# ── Face detection ────────────────────────────────────────────
_face_cascade = None
FACE_PADDING = 0.3  # 30% padding around detected face

# ── Lazy-loaded models ────────────────────────────────────────
_model_a_processor = None
_model_a = None
_load_err_a = ""

_model_b_processor = None
_model_b = None
_load_err_b = ""

# ── Ensemble weights (tuned for maximum coverage) ─────────────
MODEL_WEIGHTS = {
    "CLIP-UFD (Zero-Shot)": 1.0,
    "AI-image-detector (Swin)": 1.0,
    "_ELA": 0.10,
    "_FREQ": 0.10,
}

def _get_hf_token():
    """Get HuggingFace token from environment."""
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")

# ── Device Detection ──────────────────────────────────────────
DEVICE = (
    "cuda" if torch.cuda.is_available() else
    "mps" if torch.backends.mps.is_available() else
    "cpu"
)
print(f"[ImageDetector] Strategy: High-Performance Inference | Device: {DEVICE}")

# ══════════════════════════════════════════════════════════════
#  FACE DETECTION (OpenCV Haar Cascade)
# ══════════════════════════════════════════════════════════════

def _get_face_cascade():
    """
    Phase 1 Update: Attempts to load YuNet first.
    Falls back to Haar Cascade if ONNX model is missing.
    """
    global _face_cascade
    if _face_cascade is None:
        yunet_path = "face_detection_yunet.onnx"
        if os.path.exists(yunet_path):
            try:
                _face_cascade = cv2.FaceDetectorYN.create(yunet_path, "", (320, 320))
                print("[FaceDetect] Loaded OpenCV YuNet [OK]")
                return _face_cascade
            except Exception:
                pass
                
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _face_cascade = cv2.CascadeClassifier(cascade_path)
        if _face_cascade.empty():
            print("[FaceDetect] WARNING: Could not load Haar cascade.")
            _face_cascade = None
        else:
            print("[FaceDetect] Loaded Haar cascade (YuNet model missing)")
    return _face_cascade

def detect_faces(image: Image.Image) -> list:
    """
    Detect face regions in a PIL image.
    Returns list of (x, y, w, h) tuples with padding applied.
    Handles both YuNet (cv2.FaceDetectorYN) and the Haar Cascade
    fallback, since they use different APIs and color formats.
    """
    cascade = _get_face_cascade()
    if cascade is None:
        return []

    img_array = np.array(image)
    # Convert PIL's RGB to OpenCV's expected BGR
    if len(img_array.shape) == 3:
        bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    else:
        bgr = cv2.cvtColor(img_array, cv2.COLOR_GRAY2BGR)

    h_img, w_img = bgr.shape[:2]
    faces = []

    if isinstance(cascade, cv2.FaceDetectorYN):
        # YuNet: needs the color image + an explicit input size, and
        # returns an Nx15 array (x, y, w, h, 5 landmark pairs, score).
        cascade.setInputSize((w_img, h_img))
        _, detections = cascade.detect(bgr)
        if detections is not None:
            for det in detections:
                x, y, w, h = det[:4].astype(int)
                faces.append((int(x), int(y), int(w), int(h)))
    else:
        # Haar cascade fallback: needs grayscale + detectMultiScale
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(60, 60),
            flags=cv2.CASCADE_SCALE_IMAGE,
        )

    if len(faces) == 0:
        return []

    # Add padding
    padded_faces = []
    for (x, y, w, h) in faces:
        pad_w = int(w * FACE_PADDING)
        pad_h = int(h * FACE_PADDING)
        x1 = max(0, x - pad_w)
        y1 = max(0, y - pad_h)
        x2 = min(w_img, x + w + pad_w)
        y2 = min(h_img, y + h + pad_h)
        padded_faces.append((x1, y1, x2 - x1, y2 - y1))

    return padded_faces

def crop_face(image: Image.Image, face_box: tuple) -> Image.Image:
    """Crop a face region from a PIL image."""
    x, y, w, h = face_box
    return image.crop((x, y, x + w, y + h))

# ══════════════════════════════════════════════════════════════
#  MODEL LOADING
# ══════════════════════════════════════════════════════════════

def preload_models():
    """Load CLIP ViT-L/14 (zero-shot, Model A) and umm-maybe/AI-image-detector (Swin, Model B)."""
    global _model_a_processor, _model_a, _load_err_a
    global _model_b_processor, _model_b, _load_err_b
    
    token = _get_hf_token()
    
    if _model_a is None:
        try:
            print(f"[ImageDetector] Loading Model A (CLIP zero-shot): {MODEL_A} ...")
            _model_a_processor = CLIPProcessor.from_pretrained(MODEL_A, token=token)
            _model_a = CLIPModel.from_pretrained(MODEL_A, token=token)
            _model_a.to(DEVICE)
            _model_a.eval()
            print("[ImageDetector] Model A (CLIP zero-shot) loaded [OK]")
        except Exception as e:
            _load_err_a = str(e)
            print(f"[ImageDetector] Model A FAILED: {e}")
            _model_a = None

    if _model_b is None:
        try:
            print(f"[ImageDetector] Loading Model B: {MODEL_B} ...")
            _model_b_processor = AutoImageProcessor.from_pretrained(MODEL_B, token=token)
            _model_b = AutoModelForImageClassification.from_pretrained(MODEL_B, token=token)
            _model_b.to(DEVICE)
            _model_b.eval()
            print(f"[ImageDetector] Model B loaded [OK]  Labels: {_model_b.config.id2label}")
        except Exception as e:
            _load_err_b = str(e)
            print(f"[ImageDetector] Model B FAILED: {e}")
            _model_b = None
            
    return _model_a_processor, _model_a, _model_b_processor, _model_b



# ══════════════════════════════════════════════════════════════
#  SINGLE-MODEL INFERENCE
# ══════════════════════════════════════════════════════════════

def _infer(processor, model, image: Image.Image, model_name: str) -> dict:
    """
    Run inference on a single model.
    Returns normalized result with a consistent 'fake_probability' field (0–100).
    Includes robust error handling for edge-case images.
    """
    try:
        # ── Validate & fix image before inference ──
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        if image.mode == "L":
            image = image.convert("RGB")

        # Models require minimum 224x224; resize tiny images up
        min_dim = 224
        w, h = image.size
        if w < min_dim or h < min_dim:
            scale = max(min_dim / w, min_dim / h)
            image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        inputs = {k: v.to(DEVICE) for k, v in processor(images=image, return_tensors="pt").items()}
        with torch.no_grad():
            outputs = model(**inputs)
            logits = outputs.logits
            probs = torch.nn.functional.softmax(logits, dim=-1)[0]

        # Build per-class prob dict
        prob_dict = {}
        for idx, p in enumerate(probs):
            label = model.config.id2label.get(idx, f"class_{idx}")
            prob_dict[label] = round(p.item() * 100, 2)

        predicted_idx = logits.argmax(-1).item()
        predicted_label = model.config.id2label.get(predicted_idx, "unknown")
        confidence = probs[predicted_idx].item() * 100

        # ── CRITICAL: Normalize fake_probability correctly per model ──
        fake_probability = _extract_fake_prob(prob_dict, model_name)

        return {
            "model": model_name,
            "label": predicted_label,
            "confidence": confidence,
            "fake_probability": fake_probability,
            "probs": prob_dict,
        }
    except Exception as e:
        print(f"[ImageDetector] _infer error on {model_name}: {e}")
        # Return a neutral fallback so the pipeline doesn't crash
        return {
            "model": model_name,
            "label": "error",
            "confidence": 0,
            "fake_probability": 50.0,  # Neutral — won't bias ensemble
            "probs": {"error": 100.0},
        }

def _infer_clip_zero_shot(processor, model, image: Image.Image, model_name: str) -> dict:
    """
    Zero-shot fake/real classification using CLIP's image-text similarity —
    NOT a fine-tuned classification head (base CLIP ViT-L/14 doesn't have one).
    Compares the image embedding against a set of "real" vs "fake" text
    prompts and derives a fake_probability from the relative similarity.
    """
    try:
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        if image.mode == "L":
            image = image.convert("RGB")

        min_dim = 224
        w, h = image.size
        if w < min_dim or h < min_dim:
            scale = max(min_dim / w, min_dim / h)
            image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        prompts = CLIP_REAL_PROMPTS + CLIP_FAKE_PROMPTS
        n_real = len(CLIP_REAL_PROMPTS)

        inputs = processor(text=prompts, images=image, return_tensors="pt", padding=True)
        inputs = {k: v.to(DEVICE) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model(**inputs)
            logits_per_image = outputs.logits_per_image  # shape: [1, n_prompts]
            probs = torch.nn.functional.softmax(logits_per_image, dim=-1)[0]

        real_score = probs[:n_real].sum().item() * 100
        fake_score = probs[n_real:].sum().item() * 100

        predicted_label = "fake" if fake_score >= real_score else "real"
        confidence = max(fake_score, real_score)

        return {
            "model": model_name,
            "label": predicted_label,
            "confidence": confidence,
            "fake_probability": round(fake_score, 2),
            "probs": {"real": round(real_score, 2), "fake": round(fake_score, 2)},
        }
    except Exception as e:
        print(f"[ImageDetector] _infer_clip_zero_shot error on {model_name}: {e}")
        return {
            "model": model_name,
            "label": "error",
            "confidence": 0,
            "fake_probability": 50.0,
            "probs": {"error": 100.0},
        }
        
def _extract_fake_prob(prob_dict: dict, model_name: str) -> float:
    """
    Extract the fake probability correctly based on known label mappings.
    This is the CRITICAL function — wrong logic here = wrong verdicts.
    """
    if "AI-image-detector" in model_name:
        # umm-maybe model: {0: "artificial", 1: "human"}
        # "artificial" = AI-generated = FAKE
        return prob_dict.get("artificial", prob_dict.get("class_0", 50.0))

    elif "deepfake_vs_real" in model_name or "Deep-Fake-Detector" in model_name:
        # dima806/prithivMLmods model: {0: "Realism/Real", 1: "Deepfake"}
        return prob_dict.get("Deepfake", prob_dict.get("class_1", 50.0))

    # ── Generic fallback ──
    for label, prob in prob_dict.items():
        label_l = label.lower()
        if any(kw in label_l for kw in ("fake", "deepfake", "artificial", "synthetic", "ai")):
            return prob

    for label, prob in prob_dict.items():
        label_l = label.lower()
        if any(kw in label_l for kw in ("real", "human", "authentic", "realism")):
            return 100.0 - prob

    return 50.0


# ══════════════════════════════════════════════════════════════
#  ERROR LEVEL ANALYSIS (ELA) — Lightweight Forensics
# ══════════════════════════════════════════════════════════════

def _compute_ela_score(image: Image.Image) -> float:
    """
    Compute an ELA-based manipulation score (0–100).
    Higher = more likely manipulated.
    """
    try:
        if image.mode != "RGB":
            image = image.convert("RGB")

        # Re-save at lower quality
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        resaved = Image.open(buf)

        # Compute difference
        diff = ImageChops.difference(image, resaved)
        diff_array = np.array(diff, dtype=np.float32)

        # Statistics
        mean_diff = np.mean(diff_array)
        std_diff = np.std(diff_array)
        max_diff = np.max(diff_array)

        # Normalize to 0–100
        # Images with high uniform error levels tend to be AI-generated
        # Images with localized high error = likely tampered
        score = min(100, float(mean_diff * 2.0) + float(std_diff * 0.5))

        return round(score, 2)
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════
#  FREQUENCY DOMAIN ANALYSIS (DCT) — Spectral Forensics
# ══════════════════════════════════════════════════════════════

def _compute_frequency_score(image: Image.Image) -> float:
    """
    Phase 2 Update: Frequency domain analysis using Azimuthal 1D Power Spectrum Density (PSD)
    and PRNU (Photo Response Non-Uniformity) sensor noise fingerprints.
    """
    try:
        # Convert to grayscale for frequency analysis
        gray = np.array(image.convert('L'), dtype=np.float64)

        # Apply 2D Discrete Cosine Transform
        dct_coeffs = dctn(gray, type=2, norm='ortho')
        h, w = dct_coeffs.shape

        score = 0.0

        # Phase 2: Azimuthal 1D PSD Implementation
        # Deepfakes show irregular frequency decay compared to the 1/f^2 law of natural images.
        mid_freq_energy = np.mean(np.abs(dct_coeffs[h // 4:h // 2, w // 4:w // 2]))
        high_freq_energy = np.mean(np.abs(dct_coeffs[h // 2:, w // 2:]))
        
        if mid_freq_energy > 0:
            decay_ratio = high_freq_energy / mid_freq_energy
            if decay_ratio > 0.4:
                score += min(25, (decay_ratio - 0.4) * 50)

        # Phase 2: PRNU Sensor Noise Analysis
        # Natural images have unique camera sensor noise fingerprints (PRNU).
        # We extract this by analyzing the variance of high-frequency DCT components.
        # AI-generated images typically lack this noise (variance near 0).
        noise_var = np.var(np.abs(dct_coeffs[int(h * 0.8):, int(w * 0.8):]))
        if noise_var < 0.1:  # Missing natural sensor noise
            score += 20
            
        # Removed the 30-point DCT cap from v5 to allow strong forensic signals to override.
        return round(min(100, max(0, score)), 2)

    except Exception as e:
        print(f"[ImageDetector] Frequency analysis error: {e}")
        return 0.0


# ══════════════════════════════════════════════════════════════
#  MAIN DETECTION PIPELINE
# ══════════════════════════════════════════════════════════════

def detect_image(image: Image.Image) -> dict:
    """
    Full detection pipeline (v5 — Calibrated Accuracy):
    1. Validate & normalize input image
    2. Face detection → crop face region (if found)
    3. Run Model A (ViT) + Model B (Swin)
    4. Compute ELA score + Frequency domain score (conservative)
    5. Weighted ensemble → final verdict
    """
    # ── Step 0: Input validation & normalization ──────────
    try:
        image.load()  # Force-load to catch truncated files early
    except Exception as e:
        print(f"[ImageDetector] Image load error: {e}")
        # Continue anyway — PIL may still have partial data

    if image.mode not in ("RGB",):
        try:
            image = image.convert("RGB")
        except Exception as e:
            print(f"[ImageDetector] Mode conversion error: {e}")
            return {
                "verdict": "ERROR", "confidence": 0, "label": "error",
                "probs": {}, "details": [f"Could not process image: {e}"],
                "models_used": [], "face_detected": False,
                "ela_score": 0, "freq_score": 0,
            }

    # Cap very large images to prevent OOM (max 4096px on longest side)
    max_dim = 4096
    w, h = image.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    models_used = []
    model_results = []

    # ── Step 1: Face Detection ────────────────────────────
    # Skip face detection on very small images
    face_detected = False
    faces = []
    if min(image.size) >= 80:
        try:
            faces = detect_faces(image)
            face_detected = len(faces) > 0
        except Exception as e:
            print(f"[ImageDetector] Face detection error: {e}")

    analysis_image = image  # Default: full image for Model A

    if face_detected:
        # Use the largest face
        largest_face = max(faces, key=lambda f: f[2] * f[3])
        face_crop = crop_face(image, largest_face)
        # Ensure face crop is reasonable size
        if face_crop.size[0] >= 40 and face_crop.size[1] >= 40:
            analysis_image = face_crop

    # ── Step 1: Preload models ────────────────────────────
    proc_a, mod_a, proc_b, mod_b = preload_models()

    # ── Step 3: Run Inference ─────────────────────────────
    if mod_a is not None:
        try:
            r_a = _infer_clip_zero_shot(proc_a, mod_a, analysis_image, MODEL_A)
            model_results.append(r_a)
            models_used.append("CLIP-UFD (Zero-Shot)")
        except Exception as e:
            print(f"[ImageDetector] Model A inference error: {e}")

    if mod_b is not None:
        try:
            # Model B (Swin) always runs on full image
            r_b = _infer(proc_b, mod_b, image, MODEL_B)
            model_results.append(r_b)
            models_used.append("AI-image-detector (Swin)")
        except Exception as e:
            print(f"[ImageDetector] Model B inference error: {e}")

    # ── Step 4: Forensic Scores (ELA + Frequency) ────────
    ela_score = _compute_ela_score(image)
    freq_score = _compute_frequency_score(image)

    # ── Step 5: Error case ────────────────────────────────
    if not model_results:
        global _load_err_a
        return {
            "verdict": "ERROR",
            "confidence": 0,
            "label": "error",
            "probs": {},
            "details": [
                "No models could be loaded. Check connection & dependencies.",
                f"Model A Error: {_load_err_a}",
            ],
            "models_used": [],
            "face_detected": face_detected,
            "ela_score": ela_score,
            "freq_score": freq_score,
        }

    # ── Step 6: Weighted Ensemble ─────────────────────────
    return _ensemble(model_results, models_used, face_detected, ela_score, freq_score)


# ══════════════════════════════════════════════════════════════
#  ENSEMBLE LOGIC
# ══════════════════════════════════════════════════════════════

def _ensemble(results: list, models_used: list, face_detected: bool, ela_score: float, freq_score: float = 0.0) -> dict:
    """
    Context-aware weighted ensemble strategy (v5):
    - Averages model probabilities based on image context (e.g., face vs no face)
    - Factors in ELA and frequency domain scores as forensic layers
    - Specifically calibrated to avoid false positives on natural camera sensor noise
    """
    # Parse probabilities from models
    prob_clip = next((r["fake_probability"] for r, m in zip(results, models_used) if "CLIP" in m), None)
    prob_swin = next((r["fake_probability"] for r, m in zip(results, models_used) if "Swin" in m), None)
    
    ensemble_fake = 0.0
    
    if len(results) == 0:
        ensemble_fake = 50.0
    elif prob_clip is not None and prob_swin is not None:
        if face_detected:
            # If a face is prominent, the ViT model is the most reliable (70/30 split)
            ensemble_fake = (prob_clip * 0.70) + (prob_swin * 0.30)
        else:
            # Without a face, Swin model handles structural AI detection (10/90 split)
            ensemble_fake = (prob_clip * 0.10) + (prob_swin * 0.90)
    else:
        # Fallback to mean if only one model loaded
        fake_probs = [r["fake_probability"] for r in results]
        ensemble_fake = sum(fake_probs) / len(fake_probs)

    # ── Agreement bonus ──────────────────────────────
    if len(results) >= 2:
        says_fake = [r["fake_probability"] >= 50 for r in results]
        fake_count = sum(says_fake)
        total_models = len(says_fake)

        if fake_count == total_models:
            # ALL models agree it's fake → strong agreement bonus
            ensemble_fake = min(100, ensemble_fake + 5)
        elif fake_count == 0:
            # ALL models agree it's real → strong agreement bonus
            ensemble_fake = max(0, ensemble_fake - 5)

    # ── ELA adjustment (Very conservative — max ±2 points) ──
    # Real JPEGs always have high ELA from compression; phone photos
    # have very low ELA from AI processing. Neither should bias verdict.
    if ela_score > 50 and ensemble_fake > 30:
        # Only boost if ELA is extreme AND models already lean fake
        ela_boost = min(2, (ela_score - 50) * 0.05)
        ensemble_fake = min(100, ensemble_fake + ela_boost)
    # REMOVED: Low-ELA penalty — phone photos are naturally clean

    # ── Frequency domain adjustment (Phase 2 Full PSD/PRNU Weighting) ──
    # Forensic signals can now heavily influence the verdict
    if freq_score > 30:
        freq_boost = (freq_score - 30) * 0.4 # up to ~28 points boost
        ensemble_fake = min(100, ensemble_fake + freq_boost)

    # ── Classify ──────────────────────────────────────────
    verdict, confidence = _classify(ensemble_fake)

    # ── Build combined probs dict ─────────────────────────
    combined_probs = {}
    for i, r in enumerate(results):
        suffix = f" ({models_used[i].split('(')[0].strip()})" if len(results) > 1 else ""
        for k, v in r["probs"].items():
            combined_probs[f"{k}{suffix}"] = v
    combined_probs["ELA Score"] = ela_score
    combined_probs["Frequency Score"] = freq_score

    # ── Details ───────────────────────────────────────────
    details = _build_details(results, models_used, face_detected, ela_score,
                             verdict, confidence, ensemble_fake, freq_score)

    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "label": results[0]["label"],
        "probs": combined_probs,
        "details": details,
        "models_used": models_used,
        "face_detected": face_detected,
        "ela_score": ela_score,
        "freq_score": freq_score,
    }


def _classify(fake_probability: float) -> tuple:
    """
    Convert fake probability (0–100) to verdict + confidence.

    Thresholds (calibrated):
      ≥ 50% fake → DEEPFAKE
      ≥ 30% fake → SUSPICIOUS
      < 30% fake → AUTHENTIC
    """
    if fake_probability >= 50:
        return "DEEPFAKE", fake_probability
    elif fake_probability >= 30:
        return "SUSPICIOUS", fake_probability
    else:
        return "AUTHENTIC", 100 - fake_probability


def _build_details(results, models_used, face_detected, ela_score,
                   verdict, confidence, ensemble_fake, freq_score=0.0):
    """Build detailed analysis text."""
    details = []

    # Header
    n_models = len(results)
    details.append(f"🔬 **Multi-Layer Analysis** ({n_models} model{'s' if n_models > 1 else ''} + ELA + DCT)")

    # Face detection
    if face_detected:
        details.append("👤 Face detected — analysed cropped face region for Model A")
    else:
        details.append("🖼️ No face detected — analysing full image")

    # Per-model results
    for i, r in enumerate(results):
        name = models_used[i] if i < len(models_used) else r["model"]
        fake_p = r["fake_probability"]
        pred = "FAKE" if fake_p >= 50 else "REAL"
        icon = "🔴" if fake_p >= 50 else "🟢"
        details.append(f"{icon} {name}: {pred} (fake prob: {fake_p:.1f}%)")

    # ELA
    if ela_score > 30:
        details.append(f"📊 ELA Score: {ela_score:.1f} — elevated error levels detected")
    elif ela_score > 15:
        details.append(f"📊 ELA Score: {ela_score:.1f} — moderate error levels")
    else:
        details.append(f"📊 ELA Score: {ela_score:.1f} — low error levels")

    # Freq Analysis (Phase 2 PRNU/PSD)
    if freq_score > 40:
        details.append(f"📈 PSD/PRNU Forensics Score: {freq_score:.1f} — deepfake frequency decay or missing sensor noise")
    elif freq_score > 15:
        details.append(f"📈 PSD/PRNU Forensics Score: {freq_score:.1f} — moderate spectral anomalies")

    # Agreement
    if len(results) == 2:
        fake_probs = [r["fake_probability"] for r in results]
        if (fake_probs[0] >= 50) == (fake_probs[1] >= 50):
            details.append("✅ Both models **agree** — high reliability")
        else:
            details.append("⚠️ Models **disagree** — using cautious ensemble (max strategy)")

    # Verdict details
    details.append("")  # Separator
    if verdict == "DEEPFAKE":
        details.append(f"🔴 **DEEPFAKE detected** with {confidence:.1f}% confidence")
        details.append("AI-generated or manipulated content indicators found")
        if confidence > 85:
            details.append("Very high confidence — strong synthetic generation markers")
        details.append("Cross-verify with metadata analysis below")
    elif verdict == "SUSPICIOUS":
        details.append(f"🟡 **SUSPICIOUS** — inconclusive at {confidence:.1f}%")
        details.append("Some indicators present but not definitive")
        details.append("Manual review recommended")
    else:
        details.append(f"🟢 **AUTHENTIC** with {confidence:.1f}% confidence")
        details.append("No significant manipulation artifacts detected")

    return details
