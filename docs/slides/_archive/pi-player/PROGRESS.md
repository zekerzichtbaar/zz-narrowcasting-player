# Progress — pi-player

## Current state
Slices done: 1 (foundation), 2 (content updates), 3 (WebSocket link), 4 (pairing + state machine), 5 (wifi setup), 6 (polish + docs). Definition of Done met for all non-`[user-gated]` items; task ready for `/complete`.

Invariants in force:
- Python 3.11 stdlib + apt only (python3-websockets, python3-pil, python3-qrcode, fonts-dejavu-core, dnsmasq-base, chrony, mpv). Pillow 9.4 / qrcode 7.4 compatible (QR from `get_matrix()`). websockets 10.4 legacy + >= 14 API.
- One agent process owns one mpv; setup screens are PNGs loadfile'd into the same mpv. mpv death -> exit 1 -> systemd restarts (`Restart=always`, `RestartSec=2`, `StartLimitIntervalSec=0`).
- Main loop evaluates `state.decide(...)` every ~1 s; `netman.Monitor` thread decides online; `WifiSetup` thread owns hotspot/portal; offline policy 600 s playing / 30 s grace otherwise.
- Everything we create in NM is `pi-player-*`; `pi-player reset` deletes them (+ token, state.json, videos).
- install.sh idempotent: config.toml written only once; chrony drop-in `/etc/chrony/conf.d/pi-player.conf` from `ntp_server` (empty -> removed); kernel cmdline tokens `consoleblank=0 vt.global_cursor_default=0 quiet loglevel=3 logo.nologo` appended only if their key is missing (backup `cmdline.txt.pi-player.bak`, reboot hint). Helpers `apply_ntp` / `apply_cmdline` testable via `PI_PLAYER_INSTALL_LIB=1 source ./install.sh`.
- `config/config.toml` keys == `config.DEFAULTS` keys/values (README table mirrors them).
- Never commit/push (user owns VCS). Repo has no commits yet.

Key files:
- `README.md` — install, setup walkthrough, reset, config reference, NTP, export spec, mock/testing, hardware test plan, troubleshooting.
- `docs/protocol.md` — the CMS contract. `mock_cms.py` — mock CMS.
- `pi_player/__main__.py` (CLI run|reset, Agent), `state.py`, `netman.py`, `portal.py`, `screens.py`, `pairing.py`, `content.py`, `ws.py`, `mpv.py`, `sync.py`, `config.py`.
- `install.sh`, `systemd/pi-player.service`, `config/config.toml`, `scripts/make_test_video.sh`.

Self-checks: `for m in sync content ws state pairing netman portal; do python3 -m pi_player.$m; done` + `bash -n install.sh`.

## Slice: 1 — foundation
Shipped:
- `docs/protocol.md` (pairing flow, manifest JSON + field table, MP4 export spec, WS messages both directions, error table).
- `pi_player/` package: agent loop, mpv IPC client, sync math with self-check, config loader.
- `install.sh`, `systemd/pi-player.service`, `config/config.toml`, `scripts/make_test_video.sh`, `.gitignore`.
Key decisions:
- Gain raised 0.1 -> 0.5: P-only controller has steady-state error = skew/gain; with 0.1 a 0.3 % skew sat at ~29 ms, with 0.5 it holds ~20-23 ms.
- `correction()` returns `("seek",)` without a target; caller seeks to `target_position(time.time())` (it owns `now`).
- Drift is measured at the midpoint of the IPC round trip.
- Missing video -> agent idles (mpv idle, black) and polls every second; later slices replace this with the WAITING screen.
- Socket path falls back to `$TMPDIR/pi-player-mpv.sock` when `/run/pi-player` isn't creatable (dev machines).
- Protocol: pairing via polling `register/status` (no WS before token); manifest 204 = no content; WS close 4401 = revoked.
Notes/leftovers:
- install.sh does not yet configure chrony's NTP server from config, nor install python3-pil/python3-qrcode/NetworkManager bits — later slices.
- `pi-player reset` subcommand not implemented yet (wrapper exists, passes args to argparse).
- Console text/cursor hiding on tty1 (kernel cmdline `consoleblank`, `vt.global_cursor_default=0`, etc.) not done — needs hardware check.
- Manifest epoch/duration not wired yet: epoch from config, duration from mpv.
Placeholder choices: pairing code format `XXX-XXX` from an unambiguous alphabet; device_id format `pi-<16 hex>`; heartbeat every 30 s; fallback manifest poll 5 min; WS backoff 1->60 s; test video colors (dark grey bg, white counter, yellow timecode, red sweep bar).
Runtime-unverified: real mpv playback (mpv not installed on dev machine, shellcheck also unavailable). Verified only against a fake mpv IPC server: initial 16 s offset -> seek, then drift held within ~23 ms under a simulated 0.3 % clock skew; mpv kill -> agent exit 1; missing video -> waits. To verify for real: `brew install mpv && scripts/make_test_video.sh /tmp/test.mp4 && python3 -m pi_player --video /tmp/test.mp4` (expect drift log near 0 after a few seconds).

