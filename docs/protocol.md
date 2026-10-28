# Pi Player <-> CMS protocol

Version: 3. Manifest and heartbeat over plain HTTPS (polling); a WebSocket "bel" (section 6)
only signals "fetch the manifest now". All bodies are JSON (UTF-8).
Times are Unix seconds (float allowed). Authenticated calls send `Authorization: Bearer <device_token>`.

`<CMS>` below is the `cms_url` from `/etc/pi-player/config.toml`, e.g. `https://cms.example.com`.

## 1. Identity

- **device_id**: stable per Pi, derived from the CPU serial (`/proc/cpuinfo` `Serial`), formatted
  `pi-<16 hex>`. Survives reinstalls. Not a secret.
- **device_token**: opaque secret issued by the CMS when an admin claims the device. Stored at
  `/var/lib/pi-player/token` (0600, root). Never shown on screen, never logged.
- **poll_secret**: random secret the CMS hands out with each pairing code (2.1). Only kept in
  memory; it proves to the status poll (2.2) that the caller is the Pi that registered the code
  (the code and the device_id are visible or guessable, the secret is not). The CMS stores only its
  sha256.

## 2. Register / pairing flow

```
Pi                                   CMS                               Admin (browser)
 |-- POST /api/devices/register ----->|                                      |
 |<- 200 {code, expires_at, ...} -----|                                      |
 |   (screen shows code + QR to pair_url)                                    |
 |                                    |<---- opens pair_url, enters code ----|
 |                                    |<---- picks group + screen name ------|
 |-- GET /api/devices/register/status (poll every ~3 s) ->|                  |
 |<- 200 {status:"claimed", device_token, ...} -----------|                  |
 |   (store token, start manifest poll + heartbeat)                          |
```

### 2.1 `POST <CMS>/api/devices/register`

Unauthenticated. Called when the Pi has network but no token, and again whenever the current code
is about to expire (the screen refreshes the code).

Request:
```json
{
  "device_id": "pi-10000000a1b2c3d4",
  "agent_version": "v1.2.3",
  "hostname": "kantine-1",
  "resolution": "1920x1080"
}
```

Response `200`:
```json
{
  "code": "K7Q-4M2",
  "expires_at": 1760437200,
  "pair_url": "https://cms.example.com/schermen?code=K7Q-4M2",
  "poll_interval": 3,
  "poll_secret": "Xo3...64 chars"
}
```

Rules:
- `code`: short, human-typable (6 chars from an unambiguous alphabet, e.g. no `0/O/1/I`), shown with
  a dash for readability. Single-use, short-lived (Zeker Zichtbaar CMS: 15 min). The admin may type
  it with or without the dash.
- A new register call for the same `device_id` invalidates any previous unclaimed code.
- `pair_url` has the code prefilled; the Pi renders it as a QR code.
- `poll_secret`: belongs to this code only; a new register gives a new one. Never shown or logged.

### 2.2 `GET <CMS>/api/devices/register/status?device_id=<id>&code=<code>`

Sends `Authorization: Bearer <poll_secret>` (the secret of this code, from 2.1; not in the URL so
it stays out of access logs). Polled every `poll_interval` seconds while the pairing screen is
shown. A missing or wrong secret answers `expired`, like an unknown code.

Responses `200`:
```json
{ "status": "pending" }
```
```json
{ "status": "expired" }
```
```json
{
  "status": "claimed",
  "device_token": "dt_9f2c...e1",
  "screen": { "id": "scr_42", "name": "Kantine links", "group_id": "grp_7" }
}
```

- `pending`: keep polling.
- `expired`: call register again for a new code.
- `claimed`: the token is returned **exactly once**; the CMS then marks the code used (a later
  poll answers `expired`). The Pi stores the token and switches to normal operation. If that one
  answer is lost, the admin pairs the screen again. Once the screen has been paired again with
  another code, an older claimed code never hands out a token any more (`expired`).

### 2.3 Unpairing

The admin removes/unpairs the screen in the CMS. The CMS revokes the token: every authenticated
call (manifest, heartbeat) then returns `401`. The Pi deletes its token and goes back to
`PAIRING` at the next manifest poll or heartbeat, so within about 30 s.

## 3. Manifest

### 3.1 `GET <CMS>/api/devices/manifest`

Authenticated. Polled every `manifest_poll` seconds (default 30), right after pairing, and right
after every bel (section 6). Without the bel a new version, a new group moment or an unpublish
reaches the Pi within one poll; with it within about a second.

