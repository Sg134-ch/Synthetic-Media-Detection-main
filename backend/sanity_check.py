from detectors.audio_detector import _load_model, _detect_with_aasist
import numpy as np

print("Loading AASIST model...")
model = _load_model()

if model is None:
    print("FAILED: model did not load. Check that backend/detectors/aasist/weights/AASIST.pth exists.")
else:
    print("Model loaded OK. Running inference on a test tone...")
    y = (0.3 * np.sin(2 * np.pi * 220 * np.linspace(0, 2, 32000))).astype(np.float32)
    r = _detect_with_aasist(model, y, 16000)
    print("Verdict:", r["verdict"], "| Confidence:", r["confidence"])