"""
Tattva.AI — Audio Deepfake Detector (v4 — AASIST + Ensemble)

Uses an ensemble of two models for deepfake audio detection:
  Model A:  AASIST (Audio Anti-Spoofing using Integrated Spectro-Temporal
            Graph Attention Networks) — Jung et al., NAVER Corp, 2021.
            Trained on ASVspoof2019 LA. Operates directly on raw waveform
            (no hand-crafted features), vendored locally from the official
            clovaai/aasist repo (MIT license) — see detectors/aasist/.
  Model B:  MelodyMachine/Deepfake-audio-detection-V2
            (secondary model for cross-validation)
  Fallback: Spectral analysis heuristics (if both models unavailable)

Ensemble Strategy (MAX — same as image detector):
  - If both agree  → average + agreement bonus
  - If they disagree → MAX(fake_prob) * 0.9  (catches more fakes)

Labels:
  AASIST:  index 0 → spoof (fake), index 1 → bonafide (real)
           (matches the ASVspoof2019 label convention it was trained on)
  Model B: Class 0 → Real, Class 1 → Fake
"""

import os
import numpy as np

try:
    import librosa
    import soundfile as sf
    LIBROSA_OK = True
except ImportError:
    LIBROSA_OK = False

# ── Model config ──────────────────────────────────────────────
AASIST_MODEL_NAME = "AASIST (clovaai, ASVspoof2019-LA)"
HF_AUDIO_MODEL_B = "MelodyMachine/Deepfake-audio-detection-V2"

# Fixed input length AASIST was trained with: ~4.0375s at 16kHz.
# Shorter clips are tiled to this length, longer clips are truncated —
# same convention as the reference implementation's data_utils.py.
AASIST_NB_SAMP = 64600

AASIST_CONFIG = {
    "architecture": "AASIST",
    "nb_samp": AASIST_NB_SAMP,
    "first_conv": 128,
    "filts": [70, [1, 32], [32, 32], [32, 64], [64, 64]],
    "gat_dims": [64, 32],
    "pool_ratios": [0.5, 0.7, 0.5, 0.5],
    "temperatures": [2.0, 2.0, 100.0, 100.0],
}
AASIST_WEIGHTS_PATH = os.path.join(
    os.path.dirname(__file__), "aasist", "weights", "AASIST.pth"
)

# Model A (AASIST) lazy-loading state
_audio_model = None
_pipeline_loaded = False

# Model B (MelodyMachine) lazy-loading state
_audio_model_b = None
_feature_extractor_b = None
_pipeline_b_loaded = False


def _get_hf_token():
    """Get HuggingFace token from environment (optional, for private/gated models)."""
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")


def _load_model():
    """Lazy-load the AASIST graph-attention anti-spoofing model (Model A)."""
    global _audio_model, _pipeline_loaded
    if _pipeline_loaded:
        return _audio_model
    _pipeline_loaded = True
    try:
        import torch
        from .aasist.aasist_model import Model as AASISTModel

        print(f"[AudioDetector] Loading model A: {AASIST_MODEL_NAME} ...")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = AASISTModel(AASIST_CONFIG)
        state_dict = torch.load(AASIST_WEIGHTS_PATH, map_location=device)
        model.load_state_dict(state_dict)
        model.to(device)
        model.eval()
        _audio_model = model
        print(f"[AudioDetector] AASIST model (A) loaded on {device} ✓")
    except Exception as e:
        print(f"[AudioDetector] Model A loading failed ({e}). Will try Model B or spectral fallback.")
        _audio_model = None
    return _audio_model


def _load_model_b():
    """Lazy-load the MelodyMachine deepfake audio detection model (Model B)."""
    global _audio_model_b, _feature_extractor_b, _pipeline_b_loaded
    if _pipeline_b_loaded:
        return _audio_model_b, _feature_extractor_b
    _pipeline_b_loaded = True
    try:
        import torch
        from transformers import AutoModelForAudioClassification, AutoFeatureExtractor
        token = _get_hf_token()
        print(f"[AudioDetector] Loading model B: {HF_AUDIO_MODEL_B} ...")
        _feature_extractor_b = AutoFeatureExtractor.from_pretrained(HF_AUDIO_MODEL_B, token=token)
        _audio_model_b = AutoModelForAudioClassification.from_pretrained(HF_AUDIO_MODEL_B, token=token)
        _audio_model_b.eval()
        print("[AudioDetector] MelodyMachine model (B) loaded ✓")
    except Exception as e:
        print(f"[AudioDetector] Model B loading failed ({e}). Continuing without Model B.")
        _audio_model_b = None
        _feature_extractor_b = None
    return _audio_model_b, _feature_extractor_b