Response `200`:
```json
{
  "version": "2026-10-14T09:30:00Z-3f1a",
  "url": "https://cdn.example.com/exports/grp_7/3f1a.mp4",
  "sha256": "9b74c9897bac770ffc029102a200c5de1b3f1e7e8f2c...",
  "size": 48213340,
  "duration": 60.0,
  "epoch": 0,
  "activate_at": 1760437500,
  "identify_until": 0,
  "orientation": "landscape",
  "rotation": 0,
  "update": null,
  "wifi_setup": null,
  "screen": { "id": "scr_42", "name": "Kantine links", "group_id": "grp_7" }
}
```

Response `200` with `"version": null` (or `204` from an older CMS): paired but no content (yet):
no playlist, nothing published, or no version of the playlist ever got a video. Pi shows `WAITING`;
a video it had (e.g. from before an unpair/re-pair, or after the CMS unpublished) is deleted. The
body still carries `rotation` (an idle portrait screen turns its own screens), `identify_until`
(show the name while the screen is identified, `0` = not), `update`, `wifi_setup` and `screen`:
`{"version": null, "identify_until": 0, "rotation": 90, "update": null, "wifi_setup": null, "screen": {...}}`.
A newly published version whose video is still being made (or failed) is **not** this: the CMS
keeps serving the previous video, with the same `version` as before, so the Pi does not even
switch. Once the new video is ready, `version` changes.
Response `401`: token revoked (Pi goes to `PAIRING`).

