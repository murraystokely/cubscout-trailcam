# Webelos Raspberry Pi Wildlife Camera
Murray Stokely <murray@stokely.org>
August 9, 2026

A simple Raspberry Pi wildlife-camera project designed to be built
progressively with Scouts. The project starts by taking a single
photograph, adds timestamps and a preview, moves on to repeated image
capture and motion detection, and finishes as a headless wildlife camera
that automatically starts at boot and serves captured photos over a
local web page.

The emphasis is on keeping each step small enough that the kids can
understand what changed and why.

## Project progression

The Python examples are intentionally split into separate scripts so
each one introduces only a small number of new ideas.

### Step 0 --- Before the camera: names for things

[`step0_intro.py`](step0_intro.py)

Written by Nolan, and kept exactly as he wrote it. Step 1 already assumes
you know what a variable is; this is the program that comes before that
assumption.

Concepts introduced:

-   Putting a value in a variable and giving it a name
-   `print`
-   Adding two strings together

The last line is the one worth stopping on:

``` python
print(message + message2 + message3)
```

`message` holds `'67'`, so a reader expecting arithmetic gets `67nolanhello
nolan` instead. Nothing was added up, because nothing here is a number ---
the quotes made them all text. That is a real lesson about types arriving
in one line, and it lands better from a program a Scout wrote than from an
explanation.

It lived on wildlifecam2's card for six weeks and nowhere else, which is a
poor place to keep the first program anybody runs.

### Step 1 --- Take the first photo

[`step1_firstphoto.py`](step1_firstphoto.py)

The first program proves that Python can communicate with the Raspberry
Pi camera.

Concepts introduced:

-   Importing `Picamera2`
-   Creating a camera object
-   Starting the camera
-   Taking a photograph
-   Saving a JPEG file

This is the basic hardware test. Before moving on, make sure this
program successfully creates a photo.

### Step 2 --- Timestamp the photos

[`step2_timestamp_photo.py`](step2_timestamp_photo.py)

The second program gives photographs useful filenames based on the
current date and time.

Concepts introduced:

-   Python's `datetime`
-   Constructing filenames
-   Creating the photo directory if it does not already exist
-   Saving photographs in an organized location

Timestamped filenames prevent each new photograph from overwriting the
previous one.

### Step 3 --- Camera preview

[`step3_preview.py`](step3_preview.py)

This step adds a live preview so the Scouts can see what the camera
sees.

Concepts introduced:

-   Camera preview
-   Framing and aiming the camera
-   The difference between developing with a monitor and eventually
    running headless

The preview is useful during development, even though the final wildlife
camera does not require a display.

### Step 4 --- Repeated photos

[`step4_repeat_photos.py`](step4_repeat_photos.py)

This program puts image capture into a loop and takes photographs
repeatedly.

Concepts introduced:

-   `while` loops
-   Delays with `time.sleep()`
-   Repeating an operation
-   Producing multiple timestamped files

This is the first step where the Raspberry Pi begins behaving like an
unattended camera rather than a normal interactive program.

### Step 5 --- Detect motion

[`step5_detect_motion.py`](step5_detect_motion.py)

Instead of saving every frame, this program compares the current image
with the previous image.

Concepts introduced:

-   Capturing images into memory
-   Converting images to grayscale
-   `cv2.absdiff()`
-   Measuring how many pixels changed
-   Choosing a motion threshold
-   `if` statements

An important part of this step is experimentation. Observe the
difference values while nothing is moving, then wave a hand or walk in
front of the camera and compare the results. The Scouts can use those
measurements to choose a sensible motion threshold instead of treating
the threshold as a mysterious magic number.

### Step 6 --- Ask the AI Camera what it can see

[`step6_ai_detect_objects.py`](step6_ai_detect_objects.py)

The Raspberry Pi AI Camera has a neural network running on the sensor
itself. This program waits until it recognises something, draws a box
round it, and saves one picture.

