import time

from datetime import datetime
from picamera2 import Picamera2

picam2 = Picamera2()
picam2.start()

MAXIMUM_PICS = 10
PIC_DELAY = 3

count = 1

# while True for never-ending loop
while count <= MAXIMUM_PICS:
    filename = datetime.now().strftime("photos/step4_%Y%m%d_%H%M%S.jpg")
    picam2.capture_file(filename)
    print(f"{count}: Saved {filename}")
    count += 1
    time.sleep(PIC_DELAY)
