# Running the wildlife camera on a Pi Zero 2 W

A Raspberry Pi Zero 2 W has 512 MB of RAM. After the GPU split the ARM
sees about **415 MB**, and `step8_reject_shadows.py` wants roughly 170 MB
of that. Everything still fits — but only once you stop the desktop and a
handful of daemons from eating the difference.

This is what we did to `wildlifecam10` on 27 August 2026, why, and the
commands used to measure it. If a Zero 2 W is "very slow", start here.

---

## The symptom

The camera ran, but the whole machine crawled. `nginx` was up and
listening on port 80 yet the photo page would not load. SSH took seconds
per keystroke.

The instinct is to blame the CPU or a weak power supply. Both were wrong.

---

## Diagnosing it: measure before you change anything

Run these over SSH. None of them need `sudo`.

### 1. Is it the CPU?

```bash
uptime                # load average
nproc                 # how many cores that load is spread over
vmstat 2 5            # the important one
```

`vmstat` output, columns that matter:

```
procs -----------memory---------- ---swap-- -----io---- -------cpu-------
 r  b   swpd   free   buff  cache   si   so    bi    bo  us sy id wa st
 2  0 223580 123844   3064  71696  665 1595  6562  2167  13 10 66 10  0
```

- `id` — **idle**. On our sick machine this was 66–87%. The CPU was *not*
  the problem.
- `wa` — **iowait**, time spent waiting on storage. 10% here.
- `si` / `so` — **swap in / swap out**, in KB/s. 665 and 1595. This is the
  smoking gun.
- `swpd` — total KB swapped out. 223 MB.

**If `id` is high and `si`/`so` are non-zero, the machine is swapping, not
computing.** Swap on this board is the SD card, which is glacial.

### 2. Is it power or heat?

```bash
vcgencmd get_throttled     # want throttled=0x0
vcgencmd measure_temp
vcgencmd measure_clock arm # want ~1000000000 on a Zero 2 W
```

Any non-zero value from `get_throttled` means undervoltage or thermal
capping — a different problem with a different fix (better PSU, better
cable, heatsink). Ours was `0x0` at 59 °C and a full 1 GHz, so power was
fine.

### 3. How much memory is actually free?

```bash
free -m
```

Look at the **`available`** column, not `free`. `free` excludes cache that
the kernel would happily give back; `available` is the honest number.

### 4. Who is using the swap?

This is the useful one, and there is no standard tool for it:

```bash
for p in /proc/[0-9]*; do
  s=$(awk '/^VmSwap/{print $2}' $p/status 2>/dev/null)
  [ -n "$s" ] && [ "$s" -gt 0 ] && echo "$s $(cat $p/comm 2>/dev/null) ${p#/proc/}"
done | sort -rn | head -10 | awk '{printf "  %6d kB  %-18s pid %s\n", $1, $2, $3}'
```

And the total across everything:

```bash
awk '/^VmSwap/{s+=$2} END {printf "  %.1f MB swapped out\n", s/1024}' /proc/[0-9]*/status
```

### 5. Watch one process settle

```bash
p=$(pgrep -f step8_reject_shadows | head -1)
awk '/^VmRSS|^VmSwap/{printf "  %-8s %6.1f MB\n", $1, $2/1024}' /proc/$p/status
```

`VmRSS` is what is in RAM, `VmSwap` is what has been pushed to the card.
**A large `VmSwap` on the detector is the thing to fix** — every numpy
operation that touches a swapped page becomes an SD-card read.

### 6. What is running at all?

```bash
ps -eo pid,rss,pcpu,etime,comm --sort=-rss | head -15
systemctl list-units --type=service --state=running --no-legend --no-pager
systemctl --user list-units --type=service --state=running --no-legend
loginctl list-sessions
```

The `--user` line matters: some daemons are **user** services, not system
ones, and do not appear in the normal list.

---

## What we found

| | |
| --- | --- |
| CPU idle | **66–87%** — never the bottleneck |
| `throttled` | `0x0` — power and cooling fine |
| swap in use | **223 MB** |
| swapped out, all processes | **185 MB** |
| available RAM | 130 MB |
| the detector | 25 MB resident, **53 MB swapped** |

The detector had two thirds of its working set on the SD card.

Where the memory had gone:

```
python3 (the detector)   53.9 MB swapped
packagekitd              26.3 MB
pcmanfm                  13.8 MB  ┐
xdg-desktop-portal       10.0 MB  │
labwc                     9.2 MB  │  the desktop,
x-terminal-emulator       8.4 MB  │  about 60 MB
pipewire                  8.3 MB  │
wireplumber               8.2 MB  │
wf-panel-pi               7.7 MB  ┘
```

---

## The fixes, in order of how much they return

### 1. Boot to console, not to the desktop

The single biggest win. The camera is headless in the field; the desktop
is only ever seen during setup.

```bash
sudo systemctl set-default multi-user.target
sudo reboot
```

`set-default` only changes the *next* boot — it does not tear down the
running session, so the reboot is part of the fix, not optional.

### 2. Stop the package-update daemon

```bash
sudo systemctl mask packagekit packagekit-offline-update
```

