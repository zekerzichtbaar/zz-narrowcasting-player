# pi-player

Narrowcasting screen client for the Raspberry Pi 4. It plays the single MP4 our CMS exports,
looped forever, fullscreen on HDMI with no sound. Every screen in a group stays frame-accurate
in sync with the others. When the CMS publishes a new version, all screens download it and
switch at the same moment. First-time setup (wifi and pairing) is done on the screen itself.

- Protocol between Pi and CMS: [`docs/protocol.md`](docs/protocol.md)
- Mock CMS for local testing: `mock_cms.py`
- Sync test video: `scripts/make_test_video.sh`

## Contents

1. [Install over SSH](#1-install-over-ssh)
2. [First-time setup on the screen](#2-first-time-setup-on-the-screen)
3. [Reset](#3-reset)
4. [Configuration reference](#4-configuration-reference)
5. [Time sync (NTP)](#5-time-sync-ntp)
6. [CMS export spec](#6-cms-export-spec)
7. [Local testing: mock CMS and test video](#7-local-testing-mock-cms-and-test-video)
8. [Hardware test plan](#8-hardware-test-plan)
9. [Troubleshooting](#9-troubleshooting)

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

- Installs the apt packages: `chrony python3 python3-pil python3-qrcode fonts-dejavu-core dnsmasq-base` and GStreamer (`python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-{base,good,bad} gstreamer1.0-libav gstreamer1.0-gl`). It uses no pip and no venv.
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

## 2. First-time setup on the screen

At any moment the screen shows exactly one of these four states:

| State | When | Screen |
|---|---|---|
| `WIFI_SETUP` | no network | hotspot SSID, password, QR to join |
| `PAIRING` | network, not paired | pairing code + QR to the CMS pairing page |
| `WAITING` | paired, nothing published | "Waiting for content" + screen name |
| `PLAYING` | paired + video | the synced MP4 |

Walkthrough on a fresh Pi with no ethernet:

1. **Wifi setup.** About 30 s after boot, the screen shows *Set up wifi* with the hotspot
   name `Narrowcast-XXXX`, a password and a QR code. The last 4 characters of the name come
   from the device ID. The password is new on every agent start: being in front of the screen
   is the authorization.
2. On a phone, scan the QR code, or join the hotspot by hand. The phone's captive-portal sheet
   or notification should open the setup page. If it doesn't, browse to **http://10.42.0.1**.
3. Pick your network (or type a hidden SSID), enter its password and tap connect. The
   hotspot goes down while the Pi tries to join. If that fails, the hotspot comes back and
   both the screen and the page show the error (e.g. *Wrong password?*). Rejoin and try again.
   *Rescan* drops the hotspot briefly to refresh the network list, because the Pi 4 can't
   scan and run a hotspot at the same time.
4. **Pairing.** Once the Pi is online, the screen shows a short code (`K7Q-4M2`) and a QR
   code that opens the CMS pairing page with the code filled in. Enter the code in the CMS
   and choose a name and group for the screen. Codes expire after about 10 minutes and are
   replaced on the screen automatically.
5. **Waiting / playing.** No reboot is needed. The screen moves to *Waiting for content*, or
   starts playing straight away if its group already has a published video.

If ethernet is connected, the Pi skips wifi setup. Wifi setup also comes back on its own:

- **Network lost while playing:** the screen keeps playing for `offline_timeout` (10 min).
  After that it shows the wifi-setup screen.
- **Network lost while pairing or waiting:** the wifi-setup screen appears after 30 s.
- **While the hotspot is up:** every `wifi_retry` (2 min) the Pi drops the hotspot briefly
  and retries its saved networks. When a network (or ethernet) is back, it returns to normal.

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
| `offline_timeout` | `600` | Network lost while playing: keep playing this many seconds, then show wifi setup. |
| `wifi_retry` | `120` | While the hotspot is up: retry saved wifi networks every N seconds. The retry is postponed while someone is using the setup page. |
| `ntp_server` | `""` | chrony time source. Empty = the distro's internet pool. See [section 5](#5-time-sync-ntp). |
| `logo` | `"/etc/pi-player/logo.png"` | Optional PNG shown top-left on the setup screens (max 360x140). If the file is missing, no logo is shown. |
| `state_dir` | `"/var/lib/pi-player"` | Token, `current.mp4`, `next.mp4`, `state.json`. |
| `video` | `""` | Video to play. Empty = `<state_dir>/current.mp4`. |
| `epoch` | `0` | Sync reference: frame 0 plays at `epoch + k*duration` (Unix seconds). The manifest's `epoch` overrides it. |
| `display_mode` | `"1920x1080@60"` | HDMI mode, set on the kernel cmdline by `install.sh` (re-run + reboot after changing). Smoothest when the refresh is a multiple of the fps: `@60` for 30 fps, `@50` for 25 fps. Many PC monitors reject `@50` ("Out of range"); TVs accept both. Empty = leave the cmdline alone. |
| `video_sink` | `"kmssink"` | GStreamer sink, plus properties (e.g. `"kmssink connector-id=32"` with two HDMI outputs). Dev machine: `"autovideosink"`. |
| `rotate_method` | `"gl"` | Screens hung upright (manifest `rotation` 90/270): `"gl"` turns the video on the GPU (`glupload ! glcolorconvert ! glvideoflip` into `gl_sink`), `"cpu"` with `videoflip`. GL that is missing or fails to start falls back to `"cpu"` by itself. The Pi 4 display itself cannot turn a plane 90 degrees (vc4 only flips), so KMS rotation is not an option. |
| `gl_sink` | `"glimagesink"` | Sink for GPU-turned video (GBM/KMS on the console; the agent sets `GST_GL_WINDOW=gbm` when there is no X/Wayland). |
| `rotation` | `0` | Only without content updates (dev, `--video`): how the panel hangs. Otherwise the CMS decides via the manifest. |
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

## 8. Hardware test plan

**Wifi setup** (phone needed; test iPhone and Android):

1. Run `sudo ./install.sh` on a Pi with ethernet. Unplug the ethernet and make sure there is
   no saved wifi (`sudo pi-player reset`).
2. Within about 30 s the screen shows *Set up wifi* with `Narrowcast-XXXX`, a password and a
   QR code.
3. Scan the QR code with an iPhone and with an Android phone. The captive sheet or
   notification should open the page. If it doesn't, browse to http://10.42.0.1.
4. Pick the network and enter a wrong password first. Within about 1 min (after rejoining),
   the screen and the page should both show *Wrong password?*.
5. Enter the correct password. The screen should show the pairing code.
6. Turn that wifi AP off. The setup screen should come back after 30 s (or 10 min if
   playing). Turn the AP back on: the Pi should rejoin within `wifi_retry` (2 min).
7. Plug in ethernet while the setup screen is showing. It should leave setup within about 5 s.

In `journalctl -u pi-player -f`, look for `hotspot ... up`, `portal on` and `wifi: connected`.

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
5. Unplug the network for less than 10 min: playback continues, and after reconnecting the
   heartbeat resumes and any pending update is applied. Unplug for more than 10 min: the
   wifi-setup screen appears. Restore the network: playback resumes.

**Rotated screen** (CMS "Stand van het scherm" 90 or 270; mock: `curl -X POST 'http://localhost:8080/admin/rotate?rotation=90'`):

1. Hang the screen turned clockwise (or set `rotation=90` on the mock with the screen flat) and
   publish a portrait video (`&orientation=portrait`). Within a few seconds the video restarts
   upright, without downloading again; the setup, waiting and identify screens are upright too.
2. `journalctl -u pi-player -f` shows `rotation 0 -> 90` and `playing ... rotation 90`. A line
   `GPU rotation failed ... turning on the CPU` means GL did not start (check `gstreamer1.0-gl`).
3. Measure: the once-a-minute `frames this loop` line should show no growing `dropped`, and
   `top` should leave headroom for the agent. Film it like the sync test: still within 1 frame
   of a landscape neighbour in the same group. If GL stutters, try `rotate_method = "cpu"`.

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
- **Captive page doesn't open on the phone:** browse to **http://10.42.0.1** by hand. Some
  phones only show a notification, and some stay on mobile data. Turn mobile data off.
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
