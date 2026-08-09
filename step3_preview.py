import time
from picamera2 import Picamera2, Preview

picam2 = Picamera2()
picam2.start_preview(Preview.QT)
picam2.start()

print("Showing preview for 10 seconds.")
time.sleep(10)
picam2.stop_preview()
print("Done.")
