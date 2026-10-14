# Acceptance checklist — pi-player

Code-complete; these need real Pis, screens and a phone. Detailed steps also in `README.md` §7–§8.

Setup for all items: on your machine `scripts/make_test_video.sh media/test.mp4 25 60` and
`python3 mock_cms.py --port 8080 --media media`. On each Pi set `cms_url = "http://<your-machine>:8080"`
in `/etc/pi-player/config.toml`. Watch logs with `journalctl -u pi-player -f`.

- [ ] **1. install.sh on a real Pi (fresh Pi OS Lite 64-bit Bookworm)**
  `sudo ./install.sh`, then run it again. Expected: both runs exit 0; second run changes nothing
  (no new cmdline tokens, `/boot/firmware/cmdline.txt` still one line, backup `cmdline.txt.pi-player.bak` exists);
  `systemctl status pi-player` active; `systemd-analyze verify /etc/systemd/system/pi-player.service` clean;
  with `ntp_server` set, `/etc/chrony/conf.d/pi-player.conf` exists and `chronyc sources` shows it.

- [ ] **2. Wifi setup via phone** `[user-gated]`
  Ethernet unplugged, `sudo pi-player reset`. Expected: within ~30 s screen shows "Set up wifi",
  `Narrowcast-XXXX`, password, QR. Join from iPhone and Android → captive page opens (else http://10.42.0.1).
  Wrong password → "Wrong password?" on screen + page. Correct password → pairing screen.
  Logs: `hotspot ... up`, `portal on`, `wifi: connected`.

- [ ] **3. Pairing without reboot** `[user-gated]`
  Open `http://<your-machine>:8080/pair` (or scan QR), enter code + name + group.
  Expected: screen goes to WAITING (or PLAYING if something is published) with no reboot.

- [ ] **4. Clean boot playback** `[user-gated]`
  Publish (`curl -X POST 'http://localhost:8080/admin/publish?file=test.mp4&delay=10'`), then `sudo reboot`.
  Expected: video fullscreen, muted, looping; no console text, no cursor at any point after boot.

- [ ] **5. Restart within 10 s**
  `sudo pkill mpv`, later `sudo systemctl kill pi-player`. Expected: playback back within 10 s each time, same state.

- [ ] **6. 3-Pi sync for 1 hour** `[user-gated]`
  3 Pis same group, test video published. After ~1 min drift log lines near 0 ms.
  Film all screens (high shutter or 120/240 fps slow-mo); frame counters equal or ±1 (≤ 40 ms @ 25 fps). Repeat after 1 h.
  Tune `config.toml` drift thresholds if needed.

- [ ] **7. Synchronized version switch** `[user-gated]`
  Publish a new file with `delay=60`. Expected: all 3 screens switch at the same moment.
  Also `&bad_sha=1`: screens keep old video, mock logs `download_failed`.

- [ ] **8. Offline policy** `[user-gated]`
  Unplug network < 10 min → playback continues; on reconnect heartbeat resumes in mock log and a
  pending publish is applied. Unplug > 10 min → wifi-setup screen; restore network → playback resumes.

## Open flags to keep in mind
- Placeholder choices (copy, colours, timings, pairing code format, heartbeat 30 s, wifi retry 2 min, country default NL) — see `PROGRESS.md`.
- Unsettled: HDMI refresh vs video fps (force 1080p50/60 for 25/30 fps), hotspot retry interval.
