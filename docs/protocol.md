# Pi Player <-> CMS protocol

Version: 1 (draft). Transport-generic: plain HTTPS + one WebSocket. All bodies are JSON (UTF-8).
Times are Unix seconds (float allowed). Authenticated calls send `Authorization: Bearer <device_token>`.

`<CMS>` below is the `cms_url` from `/etc/pi-player/config.toml`, e.g. `https://cms.example.com`.

## 1. Identity

- **device_id**: stable per Pi, derived from the CPU serial (`/proc/cpuinfo` `Serial`), formatted
  `pi-<16 hex>`. Survives reinstalls. Not a secret.
- **device_token**: opaque secret issued by the CMS when an admin claims the device. Stored at
  `/var/lib/pi-player/token` (0600, root). Never shown on screen, never logged.

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
 |   (store token, connect WS)                                               |
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
  "pair_url": "https://cms.example.com/pair?code=K7Q-4M2",
  "poll_interval": 3
}
```

Rules:
- `code`: short, human-typable (6 chars from an unambiguous alphabet, e.g. no `0/O/1/I`), shown with
  a dash for readability. Single-use, short-lived (default 10 min).
- A new register call for the same `device_id` invalidates any previous unclaimed code.
- `pair_url` has the code prefilled; the Pi renders it as a QR code.

### 2.2 `GET <CMS>/api/devices/register/status?device_id=<id>&code=<code>`

Unauthenticated, polled every `poll_interval` seconds while the pairing screen is shown.

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
- `claimed`: the token is returned **exactly once**; the CMS then marks the code used. The Pi
  stores the token and switches to normal operation.

### 2.3 Unpairing

The admin removes/unpairs the screen in the CMS. The CMS revokes the token and, if the device is
connected, sends a `unpaired` WS message (section 4.1). Any authenticated HTTP call with a revoked
token returns `401`. On either signal the Pi deletes its token and goes back to `PAIRING`.

## 3. Manifest

### 3.1 `GET <CMS>/api/devices/manifest`

Authenticated. Fetched on WS connect, on `update_available`, and as a slow fallback poll
(default every 5 min).

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
  "screen": { "id": "scr_42", "name": "Kantine links", "group_id": "grp_7" }
}
```

Response `204`: paired but no content (yet) — Pi shows `WAITING`; a video it had (e.g. from before
an unpair/re-pair, or after the CMS unpublished) is deleted.
Response `401`: token revoked (Pi goes to `PAIRING`).

Fields:
| field | type | meaning |
|---|---|---|
| `version` | string | Opaque, unique per export. Pi compares for equality only. |
| `url` | string | MP4 download URL. May be pre-signed; if it is on the CMS host the Pi sends the bearer token. |
| `sha256` | string | Lowercase hex of the whole file. A mismatch means the download is discarded. |
| `size` | int | Bytes (optional, for progress/space checks). |
| `duration` | float | Exact video duration in seconds (frames / fps). Used for sync math. |
| `epoch` | float | Sync reference: frame 0 plays at `epoch + k * duration`. Same for all screens in a group. Default `0`. |
| `activate_at` | float | Unix time from which this version may be shown. All screens in a group get the same value. |
| `screen` | object | Name/group for display on the `WAITING` screen. |

Sync math on the Pi: `position = (now - epoch) mod duration`.

Activation: the Pi downloads to `next.mp4`, verifies `sha256`, then switches at `activate_at`.
If the download finishes after `activate_at`, the Pi keeps playing the old video and switches at
the next loop boundary of the new video (`now` where `(now - epoch) mod duration ~ 0`).
If nothing is on screen yet (first content after pairing), the Pi shows a verified download
immediately instead of waiting for `activate_at`; sync math still puts it at the group position.
The CMS should set `activate_at` far enough ahead for all screens to download
(e.g. now + 2 min, or later for big files).

### 3.2 MP4 export requirements (CMS side)

- Container MP4, video H.264 (High profile, level <= 4.2), 1920x1080, yuv420p.
- **Constant frame rate**, 25 or 30 fps.
- Keyframe at frame 0 (and preferably every 1-2 s so seeks are cheap).
- No audio needed (the Pi is muted); an audio track is ignored.

## 4. WebSocket

`wss://<CMS host>/api/devices/ws` with `Authorization: Bearer <device_token>` (header; if the
stack can't read headers on upgrade, `?token=<device_token>` is accepted as fallback).
Close code `4401` = token invalid/revoked (equivalent to `unpaired`).

Every message is one JSON object with a `type` field. Unknown types are ignored by both sides.

The Pi reconnects with exponential backoff (1 s, 2 s, 4 s ... max 60 s, with jitter).

### 4.1 CMS -> Pi

`update_available` — a new version was published for this screen's group. Pi fetches the manifest.
```json
{ "type": "update_available", "version": "2026-10-14T09:30:00Z-3f1a" }
```

`unpaired` — the screen was removed in the CMS. Pi deletes its token, goes to `PAIRING`.
```json
{ "type": "unpaired" }
```

`screen_updated` — name/group changed (optional; Pi refetches the manifest).
```json
{ "type": "screen_updated", "screen": { "id": "scr_42", "name": "Kantine rechts", "group_id": "grp_7" } }
```

### 4.2 Pi -> CMS

`hello` — sent once right after the socket opens.
```json
{
  "type": "hello",
  "device_id": "pi-10000000a1b2c3d4",
  "agent_version": "0.1.0",
  "version": "2026-10-14T09:00:00Z-77b0"
}
```

`heartbeat` — every 30 s (also used as the "status" message: sent immediately on any state change).
```json
{
  "type": "heartbeat",
  "device_id": "pi-10000000a1b2c3d4",
  "time": 1760437213.42,
  "state": "PLAYING",
  "version": "2026-10-14T09:00:00Z-77b0",
  "pending_version": "2026-10-14T09:30:00Z-3f1a",
  "drift_ms": 3.1,
  "uptime": 86400,
  "ip": "192.168.1.42",
  "network": "wifi"
}
```

| field | meaning |
|---|---|
| `state` | `WIFI_SETUP` \| `PAIRING` \| `WAITING` \| `PLAYING` (only the latter two are ever seen over WS) |
| `version` | version currently on screen, `null` if none |
| `pending_version` | downloaded/downloading version waiting for `activate_at`, `null` if none |
| `drift_ms` | last measured playback position minus target position, in ms (`null` when not playing) |
| `network` | `ethernet` \| `wifi` |

`download_failed` — optional diagnostic.
```json
{ "type": "download_failed", "version": "2026-10-14T09:30:00Z-3f1a", "reason": "sha256 mismatch" }
```

## 5. Error handling summary

| situation | Pi behaviour |
|---|---|
| HTTP `401` / WS close `4401` / `unpaired` | delete token -> `PAIRING` |
| manifest `204` | `WAITING` |
| network errors | keep current state/video; retry with backoff; offline > `offline_timeout` while playing -> `WIFI_SETUP` |
| sha256 mismatch / download error | discard temp file, keep old video, retry on next manifest fetch |
