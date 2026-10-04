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
- **First fix is slow.** With open sky, under a minute. Under oak canopy, or
  on a cold start after travelling, it can be several minutes, and the
  dongle's antenna is small. The program has to be patient and say what it
  is waiting for.
- **It costs battery.** Roughly a fifth of what a Zero 2 W draws. That is
  why the design below works just as well if the dongle is plugged in at
  deploy time, left until it has a fix, and then unplugged.
- **No PPS over USB**, so the time is good to a few hundred milliseconds,
  not microseconds. That is a thousand times better than we have now and
  more than the photographs need.
- **Nothing else may grab the port.** `ModemManager` probes every new
  `ttyACM` device and can hold it for a while; `gpsd`, if installed, has its
  own udev rule that takes it. Neither should be on a camera image. Check
  with `systemctl status ModemManager gpsd` when building the image.

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
                                       read at every save by step8 / step9
                                                        └──> sidecar + EXIF
```

**`wildlife_gps.py`** is a new, small, standalone program, started by systemd
when the dongle is plugged in and stopped when it is pulled out. It reads
NMEA, and once it trusts the fix it measures how wrong the clock is, sets
it, and writes the state file and the log.

**step 8 and step 9** never touch the dongle. When they save a photograph
they read the state file, decide whether it belongs to this boot, and copy
what it says into the JSON sidecar and the JPEG's EXIF.

Keeping them apart is the point. The camera programs stay unprivileged and
keep working with no dongle, a broken dongle, or a dongle pulled out
mid-run; a crash in GPS parsing can never cost a photograph. And
`wildlife_gps.py` is small enough to be its own lesson.

### Why not gpsd and chrony

The textbook answer is `gpsd` reading the receiver and `chrony` disciplining
the clock from it. It is better at sub-millisecond time, which we do not
need, and it costs: chrony replaces `systemd-timesyncd` on eleven cards (and
the `timesync/clock` floor the clone instructions rely on), gpsd is another
daemon on a 512 MB Pi, and neither writes the one file the camera programs
want. It is also a black box to a fourth grader, where `$GPRMC,...,A,...`
followed by "the `A` means it knows where it is" is not. Revisit if we ever
want time good to the millisecond across cameras, for example to match one
animal across two cameras' frames.

## Plugged in at the start, at the end, or both

The dongle does not have to stay attached. The three ways it will actually
be used:

- **At the start of a deployment.** Plug in at the post, wait for a fix,
  unplug. The clock is right from then on, give or take drift, and every
  photograph in that boot gets the position.
- **At the end.** Plug in when collecting the camera, before shutting it
  down or pulling the card. The clock has been wrong (or drifting) for the
  whole run; this one fix says by exactly how much, at a known uptime.
- **Both.** Two fixes in one boot, days or weeks apart. The first sets the
  clock; the second measures how far it has drifted since. That is the
  drift rate of this camera's crystal, and with it every photograph in
  between can be corrected, assuming the drift is linear --- see
  "Correcting the time".

Each time the dongle is plugged in is an **attachment**, and every
attachment leaves a record, whether or not it changes the clock. The design
below is built so that the second attachment never overwrites what the
first one learned.

## Nothing is written until a fix is trusted

A receiver that has just been plugged in spends its first seconds to
minutes saying `V` (no fix), then often gives a few readings that are
valid by its own account but wrong: a position hundreds of metres off, or
a time from a stale almanac. None of that may reach a file, a photograph
or the clock.

A fix is **trusted** when all of these hold:

1. the sentence's checksum is right (anything else is dropped unread);
2. `RMC` says `A`, `GGA` says fix quality 1 or better, at least four
   satellites, and HDOP of 5 or less;
3. the date is not earlier than the `timesync/clock` floor, which is the
   earliest date this card could honestly claim, so a GPS date before it
   means the receiver is wrong, not the card (this also catches week-number
   rollover bugs without having to know which firmware has them);
4. the position is not `0, 0` and is not blank;
5. **five consecutive seconds** pass all of the above, agree with each
   other's position to within about 50 m, and their GPS times advance in
   step with the Pi's monotonic clock to within a second.

Until the first trusted fix of an attachment, `wildlife_gps.py` writes
**nothing**: no `gps.json`, no log row, no clock change. It prints a status
line to the journal once a minute (`waiting: 3 satellites, no fix yet`) so
that `journalctl -u wildlife-gps` says what it is waiting for. If the fix is
lost mid-attachment (someone's hand over the dongle, the tree canopy), it
stops writing and has to pass the five-second test again before it starts.

A `gps.json` left over from an earlier boot is left alone until this boot's
first trusted fix replaces it. The camera programs already treat it as
"earlier boot" (below), so a stale file is never mistaken for a new one.

The camera programs check too: `where_and_when()` ignores a `gps.json`
without a `position` block or with an unparseable one. Two checks for the
same thing, because the cost of a wrong location in a photograph is that
it is believed.

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

- **`boot`** is the same eight characters step 8 and step 9 already write
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

While the dongle stays attached, it repeats the same measure-then-correct
every ten minutes, with each one going into the log. A camera left with the
dongle on all deployment therefore has a correct clock throughout and a
drift measurement every ten minutes, at the price of battery.

NMEA sentences arrive a few hundred milliseconds after the instant they
describe. That bias is the same at every attachment, so it cancels out of
the drift rate, and it is well inside the one-second threshold.

`systemd-timesyncd` stays installed. With a network it will agree with GPS
to well inside a second; without one it does nothing. They do not fight.

**Privilege.** Setting the clock needs `CAP_SYS_TIME`. Run the service as
the `webelos` user with `AmbientCapabilities=CAP_SYS_TIME` rather than as
root.

**What the camera programs feel.** The clock jumps while they run: forward
by hours or days at the first attachment, by seconds in either direction
at a later one. Step 9 already uses `time.monotonic()` for its intervals
and handles this. **Step 8 uses `time.time()`** for its cooldown, heartbeat
and confirmation timeouts (`step8_reject_shadows.py` around line 1139). A
forward jump is harmless; a backward one would hold the cooldown shut
until wall time caught up. Switching those to `time.monotonic()` is part of
this work and should land first. Day folders and names need nothing: after
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
   is `ttyACM0` or `ttyACM1`, and tags the device for systemd with a
   request to start our service:

   ```
   # /etc/udev/rules.d/90-wildlife-gps.rules
   SUBSYSTEM=="tty", ATTRS{idVendor}=="1546", ATTRS{idProduct}=="01a7", \
       SYMLINK+="gps0", TAG+="systemd", ENV{SYSTEMD_WANTS}="wildlife-gps.service"
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

