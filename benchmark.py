import os
import json
import time
import argparse
from glob import glob
from sklearn.metrics import accuracy_score, precision_score, recall_score, confusion_matrix
import asyncio
from PIL import Image

# Import backend detectors
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))
from detectors.image_detector import detect_image
from detectors.video_detector import detect_video
from detectors.audio_detector import detect_audio

async def evaluate_image(filepath):
    try:
        pil_image = Image.open(filepath).convert("RGB")
        res = await detect_image(pil_image)
        # return 1 for Fake, 0 for Real
        return 1 if res['verdict'] == "DEEPFAKE" else 0
    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        return -1

async def evaluate_video(filepath):
    try:
        res = await detect_video(filepath)
        return 1 if res['verdict'] == "DEEPFAKE" else 0
    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        return -1

async def evaluate_audio(filepath):
    try:
        res = await detect_audio(filepath)
        return 1 if res['verdict'] == "DEEPFAKE" else 0
    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        return -1

async def run_benchmark(dataset_dir):
    results = {}
    for modality in ['image', 'video', 'audio']:
        print(f"\n--- Benchmarking {modality.upper()} ---")
        y_true = []
        y_pred = []
        
        for label, class_val in [('real', 0), ('fake', 1)]:
            dir_path = os.path.join(dataset_dir, modality, label)
            if not os.path.exists(dir_path):
                print(f"Directory not found: {dir_path}")
                continue
            
            for ext in ('*.jpg', '*.png', '*.mp4', '*.wav', '*.mp3'):
                for filepath in glob(os.path.join(dir_path, ext)):
                    print(f"Evaluating {filepath}...")
                    if modality == 'image':
                        pred = await evaluate_image(filepath)
                    elif modality == 'video':
                        pred = await evaluate_video(filepath)
                    elif modality == 'audio':
                        pred = await evaluate_audio(filepath)
                    
                    if pred != -1:
                        y_true.append(class_val)
                        y_pred.append(pred)
        
        if len(y_true) > 0:
            acc = accuracy_score(y_true, y_pred)
            prec = precision_score(y_true, y_pred, zero_division=0)
            rec = recall_score(y_true, y_pred, zero_division=0)
            cm = confusion_matrix(y_true, y_pred, labels=[0, 1]).tolist()
            print(f"Accuracy: {acc:.4f}")
            print(f"Precision: {prec:.4f}")
            print(f"Recall: {rec:.4f}")
            print(f"Confusion Matrix: {cm}")
            results[modality] = {
                'accuracy': acc,
                'precision': prec,
                'recall': rec,
                'confusion_matrix': cm
            }
        else:
            print(f"No valid data found for {modality}.")
            
    with open('benchmark_results.json', 'w') as f:
        json.dump(results, f, indent=4)
    print("\nBenchmark complete. Saved to benchmark_results.json")
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=str, default='benchmark_data')
    args = parser.parse_args()
    asyncio.run(run_benchmark(args.dataset))
