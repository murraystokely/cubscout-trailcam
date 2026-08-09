import os
from datetime import datetime
from picamera2 import Picamera2

PHOTO_DIR="photos"

# Create the photos directory if it doesn't exist.

if not os.path.exists(PHOTO_DIR):
    print(f"Creating directory '{PHOTO_DIR}'...")
    os.makedirs(PHOTO_DIR)
else:
    print(f"Directory '{PHOTO_DIR}' already exists.")
    
picam2 = Picamera2()
picam2.start()

filename = datetime.now().strftime(f"{PHOTO_DIR}/step2_%Y%m%d_%H%M%S.jpg")
picam2.capture_file(filename)
print(f"Saved {filename}")