## How step 8 and step 9 read it

At every saved photograph --- not at every look --- each program calls one
function, written out in each file the way `boot_id()` and
`seconds_since_boot()` already are, so either step still reads top to
bottom on its own:

```python
def where_and_when():
    """What the GPS program last wrote, and whether it is about this boot."""
    try:
        with open("/var/lib/wildlifecam/gps.json") as f:
            gps = json.load(f)
    except (OSError, ValueError):
        return None                      # no dongle has ever been plugged in
    if "lat" not in gps.get("position", {}):
        return None                      # not a fix we can use
    gps["this_boot"] = gps.get("boot") == BOOT_ID
    return gps
```

Reading a few hundred bytes per saved photograph costs nothing next to
writing the photograph. Reading at every save rather than once at startup
is what lets a fix that arrives ten minutes into the run reach every
photograph after it.

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

**One write, not two.** Step 8 saves with `cv2.imwrite`; step 9 with
`picam2.capture_file`. Neither writes our tags. Rather than save the file
and then rewrite it with EXIF inserted (two writes of a 1--3 MB file per
photograph), encode to memory first --- `cv2.imencode` in step 8, capture
into a `BytesIO` in step 9 --- `piexif.insert` into the bytes, and write
once. If building the EXIF fails for any reason, write the photograph
without it: as with the sidecar, metadata is never worth losing a
photograph over.

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
  with the dongle on all month: one row a minute is about 4 MB.

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
| On throughout | A point every minute; drift corrected as it happens. |
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

