# Campout base station --- design

**Status:** design, no code yet (2026-10-04).
**Companion to:** [`gps-design.md`](gps-design.md), which gives each camera
the time and its place from a dongle carried round at setup and takedown,
and the README's "Camping without Internet", which already suggests a
travel router everyone joins.

A small battery-powered box that goes to the campout and sits somewhere
high in the middle of the site. When it boots it gets the time and its
position from a GPS dongle, makes a Wi-Fi network called `webelos`, hands
out addresses to the cameras that join it, and serves them the time. Later
it can also tell them where the site is.

---

## What it changes

- **The cameras get the right time on their own.** Any camera in range
  syncs its clock from the base station over the network, the same way it
  does at home from the internet. The photographs' sidecars say
  `"clock": {"source": "network"}`. Nobody has to carry a dongle round to
  set clocks.
- **The GPS dongle visit becomes about position only.** `wildlife_gps.py`
  already handles a camera whose clock the network has set: it measures,
  logs and leaves the clock alone (gps-design.md, "When there is a network
  time server as well"). A dongle visit at setup is still the only way to
  learn where each *camera* is; the base station knows where the *site* is.
- **The laptop syncs every camera over Wi-Fi, at camp.** Join `webelos`
  and run `sync/sync_cameras.py` as at home: its mDNS discovery works on
  any network the cameras and laptop share. No more pulling cards.
- **The takedown drift measurement matters less.** A camera in range stays
  synced all weekend. It still matters for cameras out of range.

## Two constraints that shape it

**The cameras only speak 2.4 GHz.** A Pi Zero 2 W has 802.11n on 2.4 GHz
and nothing else, with a small antenna printed on the board. So the base
must offer a 2.4 GHz network, and the cameras' little antennas, not the
base's, decide the range. A good antenna on the base still helps (it hears
the cameras better), and so does height: on a pole or a branch, not on a
picnic table. Expect tens of metres through trees, not hundreds, and plan
to range-test before choosing camera spots.

