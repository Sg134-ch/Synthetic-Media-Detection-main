import os, sys, json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.append('backend')
from PIL import Image
from detectors.image_detector import detect_image
from detectors.audio_detector import detect_audio
from detectors.video_detector import detect_video

print("=================== IMAGE DETECTOR BREAKDOWN ===================")
for label in ['real', 'fake']:
    for f in sorted(os.listdir(f'benchmark_data/image/{label}')):
        path = f'benchmark_data/image/{label}/{f}'
        img = Image.open(path).convert('RGB')
        res = detect_image(img)
        print(f"\n[{label.upper()}] {f} (size={img.size}):")
        print(f"  Verdict: {res.get('verdict')} | Confidence: {res.get('confidence')}%")
        print(f"  Face detected: {res.get('face_detected')} | ELA score: {res.get('ela_score')}")
        print(f"  Models used: {res.get('models_used')}")
        print(f"  Probs: {res.get('probs')}")

print("\n=================== AUDIO DETECTOR BREAKDOWN ===================")
for label in ['real', 'fake']:
    for f in sorted(os.listdir(f'benchmark_data/audio/{label}')):
        path = f'benchmark_data/audio/{label}/{f}'
        res = detect_audio(path)
        print(f"\n[{label.upper()}] {f}:")
        print(f"  Verdict: {res.get('verdict')} | Confidence: {res.get('confidence')}%")
        print(f"  Method: {res.get('method')}")
        print(f"  Models used: {res.get('models_used')}")
        print(f"  Probs: {res.get('probs')}")
        if 'features' in res:
            print(f"  Features: {res.get('features')}")

print("\n=================== VIDEO DETECTOR BREAKDOWN ===================")
for label in ['real', 'fake']:
    for f in sorted(os.listdir(f'benchmark_data/video/{label}')):
        path = f'benchmark_data/video/{label}/{f}'
        res = detect_video(path)
        print(f"\n[{label.upper()}] {f}:")
        print(f"  Verdict: {res.get('verdict')} | Confidence: {res.get('confidence')}%")
        print(f"  Frames analyzed: {res.get('frame_count')} | Duration: {res.get('duration')}s")
        print(f"  Flagged frames: {res.get('flagged_frames')}")
        if 'temporal_analysis' in res:
            print(f"  Temporal Anomaly Score: {res['temporal_analysis'].get('temporal_anomaly_score')}")
