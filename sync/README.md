# Syncing photos from the cameras to a laptop

[`sync_cameras.py`](sync_cameras.py) runs on a **laptop** (macOS or Linux),
not on a Raspberry Pi. It finds the wildlife cameras on the local Wi-Fi
network and copies their photographs to the laptop with `rsync`, giving each
camera its own directory.

This is the last item on the "possible next steps" list in the main
[README](../README.md): automatically copy photographs to a laptop when
returning to camp.

## Quick start

```bash
cd sync
./sync_cameras.py --list     # who is out there?
./sync_cameras.py            # copy everything new
```

The result looks like this:

``` text
~/wildlifecam-photos/
├── wildlifecam1/
│   ├── 2026-08-09/
│   │   ├── 150501.jpg
│   │   └── 150722.jpg
│   └── 2026-08-10/
│       └── 081207.jpg
└── wildlifecam2/
    └── 2026-08-10/
        └── 073344.jpg
```

Each camera keeps its own directory, and the day directories from the camera
are preserved inside it. Running the program again copies only the
photographs that are new, so it is quick and safe to run as often as you
like.

## How the cameras are found

The program tries several methods, stopping as soon as it has answers:

1. **Cameras you name yourself** with `--host`, or in `cameras.conf`.
2. **Guessed mDNS names** --- `wildlifecam.local`, `wildlifecam1.local`, up
   through `wildlifecam9.local`. Raspberry Pi OS announces its own hostname
   on the network, so this works with no configuration at all.
3. **mDNS browsing** with `dns-sd` (already on macOS) or `avahi-browse`
   (`sudo apt install avahi-utils` on Linux). This finds cameras even if
   they were numbered unexpectedly.
4. **A subnet scan**, only with `--scan`. This checks every address on the
   local network for an ssh port. Use it on a network where `.local` names
   do not work --- some travel routers and guest networks block mDNS.

A camera found by scanning has to identify itself before anything is copied
from it: the program fetches `http://<address>/` and looks for the word
"wildlife" on the page. The example `index.html` in the main README already
contains it. If you customized the page, you can drop a marker file on the
Pi instead:

```bash
echo "wildlife camera" | sudo tee /var/www/html/wildlifecam.txt
```

Hosts you named yourself, and hosts whose name matches `wildlifecam*`, are
trusted without that check.

## Setting up passwordless login

Without an ssh key, `rsync` asks for the Pi's password once per camera.
That is fine for two cameras and tiresome for six. Copy your key to each
camera once:

```bash
ssh-keygen -t ed25519        # only if you do not already have a key
ssh-copy-id webelos@wildlifecam1.local
```

After that the sync runs unattended.

## Naming the cameras

Give each Pi a distinct hostname before the campout so the laptop can tell
them apart. On the camera:

```bash
sudo hostnamectl set-hostname wildlifecam3
sudo reboot
```

`wildlifecam3.local` then works from any machine on the same network.

For friendlier directory names on the laptop, copy `cameras.conf.example` to
`cameras.conf` and map hostnames to Scout names:

``` text
alice   wildlifecam1.local
ben     wildlifecam2.local
```

`cameras.conf` is ignored by git, so each family can keep their own.

## Useful options

| Option | What it does |
| --- | --- |
| `--list` | Find cameras and print them; copy nothing. |
| `--dest DIR` | Where to put the photos (default `~/wildlifecam-photos`). |
| `--host NAME` | Sync a specific camera. May be repeated. |
| `--user NAME` | ssh user on the cameras (default `webelos`). |
| `--dry-run` | Show what would be copied without copying it. |
| `--scan` | Also scan the local subnet when mDNS does not work. |
| `--subnet CIDR` | Scan a particular network, e.g. `192.168.1.0/24`. |
| `--delete` | Remove local photos that are gone from the camera. |
| `--insecure-hostkeys` | Ignore a changed ssh host key after reimaging a Pi. |
| `--verbose` | List every file as it is copied. |

Run `./sync_cameras.py --help` for the full list.

## Troubleshooting

**"No wildlife cameras found."**
Check that the laptop and the cameras are on the same Wi-Fi network --- a
laptop on 5 GHz guest Wi-Fi cannot see a Pi on the main network. Then try
`ping wildlifecam1.local`. If the name does not resolve but you know the
address, use `--host 192.168.1.57`, or try `--scan`.

**Nothing is found by name, but `--scan` works.**
Finding cameras by their `.local` name relies on multicast, and some Wi-Fi
networks carry multicast unreliably --- a query to the camera may simply never
arrive, even though the camera is powered on and reachable. (Raspberry Pi
Wi-Fi power saving makes this worse, since the Pi wakes for multicast only
now and then.) Use `--scan` on such a network: it probes each address by
ordinary unicast, which Wi-Fi delivers reliably, and still labels the camera
with its real `wildlifecam*` name.

**`.local` names do not resolve on Linux, but work from a Mac.**
Something else on the laptop is probably holding the mDNS multicast socket.
Google Chrome is the usual culprit: it binds `224.0.0.251:5353` for Chromecast
discovery, and the Linux kernel then delivers every mDNS reply to Chrome
instead of to `avahi-daemon`, so system name resolution quietly times out. You
can confirm it with `ss -ulnp | grep 5353` (look for `chrome` on
`224.0.0.251:5353`). Closing Chrome restores resolution. You should not need
to, though --- `sync_cameras.py` sends its own mDNS query from an ordinary
port, which the camera answers by unicast, so it keeps finding cameras by name
regardless of what else is running. See the comment on `mdns_query()` for the
details.

**It asks for a password over and over.**
Set up an ssh key as described above.

**"Host key verification failed."**
The Pi was reimaged and now has a different identity. Either remove the old
entry with `ssh-keygen -R wildlifecam1.local` or pass `--insecure-hostkeys`.

**"Permission denied" reading the photos.**
The photo directory on the Pi must be readable by the ssh user. The default
`/var/www/html/photos` created by the camera program already is.

**The copy is slow.**
That is normal on Wi-Fi with a lot of new photographs. `rsync` resumes
partly-copied files, so interrupting it with Ctrl-C and running it again
later does not start over.