Concepts introduced:

-   The `IMX500` device and loading a model onto the sensor
-   Reading detections out of the frame's metadata
-   Confidence scores, and choosing a cut-off
-   `picam2.pre_callback` to draw on every frame before it is saved
-   `cv2.rectangle()` and `cv2.putText()`

Worth doing with the Scouts: hold up a cup, a book, a bag, a dog. The
model knows eighty everyday objects and it is quite good at those. Then
try it on a photograph of a deer or a raccoon and watch it guess
something else entirely, because neither is on its list.

That is not a fault in the camera, and it decides how the later steps use
it. **A "yes" from this model is worth something; a "no" means nothing at
all**, because most of the animals we care about were never in its
vocabulary.

### Step 7 --- Better motion detection with the AI Camera

[`step7_ai_motion_detection.py`](step7_ai_motion_detection.py)

Step 5 saved a picture whenever enough pixels changed, which works until the
wind blows. Step 7 asks a better question: it finds the biggest *connected
patch* of changed pixels, and compares each frame against a memory of what
the scene usually looks like rather than against the frame before. That
second change is what lets it photograph a deer standing still.

It also uses the neural network built into the AI Camera --- but in one
direction only. The model knows eighty everyday objects and not one of them
is a deer, so a "no" from it means nothing; a "yes" can rescue a blob we were
unsure about.

This is the lesson, and it is meant to be read top to bottom.

### Step 8 --- Telling a shadow from an animal

[`step8_reject_shadows.py`](step8_reject_shadows.py)

Step 7 plus the unglamorous half: telling a branch's shadow from an animal,
recording unbiased training bursts so thresholds can be measured instead of
guessed, a measurements CSV, rate limits and a disk guard, and a fingerprint
of its own source in every photograph so you can tell what a camera is really
running.

None of that teaches anything about finding animals in pictures, which is
exactly why it lives in its own file. **This is the one that gets deployed.**

### Step 9 --- The same lesson, without the AI Camera

[`step9_plain_motion.py`](step9_plain_motion.py)

Steps 6, 7 and 8 were written for the AI Camera, and because of that the
cameras with an ordinary Camera Module never got them. They stayed on step 5:
save a photograph whenever enough pixels changed *anywhere*.

Step 7 had already explained why that fails --- "ten thousand pixels of
shivering leaves count exactly the same as ten thousand pixels of deer" ---
but only the AI cameras got the fix. It is worth knowing how badly that went.
wildlifecam14 watched a fence line for twenty-one hours and kept **1,109
photographs, not one of which had an animal in it.** Every one was wind in an
oleander. A squirrel on that fence covers about 1,100 pixels of the small
frame, and step 5 was waiting for 20,000.

Step 9 takes step 7's two ideas and leaves the neural network behind:

-   The biggest **joined-up patch** of change, instead of the total number of
    changed pixels anywhere. Wind scatters thousands of tiny specks across the
    frame; a squirrel is one solid lump. They look identical if you only
    count, and nothing alike if you ask which pixels are touching.
-   A **memory** of what the scene usually looks like, instead of the frame
    before it --- so an animal that stops moving does not vanish, and an
    animal holding still is exactly the photograph worth having.

And one change that is not an idea at all, just a number: it looks four times
a *second* instead of four times a *minute*. A squirrel crosses this fence in
about a second, so step 5 could miss one entirely in the gap between two
looks.

It also writes a measurements CSV with one row per look --- kept or not ---
which is how you choose the blob threshold for a new camera position instead
of guessing it. Sidecars only exist for photographs that *were* kept, so on
their own they can never tell you what the camera walked past:

``` bash
python3 step9_plain_motion.py --record
```

That step 8 is 1,645 lines and only 16 of them touch the AI camera is the
whole point of this step: the neural network was never the part doing the
work.

### Final --- Choosing which step to run

[`final_motion_capture.py`](final_motion_capture.py)

