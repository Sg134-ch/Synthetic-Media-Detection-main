import os
import cv2
import numpy as np
import scipy.io.wavfile as wavfile

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

def generate_image(filepath):
    # Generate a random color image 224x224
    img = np.random.randint(0, 256, (224, 224, 3), dtype=np.uint8)
    cv2.imwrite(filepath, img)

def generate_video(filepath):
    # Generate a simple 1-second video (10 frames)
    out = cv2.VideoWriter(filepath, cv2.VideoWriter_fourcc(*'mp4v'), 10, (224, 224))
    for _ in range(10):
        frame = np.random.randint(0, 256, (224, 224, 3), dtype=np.uint8)
        out.write(frame)
    out.release()

def generate_audio(filepath):
    # Generate 1-second audio of noise (sample rate 16000)
    sample_rate = 16000
    audio = np.random.uniform(-1, 1, sample_rate).astype(np.float32)
    wavfile.write(filepath, sample_rate, audio)

def main():
    base_dir = create_dataset_dirs()
    print(f"Created directories in {base_dir}")
    
    # Generate 2 real and 2 fake files per modality
    for label in ['real', 'fake']:
        for i in range(2):
            generate_image(f"{base_dir}/image/{label}/sample_{i}.jpg")
            generate_video(f"{base_dir}/video/{label}/sample_{i}.mp4")
            generate_audio(f"{base_dir}/audio/{label}/sample_{i}.wav")
            
    print("Dummy dataset generated successfully.")

if __name__ == "__main__":
    main()