## Slice: 2 — content updates
Shipped:
- `pi_player/content.py`: manifest fetch, verified download with atomic rename, `switch_time` activation logic, state.json, background `Content` thread (start + `manifest_poll` + `poke()`), self-check.
- Agent wiring: `play_synced` uses manifest epoch/duration, sleeps until exactly the switch time, returns for `switch()`; waiting loop also activates first content.
- `mock_cms.py` (HTTP only), `manifest_poll` config knob, one-line protocol addition (first content shows immediately).
Key decisions:
- First content (nothing on screen) is shown immediately, not at `activate_at` — added to protocol.md 3.1.
- Pending manifest survives restart if `next.mp4` exists (trusted as verified); CMS moving `activate_at` for the same version re-times the switch; a newer version supersedes a waiting one; manifest back to the current version drops pending.
- Relative manifest `url` is resolved against `cms_url`; bearer sent only when the URL is on the CMS host.
- If `state_dir` isn't writable (dev without --config), content updates are disabled with a warning instead of crashing.
- 401 is logged only (`Unauthorized`); token deletion/PAIRING belongs to the pairing slice.
Notes/leftovers:
- Failed download is retried on every manifest fetch (300 s poll), no extra backoff. No disk-space check vs `size`.
- No `download_failed` WS message yet (WS slice).
- E2E verified with fake mpv: publish a (first content) -> played at once and seeked to group position; publish b delay 8 s -> activated 6 ms after `activate_at`; bad_sha -> rejected every poll, b kept playing.
Runtime-unverified: real mpv `loadfile` switch latency/visual glitch on Pi (fake mpv only); mpv keeps the old inode after `os.replace`, expected fine on Linux.

## Slice: 3 — WebSocket link
Shipped:
- `pi_player/ws.py`: asyncio client in a daemon thread; `hello` on open, `heartbeat` every 30 s + on state/version/pending change, `download_failed` via queue; `update_available`/`screen_updated` -> `Content.poke()`; `unpaired` / close 4401 / handshake 401 -> delete token. Self-check for backoff, ws_url, heartbeat payload, close/handshake code extraction, network_kind.
- `content.py`: `forget_token()`, `on_fail` hook in `download()`, manifest 401 now deletes the token.
- `__main__.py`: starts `ws.Link`, publishes state (PLAYING/WAITING) + last drift to `ws.STATUS`.
- `mock_cms.py`: WS on the same port (no deps), push on publish, `/admin/unpair`, `/admin/pair`.
Key decisions:
- Unpaired -> keep playing current video (no screen to show yet); pairing slice switches to PAIRING.
- Heartbeat `uptime` = agent process uptime, not system uptime. `drift_ms` is null unless state is PLAYING; first heartbeat after a load may carry the pre-seek drift.
- `network`: ethernet if any wired iface is up, else wifi, else null (not tied to the default route).
- Mock WS is hand-rolled (stdlib) so the mock still has zero deps; one port for HTTP+WS.
- `device_id()` lives in ws.py for now; pairing slice may move it to its own module.
Verified locally (fake mpv): hello + heartbeats logged; publish -> immediate fetch (no poll wait) -> switch at activate_at; bad_sha -> `download_failed` logged by mock; mock killed -> backoff 1, 2, 4, 8.6 s -> reconnect + hello; unpair -> token deleted; token restored while revoked -> handshake 401 -> deleted. Ran with websockets 17.0.1 and 10.4.
Notes/leftovers:
- Pre-existing (slice 2): a newer manifest supersedes a verified pending one even if the newer download then fails -> nothing pending. Acceptable, but note for review.
- install.sh unchanged (python3-websockets already in apt list).
Placeholder choices: backoff jitter ±20 %; no-token recheck every 5 s; ws open_timeout 15 s; mock `/admin/pair` endpoint name.
Runtime-unverified: real Pi (Bookworm python3-websockets 10.4 on Python 3.11, real `/sys/class/net`, real mpv).