This is what systemd starts at boot, and it contains no motion detection at
all. It looks at which camera is plugged in and hands over to the newest step
written for that camera:

| camera | program |
| --- | --- |
| Raspberry Pi AI Camera (IMX500) | [`step8_reject_shadows.py`](step8_reject_shadows.py) |
| an ordinary Camera Module | [`step9_plain_motion.py`](step9_plain_motion.py) |

Upgrade the hardware, reboot, and the right program runs by itself --- there
is no service file to edit on eleven Raspberry Pis.

It used to be lopsided. The AI Camera was handed to a numbered step, while the
ordinary Camera Module ran a copy of step 5's rule buried at the bottom of
*this* file, where no Scout reads it and nobody thought to improve it. That is
how the plain cameras spent a month on a rule that could not see a squirrel.
Both paths now point at a numbered step, so the next time one track learns
something, the other one is a file you can find.

Both programs run the camera with two streams: a small 640x480 one the motion
detection watches, and a big 2304x1296 one saved when something moves. Both
write into the same layout, so nginx and `sync_cameras.py` cannot tell them
apart. A typical photo layout is:

``` text
/var/www/html/photos/
├── 2026-08-09/
│   ├── 150501.jpg
│   ├── 150722.jpg
│   └── 151104.jpg
└── 2026-08-10/
    ├── 081207.jpg
    └── 082013.jpg
```

The daily directories are created automatically as needed.

## Running the wildlife camera automatically at boot

The finished camera should work without a monitor or keyboard. When
power is connected, Raspberry Pi OS boots and `systemd` starts the
wildlife-camera program.

Create:

``` text
/etc/systemd/system/wildlife-camera.service
```

with contents similar to:

``` ini
[Unit]
Description=Webelos Wildlife Camera
After=network.target

[Service]
Type=simple
User=webelos
WorkingDirectory=/home/webelos
ExecStart=/usr/bin/python3 /home/webelos/final_motion_capture.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Adjust the path if the repository is stored somewhere else on the Pi.

Enable the service:

``` bash
sudo systemctl daemon-reload
sudo systemctl enable wildlife-camera
sudo systemctl start wildlife-camera
```

### Cameras that have been upgraded to the AI Camera

The service always starts `final_motion_capture.py`, but that program does no
motion detection itself. It checks what camera is actually plugged in and
hands straight over: a Raspberry Pi AI Camera goes to
[`step8_reject_shadows.py`](step8_reject_shadows.py), which uses the AI built
into the sensor, and an ordinary Camera Module goes to
[`step9_plain_motion.py`](step9_plain_motion.py), which reaches nearly the
same decision without one.

There is deliberately no fallback to an older step. A camera quietly running
last week's rules is worse than one that does not start, because the first
kind is discovered weeks later on a card full of leaves. If the step it wants
is missing the service exits and says so in the journal, so **copy both step
files to a card, not just the launcher.**

So upgrading a camera is: swap the hardware, reboot, done. There is no
service file to edit, and a Pi still on the older camera module carries
on exactly as before. The handover uses `os.execv`, which replaces the
running program rather than starting a second one, so `systemctl status`
and `Restart=` keep working as they always did.

Both programs write into the same place, so nginx and
[`sync/sync_cameras.py`](sync/sync_cameras.py) cannot tell which one ran:

``` text
/var/www/html/photos/2026-08-23/
├── 141530.jpg               <- the photograph
├── 141530_annotated.jpg     <- the same frame with the boxes drawn on
└── 141530.json              <- what the camera measured and what the AI saw
```

The last two are new with the AI Camera. Opening the annotated copy in
the browser next to the original is the quickest way to see *why* the
camera decided to keep a picture.

To check which program a camera is running:

``` bash
journalctl -u wildlife-camera | grep "handing over"
```

Check it:

``` bash
systemctl status wildlife-camera
```

View its journal:

``` bash
journalctl -u wildlife-camera
```

or follow the log:

``` bash
journalctl -u wildlife-camera -f
```

The real test is to reboot:

``` bash
sudo reboot
```

Do not log into the Pi to start anything. After it boots, walk in front
of the camera and verify that a new photograph appears on the website.

## nginx and the wildlife-camera website

nginx provides a very small local website. This is particularly useful
when camping because a phone or laptop can browse the photographs
without using SSH or SCP.

Install nginx:

``` bash
sudo apt update
sudo apt install nginx
```

The web files live under:

``` text
/var/www/html/
```

Our layout is:

``` text
/var/www/html/
├── index.html
├── pack-logo.png
└── photos/
```

The `index.html` page can contain the Pack logo, the Scout's name, a
short description of the project, and a link to `/photos/`.

For example:

``` html
<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>Webelos Wildlife Camera</title>
</head>
<body>
    <img src="pack-logo.png" alt="Pack logo" width="180">

    <h1>Webelos Wildlife Camera</h1>

    <h2>Camera built by: YOUR NAME HERE</h2>

    <p>
        This Raspberry Pi wildlife camera watches for movement
        and saves pictures of wildlife it sees.
    </p>

    <p><a href="/photos/">View Wildlife Photos</a></p>