def detect_audio(audio_path: str) -> dict:
    """
    Analyse an audio file for deepfake characteristics.

    Uses an ensemble of two models when available (MAX strategy for
    fake probability — catches more fakes, same pattern as image detector).
    Gracefully degrades to a single model or spectral fallback.

    Returns
    -------
    dict with keys:
        verdict    : str — "DEEPFAKE" | "AUTHENTIC" | "SUSPICIOUS"
        confidence : float (0-100)
        details    : list[str]
        method     : str — "aasist_ensemble", "aasist",
                           "melodymachine", or "spectral_analysis"
        features   : dict — extracted audio features
        models_used: list[str] — which models contributed to the result
    """
    if not LIBROSA_OK:
        return _error("librosa not installed. Cannot process audio.")

    if not os.path.isfile(audio_path):
        return _error("Audio file not found.")

    # Load audio
    try:
        y, sr = librosa.load(audio_path, sr=16000, mono=True, duration=30)
    except Exception as e:
        return _error(f"Could not load audio: {e}")

    if len(y) < sr * 0.5:
        return _error("Audio too short (minimum 0.5 seconds).")

    # ── Attempt to load both models ──────────────────────────
    model_a = _load_model()
    model_b, extractor_b = _load_model_b()

    has_a = model_a is not None
    has_b = model_b is not None and extractor_b is not None

    # ── Run available models ─────────────────────────────────
    result_a = None
    result_b = None

    if has_a:
        result_a = _detect_with_aasist(model_a, y, sr)
        # If AASIST inference failed, it falls back to spectral internally;
        # detect that by checking the method field.
        if result_a.get("method") == "spectral_analysis":
            result_a = None  # Model A failed at inference time

    if has_b:
        result_b = _detect_with_model_b(model_b, extractor_b, y, sr)
        if result_b is not None and result_b.get("method") == "spectral_analysis":
            result_b = None  # Model B failed at inference time

    # ── Ensemble logic ───────────────────────────────────────
    if result_a is not None and result_b is not None:
        return _ensemble_results(result_a, result_b, y, sr)

    # ── Single model fallback ────────────────────────────────
    if result_a is not None:
        result_a["models_used"] = [AASIST_MODEL_NAME]
        return result_a

    if result_b is not None:
        result_b["models_used"] = [HF_AUDIO_MODEL_B]
        return result_b

    # ── Fallback: spectral analysis (both models unavailable) ─
    result = _detect_spectral(y, sr)
    result["models_used"] = []
    return result


def _pad_to_aasist_length(y: np.ndarray, max_len: int = AASIST_NB_SAMP) -> np.ndarray:
    """
    Tile/truncate a raw waveform to AASIST's fixed training length.
    Same convention as the reference implementation's data_utils.pad().
    """
    x_len = y.shape[0]
    if x_len >= max_len:
        return y[:max_len]
    num_repeats = int(max_len / x_len) + 1
    return np.tile(y, num_repeats)[:max_len]


