> Completed (pending acceptance: 8 items, see ACCEPTANCE.md)

# Pi Player — narrowcasting screen client

## Goal
A player that runs on a Raspberry Pi 4 behind a screen and plays the single MP4 exported by our narrowcasting CMS, looping forever. Multiple screens in one space (e.g. 3 screens in a canteen) must play **frame-accurately in sync**. When the CMS publishes a new MP4, every Pi downloads it and all screens in a group switch to it at the same moment. Installed over SSH with one command; first-time setup (wifi + pairing with the CMS) happens **on the screen itself**, no SSH or keyboard needed.

## Scope
- `install.sh`: run over SSH on a fresh Pi OS Lite → installs deps, config, systemd service; idempotent (re-run = upgrade).
- Player agent (Python 3, runs as systemd service) driving `mpv` via its JSON IPC socket.
- Fullscreen playback on HDMI via DRM/KMS (no desktop), muted, no cursor/OSD, auto-restart on crash, starts on boot.
- **On-screen state machine** — the agent always shows exactly one of:
  1. `WIFI_SETUP` — no network: hotspot active; screen shows SSID, password, QR to join.
  2. `PAIRING` — network but no token: screen shows short pairing code + QR to CMS pairing page (code prefilled).
  3. `WAITING` — paired, no content yet: "Waiting for content" + screen name.
  4. `PLAYING` — the synced MP4.
- **Wifi setup**: NetworkManager hotspot (`nmcli`) + captive portal (stdlib `http.server`): phone joins hotspot, portal auto-opens, pick network from list, enter password, Pi connects. Ethernet connected → skip wifi setup.
- **Offline policy**: playing and network lost → keep playing for 10 min (configurable); still offline → stop video, go to `WIFI_SETUP`. While in hotspot mode, periodically retry the known network(s); network back (wifi or ethernet) → resume.
- **Pairing/registration**: Pi generates a stable device ID, calls CMS register endpoint, gets short code (expires, refreshes on screen). Admin enters code in CMS + picks group/name → Pi receives device token, stores it (root-only, 0600). Token revoked/unpaired in CMS → back to `PAIRING`.
- Setup screens rendered as PNG (Pillow + `python3-qrcode`) and shown by the same mpv instance — one display pipeline for everything.
- Clock-based sync, no master: `chrony` keeps clocks aligned; each Pi computes `position = (now - epoch) mod duration` and corrects mpv (small speed nudge for small drift, seek for large drift).
- Updates: WebSocket from CMS as "new version" signal + HTTP manifest fetch (on connect, on signal, and slow fallback poll). Download to temp → sha256 verify → atomic rename → switch at `activate_at` so all screens in a group switch together.
- Status back to CMS over the same WebSocket: online heartbeat, state, current version, measured drift.
- Protocol spec (register/pairing, manifest JSON, WS messages) documented in this repo, plus a tiny mock CMS server (incl. a pairing page) for local testing.
- Local reset: `sudo pi-player reset` wipes token + wifi credentials → back to first-boot flow.

## Non-goals
- Building the real CMS endpoints/UI (that lives in the CMS repo — we deliver the spec + mock only).
- Audio (always muted for now).
- Playlists / multiple files on the Pi — the CMS always renders exactly one MP4.
- Video walls (one image spanning screens / bezel compensation) — each screen plays the full video.
- Pi 5 / other hardware support, remote shell/fleet management, OTA updating of the agent itself beyond re-running `install.sh`.
- Enterprise wifi (WPA2-Enterprise/802.1X), static IP config, proxies via the portal — SSH can handle those edge cases.
- Branding/theming of setup screens beyond a clean default (logo file swappable is enough).

## Key decisions & constraints
- Hardware: Raspberry Pi 4, Raspberry Pi OS Lite 64-bit (Bookworm, NetworkManager default). H.264 hardware decode → CMS export target: **1080p H.264, constant frame rate (25 or 30 fps), keyframe at frame 0**. Document this for the CMS export.
- Player: `mpv` with `--vo=gpu --gpu-context=drm` (or `--vo=drm`), `--hwdec=auto`, `--loop-file=inf`, `--mute`, `--input-ipc-server`, `--image-display-duration=inf` for setup screens.
- Agent: Python 3 stdlib + apt packages only (`python3-websockets`, `python3-pil`, `python3-qrcode`). No pip, no venv.
- Wifi: plain `nmcli` (hotspot + connect) + own small captive portal; NetworkManager's shared-mode dnsmasq with a wildcard DNS entry for captive-portal detection. Pi 4 radio can't reliably do AP + client at once → **scan networks before starting the hotspot** (cache list; rescan button drops AP briefly).
- Hotspot: WPA2 with random password shown on screen (physical presence = authorization). SSID `Narrowcast-<last 4 of device ID>`.
- Pairing: code is short-lived (e.g. 10 min) and single-use; device token never shown on screen. Device ID stable across reinstalls (derived from Pi serial).
- Sync: no leader. Shared reference = `epoch` (from manifest/group, or fixed Unix epoch 0) + video duration. Clock via `chrony`; configurable NTP server so one LAN box/Pi can be the time source for sub-ms LAN alignment. Target: drift < 1 frame (≤ 40 ms) between screens after settling.
- Drift correction: |drift| < ~1 frame → do nothing; small → `speed` 0.98–1.02 until converged; large (> ~0.5 s) → hard seek. Thresholds are config knobs (real hardware needs tuning).
- Update activation: manifest carries `activate_at` (Unix time). Pi that hasn't finished download by then keeps old video and switches at next aligned loop boundary once ready.
- Config: `/etc/pi-player/config.toml` (CMS URL, NTP server, thresholds, offline timeout) — `tomllib` is stdlib. Secrets/state separate: `/var/lib/pi-player/` holds `token` (0600), `current.mp4`, `next.mp4`, `state.json`.
- Keep it lazy: one agent process (portal runs inside it), few files, no frameworks. State machine logic as pure functions with self-checks.

