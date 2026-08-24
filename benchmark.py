import os
import json
import argparse
from glob import glob
from sklearn.metrics import accuracy_score, precision_score, recall_score, confusion_matrix
from PIL import Image

# Import backend detectors
import sys
# Add the 'backend' directory to the Python path so we can import 'detectors'
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from detectors.image_detector import detect_image
from detectors.video_detector import detect_video
from detectors.audio_detector import detect_audio

def evaluate_image(filepath):
    try:
        pil_image = Image.open(filepath).convert("RGB")
        # detect_image is synchronous
        res = detect_image(pil_image)
        # return 1 for Fake, 0 for Real
        return 1 if res['verdict'] == "DEEPFAKE" else 0
    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        return -1

def evaluate_video(filepath):
    try:
        # detect_video is synchronous
        res = detect_video(filepath)
        return 1 if res['verdict'] == "DEEPFAKE" else 0
    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        return -1

def evaluate_audio(filepath):
    try:
        # detect_audio is synchronous
        res = detect_audio(filepath)
        return 1 if res['verdict'] == "DEEPFAKE" else 0
    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        return -1

def run_benchmark(dataset_dir):
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
                        pred = evaluate_image(filepath)
                    elif modality == 'video':
                        pred = evaluate_video(filepath)
                    elif modality == 'audio':
                        pred = evaluate_audio(filepath)
                    
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
    
    # Run synchronously
    run_benchmark(args.dataset)