def _detect_with_aasist(model, y, sr):
    """Use the AASIST graph-attention anti-spoofing model (raw waveform input)."""
    try:
        import torch
        import torch.nn.functional as F

        device = next(model.parameters()).device

        # AASIST expects mono 16kHz raw waveform, tiled/truncated to ~4.04s
        y_16k = y if sr == 16000 else librosa.resample(y, orig_sr=sr, target_sr=16000)
        x = _pad_to_aasist_length(y_16k.astype(np.float32))
        x_tensor = torch.from_numpy(x).unsqueeze(0).to(device)  # [1, nb_samp]

        with torch.no_grad():
            _, logits = model(x_tensor)
            probs = F.softmax(logits, dim=1)[0]

        # Label convention (from ASVspoof2019 training data): 0 = spoof, 1 = bonafide
        prob_fake = probs[0].item() * 100
        prob_real = probs[1].item() * 100

        prob_dict = {"real": round(prob_real, 2), "fake": round(prob_fake, 2)}

        if prob_fake >= 70:
            verdict = "DEEPFAKE"
            confidence = prob_fake
        elif prob_fake >= 40:
            verdict = "SUSPICIOUS"
            confidence = prob_fake
        else:
            verdict = "AUTHENTIC"
            confidence = 100 - prob_fake

        # Also extract spectral features for additional details
        features = _extract_features(y, sr)

        details = [
            f"🔬 **AASIST Analysis** (Graph Attention Anti-Spoofing, ASVspoof2019-LA)",
            f"Real probability: {prob_real:.1f}%",
            f"Fake probability: {prob_fake:.1f}%",
            f"Spectral centroid mean: {features['spectral_centroid_mean']:.1f} Hz",
            f"Zero-crossing rate: {features['zcr_mean']:.4f}",
            f"RMS energy std: {features['rms_std']:.4f}",
        ]

        if verdict == "DEEPFAKE":
            details.append("Audio exhibits strong characteristics of synthetic generation")
            details.append("Potential TTS / voice cloning artifacts detected (ElevenLabs, Polly, etc.)")
        elif verdict == "AUTHENTIC":
            details.append("Audio exhibits natural human speech patterns")
            details.append("No synthetic generation markers detected")
        else:
            details.append("Inconclusive — some anomalies detected, manual review recommended")

        return {
            "verdict": verdict,
            "confidence": round(confidence, 2),
            "details": details,
            "method": "aasist",
            "features": features,
            "probs": prob_dict,
            "_prob_fake": prob_fake,
            "_prob_real": prob_real,
        }

    except Exception as e:
        print(f"[AudioDetector] AASIST inference failed: {e}, falling back to spectral.")
        return _detect_spectral(y, sr)


def _detect_with_model_b(model, extractor, y, sr):
    """Use the MelodyMachine Deepfake-audio-detection-V2 model."""
    try:
        import torch

        # Process audio with feature extractor
        inputs = extractor(y, sampling_rate=16000, return_tensors="pt", padding=True)

        with torch.no_grad():
            outputs = model(**inputs)
            logits = outputs.logits
            probs = torch.nn.functional.softmax(logits, dim=-1)[0]

        # Class 0 = Fake, Class 1 = Real
        prob_fake = probs[0].item() * 100
        prob_real = probs[1].item() * 100

        prob_dict = {
            model.config.id2label.get(0, "real"): round(prob_real, 2),
            model.config.id2label.get(1, "fake"): round(prob_fake, 2),
        }

        # Determine verdict
        if prob_fake >= 70:
            verdict = "DEEPFAKE"
            confidence = prob_fake
        elif prob_fake >= 40:
            verdict = "SUSPICIOUS"
            confidence = prob_fake
        else:
            verdict = "AUTHENTIC"
            confidence = 100 - prob_fake

        # Also extract spectral features for additional details
        features = _extract_features(y, sr)

        details = [
            f"🔬 **MelodyMachine Analysis** (Deepfake-audio-detection-V2)",
            f"Real probability: {prob_real:.1f}%",
            f"Fake probability: {prob_fake:.1f}%",
            f"Spectral centroid mean: {features['spectral_centroid_mean']:.1f} Hz",
            f"Zero-crossing rate: {features['zcr_mean']:.4f}",
            f"RMS energy std: {features['rms_std']:.4f}",
        ]

        if verdict == "DEEPFAKE":
            details.append("Model B: Audio exhibits strong characteristics of synthetic generation")
        elif verdict == "AUTHENTIC":
            details.append("Model B: Audio exhibits natural human speech patterns")
        else:
            details.append("Model B: Inconclusive — some anomalies detected")

        return {
            "verdict": verdict,
            "confidence": round(confidence, 2),
            "details": details,
            "method": "melodymachine",
            "features": features,
            "probs": prob_dict,
            "_prob_fake": prob_fake,
            "_prob_real": prob_real,
        }

    except Exception as e:
        print(f"[AudioDetector] MelodyMachine inference failed: {e}")
        return None