Fields:
| field | type | meaning |
|---|---|---|
| `version` | string | Opaque. Pi compares for equality only. Changes whenever the Pi must react: new file, new `activate_at`/`epoch` (e.g. the group moment). A new `version` with the same `sha256` as the file on screen or waiting is **not** downloaded again. |
| `url` | string | MP4 download URL. May be pre-signed; if it is on the CMS host the Pi sends the bearer token. |
| `sha256` | string | Lowercase hex of the whole file. A mismatch means the download is discarded. |
| `size` | int | Bytes (optional, for progress/space checks). |
| `duration` | float | Exact video duration in seconds (frames / fps). Used for sync math. |
| `epoch` | float | **Ignored by the Pi since v0.8.6**: every screen uses epoch `0` (frame 0 at `k * duration` since 1970), so screens with the same duration line up whatever the CMS sends. Kept for older players. |
| `activate_at` | float | Unix time from which this version may be shown. All screens in a group get the same value. `0` = right away (at the next loop boundary when something already plays). |
| `identify_until` | int | "Scherm identificeren" in the CMS: show `screen.name` big, full screen, instead of the video or setup screen until this Unix time (the video re-joins the group clock afterwards). `0` = not identifying. **Not** part of `version`: it never makes the Pi download or switch, and the Pi acts on it also when `version` is unchanged. Arrives within one poll; the CMS keeps it for 60 s. |
| `orientation` | string | `landscape` or `portrait`: the format of the playlist. Informational; the Pi plays whatever size the file has (fitted, black bars). A portrait playlist on a panel at `rotation` 90/270 comes as a 1920x1080 file with the picture already turned (see `rotation`). |
| `rotation` | int | How the panel physically hangs: `0`, `90` or `270` degrees turned **clockwise** (CMS "Stand van het scherm"). The Pi only turns its own setup/waiting/wifi/identify screens with it (picture turned back the other way: 90 -> 90 degrees counter-clockwise) and keeps the value in `state.json` for restarts without network. The Pi **never turns the video** (too heavy on a Pi 4): for a portrait playlist on a panel at 90/270 the CMS serves a variant of the render with the picture already turned that way (`url`, `sha256`, `size` and `version` are the variant's), a plain 1920x1080 file that plays zero-copy like a landscape one. A rotation change therefore reaches the video as a new `version` (a different file); the field itself is **not** part of `version`. Also in the `version: null` body. Missing (older CMS) = keep the last one. |
| `update` | object \| null | "Speler bijwerken" in the CMS (Zeker Zichtbaar only): `{"version": "v1.3.0", "url": "https://github.com/zekerzichtbaar/zz-narrowcasting-player/archive/refs/tags/v1.3.0.tar.gz", "attempt": 1760437213}`, the release this player should install, or `null`. `attempt` (optional) is unique per request in the CMS (a new one on every "Speler bijwerken" / "Opnieuw proberen", e.g. the request time in Unix seconds): a positive int or a short token (`[A-Za-z0-9][A-Za-z0-9._+-]{0,63}`); the Pi compares it as a string for equality only and ignores an invalid one (as if missing). A failed update is retried only when `attempt` changes (section 7). Missing `attempt` (older CMS) = a failed version is never tried again. Also in the `version: null` body. **Not** part of `version`: an update never changes the content or the group clock. The CMS keeps serving it until a heartbeat reports `agent_version` equal to `update.version`, then it is `null` again (also when `agent_version` already equals it). `url` is GitHub's tarball of the tag (https, no own signing); the tarball has no git, so the updater passes `PI_PLAYER_VERSION=<update.version>` to `install.sh`. A player without the updater ignores the field (unknown fields are ignored) and keeps playing. Missing (older CMS) = no update. What the Pi does with it: section 7. |
| `wifi_setup` | int \| null | "Wifi opnieuw instellen" in the CMS (Zeker Zichtbaar only): the request time (Unix s, new per click), served for 5 minutes, else `null`. A new value opens the wifi-setup screen with its countdown (`offline_timeout`), also while the wifi works; it ends early only once a network was set up from the phone, otherwise the Pi plays on over its old wifi after the countdown. Also in the `version: null` body. **Not** part of `version`. The Pi remembers the last value it acted on in memory (an agent restart within the 5 minutes opens the setup once more). Missing (older CMS) or not an int = not asked. |
| `screen` | object | Name/group for display on the `WAITING` screen and for identifying. |

Sync math on the Pi: `position = now mod duration` (epoch 0; the manifest's `epoch` is ignored, a CMS once
gave two screens of one group different epochs).

Activation: the Pi downloads to `next.mp4`, verifies `sha256`, then switches at `activate_at`.
If the download finishes after `activate_at`, the Pi keeps playing the old video and switches at
the next loop boundary of the new video (`now` where `now mod duration ~ 0`).
If nothing is on screen yet (first content after pairing), the Pi shows a verified download
immediately instead of waiting for `activate_at`; sync math still puts it at the group position.
The CMS should set `activate_at` far enough ahead for all screens to download
(e.g. now + 2 min, or later for big files).

Waiting for the group: the Zeker Zichtbaar CMS only sets a group moment once every online screen
of the group has the version stored (reported via `pending_version` in the heartbeat). Until then
it serves `activate_at = 4102444800` (2100-01-01) and `epoch = 0`: the Pi downloads and verifies,
reports it as `pending_version` and keeps playing the old video (or shows the new one right away
when nothing plays). The group moment then arrives as a new `version` with the same `sha256`,
`activate_at = epoch = moment`; the Pi reuses the file it has and switches exactly at the moment.

### 3.2 MP4 export requirements (CMS side)

- Container MP4, video H.264 (High profile, level <= 4.2), 1920x1080 (landscape) or 1080x1920
  (portrait playlist), yuv420p. For a portrait playlist on a panel at 90/270: 1920x1080 with the
  picture turned (ffmpeg `transpose=2` for 90, `transpose=1` for 270), the Pi plays it as is.
- **Constant frame rate**, 25 or 30 fps.
- Keyframe at frame 0 (and preferably every 1-2 s so seeks are cheap).
- No audio needed (the Pi is muted); an audio track is ignored.

## 4. Heartbeat

### 4.1 `POST <CMS>/api/devices/heartbeat`

Authenticated. Sent every 30 s while the Pi has a token, and right away when `state`, `version`,
`pending_version` or `update` changes (so a finished download or a failed update is reported within a second).

Request:
```json
{
  "device_id": "pi-10000000a1b2c3d4",
  "agent_version": "v1.2.3",
  "time": 1760437213.42,
  "state": "PLAYING",
  "version": "12-0-9b74c9897bac",
  "pending_version": "13-4102444800-3f1a0c55e2d1",
  "drift_ms": 3.1,
  "uptime": 86400,
  "ip": "192.168.1.42",
  "network": "wifi",
  "storage": {"free_bytes": 24800000000, "used_bytes": 21300000},
  "update": {"version": "v1.3.0", "status": "failed", "reason": "install: install.sh exited 3", "attempt": "1760437213"}
}
```

Response `204`. `401` = token revoked (Pi goes to `PAIRING`). Other errors are logged and the
next heartbeat simply follows 30 s later.

| field | meaning |
|---|---|
| `state` | `WAITING` \| `PLAYING` (the other states have no token or no network) |
| `version` | version currently on screen, `null` if none |
| `pending_version` | verified download waiting for `activate_at`, `null` if none |
| `drift_ms` | last measured playback position minus target position, in ms (`null` when not playing) |
| `network` | `ethernet` \| `wifi` (`null` if neither is up) |
| `agent_version` | player release: the git tag, `v` included (`v1.2.3`); `dev` or `dev-<git describe>` for a build that is not a tagged release |
| `ip` | local IPv4 address the Pi routes to the CMS from (`null` if unknown) |
| `storage` | `{"free_bytes", "used_bytes"}`: free space on the disk of the state dir and the size of the files the player keeps there (current + next mp4), for "Opslag" and "past" in the CMS (playlist <= free + used). Left out when the disk can't be read. The CMS keeps the last value when it is missing, clears it on an explicit `null` and ignores an invalid one. |
| `update` | the last update this Pi tried, from `update.json` (section 7): `{"version", "status": "running"\|"done"\|"failed", "reason", "attempt"}`. `reason` (max 500 chars, English, e.g. `download: ...`, `install: install.sh exited 3`, `no heartbeat within 300 s`, `start: ...`) only with `failed`, else `null`; `attempt` = the manifest's `update.attempt` that started it, as a **string** (`null` when the CMS sent none). Left out when the Pi never tried an update. A failure belongs to the CMS's current request only when `version` and `attempt` both match it; an older one is history. |

The CMS uses it as "online" (last seen) and as the stored version for group activation:
`pending_version` if set, else `version`. It also stores `agent_version`, `ip` and `network` to show
in the beheer (an invalid value is ignored, never an error), and clears an asked update (manifest
`update`) once `agent_version` equals its `version`; `drift_ms` and `uptime` are diagnostics
the CMS may ignore. Unknown fields are ignored. An older player sends no `update` field: the CMS then
knows nothing about the outcome (no error) and only sees `agent_version`.

`agent_version` comes from `pi_player/VERSION`, written by `install.sh`: `PI_PLAYER_VERSION` (the
image build passes its `VERSION`, the release tag on a tag push), else a `VERSION` file already in
the source, else the exact `v*` tag of a git checkout, else `dev-<git describe>`, else `dev`.

## 5. Error handling summary

| situation | Pi behaviour |
|---|---|
| HTTP `401` (manifest or heartbeat) | delete token -> `PAIRING` |
| manifest `204` | `WAITING` |
| network errors | keep current state/video; retry with backoff; with token + content never `WIFI_SETUP`, except for `offline_timeout` after a start without any NetworkManager connection (countdown, then playing) |
| "offline" | NetworkManager not connected (hotspot excluded), or `GET <CMS>/up` gets no HTTP answer (DNS/connect/TLS/timeout; any status code counts as reachable) twice in a row. Probed every 30 s while it answers. A wifi without internet is offline. |
| sha256 mismatch / download error | discard temp file, keep old video, retry on next manifest fetch |

## 6. Bel (WebSocket signal)

Optional. The CMS rings the screen when something changed (publish, a new group moment, a ready
video, unpair, "Scherm identificeren"), so the Pi does not wait for the next 30 s poll. It is only a
signal: on every bel the Pi fetches the manifest (3.1) over HTTP, which stays the single source of
truth. Without a bel (no Reverb on the CMS, Reverb down, a firewall) everything works on the poll.
The Zeker Zichtbaar CMS uses Laravel Reverb, which speaks the Pusher protocol (version 7).

### 6.1 `GET <CMS>/api/devices/bell`

Authenticated. Response `200`:
```json
{ "key": "6d49c0853cbe8018f48c", "host": null, "port": 443, "scheme": "https", "channel": "private-screen.42" }
```
`host` null = the host of `cms_url`; `scheme` `https` = `wss`, anything else `ws`. Never a secret.
Response `204`: the CMS has no bel (ask again after 5 min). `401`: token revoked (the manifest
poll handles that).

### 6.2 Connecting and subscribing

```
Pi                                                 Reverb                  CMS
 |-- WebSocket ws(s)://host:port/app/{key}?protocol=7&client=pi-player&version=..&flash=false ->|
 |<- {"event":"pusher:connection_established","data":"{\"socket_id\":\"123.456\",\"activity_timeout\":30}"}
 |-- POST <CMS>/api/broadcasting/auth  {socket_id, channel_name}  (bearer device token) ------>|
 |<- 200 {"auth": "key:hmac"}   (403: not this token's channel, unknown token) ----------------|
 |-- {"event":"pusher:subscribe","data":{"channel":"private-screen.42","auth":"key:hmac"}} ->|
 |<- {"event":"pusher_internal:subscription_succeeded","channel":"private-screen.42"} ------|
```

- A token may only subscribe to its own channel (the one from 6.1).
- Keepalive: when nothing arrived for `activity_timeout` seconds the Pi sends `pusher:ping`; no
  frame within 10 s after that = dead connection. The Pi answers a server `pusher:ping` with
  `pusher:pong` (and WebSocket ping frames with pong frames).
- Reconnect after a close, an error, a failed auth or a missing pong: 1, 2, 4 ... 60 s with
  +-25 % jitter, back to 1 s once subscribed. `pusher:error` 4000-4099 waits the longest delay.
- After every re-subscribe the Pi fetches the manifest once (a bel may have been missed).
- A new token (paired again) closes the connection and starts over with 6.1.

### 6.3 Events on the channel

| event | data | Pi behaviour |
|---|---|---|
| `poll` | `[]` | fetch the manifest now |
| `identify` | `{"identify_until": "2026-10-14T09:31:00.000Z"}` | fetch the manifest now (it carries `identify_until`) |

Bels coalesce: a bel while a manifest fetch (or download) runs gives one more fetch after it,
never two fetches at once.

## 7. Updater ("Speler bijwerken")

`pi_player/updater.py`. The CMS only asks (manifest `update`, section 3.1); the Pi does the rest:

1. The agent (thread `Updater`, every 10 s) takes `update` from the last manifest. It acts only if
   `update.version` differs from its own `agent_version` and `update.url` starts with
   `https://github.com/zekerzichtbaar/zz-narrowcasting-player/` (config `update_url_prefix` for a
   fork): a compromised CMS cannot hand the Pi other code. No own signing (https + GitHub).
2. It copies the running code to `/opt/pi-player/prev/pi_player` (plus the unit file to
   `prev/pi-player.service`) and starts the runner from that copy as a transient systemd unit:
   `systemd-run --unit pi-player-update ... python3 -m pi_player.updater apply <version> <url>`.
   The runner is the **old** code in its own unit, so it survives the restart of `pi-player.service`
   and the fallback never depends on the new release.
3. The runner downloads the tarball (max 100 MB), unpacks it safely (plain files and directories,
   nothing outside the temp dir, one top directory with `install.sh` and `pi_player/`) and runs that
   release's `install.sh` with `PI_PLAYER_VERSION=<update.version>` (a GitHub tarball has no git).
   `install.sh` keeps `/etc/pi-player/config.toml` and `/var/lib/pi-player` (token, videos,
   `state.json`) and restarts `pi-player.service`; the content plays on from `current.mp4`.
4. Success = the new agent's first heartbeat answered `2xx`: it writes
   `/var/lib/pi-player/alive.json` `{"version", "at"}`. The runner waits up to **5 minutes** for an
   `alive.json` with the new version, written after `install.sh` started.
5. `install.sh` failing or no such heartbeat in time (the new agent crashes, hangs, or cannot reach
   the CMS) = rollback: `prev/pi_player` and the unit file go back, `systemctl daemon-reload` and a
   restart of `pi-player.service`. The old agent then reports its old `agent_version` again.
   A failed download changes nothing and restarts nothing.

`/var/lib/pi-player/update.json` = `{"version", "status": "running"|"done"|"failed", "from", "at",
"reason", "attempt"}` (`attempt` = the manifest's `update.attempt` as a string, passed to the runner as
`--attempt`; `null` without one). One update at a time (a `running` status younger than 45 minutes
blocks the next; older = the runner died, e.g. a reboot during the update). A `failed` version is not
tried again for the same `attempt` while the CMS keeps asking for it; a new `attempt` ("Opnieuw
proberen" in the beheer) or a different version is. Without `attempt` (an older CMS) a failed
version stays blocked, as before. The heartbeat reports `update.json` as its `update` field
(section 4.1), so the CMS can show "Update mislukt" with the reason. An older player ignores
`attempt` and never retries the same version. To retry by hand: `sudo rm /var/lib/pi-player/update.json`.
Logs: `journalctl -u pi-player-update`.

On the tv: while `update.json` says `running` for another version than its own, the agent shows a
small "Aan het updaten..." label bottom-left (for the viewer, also at rotation 90/270) over the
playing video, never over the setup screens. It sits on its own DRM overlay plane (a second
`kmssink` on the agent's shared vc4 fd, the first free ARGB overlay plane above the video's), so the
video stays zero-copy. It goes when the runner cannot start, when the update fails or with the old
agent (`install.sh` restarts it; the new agent shows none). A label that cannot be shown (no free
plane, GStreamer < 1.22 without kmssink `fd`, no vc4) is a warning in the log; video and update go on.

No reboot: `install.sh` never reboots; when a release changes the kernel cmdline (console, HDMI
mode) that takes effect at the next reboot. The first updater gets onto a Pi by hand (`install.sh`
over SSH or a new image); a player without it ignores `update` and keeps playing.
