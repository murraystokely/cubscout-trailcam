# The phone at the campout --- design

**Status:** design, no code yet (2026-10-04).
**Companion to:** [`gps-design.md`](gps-design.md), which gives each camera
the time and its place from a GPS dongle carried round at setup and
takedown. This does the same job with the Android phone that is in a
pocket anyway, and adds a record of where every camera was put.

At setup the phone makes a Wi-Fi hotspot called `webelos`. A camera within
range joins it, asks the phone for the time, and sets its clock. Standing
at the camera, you tap "put wildlifecam11 here" on the phone; the phone
saves its GPS position, and the camera fetches it as its own location. At
takedown the same visit measures how far the camera's clock drifted.

---

## Why the phone

- **It already has the time and GPS**, with a better antenna than the
  VK-162 and a fix in seconds rather than a minute and a half.
- **It records where each camera went**, at the camera, to a few metres,
  on a screen you can see. That is a second record, kept on the phone,
  whether or not the camera ever gets it.
- **Nothing else to carry.** No dongle, no OTG adapter, no base station, no
  second battery.

The dongle stays as the fallback: it works with no phone and no Wi-Fi.

### Considered first: a base station

A battery-powered Raspberry Pi 5 with a USB Wi-Fi adapter, the VK-162,
gpsd and chrony, serving NTP all weekend on its own hotspot. It would keep
every camera in range on the right time all weekend, and the laptop could
sync every camera from one spot. It was set aside because it is one more
box, battery and antenna to carry and look after; because it only knows
where the *site* is, never where a camera is; and because the cameras'
small 2.4 GHz antennas would limit its range anyway. Two things learned
while designing it carry over:

- **A WPA2 password needs at least 8 characters.** `webelos` has 7.
- **The cameras only speak 2.4 GHz.** The hotspot must offer it.

## How it works

```
 Android phone                                  camera (Pi Zero 2 W)
 ---------------------------------------        ------------------------------
 hotspot "webelos", 2.4 GHz
 Termux: campout_server.py on port 8123  <----  joins "webelos"
   GET  /now             phone's clock          wildlife_gps.py, phone mode:
   GET  /placement/<cam> where you put it         measure clock, set it, log it
   POST /checkin         what the camera did      fetch its placement -> gps.json
   GET  /                the page you use        step 10 copies gps.json into
                                                   every photograph, as now
```

### The phone side: Termux, not an app

There is no app to write or install from a store. **Termux** is a Linux
terminal for Android, and its **Termux:API** add-on lets programs read the
phone's GPS. Install both from F-Droid (the Play Store builds are out of
date), then `pkg install python termux-api`.

The server, `campout_server.py`, is plain Python using only the standard
library, like the rest of this project, so it reads the same way. It
listens on port 8123 on every interface, so the cameras on the hotspot can
reach it. (Port 8123 because Android does not let ordinary apps use ports
below 1024, which rules out the real NTP port, 123. That is why the
cameras ask over HTTP instead of using `timesyncd`.)

`termux-wake-lock` keeps it running with the screen off, and Termux needs
its battery optimisation turned off in Android's settings, or Android
will stop it.

**The endpoints:**

| Request | Answer |
|---|---|
| `GET /now` | `{"unix": 1791163789.123}`: the phone's clock, read as late as possible. |
| `GET /placement/wildlifecam11` | Where you put that camera, `{"lat", "lon", "alt_m", "accuracy_m", "placed_utc"}`, or 404 until you have. |
| `POST /checkin` | The camera reports back: boot, how wrong its clock was, whether it set it, whether it has its placement. |
| `GET /` | The page you use, in the phone's own browser at `http://localhost:8123/`. |

**The page** is the "little GUI":

- **Cameras seen**, from their check-ins: `wildlifecam11 — clock was 7.7
  days slow, set ✓ — placement ✓ — 2 min ago`. This is how you know a
  camera is done before walking on, the job the status file does for the
  dongle.
- **Place a camera here:** pick a camera, tap the button. The phone takes
  a GPS fix with `termux-location`, waits until it is accurate to about
  10 m, shows the accuracy, and saves it with a time and an optional note
  ("oak by the creek, facing the trail").
- **Placements so far**, with a button to export them as CSV to the
  phone's Documents folder. That file is the second record.

Placements are kept in a small JSON file in Termux's home folder, written
the same safe way as `gps.json` (write a temporary file, then rename it),
so a crash never leaves half a file.

### The camera side: the phone as a second source