def _ensemble_results(result_a, result_b, y, sr):
    """
    Combine results from both audio models using MAX strategy.
    Same pattern as the image detector:
      - If both agree → average + agreement bonus
      - If they disagree → MAX(fake_prob) * 0.9 (catches more fakes)
    """
    fake_a = result_a["_prob_fake"]
    fake_b = result_b["_prob_fake"]
    real_a = result_a["_prob_real"]
    real_b = result_b["_prob_real"]

    verdict_a = result_a["verdict"]
    verdict_b = result_b["verdict"]

    # ── Ensemble combination ──────────────────────────────────
    if verdict_a == verdict_b:
        # Models agree — average with agreement bonus
        combined_fake = (fake_a + fake_b) / 2.0
        combined_real = (real_a + real_b) / 2.0
        agreement_bonus = 5.0
        if combined_fake > combined_real:
            combined_fake = min(100.0, combined_fake + agreement_bonus)
        else:
            combined_real = min(100.0, combined_real + agreement_bonus)
        agreement_note = "Models AGREE ✓ — averaged with confidence bonus"
    else:
        # Models disagree — use Confidence-Weighted Probability Calibration
        weight_a = abs(fake_a - 50.0)
        weight_b = abs(fake_b - 50.0)
        total_weight = max(weight_a + weight_b, 1.0)
        combined_fake = ((fake_a * weight_a) + (fake_b * weight_b)) / total_weight
        combined_real = 100.0 - combined_fake
        agreement_note = f"Models DISAGREE (A: {fake_a:.1f}%, B: {fake_b:.1f}%) — calibrated via certainty weighting"

    # ── Final verdict ─────────────────────────────────────────
    if combined_fake >= 70:
        verdict = "DEEPFAKE"
        confidence = combined_fake
    elif combined_fake >= 35:
        verdict = "SUSPICIOUS"
        confidence = combined_fake
    else:
        verdict = "AUTHENTIC"
        confidence = combined_real

    # Also extract spectral features for additional details
    features = _extract_features(y, sr)

    details = [
        f"🔬 **Ensemble Audio Analysis** (2 models)",
        f"",
        f"📊 **Model A — AASIST** (Graph Attention Anti-Spoofing)",
        f"  Real: {real_a:.1f}% | Fake: {fake_a:.1f}% → {verdict_a}",
        f"",
        f"📊 **Model B — MelodyMachine** (Deepfake-audio-detection-V2)",
        f"  Real: {real_b:.1f}% | Fake: {fake_b:.1f}% → {verdict_b}",
        f"",
        f"🧮 **Ensemble Result**: {agreement_note}",
        f"  Combined fake probability: {combined_fake:.1f}%",
        f"  Combined real probability: {combined_real:.1f}%",
        f"",
        f"Spectral centroid mean: {features['spectral_centroid_mean']:.1f} Hz",
        f"Zero-crossing rate: {features['zcr_mean']:.4f}",
        f"RMS energy std: {features['rms_std']:.4f}",
    ]

    if verdict == "DEEPFAKE":
        details.append("Ensemble: Audio exhibits strong characteristics of synthetic generation")
        details.append("Potential TTS / voice cloning artifacts detected")
    elif verdict == "AUTHENTIC":
        details.append("Ensemble: Audio exhibits natural human speech patterns")
        details.append("No synthetic generation markers detected")
    else:
        details.append("Ensemble: Inconclusive — manual review recommended")

    # Merge probability dicts from both models
    combined_probs = {}
    for k, v in result_a.get("probs", {}).items():
        combined_probs[f"model_a_{k}"] = v
    for k, v in result_b.get("probs", {}).items():
        combined_probs[f"model_b_{k}"] = v
    combined_probs["ensemble_fake"] = round(combined_fake, 2)
    combined_probs["ensemble_real"] = round(combined_real, 2)

    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "details": details,
        "method": "aasist_ensemble",
        "features": features,
        "probs": combined_probs,
        "models_used": [AASIST_MODEL_NAME, HF_AUDIO_MODEL_B],
    }


