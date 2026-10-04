# GPS --- design

**Status:** design, no code yet (2026-10-04).
**Companion to:** [`../ai/sites-design.md`](../ai/sites-design.md), which
says *"the camera does not know either"* about where it was, and the
millisecond-names commit (`bd2d1e1`), which exists because the camera does
not know when it is.

A VK-162 is a USB GPS receiver the size of a matchbox. Plug one into a trail
camera and the camera can answer both questions itself: the clock gets set
from the satellites, and every photograph carries the place it was taken.

---

## The problem it solves

A Raspberry Pi has no battery-backed clock. At power-up it sets the time to
the modification time of `/var/lib/systemd/timesync/clock` and stays there
until `systemd-timesyncd` reaches a time server, which on a fence post or at
a campsite is never. What that has cost so far:

- The 24 September cards filed a two-day run under a week-old folder.
- At Grant Park, wildlifecam10 lived through the same minutes twice and
  saved six photographs over ones from the day before.
- Every deployment's real start time has to be pieced together afterwards
  from the journal's boot list.
- Which site a photograph came from is a row typed by hand into
  `ai/deployments.csv`, keyed by boot id because the dates cannot be trusted.

GPS fixes the first three outright and turns the fourth into something the
camera writes down for itself.

## The hardware

The VK-162 is a u-blox 7 receiver behind a USB serial chip. Linux needs no
driver: it appears as `/dev/ttyACM0` (USB id `1546:01a7`) and sends plain
text --- NMEA sentences --- once a second at 9600 baud:

```
$GPRMC,160211.00,A,3720.00000,N,12142.00000,W,0.021,,111026,,,A*63
$GPGGA,160211.00,3720.00000,N,12142.00000,W,1,07,1.21,412.3,M,-30.1,M,,*67
```

`RMC` carries the date, time and whether the fix is valid (`A`, or `V` for
not yet). `GGA` carries the number of satellites, how good the geometry is
(HDOP) and altitude. Those two sentences are everything this design needs.

Things to know before plugging one in:

- **A Pi Zero 2 W needs a micro-USB OTG adapter**, on the port marked USB,
  not PWR. The Pi 4s and 5s take it directly.
- **First fix: about a minute and a half in the open.** Measured on
  2026-10-04 with the dongle on a laptop in the back garden, cold (the
  VK-162 keeps nothing between plug-ins): first sentence at once, time of
  day at 0:59, first valid fix at **1:15**, with 7 satellites and HDOP
  1.1; the trust test (below) would accept it at 1:25. Under oak canopy it will be slower, and
  the dongle's antenna is small, so the program has to be patient and say
  what it is waiting for.
- **Position: a few metres.** Over the ten minutes after the trusted fix,
  half the fixes were within 2.3 m of their median and 95% within 5.8 m;
  the first valid fix was 3.6 m off. A median over a few minutes is
  plenty for a trail camera.
- **It costs battery, and money.** Roughly a fifth of what a Zero 2 W
  draws, and a dongle per camera adds up. So we do not leave one on each
  camera: a dongle or two go round the cameras at setup and again at
  takedown (decided 2026-10-04). Everything below is designed for that.
