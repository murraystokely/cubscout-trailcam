# The phone at the campout --- design

**Status:** design, no code yet (2026-10-04).
**Companion to:** [`gps-design.md`](gps-design.md), which gives each camera
the time and its place from a GPS dongle carried round at setup and
takedown. This does the same job with an Android phone and an app of our
own, and adds a record of where every camera was put.

At setup the phone runs a Wi-Fi hotspot called `webelos` and the
**Campout** app. A camera within range joins the hotspot, asks the app for
the time, and sets its clock. Standing at the camera, you tap "Place
wildlifecam11 here"; the app saves the phone's GPS position, and the
camera fetches it as its own location. At takedown the same visit measures
how far the camera's clock drifted, and the app shows it.

---

## Why the phone, and why an app

- **The phone already has the time and GPS**, with a better antenna than
  the VK-162 and a fix in seconds rather than a minute and a half.
- **It records where each camera went**, at the camera, to a few metres,
  on a screen. That record stays on the phone whether or not the camera
  ever gets it.
- **Nothing else to carry.** No dongle, no OTG adapter, no base station.
- **A real app** (Kotlin, built in Android Studio) because it should look
  nice and be easy to use at a trailhead, by whoever is holding the phone.

The dongle stays as the fallback: it needs no phone and no Wi-Fi.

### Considered first

- **A base station:** a Raspberry Pi 5 with a USB Wi-Fi adapter, the
  VK-162, gpsd and chrony, serving NTP all weekend on its own hotspot. Set
  aside because it is one more box, battery and antenna to carry; because
  it knows where the *site* is, never where a camera is; and because the
  cameras' small 2.4 GHz antennas would limit its range anyway.
- **A Python server in Termux** on the phone. It needs no app development,
  but it is a terminal, not something to hand to a parent at a trailhead.

Two things learned along the way carry over:

- **A WPA2 password needs at least 8 characters.** `webelos` has 7.
- **The cameras only speak 2.4 GHz.** The hotspot must offer it.

## How it works

```
 Android phone                                   camera (Pi Zero 2 W)
 ----------------------------------------        ------------------------------
 hotspot "webelos", 2.4 GHz (turned on            joins "webelos"
   in Settings by hand)                           wildlife_gps.py, phone mode:
 Campout app                                        finds the phone at its
   foreground service: HTTP on port 8123  <-----    default gateway
     GET  /now                                      measures its clock, sets it
     GET  /placement/<camera>                       fetches its placement
     POST /checkin                                  writes gps.json, logs it
   screens: Cameras, Place, Placements           step 10 programs copy gps.json
                                                    into every photograph, as now
```

## What an Android app can and cannot do here

**It cannot see the hotspot's client list.** The callback that reports
connected devices (`SoftApCallback.onConnectedClientsChanged`) is a system
API, for the Settings app and the phone maker only. The old workarounds,
reading `/proc/net/arp` or running `ip neigh`, were blocked in Android 10
and 11. So **the cameras introduce themselves**: each one checks in to the
app, which then knows its address, its name, how wrong its clock was and
whether it has its placement. That is more useful than a list of devices,
which would also include phones and laptops with no names.

**It cannot turn on the normal hotspot or name it.** The only hotspot an
app may start (`LocalOnlyHotspot`) gets a random name and password every
time, which no camera could have saved. So the hotspot is turned on by
hand in Settings, set up once as `webelos`. The app can open the right
settings screen with one button, and checks whether the hotspot looks up.

**It can keep its server running with the screen off**, as a foreground
service (below).

## The app

### Screens

**Cameras** (the home screen)

- Server status: running or stopped, the phone's hotspot address and
  port, a start/stop switch.
- A check that the hotspot is on, with a button to open its settings.
- **Every camera that has checked in**, newest first:
  `wildlifecam11 — clock was 7.7 days slow, set ✓ — placed ✓ — 2 min ago`.
  This is how you know a camera is done before walking on. At takedown
  the same row says `clock had drifted 48 s fast`.
- **The phone's own clock against GPS**, from the last fix: "phone clock
  within 0.2 s of GPS". The phone is the cameras' time source, so it is
  worth seeing that it is right.

**Place**

- Pick a camera: from the ones that have checked in, or type a name.
- A live GPS reading: the accuracy in metres, counting down as the fix
  improves. **"Place here" turns on at 10 m or better.**
