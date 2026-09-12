import requests
import json
import time

print("--- Testing FastAPI Backend ---")
for i in range(10):
    try:
        r = requests.get("http://127.0.0.1:8000/")
        print("Backend root status:", r.status_code, r.json())
        break
    except Exception as e:
        print(f"Waiting for backend... ({e})")
        time.sleep(2)

print("\n--- Testing Next.js Frontend ---")
try:
    r = requests.get("http://localhost:3000/")
    print("Frontend status:", r.status_code, f"(HTML size: {len(r.text)} bytes)")
except Exception as e:
    print("Frontend error:", e)

print("\n--- Testing /detect/auto (Image) ---")
with open("benchmark_data/image/real/sample_0.jpg", "rb") as f:
    r = requests.post("http://127.0.0.1:8000/detect/auto", files={"file": ("sample_0.jpg", f, "image/jpeg")})
    print("Image detect status:", r.status_code)
    print("Image result summary:", json.dumps({
        "media_type": r.json().get("media_type"),
        "verdict": r.json().get("verdict"),
        "confidence": r.json().get("confidence"),
        "details_keys": list(r.json().get("details", {}).keys())
    }, indent=2))

print("\n--- Testing /detect/full (Image with Heatmap & Forensics) ---")
with open("benchmark_data/image/real/sample_0.jpg", "rb") as f:
    r = requests.post("http://127.0.0.1:8000/detect/full", files={"file": ("sample_0.jpg", f, "image/jpeg")})
    print("Full Image detect status:", r.status_code)
    data = r.json()
    print("Full Image result:", json.dumps({
        "media_type": data.get("media_type"),
        "verdict": data.get("verdict"),
        "confidence": data.get("confidence"),
        "forensics_keys": list(data.get("forensics", {}).keys())
    }, indent=2))

print("\n--- Testing /detect/auto (Audio) ---")
with open("benchmark_data/audio/real/sample_0.wav", "rb") as f:
    r = requests.post("http://127.0.0.1:8000/detect/auto", files={"file": ("sample_0.wav", f, "audio/wav")})
    print("Audio detect status:", r.status_code)
    print("Audio result summary:", json.dumps({
        "media_type": r.json().get("media_type"),
        "verdict": r.json().get("verdict"),
        "confidence": r.json().get("confidence"),
        "details": r.json().get("details", {})
    }, indent=2))

print("\n--- Testing Next.js Proxy /api/detect/full ---")
with open("benchmark_data/image/real/sample_0.jpg", "rb") as f:
    r = requests.post("http://localhost:3000/api/detect/full", files={"file": ("sample_0.jpg", f, "image/jpeg")})
    print("Next.js proxy status:", r.status_code)
    if r.status_code == 200:
        print("Next.js proxy returned verdict:", r.json().get("verdict"))
    else:
        print("Next.js proxy returned:", r.text[:200])

print("\n--- All basic E2E endpoint checks finished ---")
