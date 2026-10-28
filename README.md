# pi-player

Narrowcasting screen client for the Raspberry Pi 4. It plays the single MP4 our CMS exports,
looped forever, fullscreen on HDMI with no sound. Every screen in a group stays frame-accurate
in sync with the others. When the CMS publishes a new version, all screens download it and
switch at the same moment. First-time setup (wifi and pairing) is done on the screen itself.

- Protocol between Pi and CMS: [`docs/protocol.md`](docs/protocol.md)
- Wifi setup over Bluetooth (App Clip): [`docs/ble-setup.md`](docs/ble-setup.md), iOS app in [`ios/`](ios/)
- Mock CMS for local testing: `mock_cms.py`
- Sync test video: `scripts/make_test_video.sh`

## Contents

0. [Flash the image](#0-flash-the-image) (easiest: no SSH, no network needed)
1. [Install over SSH](#1-install-over-ssh)
2. [First-time setup on the screen](#2-first-time-setup-on-the-screen)
3. [Reset](#3-reset)
4. [Configuration reference](#4-configuration-reference)
5. [Time sync (NTP)](#5-time-sync-ntp)
6. [CMS export spec](#6-cms-export-spec)
7. [Local testing: mock CMS and test video](#7-local-testing-mock-cms-and-test-video)
8. [Hardware test plan](#8-hardware-test-plan)
9. [Troubleshooting](#9-troubleshooting)

## 0. Flash the image

The quickest way to a working screen: a ready-to-flash image with Raspberry Pi OS Lite 64-bit
(Bookworm) and pi-player preinstalled, the CMS URL (`https://app.zekerzichtbaar.tv`)
and wifi country (`NL`) already set. No SSH, no ethernet and no install step needed.

1. **Download** `pi-player-<version>.img.xz` and its `.sha256` from the
   [Releases page](https://github.com/zekerzichtbaar/zz-narrowcasting-player/releases)
   (releases `v*` are the normal images; `image-<n>` prereleases are test builds).
2. **Verify** the download, in the folder with both files:

   ```sh
   sha256sum -c pi-player-<version>.img.xz.sha256          # Linux
   shasum -a 256 -c pi-player-<version>.img.xz.sha256      # macOS
   ```

3. **Flash** it to the SD card (8 GB or more), there is no need to unpack the `.xz` first:
   - **Raspberry Pi Imager:** *Choose OS* → *Use custom* → pick the `.img.xz`. When Imager asks
     *"Would you like to apply OS customisation settings?"*, choose **No**. Customisation
     overwrites the image's user, SSH and wifi settings.
   - **balenaEtcher:** *Flash from file* → pick the `.img.xz` → select the card → *Flash*.
4. **Wifi in advance (optional, no phone needed):** take the card out and put it back in the
   laptop. A drive called `bootfs` appears (the FAT boot partition, readable on Windows, macOS and
   Linux). Open `wifi.txt` on it in any text editor (Notepad, TextEdit in plain-text mode) and fill
   in the customer's wifi:

   ```
   ssid=Kantine
   password=het-wachtwoord
   hidden=no
   ```

   Everything after the `=` counts, so spaces and characters like `:` or `#` in the name are fine
   (no quotes needed). Leave `password` empty for an open network, use `hidden=yes` for a network
   that does not broadcast its name. Save, eject the drive properly. Skip this step and the
   customer sets the wifi with a phone ([section 2](#2-first-time-setup-on-the-screen)).
5. **First boot:** put the card in a Pi 4, connect HDMI, power on. Pi OS expands the root
   filesystem to the full card and **reboots once by itself**. With a filled-in `wifi.txt` the
   Pi joins that network and goes straight to the pairing code; otherwise the screen shows the
   `WIFI_SETUP` screen. Continue with [section 2](#2-first-time-setup-on-the-screen).

**Default login:** user **`pi`**, password **`pi-player`**. SSH is enabled. The password is the
same on every card flashed from the image, so change it right after the first login:

```sh
ssh pi@<pi-host>      # find the IP in your router, or use raspberrypi.local
passwd                # set a new password
```

Every card still gets its own machine ID and SSH host keys at first boot, and pairs itself with
the CMS. Upgrade pi-player later as described in [section 1](#1-install-over-ssh) (re-run the
install line over SSH). Note that every card boots with the hostname `raspberrypi`; change it with
`sudo raspi-config nonint do_hostname <name>` if several screens share a LAN.

### Building the image

The image is built by GitHub Actions (`.github/workflows/image.yml`): pushing a tag `v*` builds
it and attaches it to the Release for that tag. *Run workflow* (`workflow_dispatch`) builds a
test image as a prerelease `image-<run>` and lets you change the user, password, CMS URL and
wifi country. These are not secrets: the password hash sits on the boot partition of the image.

To build locally you need Linux and root (on x86 also `qemu-user-static binfmt-support`; macOS
is not supported):

```sh
sudo apt-get install curl ca-certificates xz-utils fdisk util-linux mount e2fsprogs zerofree openssl
sudo image/build.sh
sudo CMS_URL=https://cms.example IMAGE_USER=pi IMAGE_PASSWORD=secret WIFI_COUNTRY=BE \
     VERSION=1.2.3 OUT_DIR=/tmp/out image/build.sh
```

The script downloads the pinned Pi OS base image (checksum-verified, cached in `OUT_DIR`), runs
`install.sh` in a chroot, checks the result and writes `$OUT_DIR/pi-player-$VERSION.img.xz` plus
`.sha256` (default `OUT_DIR` is `image/out/`). It takes about 5 to 15 minutes.

## 1. Install over SSH

You need a Raspberry Pi 4 running **Raspberry Pi OS Lite 64-bit (Bookworm)** with SSH enabled
and a network connection for the install itself. Ethernet is easiest. The wifi can be set up
later on the screen.

One line, on the Pi (downloads the code from GitHub):

```sh
wget -qO- https://raw.githubusercontent.com/zekerzichtbaar/zz-narrowcasting-player/master/install.sh | sudo bash
sudo reboot                           # once, so the kernel console text disappears
```

Upgrade = run the same line again. Or from a local checkout:

```sh
# from your machine
scp -r zz-narrowcasting-screen pi@<pi-host>:   # or: ssh in and git clone the repo
ssh pi@<pi-host>
cd zz-narrowcasting-screen
sudo ./install.sh                     # wifi country defaults to NL
sudo WIFI_COUNTRY=BE ./install.sh     # ...or set it explicitly
sudo reboot                           # once, so the kernel console text disappears
```

`install.sh` does the following:

- Installs the apt packages: `chrony python3 python3-pil python3-qrcode fonts-dejavu-core dnsmasq-base` (DejaVu only as a fallback: the setup screens use the fonts in `pi_player/fonts/`, same look as the CMS player) and GStreamer (`python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-{base,good,bad} gstreamer1.0-libav`). It uses no pip and no venv.
- Copies the agent to `/opt/pi-player` and installs the `pi-player` command in `/usr/local/bin`.
- Writes `/etc/pi-player/config.toml` on the first run only. Later runs keep your edits.
- Creates `/var/lib/pi-player/` (mode 0700) for the token, the videos and `state.json`.
- Adds the captive-portal DNS drop-in for the setup hotspot (`/etc/NetworkManager/dnsmasq-shared.d/pi-player-portal.conf`).
- Sets the wifi country if none is set, and runs `rfkill unblock wifi`.
- Writes the chrony drop-in from `ntp_server` (see [section 5](#5-time-sync-ntp)).
- Adds any missing kernel cmdline tokens to `/boot/firmware/cmdline.txt` for a clean console: `consoleblank=0 vt.global_cursor_default=0 quiet loglevel=3 logo.nologo`. Tokens that are already there are never duplicated. If you already set one of these keys yourself (e.g. `loglevel=7`), your value is left alone. A one-time backup is saved as `cmdline.txt.pi-player.bak`. It also sets `video=HDMI-A-1:<display_mode>` (default `1920x1080@60`), replacing an existing `video=HDMI-A-1:` token. You need to **reboot** before the change takes effect.
- Installs and enables `pi-player.service`, then restarts it.

**Upgrading:** re-run `sudo ./install.sh` from a newer checkout. The script is idempotent: it
keeps your config, token, videos and pairing.

The service starts on boot and takes over tty1 (the login prompt on HDMI is disabled). It runs
with `Restart=always`, `RestartSec=2` and `StartLimitIntervalSec=0`. If the video pipeline fails, the agent
exits, and if the agent dies or is killed, systemd starts it again within about 2 seconds. It
never gives up, and on restart it shows whatever state is correct at that moment.

The screens (pairing, waiting, wifi setup, identify) share one pipeline that stays up, so going from
screen to screen never shows the console. At startup the agent puts tty1 in graphics mode and wipes the
framebuffer: the short moment between a screen and the video (or a restart) is black, not a terminal.

## 2. First-time setup on the screen

At any moment the screen shows exactly one of these four states:

| State | When | Screen |
|---|---|---|
| `WIFI_SETUP` | offline: no network, or the CMS unreachable | QR to the App Clip, hotspot SSID + password (`setup_url = ""`: QR to join the hotspot, QR + address of the setup page) |
| `PAIRING` | network, not paired | pairing code + QR to the CMS pairing page |
| `WAITING` | paired, nothing published | "Waiting for content" + screen name |
| `PLAYING` | paired + video | the synced MP4 |

There are two ways to get a Pi on wifi. Both keep working side by side:

- **`wifi.txt` on the card** (Zeker Zichtbaar prepares the card, see [section 0](#0-flash-the-image)
  step 4). At every agent start the Pi looks for `/boot/firmware/wifi.txt`. A filled-in file is
  saved as the NetworkManager connection `pi-player-<ssid>` (autoconnect, like one set via the
  phone) and the Pi joins it before the hotspot can start. Right after saving, the file is renamed
  to `wifi.txt.done` with `password=***`, so no readable password stays on the FAT partition (the
  NetworkManager copy is root-only on the ext4 partition). Working ethernet wins: the network is
  then only saved, for when the cable goes. If joining fails (wrong password, out of range), the
  connection stays saved (the `wifi_retry` picks it up once it is in range), the error shows on the
  wifi-setup screen (e.g. *wifi.txt: verbinden met Kantine mislukt: Verkeerd wachtwoord?*) and the
  hotspot starts as usual, so the phone routes below can still fix it. An unusable file (no `ssid=`,
  a password under 8 characters) is masked and renamed too, with the reason on the screen. The empty
  template that comes with the image is left alone. To change the wifi later the same way, write a
  new `wifi.txt` to the card and boot.
- **The phone**, the default when a customer powers the Pi up themselves: an iPhone with the App
  Clip over Bluetooth, any other phone via the hotspot.

Walkthrough of the phone routes on a fresh Pi with no ethernet:

1. **Wifi setup.** About 30 s after boot, the screen shows *Wifi instellen* with a QR code,
   the hotspot name `Narrowcast-XXXX` and a password. The last 4 characters of the name come
   from the device ID. The QR key and the password are new on every agent start: being in front
   of the screen is the authorization.
2. **iPhone:** scan the QR code with the camera. The App Clip *Scherm instellen* opens, finds
   the screen over Bluetooth and lists the networks the Pi sees. Pick one, enter its password;
   the clip shows the progress, the error (e.g. *Verkeerd wachtwoord?*) or, once the screen is
   online, its pairing code with a button to the CMS pairing page. The phone stays on its own
   network the whole time. Protocol: [`docs/ble-setup.md`](docs/ble-setup.md).
3. **Other phones** (or an iPhone without the App Clip): join the hotspot by hand; scanning the
   QR on Android opens a CMS page with the same steps. The phone's captive-portal sheet or
   notification should open the setup page by itself, usually within a few seconds: the hotspot's
   DHCP hands out the portal address (option 114, RFC 8910), every DNS name resolves to the Pi,
   and https probes are refused at once instead of timing out. **Nothing opens** (Android with
   *Private DNS*, a phone that stays on mobile data, an older phone): browse to
   **http://10.42.0.1** while still on the hotspot. With `setup_url = ""` (no Bluetooth) the
   screen shows two QR codes instead: **code 1** joins the hotspot, **code 2** opens that page.
4. On the setup page, pick your network (or type a hidden SSID), enter its password and tap
   *Verbinden*. The page (in Dutch, like the tv screens) says what happens next: the hotspot goes
   down while the Pi tries to join, the phone falls back to its own wifi or mobile data and the
   page stops answering, so watch the tv. If joining fails, the hotspot comes back within a minute
   and both the screen and the page show the error (e.g. *Verkeerd wachtwoord?*). Rejoin and try
   again. *Lijst vernieuwen* drops the hotspot briefly to refresh the network list, because the
   Pi 4 can't scan and run a hotspot at the same time (the App Clip's refresh does the same).
5. **Pairing.** Once the Pi is online, the screen shows a short code (`K7Q-4M2`) and a QR
   code that opens the CMS pairing page with the code filled in. Enter the code in the CMS
   and choose a name and group for the screen. Codes expire after about 10 minutes and are
   replaced on the screen automatically.
6. **Waiting / playing.** No reboot is needed. The screen moves to *Waiting for content*, or
   starts playing straight away if its group already has a published video.

If ethernet is connected (and reaches the CMS), the Pi skips wifi setup.

**Online** means: NetworkManager has an ethernet or wifi connection (not the setup hotspot) *and*
the CMS answers `GET <cms_url>/up` (any HTTP status, 5 s timeout, every 30 s while it answers;
two failed probes in a row count). A wifi without internet, or one behind a guest-network login
page, therefore counts as offline. After joining a network from the setup page the Pi checks the
CMS too and shows *Verbonden met X, maar de server is niet bereikbaar* when it can't get there.
A paired Pi with content never brings up the hotspot for that: a hotspot can't fix a wifi
without internet or a CMS that is down, so it keeps playing.

When wifi setup shows:

- **New or unpaired Pi (or no content yet) without a network:** the wifi-setup screen appears
  after 30 s and stays until there is a network.
- **Paired Pi with content, started without any connection** (NetworkManager not connected after
  30 s): the wifi-setup screen with a countdown, *Over 10 min speelt dit scherm verder met de
  laatste versie*, for `offline_timeout` (10 min) after the start. Then it plays the last version
  and never shows setup again until the next start. While a phone is on the hotspot the countdown
  holds at 1 min. A network that comes back during the countdown ends it: the screen plays.
  So to set up a new wifi on a playing screen: **turn it off and on**.
- **Network lost while playing:** the screen keeps playing, however long it takes. The CMS shows
  it as offline (and mails after 15 min within opening hours).
- **While the hotspot is up:** every `wifi_retry` (2 min) the Pi drops the hotspot briefly
  and retries its saved networks. When a network (or ethernet) is back and the CMS answers, it
  returns to normal. The retry waits while someone is setting up: after a request to the setup
  page, and as long as a phone is connected to the hotspot (`iw dev wlan0 station dump`), so
  the hotspot never drops away under a phone mid-setup.

**Unpairing in the CMS** (or a revoked token) sends the screen back to the pairing code.

## 3. Reset

```sh
sudo pi-player reset
```

This deletes the device token, `state.json`, the videos and every NetworkManager connection
pi-player created (`pi-player-*`), then restarts the service. The Pi goes back to the
first-boot flow. A wifi connection set up by Raspberry Pi Imager (`preconfigured`) is left
alone. Remove it yourself if you want the setup hotspot:
`sudo nmcli connection delete preconfigured`.

## 4. Configuration reference

The file is `/etc/pi-player/config.toml`. Restart the service after editing:
`sudo systemctl restart pi-player`. The exception is `ntp_server`, which needs a re-run of
`install.sh`. Keys you leave out fall back to these defaults (`pi_player/config.py`):

| Key | Default | Meaning |
|---|---|---|
| `cms_url` | `"http://localhost:8080"` | CMS base URL. The device token lives in `<state_dir>/token` (0600, written by pairing). |
| `manifest_poll` | `30` | Manifest poll interval in seconds. This poll is how new versions, group moments and unpublishes reach the screen. Re-running `install.sh` changes an old default of `300` to `30`. |
| `offline_timeout` | `600` | Paired Pi with content, started without any connection: show wifi setup (with a countdown) this many seconds after the start, then play. A network lost while playing never brings up setup. |
| `wifi_retry` | `120` | While the hotspot is up: retry saved wifi networks every N seconds. The retry is postponed while someone is using the setup page. |
| `setup_url` | `https://app.zekerzichtbaar.tv/setup` | The wifi-setup QR opens this URL with `?d=<id>&k=<key>`: the App Clip on an iPhone (Bluetooth setup), the CMS fallback page elsewhere. Empty = no Bluetooth setup; the QR joins the hotspot. |
| `ntp_server` | `""` | chrony time source. Empty = the distro's internet pool. See [section 5](#5-time-sync-ntp). |
| `logo` | `"/etc/pi-player/logo.png"` | Optional PNG shown top-left on the setup screens (max 360x140), instead of the Zeker Zichtbaar brand. If the file is missing, the brand shows. |
| `state_dir` | `"/var/lib/pi-player"` | Token, `current.mp4`, `next.mp4`, `state.json`. |
| `video` | `""` | Video to play. Empty = `<state_dir>/current.mp4`. |
| `epoch` | `0` | Sync reference: frame 0 plays at `epoch + k*duration` (Unix seconds). The manifest's `epoch` overrides it. |
| `display_mode` | `"1920x1080@60"` | HDMI mode, set on the kernel cmdline by `install.sh` (re-run + reboot after changing). Smoothest when the refresh is a multiple of the fps: `@60` for 30 fps, `@50` for 25 fps. Many PC monitors reject `@50` ("Out of range"); TVs accept both. Empty = leave the cmdline alone. |
| `video_sink` | `"kmssink"` | GStreamer sink, plus properties (e.g. `"kmssink connector-id=32"` with two HDMI outputs). Dev machine: `"autovideosink"`. |
| `rotation` | `0` | Only without content updates (dev, `--video`): how the panel hangs, for the setup screens. Otherwise the CMS decides via the manifest. The video is never turned on the Pi: for a panel hung upright (90/270) the CMS serves a 1920x1080 file with the picture already turned, which plays zero-copy into `kmssink` (turning on a Pi 4 was too heavy: GPU froze with wrong colours, CPU managed 1 fps). Re-running `install.sh` removes the old `rotate_method`/`gl_sink` settings. |
| `update_url_prefix` | GitHub repo | Player updates ("Speler bijwerken") are only installed from a URL starting with this (default `https://github.com/zekerzichtbaar/zz-narrowcasting-player/`). See `docs/protocol.md` section 7. |
| `[sync] interval` | `1.0` | How often to check the position (s). |
| `[sync] seek_threshold` | `0.5` | If \|drift\| is above this (clock stepped, pipeline stalled), re-join the group clock (s). |

The CLI is `pi-player [run|reset] [--config PATH] [--video PATH] [--epoch N] [--rotation 0|90|270]`.

## 5. Time sync (NTP)

There is no master screen. The video plays in GStreamer on the system clock, with its start
time chosen so that media position `p` is on screen at `epoch + p` (`pi_player/sync.py`). It
loops with gapless segment seeks, so every loop adds exactly one duration and nothing ever
needs correcting. Sync between screens is therefore only as good as the agreement between
their clocks. chrony keeps the clocks aligned.

- `ntp_server = ""` (the default) uses Debian's internet pool. That is usually fine to a few
  ms, but screens behind the same router can still differ by a few ms.
- For the tightest alignment, point all screens at **one LAN time source**: set
  `ntp_server = "192.168.1.10"` (any host or IP) and re-run `sudo ./install.sh`. This writes
  `/etc/chrony/conf.d/pi-player.conf` containing `server 192.168.1.10 iburst prefer` and
  restarts chrony. Setting the key back to `""` and re-running removes the file.
- **Making a LAN box the time source** (one Pi, or any Linux box that is always on): install
  chrony on it and add `/etc/chrony/conf.d/lan-server.conf`:
  ```
  allow 192.168.0.0/16     # your LAN
  local stratum 10         # keep serving time even if its own internet sync is down
  ```
  Then run `sudo systemctl restart chrony`. That box can be one of the player Pis. Its own
  `ntp_server` stays empty.
- Check with `chronyc tracking` (look at *System time* offset) and `chronyc sources -v`
  (the `*` marks the source in use).

## 6. CMS export spec

The CMS must render exactly **one** MP4 per group (full spec in
[`docs/protocol.md` §3.2](docs/protocol.md)):

- MP4 container, **H.264** (High profile, level ≤ 4.2), **1920x1080**, yuv420p
- **Constant frame rate, 25 or 30 fps**
- **Keyframe at frame 0** (and preferably every 1–2 s so seeks are cheap)
- Audio is not needed. It is ignored (the Pi is always muted).

The rest of the CMS contract is in [`docs/protocol.md`](docs/protocol.md). It covers the
register/pairing endpoints, the manifest JSON (`version`, `url`, `sha256`, `size`,
`duration`, `epoch`, `activate_at`), the heartbeat and the bel. Manifest and heartbeat are plain
HTTPS: the Pi polls the manifest every 30 s (`GET /api/devices/manifest`) and posts a heartbeat every
30 s and on every state/version change (`POST /api/devices/heartbeat` with `state`, `version`,
`pending_version`, `drift_ms`). A `401` on either means the screen was unpaired. A new
`version` with the same `sha256` (for example a new group moment) reuses the local file.
The bel (`pi_player/bell.py`, stdlib WebSocket, Pusher protocol / Laravel Reverb) is only a
signal: on `poll` or `identify` the Pi fetches the manifest right away instead of at the next
poll. Without Reverb, or while it is down, the 30 s poll carries on.

## 7. Local testing: mock CMS and test video

```sh
# a 60 s 1080p25 test video with a big frame counter, timecode and a sweeping bar
scripts/make_test_video.sh media/test.mp4 25 60

# mock CMS (stdlib only) serving MP4s from ./media
python3 mock_cms.py --port 8080 --media media
```

On the Pi, set `cms_url = "http://<your-machine>:8080"` in `/etc/pi-player/config.toml` and
restart the service. Then:

- **Pair:** open `http://<your-machine>:8080/pair` (or scan the QR code on the screen), enter the code, name and group.
- **Publish:** `curl -X POST 'http://localhost:8080/admin/publish?file=test.mp4&delay=60'`. All screens switch 60 s from now (they pick it up at their next manifest poll, within 30 s). The mock logs every heartbeat (`state`, `version`, `pending_version`, `drift_ms`).
- **Group wait + moment:** publish with `&wait=1` (screens download and report `pending_version`, a screen with nothing on it starts right away), then `curl -X POST 'http://localhost:8080/admin/activate?delay=45'`: same file, new version, every screen switches at the same moment without downloading again.
- **Failed download:** add `&bad_sha=1`. The Pi rejects the file, keeps playing the old one, logs the mismatch and retries at the next poll.
- **Unpublish:** `curl -X POST http://localhost:8080/admin/unpublish` (the screen goes to WAITING).
- **Player update:** `curl -X POST 'http://localhost:8080/admin/update?version=v1.3.0'` puts `update` in every manifest until a heartbeat reports that `agent_version`. The Pi only installs it from the GitHub URL (`update_url_prefix`) and needs `systemd-run` (a dev machine marks it failed in `update.json`). While the runner works, a playing Pi shows "Aan het updaten..." bottom-left over the video on its own plane (`journalctl -u pi-player | grep "update label"`; preview: `python3 -m pi_player.screens OUT_DIR` writes `update-label-*.png`).
- **Unpair:** `curl -X POST 'http://localhost:8080/admin/unpair[?device_id=pi-...]'` (the screen goes to PAIRING at its next poll or heartbeat).

Without a Pi, you can run the agent on Linux with GStreamer (`video_sink = "autovideosink"`
opens a window): `python3 -m pi_player --config my-dev-config.toml`, with `state_dir`
pointing at a writable path. To play just a file with sync and no CMS (dev machine where `/var/lib/pi-player` isn't
writable, so content/pairing are off): `python3 -m pi_player --video media/test.mp4`.

Self-checks (pure logic, no hardware needed):

```sh
for m in sync content heartbeat state pairing netman portal bell gst; do python3 -m pi_player.$m || echo "FAIL $m"; done
python3 -m pi_player.screens check            # setup screens turned for a rotated panel
python3 -m pi_player.screens /tmp/screens      # render the setup screens as PNGs to look at
python3 -m pi_player.portal serve 8000         # preview the captive page with a fake network list
```

The setup screens copy the look of the CMS Chromium player (`resources/css/player.css`, house style
"Samen": colours, Geist / Geist Mono / Bricolage Grotesque from `pi_player/fonts/`, layout), as a still
frame since a PNG cannot animate. Change the player style in the CMS, change `screens.py` with it.
Rendering the PNGs needs Pillow and qrcode; on a Mac without them:
`python3.13 -m venv /tmp/pp && /tmp/pp/bin/pip install pillow qrcode`, then use `/tmp/pp/bin/python`.

## 8. Hardware test plan

**Wifi setup via the hotspot** (phone needed; test iPhone and Android; set `setup_url = ""` in
`/etc/pi-player/config.toml` for the two-code screen, with the App Clip QR join the hotspot by hand):

1. Run `sudo ./install.sh` on a Pi with ethernet. Unplug the ethernet and make sure there is
   no saved wifi (`sudo pi-player reset`).
2. Within about 30 s the screen shows *Wifi instellen* with `Narrowcast-XXXX`, a password,
   code 1 (join) and code 2 (setup page) plus `http://10.42.0.1`.
3. On the Pi: `cat /etc/NetworkManager/dnsmasq-shared.d/pi-player-portal.conf` shows
   `address=/#/10.42.0.1` and `dhcp-option=114,"http://10.42.0.1/"`. From a laptop joined to the
   hotspot: `curl -m 3 https://10.42.0.1` fails at once with *Connection refused* (not a
   time-out), and `curl -H 'Accept: application/captive+json' http://10.42.0.1/` answers
   `{"captive": true, ...}`.
4. Scan code 1 with an iPhone (iOS 14+) and with an Android 11+ phone. The captive sheet or
   notification should open the Dutch setup page within about 10 s, without touching anything.
5. On an Android phone with *Private DNS* on (Settings > Network > Private DNS, e.g.
   `dns.google`) and/or mobile data on, join via code 1: if no page opens, scan code 2. It
   should open http://10.42.0.1 with the setup page.
6. Join the hotspot with a phone and leave it there without opening the page for more than
   3 minutes: the hotspot must stay up (the log says `phone on the setup hotspot, retry
   postponed`). Then save a wifi that is out of range and repeat: the hotspot still stays up while the phone is on it. Disconnect the phone: about 2 min later the Pi retries its saved networks (without a saved wifi it never drops the hotspot to retry).
7. Pick the network and enter a wrong password first. The page says the hotspot goes off and to
   watch the tv. Within about 1 min the hotspot comes back, and the screen and (after
   rejoining) the page show *Verkeerd wachtwoord?*.
8. Enter the correct password. The screen should show the pairing code.
9. Turn that wifi AP off. Without content the setup screen should come back after 30 s; a
   playing screen keeps playing. Turn the AP back on: the Pi should rejoin within `wifi_retry`
   (2 min). With content and the AP off, restart the Pi: after 30 s the setup screen with the
   countdown, after 10 min the video.
10. Plug in ethernet while the setup screen is showing. It should leave setup within about 5 s.

In `journalctl -u pi-player -f`, look for `hotspot ... up`, `portal on` and `wifi: connected`.

**Wifi setup over Bluetooth** (iPhone with the App Clip; before App Store approval: build the
clip from `ios/` and add a Local Experience for `https://app.zekerzichtbaar.tv/setup`, see
[`ios/README.md`](ios/README.md)):

1. Same start as above. The journal shows `bluetooth: wifi setup service registered` and
   `bluetooth: advertising as ZZ-XXXX`; the screen's QR is the App Clip link.
2. Scan the QR with the iPhone camera. The clip finds the screen and lists the networks.
3. Wrong password first: the clip shows *Verkeerd wachtwoord?* within about a minute.
4. Correct password: the clip shows *Het scherm is online* with the same pairing code as the
   screen; *Koppel dit scherm* opens the CMS.
5. Two screens side by side: each QR reaches only its own screen.
6. Restart the agent (`sudo systemctl restart pi-player`) and use the old QR: the clip says the
   code is outdated; the new QR works.

**`wifi.txt`** (SD-card reader + laptop):

1. Flash the image, fill in `wifi.txt` on `bootfs` with a reachable network (try one with `:` or
   `#` in its name, and save once from Windows Notepad: BOM + CRLF), boot without ethernet. The Pi
   should reach the pairing code without the wifi-setup screen (or only briefly); the log says
   `wifi.txt: connected to '...'`.
2. Put the card back in the laptop: only `wifi.txt.done` is there, with `password=***`.
   `sudo nmcli connection show` on the Pi lists `pi-player-<ssid>`.
3. Write a `wifi.txt` with a wrong password and boot: the wifi-setup screen shows
   *wifi.txt: verbinden met ... mislukt: Verkeerd wachtwoord?* with the hotspot, and setting the
   wifi with a phone still works. Same with a network that is switched off (*Netwerk niet
   bereikbaar.*); switch it back on: the Pi joins it at the next `wifi_retry`.
4. With ethernet plugged in and a filled-in `wifi.txt`: the Pi stays on ethernet (log: `saved,
   ethernet is connected`); unplug the cable and the wifi takes over.

**Pairing and playback:** pair via the code (expect WAITING or PLAYING with no reboot). Reboot
and check that the video plays fullscreen, muted and looping, with no console text or cursor.
Run `sudo systemctl kill pi-player`: playback should be back within
10 s. Unpair in the mock and check that the pairing code comes back.

**Sync** (2–3 Pis side by side, same group):

1. Put the test video (`scripts/make_test_video.sh`) on the mock and publish it. All screens
   should switch at the same moment.
2. Let them settle (about 1 min). With `journalctl -u pi-player -f`, drift lines should stay
   near 0 ms.
3. Film all screens together with a phone. Use a high shutter speed, or a 120/240 fps slow-mo
   video. Step through the frames: the big frame counters should match, or differ by at most
   1 (≤ 40 ms at 25 fps). Repeat after 1 hour.
4. Publish a new version: all screens should switch together.
5. Unplug the network: playback continues however long it takes, and after reconnecting the
   heartbeat resumes and any pending update is applied.

**Rotated screen** (CMS "Stand van het scherm" 90 or 270; mock: `curl -X POST 'http://localhost:8080/admin/rotate?rotation=90'`):

1. Hang the screen turned clockwise and set "Stand van het scherm" in the CMS, with a portrait
   playlist on it. The CMS serves the turned variant of the video: the Pi downloads it as a new
   version and plays it as is, upright; the setup, waiting and identify screens are upright too.
   (The mock serves whatever file you publish: it does not turn video, so publish one that is
   already turned to test with it.)
2. `journalctl -u pi-player -f` shows `rotation 0 -> 90` and a new `playing ...` line for the variant.
3. Measure: the once-a-minute `frames this loop` line should show no growing `dropped`, and
   `top` should leave headroom for the agent. Film it like the sync test: still within 1 frame
   of a landscape neighbour in the same group.

## 9. Troubleshooting

- **Logs:** `journalctl -u pi-player -f` (agent, display, wifi, pairing, drift + dropped frames once a minute), `systemctl status pi-player`.
- **Black screen / nothing on HDMI:** check `journalctl -u pi-player` for `display:` errors. Make
  sure the HDMI cable was connected at boot, and confirm nothing else holds DRM (no desktop
  installed).
- **Console text or cursor still visible:** reboot once after the install (the cmdline change
  only takes effect at boot). Check `cat /proc/cmdline` for `vt.global_cursor_default=0 quiet loglevel=3`.
- **No hotspot / wifi missing:** wifi is rfkill-blocked until a country is set. Run
  `sudo raspi-config nonint get_wifi_country` and `rfkill list`. Fix it with
  `sudo WIFI_COUNTRY=XX ./install.sh`, or `sudo raspi-config nonint do_wifi_country XX && sudo rfkill unblock wifi`.
- **Captive page doesn't open on the phone:** stay on the hotspot and scan code 2 on the tv (only
  without `setup_url`), or browse to **http://10.42.0.1** by hand. Usual causes: Android *Private DNS* (the phone asks
  its own DNS server over TLS, so it never sees the Pi's answer: turn it off or use code 2), a
  phone that stays on mobile data because the hotspot has no internet (turn mobile data off,
  or answer "keep using this network"), and phones that only show a notification instead of a
  sheet. Check the Pi side with step 3 of the [hardware test plan](#8-hardware-test-plan): a
  missing `dhcp-option=114` line means `install.sh` did not run after the update, and an https
  probe that hangs instead of being refused means something on the Pi drops port 443 (a
  firewall): it must refuse.
- **Hotspot keeps disappearing during setup:** it should only go down for *Lijst vernieuwen*, a
  connect attempt or the `wifi_retry`, and the retry waits while a phone is connected. Check
  `iw dev wlan0 station dump` on the Pi (no `iw` = the retry can't see phones; `sudo apt install iw`).
- **`wifi.txt` did nothing:** it must be called exactly `wifi.txt` (Windows may hide a second
  `.txt`: `wifi.txt.txt` is ignored) and sit in the root of `bootfs`. The agent reads it at start
  only, so reboot after writing it. Still there after boot = not read: check
  `journalctl -u pi-player | grep wifi.txt`. A `wifi.txt.done` means it was read; the error (if any)
  is on the wifi-setup screen and in the log. A Pi on ethernet only saves the network.
- **Joined wifi but stuck at "Connecting to the CMS…":** the wifi has no route to the CMS.
  Check `cms_url` and the network. `sudo pi-player reset` (or plugging in ethernet) gets you
  back to setup.
- **Screens drift apart:** check the clocks (`chronyc tracking` on each Pi, offsets should be
  ≪ 10 ms) and use a LAN time source ([section 5](#5-time-sync-ntp)). The player itself
  doesn't drift: frame timing is locked to the clock.
- **"Out of range" / no signal:** the screen doesn't accept `display_mode` (PC monitors often
  reject 50 Hz). Set `display_mode = "1920x1080@60"`, re-run `install.sh`, reboot.
- **Judder / uneven motion:** the HDMI refresh rate doesn't match the video fps (30 fps needs
  60 Hz, 25 fps needs 50 Hz). Prefer 30 fps exports: 60 Hz works on every screen. Check `display_mode`, re-run `install.sh`, reboot, and verify
  with `cat /proc/cmdline`. The once-a-minute log line shows `dropped` frames; it should stay 0.
- **Software decoding (high CPU):** the log says `avdec` / no `v4l2h264dec`. The Pi decoder
  needs an H.264 High ≤ 4.2 yuv420p stream; untagged colour is handled.
- **Update not switching:** run `journalctl -u pi-player | grep -i -e manifest -e download -e activated`.
  A sha256 mismatch is rejected (the old video keeps playing) and retried at the next manifest poll.
  A Pi that finishes downloading after `activate_at` switches at the next loop boundary.
- **Start over:** `sudo pi-player reset`.
