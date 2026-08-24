import os
import soundfile as sf

def create_dataset_dirs():
    base_dir = "benchmark_data"
    dirs = [
        "image/real", "image/fake",
        "video/real", "video/fake",
        "audio/real", "audio/fake"
    ]
    for d in dirs:
        os.makedirs(os.path.join(base_dir, d), exist_ok=True)
    return base_dir

def fetch_datasets(base_dir):
    try:
        from datasets import load_dataset
    except ImportError:
        print("Please install datasets library: pip install datasets librosa soundfile")
        return

    print("Fetching REAL and FAKE Images from HuggingFace (dima806/deepfake_vs_real_image_detection)...")
    try:
        img_ds = load_dataset("dima806/deepfake_vs_real_image_detection", split="train", streaming=True)
        img_features = img_ds.features['label']
        
        real_count, fake_count = 0, 0
        for example in img_ds:
            label_str = img_features.int2str(example['label']).lower()
            img = example['image']
            
            if 'real' in label_str and real_count < 5:
                img.save(os.path.join(base_dir, f"image/real/sample_{real_count}.jpg"))
                real_count += 1
            elif 'fake' in label_str and fake_count < 5:
                img.save(os.path.join(base_dir, f"image/fake/sample_{fake_count}.jpg"))
                fake_count += 1
                
            if real_count >= 5 and fake_count >= 5:
                break
        print(f"✅ Downloaded {real_count} real and {fake_count} fake images.")
    except Exception as e:
        print(f"❌ Failed to fetch images: {e}")

    print("\nFetching REAL and FAKE Audio from HuggingFace (dima806/deepfake_vs_real_audio_detection)...")
    try:
        aud_ds = load_dataset("dima806/deepfake_vs_real_audio_detection", split="train", streaming=True)
        aud_features = aud_ds.features['label']
        
        real_count, fake_count = 0, 0
        for example in aud_ds:
            label_str = aud_features.int2str(example['label']).lower()
            audio_array = example['audio']['array']
            sample_rate = example['audio']['sampling_rate']
            
            if 'real' in label_str and real_count < 5:
                sf.write(os.path.join(base_dir, f"audio/real/sample_{real_count}.wav"), audio_array, sample_rate)
                real_count += 1
            elif 'fake' in label_str and fake_count < 5:
                sf.write(os.path.join(base_dir, f"audio/fake/sample_{fake_count}.wav"), audio_array, sample_rate)
                fake_count += 1
                
            if real_count >= 5 and fake_count >= 5:
                break
        print(f"✅ Downloaded {real_count} real and {fake_count} fake audio clips.")
    except Exception as e:
        print(f"❌ Failed to fetch audio: {e}")

    print("\n⚠️ NOTE: High-quality deepfake video datasets (like FaceForensics++) require academic registration and cannot be downloaded anonymously.")
    print("Please manually place a few .mp4 files into `benchmark_data/video/real` and `benchmark_data/video/fake` for video testing.")

if __name__ == "__main__":
    base_dir = create_dataset_dirs()
    fetch_datasets(base_dir)