- **No PPS over USB**, so time comes from the text sentences, not a
  precise pulse. Measured: once the leap seconds are right (see "Leap
  seconds"), sentences arrive about **45 ms** after the second they describe,
  steadily. Far better than the photographs need. The very first valid
  sentence arrived 1.2 s late, one more reason not to trust the first
  second.
- **Only our program reads the port.** That takes no setup: Raspberry Pi
  OS does not install `gpsd`, and the udev rule below tells `ModemManager`,
  if an image has it, to leave the dongle alone (one property on the rule,
  `ID_MM_DEVICE_IGNORE`). Nothing to remove from the image, nothing to
  check.

## The shape of it

Two programs, one file between them:

```
VK-162 ──NMEA──> wildlife_gps.py ──writes──> /var/lib/wildlifecam/gps.json
                   │       │                            │
                   │       └──appends──> photos/<day>/gps-<camera>-<boot>.csv
                   │                     (synced with the photos; the laptop
                   │                      corrects times from it)
                   └──measures, then sets, the system clock
                                                        │
                                read at every save by step10_ai_camera.py
                                                   and step10_camera_module.py
                                                        └──> sidecar + EXIF
```

**`wildlife_gps.py`** is a new, small, standalone program, started by systemd
when the dongle is plugged in and stopped when it is pulled out. It reads
NMEA, and once it trusts the fix it measures how wrong the clock is, sets
it, and writes the state file and the log.

**The camera programs** never touch the dongle. When they save a photograph
they read the state file, decide whether it belongs to this boot, and copy
what it says into the JSON sidecar and the JPEG's EXIF.

Keeping them apart is the point. The camera programs stay unprivileged and
keep working with no dongle, a broken dongle, or a dongle pulled out
mid-run; a crash in GPS parsing can never cost a photograph. And
`wildlife_gps.py` is small enough to be its own lesson.

### How this relates to the usual tools: gpsd, chrony, timesyncd

What a camera runs today, out of the box: **`systemd-timesyncd`**, Raspberry
Pi OS's default. It is a simple network time client. With a network it asks
a time server every half minute to half hour; if the clock is off by more
than about 0.4 s it steps it, otherwise it nudges the clock's rate
(slewing) until it agrees. Without a network it does nothing except restore
the saved floor at boot. No `gpsd`, no `chrony`. (`timedatectl
show-timesync` on a camera confirms which client is running.)

**chrony** would replace timesyncd and steer the clock continuously from
GPS and network servers together. It is the right tool for a GPS that is
always attached; for a dongle that visits for five minutes twice a
deployment it is the wrong shape. It slews rather than steps unless told
otherwise, and it keeps no record of how wrong the clock was before it
corrected it, which is the whole point of the takedown plug-in. Not
considered further.

**gpsd** is the real question. It is the standard Linux GPS daemon: it owns
the receiver, decodes whatever the receiver speaks, and hands out fixes as
JSON on a local socket, with tools (`cgps`, `gpsmon`) that show satellites
and signal strength. Would `wildlife_gps.py` be simpler as a gpsd client?

What gpsd would take off our hands:

- **The NMEA parser** --- about 60 lines, and the checksum.
- **Receiver quirks.** Different receivers, baud rates, binary protocols:
  gpsd copes; ours copes with the one dongle we have.
- **Field diagnosis.** `cgps` on a phone's SSH session shows *why* there is
  no fix --- three satellites, all weak --- better than our status line.

What it would not:

- We still need our program. gpsd does not set the clock, does not measure
  the clock's error before correcting it, does not write `gps.json` or the
  per-boot log, and has no idea about boots or attachments. That is most
  of `wildlife_gps.py`, and it all stays.
- The trust test stays too, though gpsd's `mode: 3` ("3D fix") would
  stand in for our `A`-and-four-satellites check.

What it would add: a package on every image, its configuration
(`/etc/default/gpsd`: which device, start on hotplug, `-n` to poll before
any client connects), a daemon in memory while the dongle is in, the
Python client library or a socket and JSON reader, and two programs
starting on plug-in in the right order instead of one.

So gpsd swaps 60 lines of parser we control for a daemon, a config file
and a client, while the bulk of the work is unchanged. For one known
receiver speaking plain NMEA, reading the port directly is less
machinery. It is also the better lesson: `cat /dev/gps0` and reading the
sentences is the most explainable part of the project, and gpsd puts a
layer between a Scout and that.

The design keeps the door open. `wildlife_gps.py` gets its fixes from one
function, `read_fixes(device)`, that yields `(utc, lat, lon, alt, hdop,
satellites, valid)`. If we later want gpsd --- a different receiver, a
GPS left on permanently --- only that function changes, to read gpsd's JSON
instead of the port.

### When there is a network time server as well

Normally there is not: in the field, GPS is the only source. But a camera
at home on Wi-Fi, or near a phone hotspot at camp, will reach a time server
through timesyncd, and then there are two things that can set the clock.
The rule is simple: **when the network has synchronised the clock,
`wildlife_gps.py` measures and logs but does not set it.** It knows by the
file `/run/systemd/timesync/synchronized`, which timesyncd creates when it
first reaches a server and which disappears at reboot.

The cases:

| What happens | Result |
|---|---|
| Network at boot, GPS plugged in later | timesyncd has already set the clock. GPS measures an offset of a few tenths of a second, logs it with `network_synced=1`, does not step. |
| GPS sets the clock, network appears later | timesyncd finds the clock within a second; nudges it, or steps it by a few tenths (the NMEA delay). Harmless. Photographs after that say `"network"`. |
| Both, and they disagree by more than 2 s | One of them is wrong. GPS logs the disagreement and a journal warning, and leaves the clock to timesyncd: the network source is checked continuously and the dongle is about to leave. The log keeps both numbers for the laptop to judge. |
| Network for a while, then gone | The clock keeps the rate timesyncd last set and drifts from there; nothing for GPS to do differently. |
| A Pi 5 with a battery-backed real-time clock | It boots with roughly the right time. GPS still measures and corrects as usual; the setup offset is just small. |

One consequence for drift correction: while timesyncd is adjusting the
clock's rate it adjusts uptime's rate too (the kernel steers both
together), so log rows marked `network_synced` are left out of the drift
fit. That is no loss: photographs taken while the network was in charge
already have the right time, and say so with `"network"`.

## Plugged in at setup and at takedown

We do not leave a dongle on each camera. One or two go round the cameras:

- **At setup.** Plug in at the post, wait for the fix, unplug and move to
  the next camera. The clock is right from then on, give or take drift,
  and every photograph in that boot gets the position.
- **At takedown.** Plug in when collecting the camera, before shutting it
  down or pulling the card. This one fix says exactly how far the clock
  has drifted, at a known uptime.

Two fixes in one boot, days or weeks apart, give the drift rate of this
camera's crystal, and with it every photograph in between can be
corrected, assuming the drift is linear --- see "Correcting the time".
Either one alone still helps: setup only gets the position and a clock
that starts right; takedown only back-dates the whole boot.

Each time the dongle is plugged in is an **attachment**, and every
attachment leaves a record, whether or not it changes the clock. The second
attachment never overwrites what the first one learned.

## Nothing is written until a fix is trusted

A receiver that has just been plugged in spends its first minute or so
saying `V` (no fix). Its first few valid readings can be a little off: in
the garden test the first valid position was 3.6 m from where it settled,
and its sentence arrived 1.2 s late. Waiting a few seconds is enough.

Every sentence's checksum is checked first, and a line with a wrong one is
dropped unread. Then a fix is **trusted** when:

1. the receiver has said `A` (valid) with **at least 4 satellites for 10
   seconds in a row**, and
2. the **year is 2026 or later**. A receiver with an old firmware's
   week-counting bug can report a date decades out; this one line catches
   it.

The latest of those ten seconds is the one used, for both the position
and the clock. In the garden test the first valid fix was at 1:15, so this
would have trusted it at 1:25.

Until the first trusted fix of an attachment, `wildlife_gps.py` writes
**nothing**: no `gps.json`, no log row, no clock change. It prints a status
line to the journal once a minute (`waiting: 3 satellites, no fix yet`) so
that `journalctl -u wildlife-gps` says what it is waiting for. If the fix is
lost mid-attachment (someone's hand over the dongle, the tree canopy), it
stops writing and waits for another 10 seconds in a row before it starts.

A `gps.json` left over from an earlier boot is left alone until this boot's
first trusted fix replaces it. The camera programs already treat it as
"earlier boot" (below), so a stale file is never mistaken for a new one.

The camera programs check too: `where_and_when()` ignores a `gps.json`
without a `position` block or with an unparseable one. Two checks for the
same thing, because the cost of a wrong location in a photograph is that
it is believed.

### Leap seconds: the first twelve minutes are two seconds fast

Found in the first real test, 2026-10-04. GPS time is currently 18 seconds
ahead of UTC (leap seconds), and the receiver learns that number from a
satellite message sent only every 12.5 minutes. Until then it uses the
number in its firmware, which dates from 2012 and says 16. So for roughly
the first twelve minutes after lock, its time is **2 seconds fast**, while
passing every check above:

| Time (UTC) | GPS minus laptop clock (laptop on NTP) |
|---|---|
| 22:52 -- 23:02, firmware's 16 | +1.95 s |
| from 23:03, satellites' 18 | −0.04 s |

**We accept it.** A setup or takedown visit is usually shorter than twelve
minutes, so a camera's clock will usually be set 2 seconds fast. For a
trail camera that is nothing: the photograph is the same, every camera set
up the same way is off by the same amount, and a fast setup and a fast
takedown cancel out of the drift rate. If the dongle stays in longer, the
receiver corrects itself and the program's ten-minute check picks that up.
Correcting it ourselves would mean speaking the receiver's binary protocol
to ask for its leap-second count --- possible, and tested, but not worth the
code.

## Where the location lives

```
/var/lib/wildlifecam/gps.json
```

`/var/lib` because this is state a program writes, not configuration a
person sets --- that is `/etc/wildlifecam/site`, from the sites design. Not
`/run`, which is emptied at every boot: keeping the last fix across a reboot
is useful (see below), and the file says which boot it came from, so it
never needs the filesystem to forget it.

Not in `/var/www/html/photos` either, where nginx would serve it to anyone
on the camera's Wi-Fi.

`gps.json` is the camera programs' view: *the latest trusted answer for this
boot*. The complete record --- every trusted fix and every clock change,
from every attachment --- is the per-boot log described under "The log".

What it holds:

```json
{
  "camera": "wildlifecam10",
  "boot": "91b8124e",
  "receiver": "u-blox 7 (VK-162)",

  "position": {
    "lat": 37.33333,
    "lon": -121.70000,
    "alt_m": 412.3,
    "hdop": 1.2,
    "satellites": 7,
    "samples": 180,
    "spread_m": 3.1
  },

  "clock": {
    "set_by_gps": true,
    "first_set_uptime_s": 87.0,
    "last_set_uptime_s": 2419291.0
  },

  "attachments": [
    {"first_fix_uptime_s": 84.2, "last_fix_uptime_s": 264.2,
     "gps_utc": "2026-10-11T16:02:11Z", "offset_s": 412337.6, "stepped": true},
    {"first_fix_uptime_s": 2419286.0, "last_fix_uptime_s": 2419466.0,
     "gps_utc": "2026-11-08T16:01:24Z", "offset_s": -48.3, "stepped": true}
  ]
}
```

- **`boot`** is the same eight characters steps 8 and 9 already write
  into every sidecar, from `/proc/sys/kernel/random/boot_id`.
- **`position` is a median, not the latest reading.** A trail camera does
  not move, and single GPS readings wander by several metres. It is the
  median of every trusted fix this boot, across attachments, and
  `spread_m` says how much they disagreed. If a later attachment's fixes
  sit more than about 50 m from the earlier ones, the camera has been
  moved without being switched off: keep the first attachment's position,
  and say so in the journal and the log.
- **`attachments`** has one entry per time the dongle was plugged in and
  reached a trusted fix, appended, never replaced. `offset_s` is how wrong
  the Pi's clock was at that attachment's first trusted fix, measured
  *before* anything changed it: GPS time minus Pi time, so positive means
  the Pi was behind. In the example the first attachment found the clock
  four days slow (the restored floor) and the second, four weeks later,
  found it 48 seconds fast: that 48 seconds is the drift, about 20 parts
  per million.
- **Uptimes beside every time.** `uptime_s` is the clock that does not lie,
  and it is what lets the laptop line a fix up against photographs.

**Written atomically:** write `gps.json.tmp`, then `os.replace()` it over
`gps.json`, so a camera program reading at the wrong instant gets the old
file or the new one and never half of either.

**Written rarely:** at an attachment's first trusted fix, when the clock is
set, and then at most once a minute while the median is still settling.
Not once a second; it is an SD card.

## Setting the clock

At each attachment's first trusted fix, `wildlife_gps.py`:

1. **measures first:** reads the Pi's clock and the fix's GPS time
   together, and records the offset (in `gps.json` and in the log, flushed
   to the card with `fsync` straight away --- at the end of a deployment
   someone may pull the power a minute later);
2. **then** sets the clock, if it is out by more than one second, with
   `time.clock_settime(time.CLOCK_REALTIME, ...)`, and records the step;
3. `touch`es `/var/lib/systemd/timesync/clock` so the next boot starts from
   a true time instead of a stale one;
4. logs one line to the journal: `clock was 48.3 s fast; corrected`.

Measuring before correcting is what makes the end-of-deployment plug-in
worth doing: the number that matters is how wrong the clock *had become*,
and it is gone once the clock is fixed.

If the dongle is left on for a while --- someone gets distracted at the
next camera --- it repeats the same measure-then-correct every ten minutes,
with each one going into the log. That is a side effect, not a way we plan
to run.

NMEA sentences arrive about 45 ms after the instant they describe
(measured). That bias is the same at every attachment, so it cancels out
of the drift rate; it is too small to matter for anything else.

If `/run/systemd/timesync/synchronized` exists, a network time server is
already in charge: measure and log, but skip step 2. See "When there is a
network time server as well".

**Privilege.** Setting the clock needs `CAP_SYS_TIME`. Run the service as
the `webelos` user with `AmbientCapabilities=CAP_SYS_TIME` rather than as
root.

**What the camera programs feel.** The clock jumps while they run: forward
by hours or days at the first attachment, by seconds in either direction
at a later one. Step 9 already uses `time.monotonic()` for its intervals
and handles this. **Step 8 uses `time.time()`** for its cooldown, heartbeat
and confirmation timeouts (`step8_reject_shadows.py` around line 1139), so
`step10_ai_camera.py` moves those to `time.monotonic()`. A
forward jump is harmless; a backward one would hold the cooldown shut
until wall time caught up. Day folders and names need nothing: after
the jump the next photograph goes into the right day's folder, and
`unused_name()` already copes with a name that comes round twice.

## Starting it when the dongle appears

Four pieces, each doing one thing, none of which we write except the last
two:

1. **The kernel.** When the VK-162 is plugged in, the kernel's `cdc_acm`
   driver (built into Raspberry Pi OS) recognises it as a USB serial device
   and creates `/dev/ttyACM0`. No driver to install.
2. **udev** gets an event for that new device and runs through its rules.
   Ours matches the receiver by its USB vendor and product id, adds a
   stable name, `/dev/gps0`, so the program never has to guess whether it
   is `ttyACM0` or `ttyACM1`, marks it as not a modem so `ModemManager`
   (if present) never probes it, and tags the device for systemd with a
   request to start our service:

   ```
   # /etc/udev/rules.d/90-wildlife-gps.rules
   SUBSYSTEM=="tty", ATTRS{idVendor}=="1546", ATTRS{idProduct}=="01a7", \
       SYMLINK+="gps0", ENV{ID_MM_DEVICE_IGNORE}="1", \
    TAG+="systemd", ENV{SYSTEMD_WANTS}="wildlife-gps.service"
   ```

3. **systemd** sees the tagged device as a unit of its own,
   `dev-gps0.device`, and because of `SYSTEMD_WANTS` starts
   `wildlife-gps.service` alongside it.
4. **The service** runs the program, and is bound to the device:

   ```ini
   # /etc/systemd/system/wildlife-gps.service
   [Unit]
   Description=Wildlife camera GPS (clock and location)
   BindsTo=dev-gps0.device
   After=dev-gps0.device

   [Service]
   User=webelos
   AmbientCapabilities=CAP_SYS_TIME
   ExecStart=/usr/bin/python3 -u /home/webelos/wildlife_gps.py /dev/gps0
   Restart=on-failure
   ```

   `BindsTo` is the other half: when the dongle is pulled out the device
   unit disappears and systemd stops the service. Plug it in again and the
   whole chain runs again, which is exactly an attachment.

If the dongle is already plugged in when the Pi boots, udev replays its
events for every device present at startup ("coldplug"), so the service
starts the same way. Plugged in at boot, an hour later or a month later
makes no difference.

Note the service is **not** `enable`d, unlike `wildlife-camera.service`:
nothing starts it but the dongle.

`1546:01a7` is the id u-blox 7 receivers report; confirm it with `lsusb` on
our actual dongles before writing the rule, since clones sometimes differ.
`/var/lib/wildlifecam` is created owned by `webelos` in the image, the same
way the photo directory is.

**Knowing it worked, in the field.** At the end of a deployment, the person
collecting the camera needs to know the fix has been taken before they
switch it off. `wildlife_gps.py` writes a one-line status, with no
coordinates in it, to `/var/www/html/gps-status.txt` --- `waiting: 3
satellites`, then `fix 16:02:11 UTC; clock was 48.3 s fast; corrected` ---
which a phone on the camera's Wi-Fi can read. (Check whether the VK-162's
LED changes when it has a fix; if it does, that is simpler still.)

## How the camera programs read it

Each program keeps the last `gps.json` it read in memory, and at each saved
photograph --- not at each look --- asks the filesystem one question: has
the file been replaced since? Only if it has does it read it again. One
function, written out in each file the way `boot_id()` and
`seconds_since_boot()` already are, so either step still reads top to
bottom on its own:

```python
GPS_FILE = "/var/lib/wildlifecam/gps.json"
gps_cache = {"stamp": None, "gps": None}


def where_and_when():
    """What the GPS program last wrote, and whether it is about this boot.

    Kept in memory.  We only read the file again when it has been
    replaced, which happens a few times a boot at most: at setup and at
    takedown.
    """
    try:
        info = os.stat(GPS_FILE)
    except OSError:
        return None                      # no dongle has ever been plugged in
    stamp = (info.st_ino, info.st_mtime_ns)
    if stamp != gps_cache["stamp"]:
        gps_cache["stamp"] = stamp
        gps_cache["gps"] = None
        try:
            with open(GPS_FILE) as f:
                gps = json.load(f)
            if "lat" in gps.get("position", {}):     # not a fix we can use
                gps["this_boot"] = gps.get("boot") == BOOT_ID
                gps_cache["gps"] = gps
        except (OSError, ValueError):
            pass
    return gps_cache["gps"]
```

The test is "has it **changed**", not "is it **newer**": the file's
modification time comes from the clock GPS is busy correcting, so it can
go backwards. `wildlife_gps.py` replaces the file with `os.replace()`, which
gives it a new inode every time, so the inode alone would do; the
modification time is there in case it ever does not.

### Why not check once an hour

Caching is right, and the version above is the cache; the question is only
how often to ask whether it is stale. Once an hour loses the photographs that matter most:

- **At setup** the camera program starts at boot and the fix arrives a few
  minutes later. An hourly check leaves the first hour of photographs ---
  the camera's first look at its new site, and anyone setting it up ---
  without a position, though the camera knew it.
- **At takedown** the camera may be switched off ten minutes after the
  dongle goes in. An hourly check would usually never see the takedown fix
  at all. (The log has it either way, so the laptop's time correction is
  unaffected, but the sidecars would say `"saved"` for photographs taken
  after the clock was set right.)

And the hourly check would save almost nothing. A `stat` is one system
call: microseconds, no reading of the SD card (the directory entry is in
the kernel's cache after the first time), no memory beyond a few numbers.
It happens at most once per *saved* photograph, which the camera programs
limit to 720 and 1,440 an hour, against a JPEG encode and a 1--3 MB write
taking a large fraction of a second each. Even the uncached version ---
open and parse a 1 KB file each save --- would be well under a
millisecond on a Zero 2 W, from the page cache, never touching the card.
The memory is the parsed file, a few kilobytes, whichever way it is done.

So: cached, because this is a small Pi, and checked with a `stat` at each
save, because the check is too cheap to ration.

### "Newer than the current boot" means the same boot id, not a later time

The obvious test --- is the fix's timestamp after the boot time? --- is the
one test we cannot use, because the boot time comes from the clock GPS is
there to correct. The boot id answers the real question directly: **if
`gps.json` says the same boot as the program reading it, the fix was taken
since this power-up**, and the camera has not been moved without being
switched off. (A camera picked up and moved while running is not a case we
have; if it happens with the dongle attached, the log shows the position
jumping, and a later attachment that lands somewhere else is caught as
described under "Where the location lives".)

So three cases:

| `gps.json` | Sidecar | EXIF |
|---|---|---|
| Same boot | `gps` block, `"fix": "this boot"` | GPS tags written |
| Earlier boot | `gps` block, `"fix": "earlier boot"`, with that boot's id | **Nothing** |
| Missing | No `gps` block | Nothing |

An earlier boot's fix is recorded in the sidecar because it is often right
--- a battery swap on the same post --- and the laptop can confirm it
against `deployments.csv`. It is kept out of the EXIF because EXIF has no
way to say "probably", and any photo tool will display it as fact.

## What goes into each photograph

### The sidecar

Two new blocks beside `boot` and `uptime_s`:

```json
"gps": {
  "fix": "this boot",
  "boot": "91b8124e",
  "lat": 37.33333,
  "lon": -121.70000,
  "alt_m": 412.3,
  "hdop": 1.2,
  "samples": 180
},
"clock": {
  "source": "gps",
  "set_uptime_s": 87.0
}
```

`clock.source` is the answer to "can I trust `time`?", worked out at save
time:

- `"gps"` --- `gps.json` is this boot, `set_by_gps` is true, and this
  photograph's `uptime_s` is after `first_set_uptime_s` (copied into the
  sidecar as `set_uptime_s`).
- `"network"` --- `/run/systemd/timesync/synchronized` exists, which
  `systemd-timesyncd` creates once it has reached a time server.
- `"saved"` --- neither. The time is the restored floor plus uptime, and
  should be treated the way every sidecar's time has had to be until now.

Even `"gps"` means "right when set, drifting since". The corrected time
comes from the log, on the laptop.

### The EXIF

Written only for a same-boot fix. The standard GPS tags, which every photo
viewer, `exiftool`, Apple Photos and Google Photos understand:

- `GPSLatitude` / `GPSLatitudeRef`, `GPSLongitude` / `GPSLongitudeRef`
  (degrees, minutes, seconds as rationals; `N`/`S`, `E`/`W`)
- `GPSAltitude` / `GPSAltitudeRef`
- `GPSDOP`, `GPSSatellites`, `GPSMapDatum = "WGS-84"`
- `GPSDateStamp` / `GPSTimeStamp` from the fix, in UTC

And, when `clock.source` is `gps` or `network`, the ordinary time tags:
`DateTimeOriginal` and `OffsetTimeOriginal`. When it is `saved`, leave them
out rather than stamp a time we know may be wrong.

**Library:** `piexif` --- pure Python, small, in Raspberry Pi OS as
`python3-piexif`. It builds the EXIF block and inserts it into a JPEG.

**One write, not two.** Step 8's code saves with `cv2.imwrite`; step 9's with
`picam2.capture_file`. Neither writes our tags. Rather than save the file
and then rewrite it with EXIF inserted (two writes of a 1--3 MB file per
photograph), encode to memory first --- `cv2.imencode` in
`step10_ai_camera.py`, capture into a `BytesIO` in
`step10_camera_module.py` --- `piexif.insert` into the bytes, and write
once. If building the EXIF fails for any reason, write the photograph
without it: as with the sidecar, metadata is never worth losing a
photograph over.

## File names, and how much code

Two kinds of file, named differently on purpose:

- **`step*.py` is the camera progression**: each one the program a camera
  runs, a successor to the last. GPS adds a step 10 to it, in the two
  camera programs.
- **Everything else is named for what it does.** `wildlife_gps.py` is not
  a camera program and does not replace one; it is a separate service
  that runs beside whichever camera program is running, the way
  `final_motion_capture.py` is the launcher rather than a step. Future
  helper scripts and configuration get plain names the same way, so that
  `ls step*` stays the lesson plan.

| File | What it is |
|---|---|
| `wildlife_gps.py` | New service, started by the dongle: NMEA, the trusted-fix test, the clock, `gps.json`, the log. |
| `step10_ai_camera.py` | Step 8 plus GPS, for the Raspberry Pi AI Camera. |
| `step10_camera_module.py` | Step 9 plus GPS, for an ordinary Camera Module. |

The camera names follow Raspberry Pi's own product names, "AI Camera" and
"Camera Module", in the `snake_case` the other files use. Step 9 runs on any
Camera Module, v2 or v3, so the name does not pin a version.

`final_motion_capture.py` launches the two step 10 programs instead of
step 8 and step 9, and **step 8 and step 9 stay as they are**, the way every
earlier step has: a finished lesson. That is the project's rule --- each
step small enough to see what changed --- and here what changed is exactly
the diff from step 8 to `step10_ai_camera.py` and from step 9 to
`step10_camera_module.py`: about 115 lines, all of them GPS (plus step 8's
clock fix). A Scout can read that diff as the lesson. From now on, tuning
and fixes to the camera rules go into the step 10 files.

The laptop code mentions step 8 only in comments, and identifies what
wrote a photograph by the `code` fingerprint in its sidecar, not by file
name. A step 10 sidecar gets a new fingerprint like any other code change,
so `ai/trailcam` treats it as a new run with nothing to change.

What those lines are, roughly the same in each camera program, because
each is written to be read on its own:

| Piece | Lines |
|---|---|
| `where_and_when()` with its cache | ~30 |
| Which clock to believe (`gps` / `network` / `saved`) | ~15 |
| The `gps` and `clock` blocks in the sidecar | ~15 |
| Building the EXIF (degrees to rationals, the tags) | ~40 |
| Encode to memory, insert EXIF, write once | ~15 |
| **Total** | **~115** |

Plus, in `step10_ai_camera.py`, a dozen lines of step 8's `time.time()`
moved to `time.monotonic()`.

**`wildlife_gps.py`** is new and standalone: the NMEA parser (~60), the
trust test (~15), measure-then-set (~30), `gps.json` with its median
and attachments (~50), the log (~30), the status line, the main loop and
explanations in the style of the step files --- 300 to 400 lines in all.

## The log

The sidecars only exist where there are photographs, and the
end-of-deployment fix comes after the last one. So the record of truth for
position and time is a per-boot log written by `wildlife_gps.py`:

```
/var/www/html/photos/<day>/gps-<camera>-<boot>.csv
```

the same pattern as `measurements-<camera>-<boot>.csv`, so `sync_cameras.py`
and `sync_sdcard.py` copy it with no changes and no two boots can collide.
The laptop's `bursts.py` globs `measurements-*.csv`, so it will not mistake
this for one. Like the measurements, a boot that spans midnight has a file
in more than one day folder; the laptop reads all of them for the boot.

**Only trusted fixes are written.** No row for "plugged in", "waiting" or
"no fix"; those go to the journal. Every row is a fix that passed the test
above, so every row can be believed.

```
gps_utc,pi_utc,uptime_s,offset_s,stepped_s,attachment,lat,lon,alt_m,hdop,satellites,network_synced
2026-10-11T16:02:11.00Z,2026-10-06T21:29:53.40Z,84.2,412337.6,412337.6,1,37.33333,-121.70000,412.3,1.2,7,0
2026-10-11T16:03:11.00Z,2026-10-11T16:03:11.10Z,144.2,-0.1,,1,37.33334,-121.69999,411.8,1.2,8,0
...
2026-11-08T16:01:24.50Z,2026-11-08T16:02:12.80Z,2419286.0,-48.3,-48.3,2,37.33332,-121.70001,413.0,1.4,6,0
```

- One row at each attachment's first trusted fix, then one a minute while
  the fix stays trusted, plus one at every clock check.
- **Every row is a clock check**: GPS time and Pi time read together, at a
  known uptime, both in UTC (the Pi's local time zone and daylight saving
  have no business in this file). `offset_s` is GPS minus Pi, before any correction;
  `stepped_s` is how far the clock was then moved, blank if it was not.
- `attachment` counts plug-ins within the boot, from 1.
- `network_synced` is 1 if `/run/systemd/timesync/synchronized` exists,
  meaning a time server may also have adjusted the clock; the laptop
  leaves those rows out of the drift fit.
- Each row is flushed to the card as it is written. The file is small even
  with an attachment of a few minutes it is a handful of rows.

## Correcting the time

The idea: **the Pi's uptime and its clock tick from the same crystal.**
Between clock steps (which are all logged), the gap between the Pi's time
and true time grows steadily with uptime. So within one boot, true time is
a straight line in uptime:

```
true_utc = a + b × uptime_s
```

Each trusted log row is one point on that line: `(uptime_s, gps_utc)`. Two
points fix the line; more are fitted by least squares. `b − 1` is the
crystal's drift rate. Every photograph's true time is then
`a + b × its uptime_s` --- from the uptime in its sidecar, not from its
`time`, so it does not matter whether the photograph was taken before or
after the clock was stepped.

What each pattern of plug-ins gives:

| Attachments in the boot | What the laptop can do |
|---|---|
| Start and end | Exact at both ends, linear in between. Also measures this camera's drift rate. |
| Start only | Exact at the start; drift after it uncorrected, or corrected with this camera's rate from an earlier deployment. |
| End only | The whole boot back-dated from the end fix, the same way, with or without a known rate. |
| None | `time` stays the camera's opinion, as today. |

For scale: an uncorrected crystal is typically good to a few tens of parts
per million. 20 ppm is about 1.7 seconds a day and 52 seconds a month,
which is the size of thing the end-of-deployment plug-in measures. It is
not perfectly linear --- the crystal runs a little differently cold at
night and warm in the afternoon --- so mid-deployment times are good to a
few seconds rather than exactly right. That is still far better than
"whatever the clock said", and the start-and-end case makes the leftover
error measurable rather than guessed.

**A boot is the unit, not a deployment.** A battery swap is a new boot with
a new uptime starting from zero, and nothing connects it to the previous
boot's line. So a deployment with a battery swap halfway needs a fix in
each half: plug the dongle in when swapping the battery, not only at the
start and end. Worth putting on the deployment checklist.

The camera does not rewrite anything; the laptop applies this at `scan`
time from the logs. The sidecar's `clock.source` only says whether the
`time` written at the moment was GPS-set; the corrected time is the
laptop's job, because for the end-only and start-and-end cases the
information arrives after the photograph is written.

## On the laptop

Following on, not part of the first change:

- `scan` reads the `gps` and `clock` blocks and the `gps-*.csv` logs:
  `frames` gains `lat`, `lon`, `clock_source` and a corrected
  `captured_at`, filled the same way `kind` and `site` are, with the
  correction from "Correcting the time".
- **A drift table per camera.** Every boot with two or more attachments
  yields a drift rate; keep them (camera, boot, ppm, days spanned) so a
  boot with only one fix can borrow its camera's usual rate. This is also
  how we find out whether linear is good enough: if a camera's rate is
  steady across deployments, it is.
- **Sites from coordinates.** `sites.csv` gains a centre and radius per
  site, and a frame with a same-boot fix is given its site by distance.
  `deployments.csv` becomes the fallback for cameras without a dongle,
  exactly as the sites design already makes it the fallback for cameras
  without `/etc/wildlifecam/site`.

## Privacy

Whether to remove locations from anything published is a decision for
later, not part of this design. The facts that decision will need:

- **The back garden is somebody's home.** Several cameras belong to other
  Scouts' families. A JPEG with GPS tags from a back garden is that family's
  address, readable by any photo viewer.
- **The camera serves its photos.** Anything in `/var/www/html/photos` is
  served by nginx to whoever is on the camera's Wi-Fi --- at a campsite,
  every phone in range.
- **The gallery is public.** `www.stokely.org/trailcam/` publishes
  shortlisted photographs.

The options, none of which the camera side depends on:

- **Remove GPS when publishing.** `publish-shortlist.sh` and the gallery
  build strip GPS EXIF and the `gps` block from published files, all of
  them or only for home sites.
- **Round it** to a kilometre or so instead of removing it.
- **A per-camera switch.** `/etc/wildlifecam/gps` containing `off` makes
  the camera programs use GPS for the clock only and write no position.
  About five lines in each camera program; easy to add later if wanted.

Whatever is decided, coordinates for homes stay out of the public repo,
with the camera owners table in the private one, as that data already
does.

## For Nolan

`wildlife_gps.py` is not a step, but it deserves a README section of its
own (next to the one on running at boot), because the interesting part is
entirely readable:

- `cat /dev/ttyACM0` shows the satellites talking, one line a second.
- Each line is a list of fields separated by commas --- `line.split(",")`.
- The `A` / `V` field is a yes/no answer to "do you know where you are?",
  and watching it change from `V` to `A` as the sky opens up is the moment.
- The two characters after `*` are a checksum: XOR every character between
  `$` and `*` and compare. A real use for a strange operator, and the reason
  a crackly cable produces skipped lines instead of wrong coordinates.
- `3720.00000,N` is 37 degrees and 20 *minutes* (37.333 degrees), not
  37.2 degrees. Getting this wrong puts the camera about twenty kilometres away,
  which is a good bug to find on a map.

So write the parser by hand --- `RMC` and `GGA` only, sixty lines or so ---
rather than pull in `pynmea2`. It is less code than learning the library,
and there is nothing hidden.

## Testing

- **Record a real stream** once, to a file, from a dongle on a windowsill:
  `cat /dev/ttyACM0 > nmea-cold-start.txt` for ten minutes from plug-in.
  That one file, checked in under `tests/` (with the coordinates shifted
  well away from anywhere real), drives parser and averaging tests: cold
  start `V` lines, the first `A`, bad checksums, a fix that wanders.
- **Replay it** through a pseudo-terminal (`socat`) to run `wildlife_gps.py`
  end to end without a dongle, with the clock-setting call replaced so the
  test does not change the test machine's time.
- **Nothing untrusted gets written.** Feed the replay a cold start, a
  valid run broken after 9 seconds, a year before 2026 and a corrupted
  checksum, and check that no `gps.json`, no log row and no clock step
  appears until 10 good seconds in a row.
- **Two attachments in one boot.** Replay, stop, shift the fake clock by a
  known drift, replay again: `attachments` gains a second entry, the first
  is untouched, and the measured `offset_s` is the shift.
- **The drift fit** on the laptop, from a synthetic log with a known rate:
  start-and-end, start only, end only, a step in the middle, and rows
  marked `network_synced`.
- **The reader and the three cases** --- same boot, earlier boot, missing ---
  plus a `gps.json` with no position, with hand-written files.
- **On a camera:** `exiftool -gps:all -DateTimeOriginal <photo>.jpg`, and
  `journalctl -u wildlife-gps` for the line saying how far the clock moved.
- **Unplug mid-run** and check photographs keep their position for the rest
  of the boot; **reboot without the dongle** and check they go to
  `"earlier boot"` with no EXIF.

## Order of work

1. Copy step 8 to `step10_ai_camera.py` and step 9 to
   `step10_camera_module.py`, unchanged, and point
   `final_motion_capture.py` at them. A commit of its own, so that every
   later diff against step 8 and step 9 is only the new lesson. Then move
   `step10_ai_camera.py`'s intervals onto `time.monotonic()`, which makes
   clock jumps safe whatever sets the clock.
2. `wildlife_gps.py`, the udev rule and the service: the trusted-fix
   test, measure-then-set, `gps.json`, the log and the status line. Useful
   on its own --- it fixes the clock problem and starts collecting drift
   measurements --- before any photograph carries a position.
3. `where_and_when()`, the sidecar blocks and EXIF in
   `step10_camera_module.py`, then `step10_ai_camera.py`.
4. The image: `python3-piexif` installed, `/var/lib/wildlifecam` created,
   rule and service in place; the clone instructions updated to match.
5. The README: a step 10 section for the camera programs and a section on
   the GPS service. And the deployment checklist: plug the dongle in at
   the start, at every battery swap, and at the end before switching off;
   wait for the status line to say the fix is taken.
6. Laptop: `scan` reads the new blocks and logs, corrects times with the
   drift fit, keeps the per-camera drift table, and assigns sites by
   distance.

## Open questions

- **How many dongles?** One or two, carried round. Worth timing a fix at
  each site at the first campout with them: if under-canopy fixes take ten
  minutes, setup of eight cameras wants two.
- **Precision on the card.** Five decimal places is about a metre, which is
  more than the receiver can honestly give and is fine for the private
  private archive. What reaches the public copy is the publishing
  decision under "Privacy".
- **Altitude:** recorded, but GPS altitude is noticeably worse than
  horizontal position. Worth keeping only if someone wants it.