def _detect_spectral(y, sr):
    """
    Lightweight spectral analysis fallback.
    Uses statistical features that tend to differ between real and synthetic audio.
    """
    features = _extract_features(y, sr)

    # ── Heuristic scoring ──────────────────────────────────
    # These thresholds are tuned for common TTS artifacts:
    # - Synthetic audio often has lower spectral variation
    # - TTS audio tends to have unnaturally consistent energy
    # - Real speech has more varied zero-crossing rates

    score = 50.0  # Start neutral

    # Spectral flatness: synthetic audio is often "smoother"
    if features["spectral_flatness_mean"] > 0.15:
        score += 10
    elif features["spectral_flatness_mean"] < 0.02:
        score += 8

    # RMS energy consistency: TTS has unnaturally stable energy
    if features["rms_std"] < 0.02:
        score += 12  # Too consistent → likely synthetic
    elif features["rms_std"] > 0.08:
        score -= 10  # Natural variation → likely real

    # Zero-crossing rate variance
    if features["zcr_std"] < 0.01:
        score += 10  # Too uniform
    elif features["zcr_std"] > 0.05:
        score -= 8

    # Spectral centroid variance
    if features["spectral_centroid_std"] < 200:
        score += 8
    elif features["spectral_centroid_std"] > 800:
        score -= 8

    # MFCC variance (lower in synthetic speech)
    if features["mfcc_var_mean"] < 50:
        score += 10

    # Clamp to 0-100
    score = max(0, min(100, score))

    if score >= 65:
        verdict = "DEEPFAKE"
        confidence = score
    elif score >= 45:
        verdict = "SUSPICIOUS"
        confidence = score
    else:
        verdict = "AUTHENTIC"
        confidence = 100 - score

    details = [
        f"⚠️ **execution_mode: HEURISTIC_FALLBACK** (Neural networks unavailable)",
        f"Analysis method: Spectral Feature Analysis",
        f"Spectral centroid: {features['spectral_centroid_mean']:.1f} Hz (std: {features['spectral_centroid_std']:.1f})",
        f"Spectral flatness: {features['spectral_flatness_mean']:.4f}",
        f"Zero-crossing rate: {features['zcr_mean']:.4f} (std: {features['zcr_std']:.4f})",
        f"RMS energy std: {features['rms_std']:.4f}",
        f"MFCC variance: {features['mfcc_var_mean']:.2f}",
    ]

    if verdict == "DEEPFAKE":
        details.append("Audio shows signs of synthetic generation (low spectral variation)")
        details.append("Energy patterns are unnaturally consistent")
    elif verdict == "AUTHENTIC":
        details.append("Audio exhibits natural speech variability")
        details.append("No obvious synthetic markers detected")
    else:
        details.append("Some features are borderline — manual review recommended")

    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "details": details,
        "method": "spectral_analysis",
        "features": features,
        "probs": {},
    }


def _extract_features(y, sr) -> dict:
    """Extract statistical audio features used for analysis."""
    # Spectral centroid
    cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    # Spectral flatness
    flat = librosa.feature.spectral_flatness(y=y)[0]
    # Zero-crossing rate
    zcr = librosa.feature.zero_crossing_rate(y=y)[0]
    # RMS energy
    rms = librosa.feature.rms(y=y)[0]
    # MFCCs
    mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    mfcc_var = np.var(mfccs, axis=1)

    return {
        "spectral_centroid_mean": float(np.mean(cent)),
        "spectral_centroid_std": float(np.std(cent)),
        "spectral_flatness_mean": float(np.mean(flat)),
        "zcr_mean": float(np.mean(zcr)),
        "zcr_std": float(np.std(zcr)),
        "rms_mean": float(np.mean(rms)),
        "rms_std": float(np.std(rms)),
        "mfcc_var_mean": float(np.mean(mfcc_var)),
        "duration": float(len(y) / sr),
    }


def _error(message: str) -> dict:
    return {
        "verdict": "ERROR",
        "confidence": 0,
        "details": [message],
        "method": "none",
        "features": {},
        "probs": {},
    }


def preload_models():
    """Load AASIST (Model A) and MelodyMachine (Model B) during server startup."""
    _load_model()
    _load_model_b()
