# Pi Player <-> CMS protocol

Version: 2. Plain HTTPS only (polling, no WebSocket). All bodies are JSON (UTF-8).
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
  "agent_version": "0.1.0",
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

Authenticated. Polled every `manifest_poll` seconds (default 30), and right after pairing. This
poll is the only update signal: a new version, a new group moment or an unpublish reaches the Pi
within one poll.

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
  "screen": { "id": "scr_42", "name": "Kantine links", "group_id": "grp_7" }
}
```

Response `204`: paired but no content (yet): no playlist, nothing published, or no version of the
playlist ever got a video. Pi shows `WAITING`; a video it had (e.g. from before an unpair/re-pair,
or after the CMS unpublished) is deleted. A newly published version whose video is still being
made (or failed) is **not** a `204`: the CMS keeps serving the previous video, with the same
`version` as before, so the Pi does not even switch. Once the new video is ready, `version` changes.
Response `401`: token revoked (Pi goes to `PAIRING`).

Response `200` with `"version": null`: nothing to play (handled exactly like a `204`), but the
screen is being identified, so the body carries `identify_until` and `screen`:
`{"version": null, "identify_until": 1760437560, "screen": {...}}`.

Fields:
| field | type | meaning |
|---|---|---|
| `version` | string | Opaque. Pi compares for equality only. Changes whenever the Pi must react: new file, new `activate_at`/`epoch` (e.g. the group moment). A new `version` with the same `sha256` as the file on screen or waiting is **not** downloaded again. |
| `url` | string | MP4 download URL. May be pre-signed; if it is on the CMS host the Pi sends the bearer token. |
| `sha256` | string | Lowercase hex of the whole file. A mismatch means the download is discarded. |
| `size` | int | Bytes (optional, for progress/space checks). |
| `duration` | float | Exact video duration in seconds (frames / fps). Used for sync math. |
| `epoch` | float | Sync reference: frame 0 plays at `epoch + k * duration`. Same for all screens in a group. Default `0`. |
| `activate_at` | float | Unix time from which this version may be shown. All screens in a group get the same value. `0` = right away (at the next loop boundary when something already plays). |
| `identify_until` | int | "Scherm identificeren" in the CMS: show `screen.name` big, full screen, instead of the video or setup screen until this Unix time (the video re-joins the group clock afterwards). `0` = not identifying. **Not** part of `version`: it never makes the Pi download or switch, and the Pi acts on it also when `version` is unchanged. Arrives within one poll; the CMS keeps it for 60 s. |
| `screen` | object | Name/group for display on the `WAITING` screen and for identifying. |

Sync math on the Pi: `position = (now - epoch) mod duration`.

Activation: the Pi downloads to `next.mp4`, verifies `sha256`, then switches at `activate_at`.
If the download finishes after `activate_at`, the Pi keeps playing the old video and switches at
the next loop boundary of the new video (`now` where `(now - epoch) mod duration ~ 0`).
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

- Container MP4, video H.264 (High profile, level <= 4.2), 1920x1080, yuv420p.
- **Constant frame rate**, 25 or 30 fps.
- Keyframe at frame 0 (and preferably every 1-2 s so seeks are cheap).
- No audio needed (the Pi is muted); an audio track is ignored.

## 4. Heartbeat

### 4.1 `POST <CMS>/api/devices/heartbeat`

Authenticated. Sent every 30 s while the Pi has a token, and right away when `state`, `version`
or `pending_version` changes (so a finished download is reported within a second).

Request:
```json
{
  "device_id": "pi-10000000a1b2c3d4",
  "agent_version": "0.1.0",
  "time": 1760437213.42,
  "state": "PLAYING",
  "version": "12-0-9b74c9897bac",
  "pending_version": "13-4102444800-3f1a0c55e2d1",
  "drift_ms": 3.1,
  "uptime": 86400,
  "ip": "192.168.1.42",
  "network": "wifi"
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
| `network` | `ethernet` \| `wifi` |

The CMS uses it as "online" (last seen) and as the stored version for group activation:
`pending_version` if set, else `version`. `drift_ms`, `uptime`, `ip` and `network` are diagnostics
(the CMS may ignore them). Unknown fields are ignored.

## 5. Error handling summary

| situation | Pi behaviour |
|---|---|
| HTTP `401` (manifest or heartbeat) | delete token -> `PAIRING` |
| manifest `204` | `WAITING` |
| network errors | keep current state/video; retry with backoff; offline > `offline_timeout` while playing -> `WIFI_SETUP` |
| sha256 mismatch / download error | discard temp file, keep old video, retry on next manifest fetch |