</body>
</html>
```

This is intentionally simple so the Scouts can customize their own
pages.

### Allow browsing the photo directories

nginx normally returns `403 Forbidden` when a directory contains no
index page. Enable a directory listing for the photographs.

Inside the existing `server { ... }` section of the nginx configuration,
add:

``` nginx
location /photos/ {
    autoindex on;
    autoindex_exact_size off;
    autoindex_localtime on;
}
```

Test the configuration before reloading:

``` bash
sudo nginx -t
```

Then:

``` bash
sudo systemctl reload nginx
```

The photos can now be browsed at a URL such as:

``` text
http://wildlifecam.local/photos/
```

or by using the Pi's IP address.

nginx and the wildlife-camera website do **not** require Internet
access. A phone, laptop, and Raspberry Pi only need to be connected to
the same local Wi-Fi network.

## Wi-Fi and headless operation

Each Raspberry Pi can remember several Wi-Fi networks. This is useful
because the wildlife camera might be used at different homes, at a Scout
meeting, or on a camping trip.

Current Raspberry Pi OS uses NetworkManager, which can be managed from
the command line with `nmcli`.

### Show saved networks

``` bash
nmcli connection show
```

Show Wi-Fi networks currently visible:

``` bash
nmcli device wifi list
```

### Add a Wi-Fi network

If it is OK for the Pi to connect to the network immediately:

``` bash
sudo nmcli device wifi connect "NETWORK_NAME" password "NETWORK_PASSWORD"
```

Be careful doing this over SSH: if the Pi switches networks, the SSH
connection may disappear.

To save a network without deliberately switching to it first:

``` bash
sudo nmcli connection add \
    type wifi \
    ifname wlan0 \
    con-name "NETWORK_NAME" \
    ssid "NETWORK_NAME"

sudo nmcli connection modify "NETWORK_NAME" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "NETWORK_PASSWORD"

sudo nmcli connection modify "NETWORK_NAME" \
    connection.autoconnect yes
```

Wi-Fi SSIDs are case-sensitive.

### Wi-Fi priorities

NetworkManager can prefer one saved network over another. Higher numbers
have higher autoconnect priority.

For example, make the normal home network preferred:

``` bash
sudo nmcli connection modify "Stokely" \
    connection.autoconnect-priority 100
```

Give a camping network or phone hotspot a lower priority:

``` bash
sudo nmcli connection modify "WebelosCamp" \
    connection.autoconnect-priority 50
```

Another home network could be:

``` bash
sudo nmcli connection modify "AnotherHome" \
    connection.autoconnect-priority 25