## Slice: 4 — pairing + on-screen state machine
Shipped:
- `pi_player/state.py` (pure `decide` + `OfflineTimer` + self-check over every network/token/content combo and the 10-min timer), `pi_player/screens.py` (PAIRING / WAITING / WIFI_SETUP-placeholder PNGs), `pi_player/pairing.py` (register/poll/claim, 0600 atomic token, device_id moved here).
- `__main__.py` restructured into `Agent` loop; `pi-player reset` subcommand (wipes token, state.json, videos; `systemctl try-restart` when root; TODO hook for NM wifi connections).
- `content.py`: `screen` in state.json, `clear()` on manifest 204, tolerant `activate()`, `reset()`. `ws.py`: screen_updated stores screen; session closes when the token file is gone.
- `mock_cms.py`: register/status endpoints, `/pair` page, issued tokens, per-device unpair. `install.sh`: python3-pil python3-qrcode fonts-dejavu-core. Config: `offline_timeout`, `logo`.
Key decisions:
- Manifest 204 deletes local video (otherwise a re-paired screen in an empty group would keep showing old content). Documented in protocol.md 3.1.
- Offline boot with token+video plays for `offline_timeout` too (timer starts at agent start), not only "was playing".
- PAIRING/WAITING offline -> WIFI_SETUP after a 30 s grace (constant `state.OFFLINE_GRACE`), to avoid flicker on blips.
- `reset` deletes videos too (true first-boot), not just the token.
- Pairing errors retry with 1, 2, 4 ... 15 s backoff; re-register 30 s before expiry, but always show a code >= 60 s (clock-skew guard).
Verified locally (fake mpv + mock): no token -> PAIRING png + register; claim via POST /pair -> token 0600 -> WAITING png ("Kantine links") -> publish -> PLAYING; /admin/unpair -> PAIRING png without restart (new code); re-pair -> straight to PLAYING (same version kept); `pi-player reset --config dev` -> token/state/video removed -> PAIRING; unpublish -> video removed -> WAITING; mock restart -> code expired -> re-register; unreachable CMS -> PAIRING -> WIFI_SETUP after 30 s. PNGs eyeballed (pairing, waiting).
Notes/leftovers:
- `network_ok` does a DNS lookup in the main loop every 5 s; a hanging resolver could stall the loop a few seconds. Wifi slice should replace with nmcli/NM state (or move to a thread).
- After a non-root `reset` against a running agent, Content's in-memory state still lists the old current manifest until restart (harmless: a re-fetch re-downloads). Root reset restarts the service.
- Device id on macOS falls back to hostname (no machine-id); fine for dev.
Placeholder choices: screen palette (near-black bg #121418, white text, amber #FFC400 accent, grey #969BA5), copy ("Pair this screen", "Waiting for content", "No network ... wait for wifi setup"), footer `<device_id> · pi-player <version>`, logo path `/etc/pi-player/logo.png` top-left max 360x140, OFFLINE_GRACE 30 s, network probe every 5 s, mock pair page layout and `scr_<hex>` / `dt_<hex>` ids, mock code TTL 600 s.
Runtime-unverified: real mpv showing PNGs on DRM (`--image-display-duration=inf` + loadfile replace between PNG and MP4), Bookworm Pillow 9.4 / qrcode 7.4 rendering (tested with Pillow 11 + qrcode 7.4.2), DejaVu fonts on the Pi, `systemctl try-restart` from `pi-player reset` as root.

## Slice: 5 — wifi setup (hotspot + captive portal)
Shipped:
- `pi_player/netman.py`: `nmcli -t` parsing (escaped colons), online check, scan, hotspot start/stop, connect (WPA2/WPA3-only -> sae/open/hidden), saved-network retry, `forget_ours`; `Monitor` (network probe off the main thread, replaces `state.network_ok`); `WifiSetup` controller thread; self-check on captured sample output.
- `pi_player/portal.py`: stdlib captive portal (redirect everything foreign, network list, hidden-SSID field, password validation 8-63, rescan), self-check incl. a live HTTP round trip.
- `screens.wifi_setup(ssid, password, status, error)`: SSID/password + `WIFI:T:WPA;S:..;P:..;;` QR + 2 steps + `http://10.42.0.1` fallback + red error line; ssid None -> centred status ("Connecting to X…", "Starting wifi setup…") or "No network".
- Agent wiring, `wifi_retry` config knob, `pi-player reset` deletes `pi-player-*` NM connections, install.sh dnsmasq wildcard drop-in + dnsmasq-base + wifi country/rfkill.
Key decisions:
- Explicit `nmcli connection add` + `up` (not `device wifi connect`) so names/settings are deterministic and failures are cleaned up (connection deleted again).
- Portal binds only to 10.42.0.1 and only while the AP is up (never exposed on a LAN).
- A connect/rescan job waits 2 s so the portal's answer reaches the phone before the AP drops.
- Hotspot password regenerates per agent start (not persisted) — physical presence is the authorization anyway.
- After a success the AP stays down 20 s (< OFFLINE_GRACE) so the main loop can notice; reset when WIFI_SETUP is left.
- Retry tries only saved connections whose SSID is in the fresh scan (avoids 25 s waits per absent network).
Verified locally: all self-checks; fake-mpv + mock flow without nmcli (PAIRING -> pair -> WAITING -> PLAYING -> unpair -> PAIRING; unreachable CMS -> PAIRING -> WIFI_SETUP after 30 s); controller test with faked nmcli (portal via probe redirect, wrong password -> error + AP back, retry after idle, saved network back -> AP stays down, hidden SSID connect -> success hold); PNGs and portal page eyeballed.
Notes/leftovers:
- Wifi joined but no internet/CMS -> counts as online -> PAIRING "Connecting to the CMS…" forever (no way back to setup except `pi-player reset` or ethernet). Could use NM connectivity check later; needs hardware thought.
- `ws.network_kind` reads /sys and would call the hotspot "wifi" — irrelevant (no WS in WIFI_SETUP).
- README must document: wifi country default NL (`WIFI_COUNTRY=XX`), hotspot test steps below, portal URL.
- User test steps (hardware, `[user-gated]`): (1) `sudo ./install.sh` on a Pi with ethernet; unplug ethernet, ensure no saved wifi (`sudo pi-player reset`). (2) Within ~30 s the screen shows "Set up wifi" with `Narrowcast-XXXX` + password + QR. (3) Scan the QR with iPhone and with Android; captive sheet/notification should open the page (else browse to http://10.42.0.1). (4) Pick the network, wrong password first -> screen + page show "Wrong password?" within ~1 min after rejoining. (5) Correct password -> screen goes to pairing code. (6) Turn off that wifi AP -> after 30 s grace (or 10 min if playing) setup screen returns; turn it back on -> within `wifi_retry` (2 min) Pi rejoins. (7) Plug ethernet while in setup -> leaves setup within ~5 s. Check `journalctl -u pi-player -f` for `hotspot ... up`, `portal on`, `wifi: connected`.
Placeholder choices: SSID shown big in amber, password alphabet (lowercase+digits without 0/o/1/l/i, 8 chars), error colour #FF6E64, screen + portal copy ("Set up wifi", "Connect this screen to wifi", "(secured)", signal bars), portal dark styling reusing the screen palette, wifi country default NL, HOLD 20 s, connect wait 30 s, retry `con up` wait 25 s, hotspot band bg.
Runtime-unverified: everything nmcli/NM-side (hotspot activation on brcmfmac, WPA2 settings vs iOS/Android, dnsmasq wildcard + captive-portal auto-open, scan-before-AP, `con up` error strings mapped by `friendly_error`, raspi-config `get_wifi_country`), port 80 bind as root on 10.42.0.1, real mpv showing the PNG.

## Slice: 6 — polish + docs
Shipped:
- `README.md` covering every DoD doc item (install over SSH, setup walkthrough, reset, full config reference, NTP/LAN time source, CMS export spec, mock + test video, hardware/sync test plan, troubleshooting).
- `ntp_server` config key (config.toml + DEFAULTS); install.sh writes/removes chrony drop-in and ensures `confdir /etc/chrony/conf.d`; restarts chrony only on change.
- install.sh clean-console cmdline edit (`/boot/firmware/cmdline.txt`, fallback `/boot/cmdline.txt`), one-line, no duplicates, reboot hint.
- Unit: `StartLimitIntervalSec=0`, best-effort `ExecStartPre` setterm cursor off + clear on tty1, comments on restart timing.
Key decisions:
- Cmdline tokens are key-matched: a user's own `loglevel=7` is respected, not overridden. `console=tty1` left untouched (quiet + loglevel=3 suffice; moving console to tty3 felt too invasive).
- `ntp_server` validated against `^[A-Za-z0-9.:-]+$` before being written into chrony config.
- `ntp_server` is applied by install.sh only (agent ignores it) — documented as "re-run install.sh".
Verified: all self-checks exit 0; `bash -n install.sh` 0; `apply_cmdline`/`apply_ntp` run against scratch copies (add once, second run no-op + single line, backup kept, user key respected, ntp write/no-op/invalid/remove); config.toml keys/values == DEFAULTS.
DoD audit: all non-`[user-gated]` items met in code. User-gated acceptance items: wifi-setup via phone/portal; pairing on screen -> WAITING/PLAYING without reboot; after reboot fullscreen muted loop with no console text/cursor; 3 Pis within 1 frame for 1 h; publish -> all switch together; network unplug <10 min / >10 min behaviour.
Notes/leftovers:
- Known limitation (slice 5): wifi joined but no CMS route -> stuck in PAIRING "Connecting to the CMS…" (documented in README troubleshooting).
- HDMI mode vs fps (50/60 Hz) still an open question, documented.
Placeholder choices: README wording/structure; LAN time-server example (`allow 192.168.0.0/16`, `local stratum 10`); cmdline token set; setterm ExecStartPre.
Runtime-unverified: install.sh on a real Pi (apt, chrony restart, real cmdline.txt, systemd unit parse — no systemd-analyze on dev machine), `setterm` on tty1, kernel console actually hidden after reboot, restart-within-10 s on hardware.