**A WPA2 password must be at least 8 characters.** `webelos` is 7, so a
network with that password will not start: NetworkManager and hostapd both
refuse it. Choose one with 8 or more, say `webelos1`, and set the same on
every camera. (An open network with no password would work, but then
anyone at the campground could reach the cameras' web pages and SSH.)

## Hardware

**Recommended: a Raspberry Pi 5 with a USB Wi-Fi adapter.**

| Part | Why |
|---|---|
| Raspberry Pi 5, 2 GB | Same OS, tools and Python as the cameras, so nothing new to learn. It has a real-time clock, and with the small official RTC battery it keeps the time while switched off. |
| USB Wi-Fi adapter with an external antenna, MediaTek MT7612U or MT7610U chipset | These use the `mt76` driver that is part of Linux, and support access-point mode. The Pi's own Wi-Fi works as an access point too, but its antenna is small. |
| The VK-162 dongle | The same receiver we already know. |
| USB-C power bank with Power Delivery | See the power budget. |

A Pi 4 would do instead. It draws a little less, but has no real-time
clock.

**The other way: a GL.iNet travel router running OpenWrt.** These are
built to be access points, draw less power, and have better radios. OpenWrt
has packages for `gpsd`, `chrony` and Python, and its DHCP server is the
same `dnsmasq`. It is a different operating system to learn and to debug
at a campsite, though, and the location server would be written for a
small router rather than a familiar Pi. Worth it if range or battery turns
out to be the problem; otherwise the Pi is simpler for us.

**Power budget, to be measured.** These are estimates until we put a meter
on it: Pi 5 idle at about 3 W, the Wi-Fi adapter 1--2 W, the GPS about
0.2 W, so 4--6 W in all, roughly 100--150 Wh a day. A typical 20,000 mAh
bank (about 70 Wh) would last half a day. A weekend means several banks, a
large one, or a small solar panel. A Pi 4 would draw a bit less.

## Software

Everything is standard Raspberry Pi OS Lite packages and configuration
files. No new program is needed for the first version.

```
VK-162 --> gpsd --> chrony -----> NTP on 10.42.0.1 --> cameras' timesyncd
              \
               `--> (later) location server, http://10.42.0.1/location

NetworkManager hotspot "webelos"  --> its dnsmasq: DHCP + DNS for the cameras
```

### GPS: gpsd

On the cameras we read the dongle directly and decided against gpsd
(gps-design.md, "How this relates to the usual tools"). Here gpsd is right,
for the reasons it was wrong there: the dongle is plugged in all weekend,
and several programs want the same fix at once --- chrony for the time, the
location server, and `cgps` when someone wants to see the satellites. gpsd
is the standard program that shares one receiver between many readers.

### Time: chrony

chrony replaces `systemd-timesyncd` on the base station only. It reads the
time from gpsd and serves it to the network:

```
# /etc/chrony/conf.d/gps.conf
refclock SHM 0 refid GPS offset 0.045 delay 0.2   # NMEA from gpsd
makestep 1 -1                                     # may step the clock any time
allow 10.42.0.0/24                                # serve the camera network
```

- `offset 0.045` is the delay we measured in the garden test: the
  VK-162's sentences arrive about 45 ms after the second they describe.
  Without a pulse-per-second wire this is as good as the time gets: tens
  of milliseconds, far more than the photographs need.
- **Before the first fix it serves nothing usable.** chrony marks its
  answers as unsynchronised, and the cameras' `timesyncd` ignores those, so
  a camera keeps its own clock rather than taking a wrong one. Do not add
  `local stratum` to make it answer anyway; an honest "I don't know yet" is
  better. The Pi 5's battery-backed clock means the base itself is close
  to right even before the fix.
- **Leap seconds.** For about the first twelve minutes after a cold start
  the dongle is two seconds fast (gps-design.md, "Leap seconds"), so the
  base serves that too, then corrects itself. We accepted that for the
  cameras and accept it here. A base station that is switched on once and
  left all weekend is only affected for its first twelve minutes.

### The network: a NetworkManager hotspot

Raspberry Pi OS already uses NetworkManager, and its "shared" mode makes an
access point with DHCP and DNS (it runs `dnsmasq` itself), addressed
`10.42.0.1/24` by default.

```bash
nmcli connection add type wifi ifname wlan1 con-name webelos ssid webelos \
    802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
    ipv4.method shared wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<8+ characters>" \
    connection.autoconnect yes
```

`wlan1` is the USB adapter; the built-in `wlan0` stays free, for example to
join a phone hotspot when the base needs updating. Extra dnsmasq settings
go in a drop-in file that NetworkManager's dnsmasq reads:

```
# /etc/NetworkManager/dnsmasq-shared.d/webelos.conf
dhcp-option=option:ntp-server,10.42.0.1   # DHCP option 42: the time server
domain=camp                               # wildlifecam11.camp resolves
```

Every camera that gets an address also gets a DNS name from its hostname,
`wildlifecam11.camp`, and mDNS (`wildlifecam11.local`) keeps working as
well.

## On the cameras

Two small changes, in the master images and on existing cards:

1. **A Wi-Fi profile for `webelos`**, as the README's "Wi-Fi priorities"
   already describes, with a lower autoconnect priority than the home
   network.
2. **Point `timesyncd` at the base station.** The DHCP NTP option above is
   not enough on its own: `systemd-timesyncd` learns servers from DHCP only
   through `systemd-networkd`, and the cameras use NetworkManager, which
   does not pass them on. So name the base station directly:

   ```
   # /etc/systemd/timesyncd.conf.d/campout.conf
   [Time]
   NTP=10.42.0.1 0.debian.pool.ntp.org 1.debian.pool.ntp.org
   ```

   timesyncd tries the servers in turn. At camp, 10.42.0.1 answers; at home
   it does not, and the pool servers do. To check on a camera:
   `timedatectl timesync-status` shows which server it is using. (Setting
   `NTP=` turns off timesyncd's built-in fallback list, which is why the
   pool servers are listed explicitly.)

Nothing changes in the step 10 programs or `wildlife_gps.py`. A photograph
taken once the camera has synced says `"clock": {"source": "network"}`, and
a dongle plugged in at setup measures the clock but leaves it alone.

## Location

### Is there a standard way to give clients their position?

Yes, more than one, though they are rarely used outside telephones:

- **DHCP option 123 and option 144** (RFC 6225) carry a latitude,
  longitude and altitude in a packed binary form. **Option 99** (RFC 4776)
  carries a street address. They exist so desk phones on office networks
  can report a location when someone calls emergency services. dnsmasq can
  send them, but NetworkManager on the cameras would neither ask for them
  nor show them without extra work.
- **LLDP-MED**, the same idea for wired network switches. Not Wi-Fi.
- **HELD** (RFC 5985), a web protocol for asking a "location information
  server" where you are, in XML. Heavy for this.

What Linux networks actually do is simpler:

- **gpsd over the network.** Started with `-G`, gpsd answers on port 2947
  to anyone on the network, in JSON: `{"class":"TPV","lat":…,"lon":…}`.
  Any gpsd tool works against it unchanged (`cgps 10.42.0.1`).
- **A small HTTP endpoint.** Your idea, and the one I would build:
  `GET http://10.42.0.1/location` returning JSON shaped like the cameras'
  own `gps.json` position block, from a stdlib-only Python server that asks
  the local gpsd. Easy to read from a camera with `urllib`, easy to look at
  in a phone's browser.

### What the base station's position means

**The base station knows where the base station is, not where a camera
is.** Cameras are tens of metres away, in any direction. So:

- It is exactly right for the **site**: "these photographs were taken at
  Grant Park, not the back garden." That is the fact
  [`../ai/sites-design.md`](../ai/sites-design.md) has to record by hand
  today in `deployments.csv`, and a camera could fill it in by itself.
- It is **not** a camera's position. A camera must never put it in its
  sidecar's `gps` block, which means "this camera's own fix, this boot".
  If cameras record it, it goes in a separate block, say
  `"site": {"source": "base station", "lat": …, "lon": …}`, so nothing
  downstream can mistake one for the other.

A per-camera position still comes from a dongle visit at setup, as
gps-design.md describes.

## Open questions

- **The password.** It needs 8 or more characters. It goes on every camera
  and in the master images.
- **Pi 5 or a travel router.** The Pi is the plan unless range or battery
  says otherwise.
- **Static addresses.** dnsmasq can give each camera the same address every
  time, which makes a printed cheat sheet possible. mDNS and the `.camp`
  names may be enough.
- **Should the base station collect the photographs itself?** With a USB
  drive, it could `rsync` from every camera in range overnight, so the
  laptop needs only the base. That would be a later version.

## Order of work

1. **On the bench:** a Pi with the VK-162, gpsd and chrony.
   `chronyc sources` should show the GPS selected, and a laptop on the same
   network should get the right time from it (`chronyc` or `sntp`).
2. **The hotspot:** the NetworkManager connection and the dnsmasq drop-in.
   A laptop joins `webelos`, gets a `10.42.0.x` address, and resolves
   `<laptop>.camp`.
3. **One camera:** the `webelos` profile and the timesyncd drop-in.
   `timedatectl timesync-status` shows 10.42.0.1, and its next photograph
   says `"clock": {"source": "network"}`.
4. **Range and power:** cameras at the distances a campsite needs, through
   trees; a power meter on the base for an hour.
5. **The location endpoint**, and whether the cameras record the site from
   it.
6. **The master images:** the Wi-Fi profile and the timesyncd drop-in, so
   every new camera has them.