This is the part that has to be right before the first card with a fix on
it comes home.

- **The back garden is somebody's home.** Several cameras belong to other
  Scouts' families. A JPEG with GPS tags from a back garden is that family's
  address, readable by any photo viewer.
- **The camera serves its photos.** Anything in `/var/www/html/photos` is
  served by nginx to whoever is on the camera's Wi-Fi --- at a campsite,
  every phone in range.
- **The gallery is public.** `www.stokely.org/trailcam/` publishes
  shortlisted photographs.

So:

1. **The publish step strips GPS.** `publish-shortlist.sh` and the gallery
   build remove all GPS EXIF from every published image (and drop `gps`
   from any published JSON). Not rounded --- removed. The per-site page
   says where it is in words, which is all a reader needs. This must land
   before, or with, the first camera change.
2. **No coordinates in the public repo.** No `gps.json`, no GPS logs,
   no sidecars with fixes, no `sites.csv` centres for anywhere that is a
   home. Park sites are public places and can live in the repo; home sites'
   coordinates belong in the private repo with the camera owners table.
3. **A per-camera off switch.** `/etc/wildlifecam/gps` containing `off`
   makes step 8 and step 9 use GPS for the clock only and write no
   position into sidecars or EXIF. Families who would rather not have a
   location recorded at home get that by default; the campout cameras turn
   it on.

## For Nolan

`wildlife_gps.py` is a good candidate for its own numbered step, because the
interesting part is entirely readable:

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
  burst of valid-looking but scattered fixes, a `0,0` fix, a date before
  the floor and a corrupted checksum, and check that no `gps.json`, no log
  row and no clock step appears until five good seconds in a row.
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

1. Step 8 intervals onto `time.monotonic()`. Independent and small; makes
   clock jumps safe whatever sets the clock.
2. GPS stripping in the publish path, and the `/etc/wildlifecam/gps` switch.
   Nothing on a camera writes a location until these exist.
3. `wildlife_gps.py`, the udev rule and the service: the trusted-fix
   test, measure-then-set, `gps.json`, the log and the status line. Useful
   on its own --- it fixes the clock problem and starts collecting drift
   measurements --- before any photograph carries a position.
4. `where_and_when()`, the sidecar blocks and EXIF in step 9, then step 8.
5. The image: `python3-piexif` installed, `ModemManager` and `gpsd` absent,
   `/var/lib/wildlifecam` created, rule and service in place; the clone
   instructions updated to match.
6. The deployment checklist: plug the dongle in at the start, at every
   battery swap, and at the end before switching off; wait for the status
   line to say the fix is taken.
7. Laptop: `scan` reads the new blocks and logs, corrects times with the
   drift fit, keeps the per-camera drift table, and assigns sites by
   distance.

## Open questions

- **Start-and-end, or on throughout?** The design works either way.
  Start-and-end costs a few minutes of battery and gives a linear
  correction; on throughout keeps the clock right as it goes and shows how
  far from linear the drift really is. One camera run each way for a week,
  side by side, would settle both the battery cost and whether linear is
  good enough.
- **Do we have enough dongles** for one per camera at a campout, or does
  one dongle go round the cameras at setup and again at pickup? The design
  does not care, but the checklist does.
- **Precision on the card.** Five decimal places is about a metre, which is
  more than the receiver can honestly give and is fine for the private
  archive given that the public copy is stripped. Rounding on the card is
  a cheap extra if families want it.
- **Altitude:** recorded, but GPS altitude is noticeably worse than
  horizontal position. Worth keeping only if someone wants it.