## Preconditions & external dependencies
- 2–3 Raspberry Pi 4s + HDMI screens for real sync testing (side by side). `[user]`
- Pi OS Lite 64-bit Bookworm flashed, SSH enabled, on the network for install. `[user]`
- A phone to test the hotspot/captive portal (iOS + Android ideally — captive-portal behaviour differs). `[user]`
- A test MP4 per spec (1080p H.264 CFR) — we generate one with ffmpeg incl. visible frame counter.
- CMS team to implement register/pairing endpoints + pairing UI + WS + manifest endpoints per our spec. Until then, the mock server is used.
- NTP reachable from the Pis (internet pool or a LAN time server).

## Building blocks
- `install.sh` + systemd unit + default config + `pi-player reset` command
- mpv launcher/IPC client
- State machine (WIFI_SETUP / PAIRING / WAITING / PLAYING) + offline timer
- Screen renderer (Pillow + qrcode → PNG)
- Network manager wrapper (`nmcli`: status, scan, hotspot, connect) + captive portal
- Pairing client (device ID, register, poll/WS for claim, token storage)
- Sync controller (clock math + correction loop)
- Content manager (manifest fetch, download, verify, atomic swap, activation scheduling)
- WebSocket client (signal in, heartbeat/status out, reconnect with backoff)
- Protocol spec doc + mock CMS server (with pairing page)
- Test video generator (ffmpeg with frame counter)
- Self-checks for sync math, state transitions, manifest/activation logic

## Definition of Done
- [x] `docs/protocol.md` defines register/pairing flow, manifest JSON and all WS message types (both directions) with examples.
- [x] Mock CMS server runs locally (`python3 mock_cms.py`): register/pair page, serves manifest + MP4, can push "new version" and "unpaired" WS messages.
- [x] `install.sh` on fresh Pi OS Lite installs everything idempotently; re-running it succeeds without breaking anything. _(verified by reading + helper tests; real-Pi run in ACCEPTANCE.md)_
- [x] State-machine self-check passes: correct state for every (network, token, content) combo; offline > timeout while playing → `WIFI_SETUP`; network back → resume.
- [ ] Fresh Pi without network shows wifi-setup screen with SSID/password/QR; phone joins, portal opens, Pi joins chosen wifi. `[user-gated]`
- [ ] Pi with network but no token shows pairing code + QR; entering code in mock CMS → screen goes to `WAITING`/`PLAYING` without reboot. `[user-gated]`
- [x] Unpair in mock → Pi returns to pairing screen; `sudo pi-player reset` → back to first-boot flow.
- [ ] After reboot a paired Pi plays the MP4 fullscreen, muted, looping, no console text/cursor visible. `[user-gated]`
- [x] Killing mpv or the agent → systemd restarts within 10 s and resumes the right state. _(verified by reading the unit + exit path; hardware timing in ACCEPTANCE.md)_
- [x] Sync-math self-check passes: correct target position across loop wrap, correct speed/seek decision per threshold.
- [x] Content self-check passes: sha256 mismatch rejected, atomic swap, activation only at/after `activate_at`, failed download keeps old file playing.
- [ ] 3 Pis side by side stay within 1 frame of each other for 1 hour (verified by filming the frame counter). `[user-gated]`
- [ ] Publishing a new version on the mock → all 3 screens switch to the new MP4 at the same moment. `[user-gated]`
- [ ] Network unplugged < 10 min → playback continues, reconnect picks up heartbeat + pending update. > 10 min → wifi-setup screen; restoring network → playback resumes. `[user-gated]`
- [x] Agent sends heartbeat with state + version + drift over WS; mock server logs it.
- [x] README: install over SSH, first-time setup walkthrough, config reference, CMS export spec (codec/fps/keyframe), troubleshooting.

## Open questions
- CMS stack/hosting for WS + pairing endpoints (affects auth style only; spec stays transport-generic: bearer token).
- Group `epoch`: CMS-provided per group vs fixed Unix epoch 0 — default fixed 0 unless CMS needs otherwise.
- Display refresh rate vs video fps matching (force HDMI mode 1080p50/60 to match 25/30 fps) — settle during hardware testing.
- How often to retry known wifi while in hotspot mode (each retry drops the AP briefly) — default every 2 min, tune on hardware.