**Use `mask`, not `disable`.** These units are D-Bus activated and have no
`[Install]` section, so `disable` fails with *"unit files have no
installation config"*. `mask` symlinks them to `/dev/null`, which also
blocks D-Bus activation. Check it took:

```bash
systemctl is-enabled packagekit      # want: masked
```

### 3. Stop the audio daemons

These are **user** services, so this needs no `sudo` — run it as the
camera's own user:

```bash
systemctl --user mask pipewire.socket pipewire-pulse.socket \
    wireplumber.service pipewire.service pipewire-pulse.service
systemctl --user stop pipewire.socket pipewire-pulse.socket \
    pipewire.service pipewire-pulse.service wireplumber.service
```

Worth about 13 MB, and a trail camera has no use for audio. Reverse with
`systemctl --user unmask`.

### 4. Make the kernel prefer dropping cache over swapping

```bash
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf
```

Default is 60, which is far too eager when swap is a memory card. Verify
after reboot with `cat /proc/sys/vm/swappiness`.

### 5. Turn off the console autologin

```bash
sudo systemctl revert getty@tty1.service
```

Raspberry Pi OS ships a drop-in at
`/etc/systemd/system/getty@tty1.service.d/autologin.conf` that logs the
user in on tty1 at boot — which starts a full user session even in
`multi-user.target`. Reverting removes that drop-in and restores the stock
`agetty`.

**You can still log in at the console**, you just have to type a username
and password. SSH is a separate service and is completely unaffected.

### 6. Optional: services a trail camera never uses

```bash
sudo systemctl disable --now bluetooth rpcbind rpcbind.socket nfs-blkmap
```

About 14 MB. **Keep `avahi-daemon`** — that is mDNS, and it is what makes
`wildlifecam10.local` resolve.

---

## The code change

`step8_reject_shadows.py` now detects the board and shrinks the two
settings that cost the most memory and affect nothing else:

```python
SMALL_BOARD  = "Zero 2" in board_model()          # /proc/device-tree/model

MAIN_SIZE    = (1520, 1140) if SMALL_BOARD else (2028, 1520)
BUFFER_COUNT = 2            if SMALL_BOARD else 4
```

| | normal board | Zero 2 W |
| --- | ---: | ---: |
| main buffer | 9.25 MB × 4 = 37.0 MB | 5.20 MB × 2 = 10.4 MB |
| lores buffer | 0.46 MB × 4 = 1.8 MB | 0.46 MB × 2 = 0.9 MB |
| **camera buffers** | **38.8 MB** | **11.3 MB** |

Both are safe to vary per board because **nothing in the decision chain
ever reads the `main` stream** — it is only the photograph we keep. The
cost is real and worth stating: 44% fewer pixels on the saved image,
which will hurt a laptop-side detector most on the distant animals it
already finds hardest.

### `LORES_SIZE` deliberately does *not* change

`SHADOW_MIN_RANGE` is measured in grey levels and `SHADOW_MIN_EDGE` in
grey levels *per pixel*, and **neither is scaled by `MOTION_SCALE`**. Move
the motion resolution and the shadow rule silently starts doing something
different on a board where it has never been measured. Do not do it
without re-deriving both thresholds.

### It says which settings it used

The start-up banner names the board, and `MAIN_SIZE` is written into
every photograph's JSON sidecar, so any later analysis can tell the two
apart the same way it tells builds apart by `code`:

```json
{ "code": "4a871f8d4d5a",
  "image": { "file": "194640.jpg", "width": 1520, "height": 1140 } }
```

---

## Before and after

| | before | after |
| --- | ---: | ---: |
| swap in use | 223 MB | **30 MB** |
| swapped out, all processes | 185 MB | **34 MB** |
| available RAM | 130 MB | **187 MB** |
| detector resident / swapped | 25 / 53 MB | **164 / 8.7 MB** |
| `si` / `so` | 665 / 1595 KB/s | **0 / 0** |
| iowait | 10% | **0–1%** |
| CPU idle | 66–87% | 88–89% |
| temperature | 59.1 °C | 55.8 °C |

The detector is now **larger** than before — step8 does more than step7 —
and yet almost entirely resident. That is the whole point: it was never
about making the program smaller, only about making it fit.

---

## Quick reference

Paste this into a terminal on the Pi for a one-screen health check:

```bash
echo "== board ==";      tr -d '\0' < /proc/device-tree/model; echo
echo "== throttle ==";   vcgencmd get_throttled; vcgencmd measure_temp
echo "== memory ==";     free -m
echo "== swap total =="; awk '/^VmSwap/{s+=$2} END {printf "%.1f MB\n", s/1024}' /proc/[0-9]*/status
echo "== detector ==";   p=$(pgrep -f 'step[78]_' | head -1); \
    awk '/^VmRSS|^VmSwap/{printf "%-8s %6.1f MB\n", $1, $2/1024}' /proc/$p/status
echo "== activity ==";   vmstat 2 3
```

Healthy looks like: `throttled=0x0`, `available` above ~150 MB, total
swapped under ~50 MB, `si`/`so` at zero, and the detector's `VmSwap` small
next to its `VmRSS`.
