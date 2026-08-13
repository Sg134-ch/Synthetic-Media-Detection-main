import os
import traceback

weights_path = os.path.join("detectors", "aasist", "weights", "AASIST.pth")

print("1) Checking weights file...")
if not os.path.exists(weights_path):
    print(f"   MISSING: {os.path.abspath(weights_path)}")
    print("   -> The weights file wasn't copied in correctly. Re-copy the aasist/ folder.")
else:
    size = os.path.getsize(weights_path)
    print(f"   Found: {os.path.abspath(weights_path)}")
    print(f"   Size: {size} bytes (should be 1281532)")
    if size != 1281532:
        print("   MISMATCH! The file is corrupted, truncated, or wasn't copied in binary mode.")

print()
print("2) Trying torch.load() directly...")
try:
    import torch
    print(f"   torch version: {torch.__version__}")
    state_dict = torch.load(weights_path, map_location="cpu")
    print(f"   torch.load() succeeded. Keys: {len(state_dict)}")
except Exception:
    print("   torch.load() FAILED. Full traceback:")
    traceback.print_exc()

print()
print("3) Trying to build the model architecture...")
try:
    from detectors.audio_detector import AASIST_CONFIG
    from detectors.aasist.aasist_model import Model as AASISTModel
    model = AASISTModel(AASIST_CONFIG)
    print("   Model architecture built successfully.")
except Exception:
    print("   Model architecture build FAILED. Full traceback:")
    traceback.print_exc()

print()
print("4) Trying the full load_state_dict step...")
try:
    model.load_state_dict(state_dict)
    print("   load_state_dict() succeeded!")
except Exception:
    print("   load_state_dict() FAILED. Full traceback:")
    traceback.print_exc()