- An optional note ("oak by the creek, facing the trail") and,
  optionally, a photo of the camera in place.
- Placing the same camera again replaces its placement, and the old one is
  kept in the history.

**Placements**

- Every placement: camera, time, position, accuracy, note, and whether the
  camera has fetched it.
- **Export** as a CSV file to the phone's Documents folder, or share it.
  That file is the second record.

### Inside

| Part | How |
|---|---|
| Language and UI | Kotlin, Jetpack Compose. |
| HTTP server | A small embedded server inside a foreground service: NanoHTTPD, or Ktor's CIO engine. Port 8123 on every interface, so the hotspot can reach it. Android does not let apps use ports below 1024, which rules out the real NTP port (123); that is why the cameras ask over HTTP rather than using `timesyncd`. |
| Location | The fused location provider (Google Play services), high-accuracy, only while the Place screen is open. It works offline, from the satellites alone. |
| Time | The phone's clock, `System.currentTimeMillis()`, read as late as possible when answering `/now`. |
| Storage | Placements and check-ins in a small Room database. |
| Source code | In this repository, under `android/`. Exported placements stay on the phone; coordinates of where cameras were left do not go in the repository. |

### Running with the screen off

The server runs in a **foreground service**: Android keeps it alive with
the screen off, and shows a notification while it runs. The notification
is useful in its own right: "Campout server running — 3 cameras checked
in", with a Stop button.

- **Foreground service type `connectedDevice`** in the manifest, which
  Android 14 and later require, with the `CHANGE_WIFI_STATE` permission it
  depends on. (`specialUse` would also do for an app we install ourselves.
  Not `dataSync`: Android 15 limits it to six hours a day.)
- **A partial wake lock and a Wi-Fi lock** while the server runs, so
  neither the processor nor the Wi-Fi naps between camera requests.
- **Battery set to Unrestricted** for the app, which it asks for on first
  start (`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`). This guards against Doze,
  the deep sleep a phone falls into lying still with the screen off.
- **Some phone makers kill background apps regardless** (Samsung, Xiaomi,
  OnePlus and others add their own battery savers; dontkillmyapp.com
  lists the settings). Pixels behave as documented. The bench test below
  settles it for our phone.
- The **hotspot's own idle timer** is separate: "Turn off hotspot
  automatically" must be off.

Placing a camera needs none of this: the app is on screen when you tap,
so getting a GPS fix is the simple case.

**Permissions:** `INTERNET`, `ACCESS_FINE_LOCATION`, `FOREGROUND_SERVICE`,
`FOREGROUND_SERVICE_CONNECTED_DEVICE`, `CHANGE_WIFI_STATE`,
`ACCESS_WIFI_STATE`, `WAKE_LOCK`, `POST_NOTIFICATIONS`,
`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`.

## The API between app and camera

Two programs, written separately, so the contract is spelled out here.
Everything is JSON over HTTP on port 8123, at the phone's hotspot address.

**`GET /now`**

```json
{"unix": 1791163789.123}
```

The phone's clock in seconds since 1970, UTC, read as late as possible.

**`GET /placement/wildlifecam11`** --- 404 until that camera is placed, then:

```json
{"camera": "wildlifecam11", "lat": 37.33333, "lon": -121.70000,
 "alt_m": 53.5, "accuracy_m": 4.2, "placed_unix": 1791163801.0,
 "note": "oak by the creek"}
```

**`POST /checkin`**

```json
{"camera": "wildlifecam11", "boot": "f6fe6741", "uptime_s": 412.6,
 "offset_s": 663010.803, "stepped": true, "network_synced": false,
 "placement": true, "code": "17e2c53ba105"}
```

What the camera did: how wrong its clock was (phone minus camera, before
any change), whether it set it, and whether it has its placement. The app
answers `{"ok": true}` and records the camera's address from the
connection.

## The camera side: the phone as a second source

The camera already knows how to measure its clock, set it and keep the
records (`wildlife_gps.py`, from the dongle work). The phone becomes a
second source of the same facts:

- **Started by joining `webelos`.** A NetworkManager dispatcher script
  starts `wildlife-phone.service` when the `webelos` connection comes up,
  and it stops when the connection goes down, the way the dongle's udev
  rule starts and stops the dongle service.