The camera already knows how to measure its clock, set it, and keep the
records (`wildlife_gps.py`, from the dongle work). The phone becomes a
second source of the same facts:

- **Started by joining `webelos`.** A NetworkManager dispatcher script
  starts `wildlife-phone.service` when the `webelos` connection comes up,
  and it stops when the connection goes down. It is the same idea as the
  dongle's udev rule: the visit starts it, and nothing runs otherwise.
- **The phone is found at the default gateway**, the hotspot's own
  address. Android picks that address itself, and newer versions change
  it, so nothing is written into the camera.
- **Measuring the time over HTTP**, the way NTP does it: note the Pi's
  clock just before the request (t0) and just after the answer (t1). The
  phone's time is taken to belong to the middle, (t0 + t1) / 2. Over a
  hotspot a request takes tens of milliseconds, so the answer is good to
  about half that. A sample counts only if it came back within 0.25 s;
  **three in a row** that agree make it trusted, the phone's version of
  the dongle's "10 seconds of fix".
- **Then exactly what the dongle does:** measure first, set the clock if
  it is out by more than a second (unless a time server already has it),
  write `gps.json`, and append a row to the per-boot log, so setup-and-
  takedown drift correction works the same whichever source took it. Each
  attachment in `gps.json` and each log row records `"source": "phone"` or
  `"dongle"`.
- **The placement.** The camera asks for `/placement/<its hostname>` every
  few seconds while it is connected. When you tap "here" at the camera, it
  gets that position and records it as its own: the `position` block of
  `gps.json`, which the step 10 programs copy into every photograph's
  sidecar and JPEG. It is still "this camera, this boot". It is not
  wherever the phone happens to be, because you made it this camera's
  position by standing there and choosing it.
- **The check-in** tells the phone what happened, for the page.

`photo_gps.py` and the two step 10 programs do not change: they read
`gps.json` as they do now.

### The hotspot settings

On the phone, once:

- **Name** `webelos`; **password** 8 or more characters, the same one on
  every camera.
- **Band 2.4 GHz** (some phones call it "Extend compatibility").
- **Turn off hotspot automatically: off**, or it stops after a few idle
  minutes.

On each camera, a saved Wi-Fi profile for `webelos` with a lower priority
than home, as the README's "Wi-Fi priorities" describes, plus the
dispatcher script and service.

### Things to know

- **A camera can take a minute or two to join.** NetworkManager scans
  every so often when it has no network, not continuously. The page shows
  when each camera checks in.
- **A few cameras at a time.** Android hotspots allow about 10 devices.
  Cameras out of range drop off as you walk on, which frees space anyway.
- **The phone's clock.** Android keeps it right from the mobile network,
  and with no signal for a weekend it drifts by well under a second a day.
  That is far better than a Pi's saved clock, and what the photographs
  need.
- **Placing a camera you are not standing at.** The page says which camera
  you are placing and shows the fix's accuracy. Tap "here" at the camera,
  not on the way to it. The placement keeps its time, so a mistake can be
  spotted and corrected later.
- **The laptop at camp.** It can join the hotspot too, and
  `sync_cameras.py` may find cameras near the phone by mDNS. Whether an
  Android hotspot passes multicast between devices needs testing.

## Order of work

1. **On the bench, with the phone:** Termux and Termux:API from F-Droid;
   `termux-location` gives a fix. Turn on the hotspot **with mobile data
   off** (the campsite case) and check it still runs. Run a three-line
   Python server on port 8123 and reach it from the laptop on the hotspot,
   at the gateway address. Note what address the phone gives itself.
2. **One camera** with the `webelos` profile: it joins, and
   `curl http://<gateway>:8123/now` works from it.
3. **`campout_server.py`**: the four endpoints, placements saved safely,
   CSV export, the page. Tested on the laptop first with a fake
   `termux-location`.
4. **The camera side:** the phone source in `wildlife_gps.py`, the
   dispatcher script and `wildlife-phone.service`, tested against a fake
   server.
5. **A field test:** set up two cameras with the phone, leave them, take
   them down. Check the placements on the phone match the cameras'
   `gps.json`, and the drift appears in the logs.
6. **The master images and cards:** the Wi-Fi profile, dispatcher script
   and service.

## Open questions

- **The password**, 8+ characters.
- **Should the camera use the phone's current position at all?** This
  design says never: only a placement you made. That means the camera has
  no position until you tap. That is the price of never recording a
  position that might be wrong.
- **Termux running on its own.** Termux:Boot could start the server when
  the phone starts, but starting it by hand at the trailhead is one
  command and easier to understand.