```

Inspect the saved priorities:

``` bash
nmcli -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show
```

See the currently connected Wi-Fi network:

``` bash
nmcli -t -f active,ssid dev wifi | grep '^yes'
```

This lets the camera boot headlessly and automatically connect to the
best available saved network.

### Camping without Internet

A Wi-Fi network does not need Internet access for this project.

An Android phone hotspot or a small travel router can provide a local
network even when there is no cellular service. The Raspberry Pis and
laptop can all join that network and communicate locally.

That allows:

-   Opening the wildlife-camera website
-   Browsing photographs
-   SSH access
-   Copying images with `scp`

A useful approach is to configure every camera with the same camping
SSID and password before leaving home. When the camping hotspot or
router is turned on, all of the cameras can join automatically.

## Building several cameras

Rather than repeating every installation step on each Raspberry Pi, set up
one card completely and clone it. The procedure --- configuring the master
card, saving an image of it, burning copies, and giving each copy its own
hostname and SSH host keys --- is in
[`docs/sdcard-image-instructions.md`](docs/sdcard-image-instructions.md).

## Hardware

The project intentionally works with several generations of Raspberry Pi
so older hardware can be reused.

### Raspberry Pi

Any of these are suitable:

-   **Raspberry Pi 3 Model B/B+** --- perfectly useful for the basic
    camera and motion-detection project.
-   **Raspberry Pi 4 Model B** --- faster and an excellent
    general-purpose choice.
-   **Raspberry Pi 5** --- fastest option and useful for future
    experiments with more advanced computer vision or AI hardware.

Older Pis are especially appropriate for a Scout project: reuse
equipment that is already available rather than buying new hardware
unnecessarily.

### Raspberry Pi Camera Module 3

The standard **Raspberry Pi Camera Module 3** is the recommended camera.

It provides a good-quality camera, autofocus, and direct support through
Raspberry Pi's camera software and Picamera2. Its sensor is 4608x2592;
`step9_plain_motion.py` saves photographs at 2304x1296, the size the
sensor produces natively at half resolution, which a Pi Zero 2 W can
hold in memory.

Pay careful attention to the ribbon cable and connector type. Different
Raspberry Pi models use different camera connector sizes, so make sure
each kit has the appropriate cable.

### microSD card

Each Pi needs a microSD card containing Raspberry Pi OS and the project
software.

A 32 GB card is plenty for development, although larger cards provide
more room for unattended photographs.

The final program should stop saving photographs before the filesystem
becomes completely full.

### Power supplies

Use a suitable power supply for the Pi generation:

-   Pi 3: appropriate 5 V micro-USB supply.
-   Pi 4: official 15 W USB-C supply is a good choice.
-   Pi 5: official 27 W USB-C supply is preferred, especially when
    peripherals are attached.

Reliable power is important for Raspberry Pi stability.

### Optional field equipment

Useful additions include:

-   USB battery pack for outdoor deployment
-   Simple enclosure or wildlife-camera "blind"
-   Camera ribbon cable appropriate for the Pi
-   Female-to-female jumper wires for future sensors
-   BME280 temperature/humidity/pressure sensor
-   Travel Wi-Fi router or Android phone hotspot

## Possible next steps

Once the basic wildlife camera is reliable, the project can grow without
replacing the work already completed.

Ideas include:

-   Add a BME280 environmental sensor.
-   Display current temperature and humidity on the website.
-   Log temperature readings and graph them over time.
-   Generate thumbnail galleries instead of nginx's basic directory
    listing.
-   Show the most recent wildlife photograph on the home page.
-   Count motion events.
-   Experiment with the Raspberry Pi AI Camera or an AI accelerator.
-   Try identifying animals instead of merely detecting motion.

Some of this is already started:

-   Automatically copy photographs to a laptop when returning to camp ---
    see [`sync/README.md`](sync/README.md), which finds the cameras on the
    local network and copies their photographs into a directory per camera.

The important idea is that each feature can be introduced as another
small step. The finished system is built from the same simple concepts
introduced in the first few Python programs.