- **The phone is found at the default gateway**, the hotspot's own
  address. Android chooses that address itself, and newer versions change
  it, so nothing is written into the camera.
- **Measuring the time over HTTP**, the way NTP does it: note the Pi's
  clock just before the request (t0) and just after the answer (t1); the
  phone's time belongs to the middle, (t0 + t1) / 2. Over a hotspot a
  request takes tens of milliseconds, so the answer is good to about half
  that. A sample counts only if it came back within 0.25 s, and **three in
  a row** that agree make it trusted: the phone's version of the dongle's
  ten seconds of fix.
- **Then exactly what the dongle does:** measure first; set the clock if it
  is out by more than a second, unless a time server already has it;
  write `gps.json`; append a row to the per-boot log. Setup-and-takedown
  drift correction then works the same whichever source took the
  measurement. Each attachment in `gps.json` and each log row records
  `"source": "phone"` or `"dongle"`.
- **The placement.** The camera asks for `/placement/<its hostname>` every
  few seconds while connected. When you tap "Place here" at the camera, it
  gets that position and records it as its own: the `position` block of
  `gps.json`, which the step 10 programs copy into every photograph. It is
  still "this camera, this boot", and it is **never** wherever the phone
  happens to be: you made it this camera's position by standing there and
  choosing it.
- **The check-in** tells the app what happened, after each measurement.

`photo_gps.py` and the step 10 programs do not change.

## The hotspot

On the phone, once:

- **Name** `webelos`, **password** 8 or more characters (the same on every
  camera).
- **Band 2.4 GHz** (some phones call it "Extend compatibility").
- **Turn off hotspot automatically: off.**

On each camera: a saved Wi-Fi profile for `webelos`, with a lower priority
than home (README, "Wi-Fi priorities"), the dispatcher script and the
service.

### Things to know

- **A camera can take a minute or two to join.** NetworkManager scans every
  so often when it has no network, not continuously. The Cameras screen
  shows when each one checks in.
- **A few cameras at a time.** Android hotspots allow about 10 devices.
  Cameras out of range drop off as you walk on.
- **The phone's clock.** Android keeps it right from the mobile network,
  and with no signal for a weekend it drifts by well under a second a day.
  The Cameras screen shows how it compares with GPS.
- **Placing a camera you are not standing at.** The Place screen says
  which camera, and shows the accuracy. Tap at the camera, not on the way
  to it. Each placement keeps its time, so a mistake can be spotted and
  redone.
- **The laptop at camp.** It can join the hotspot too, and
  `sync_cameras.py` may find nearby cameras by mDNS. Whether an Android
  hotspot passes multicast between devices needs testing.
- **Anyone on the hotspot can use the API.** The hotspot password is the
  protection, as it is for the cameras' web pages.

## Order of work

1. **Bench tests on the phone, before writing the real app:**
   - The hotspot runs **with mobile data off** (the campsite case). Note
     the address the phone gives itself.
   - A minimal app: a foreground service with an HTTP server answering
     `/now`. Reach it from the laptop on the hotspot. Then **screen off,
     phone still, 30 minutes**, and reach it again.
2. **One camera** with the `webelos` profile: it joins, and
   `curl http://<gateway>:8123/now` works from it.
3. **A fake app in Python** (a few lines of `http.server`, implementing the
   API above), so the camera side can be built and tested without the
   phone.
4. **The camera side:** the phone source in `wildlife_gps.py`, the
   dispatcher script and `wildlife-phone.service`, tested against the
   fake.
5. **The app:** the service and server first, then Cameras, Place,
   Placements and export.
6. **A field test:** set up two cameras with the phone, leave them, take
   them down. The placements on the phone should match the cameras'
   `gps.json`, and the drift should show on the Cameras screen and in the
   logs.
7. **The master images and cards:** the Wi-Fi profile, dispatcher script
   and service.

## Open questions

- **The hotspot password**, 8+ characters.
- **A photo with each placement?** Easy to add, and helps to find a camera
  again, but it means the app's storage holds pictures of where cameras
  are.
- **Finding cameras that have not checked in.** The app may scan its
  hotspot's addresses and ask each camera's web page to identify itself,
  as `sync_cameras.py --scan` does, or use Android's mDNS discovery
  (`NsdManager`). The check-in is enough to start with.
