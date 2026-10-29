# Managing a paired screen over Bluetooth (QR sticker)

Version 1. A printed QR sticker on each Pi opens the phone app (or the iOS App Clip). Whoever holds
the sticker manages that screen over Bluetooth LE: they see its status and change its wifi, also when
the Pi is offline (wifi password changed) and without a power cycle. First-time setup
([`ble-setup.md`](ble-setup.md), the ephemeral QR on the TV) stays as it is. This is a second
GATT service next to it, with its own trust model: a long-lived secret owned by the CMS.

```
Sticker on the Pi                         Phone (app / App Clip)
  QR: https://app.zekerzichtbaar.tv/setup?d=1E2F&s=<secret>
                          camera ──────>  app opens in management mode
Pi (BlueZ GATT server) <── BLE ───────── scans for "ZZ-1E2F", connects
  SESSION  (write)       <────────────── na (16 random bytes)
  SESSION  (read)        ──────────────> 0x01 | np (16 random bytes)
                                         both: Ks = HKDF(secret, na, np)
  REQUEST  (write)       <────────────── AES-GCM(Ks) {"op":"status"}
  RESPONSE (notify/read) ──────────────> AES-GCM(Ks) {"re":1,"ok":true,"ssid":"Kantine",...}
```

## Trust model

- Holding the sticker is the authorization, like the TV QR for first-time setup but persistent.
  Acceptable because the sticker sits on the Pi itself, and touching the Pi already means full
  control (the SD card holds the device token).
- The secret is made and owned by the CMS (per screen, 32 random bytes, stored encrypted so the QR
  can be shown again). It reaches the paired Pi in every manifest (`management_secret`,
  [`protocol.md`](protocol.md) 3.1) and the Pi keeps it in its state dir (0600) for offline use.
- "Nieuwe QR" in the CMS replaces it: the old sticker stops working once the Pi fetched its next
  manifest (within a second with the bel, else one poll). An offline Pi accepts the old sticker
  until it is online again. Pairing the screen again gives a new secret, and a `401` (unpaired)
  deletes it on the Pi.
- Nothing readable without the secret: status, diagnostics and network lists only travel encrypted.
  A generic BLE scanner learns the advertised name `ZZ-1E2F` and that a ZZ screen is there, nothing
  more.
- A sniffed frame never works twice: every session has a fresh key from both sides' random values
  (the Pi 4 has no RTC, so nothing depends on its clock), and frames inside a session are counted.

## Sticker URL

`https://app.zekerzichtbaar.tv/setup?d=<id>&s=<secret>`

| Param | Value |
|---|---|
| `d` | last 4 characters of the device ID, upper case (`1E2F`), as on the TV QR and the hotspot `Narrowcast-1E2F` |
| `s` | the screen's management secret, as in the manifest: base64url without padding (the CMS makes 43 characters) |

Same path as the TV QR, so no new App Clip experience, associated domain or intent filter: the iOS
`appclips:`/`applinks:` entitlements and the Android App Link on `/setup` already cover it. The apps
pick the flow from the parameters: `k` = first-time setup (as today, wins when both are there),
`s` = management. Without the app, iOS opens the App Clip; Android opens the CMS page `/setup`
(Play Store link). That page never shows `s`; the CMS host does see it in its request log, which is
acceptable because the CMS owns the secret (do not log query strings of `/setup`).

## Advertising

- Management service UUID `8edd2620-ddcd-4435-a57e-98a6b4dd342f`. The first-time setup service
  stays `8edd2610-...`.
- Local name `ZZ-<id>`, as in first-time setup. An advertisement holds one 128-bit service UUID
  (flags 3 + UUID 18 + name 9 = 30 of 31 bytes), so the Pi advertises:

| Pi | Advertised service UUID | GATT services |
|---|---|---|
| setup window: WIFI_SETUP, and 120 s after it | setup `8edd2610-...` (unchanged) | setup; management (locked without a secret) |
| otherwise, with a management secret stored | management `8edd2620-...` | management; setup locked |
| otherwise, no secret (unpaired, older CMS) | nothing | both locked |

- In management mode the app matches the local name `ZZ-` + `d` (case-insensitive, from the
  advertisement: `CBAdvertisementDataLocalNameKey` / `ScanRecord.getDeviceName()`, never the cached
  GAP name) with **either** service UUID in the advertisement, then requires the management service
  in service discovery. So management also works while the Pi shows the wifi-setup screen (decision
  on the open question: yes; it shares the radio with the hotspot, see `scan`).
- The setup service is locked outside the setup window: reads of its STATUS and NETWORKS and writes
  to its COMMAND fail with *insufficient authorization* (0x08), and STATUS sends no notifications
  (a phone still subscribed from the setup window gets nothing more). Without this, the management
  advertising of a paired Pi would let anyone connect and read the plaintext setup values. Inside
  the setup window it behaves exactly as in [`ble-setup.md`](ble-setup.md).
- bluetoothd reads an advertisement's service UUID when it is registered, so switching between the
  two (setup window over, secret stored or deleted) unregisters the one advertisement and registers
  it again on the next refresh (about 0.5 s without advertising; a failure retries after 10 s).
- Both services are always registered (one GATT application, so rotation or unpairing never
  re-registers it and iOS's GATT cache stays valid). Without a stored secret the management service
  is locked: a SESSION write fails with 0x08. When the secret is deleted (401, `null` in the
  manifest) the Pi stops the management advertising and ends every session.
- The adapter's name is not the hostname: bluetoothd serves it as the GAP Device Name, readable by
  anyone who connects. The Pi sets `Adapter1.Alias` to `ZZ-<id>` when it registers.
- No pairing/bonding (no characteristic requires link encryption, so iOS shows no dialog): all
  protection is in the frames.

### Two Pis with the same `d`

`d` is only 4 characters (the name budget is 8: `ZZ-` + at most 5). Decision: keep 4 (it matches the
TV QR and the hotspot name) and let the app try candidates. After the first matching advertisement
the app keeps scanning for about 2 s, then tries the candidates strongest RSSI first: handshake +
`status`. A Pi with another secret rejects that first frame (0x08), so the app disconnects and tries
the next one. When every candidate rejects it, the sticker is out of date ("QR verlopen": a new QR
was made, or the screen was paired again). `status` returns the full device ID, so the app can also
show which screen it reached. Chance of a collision: 1 in 65536 per pair of Pis in range.

## Characteristics

Management service `8edd2620-ddcd-4435-a57e-98a6b4dd342f`:

| Name | UUID | Properties | Value |
|---|---|---|---|
| SESSION | `8edd2621-ddcd-4435-a57e-98a6b4dd342f` | write (with response), read | handshake, see below |
| REQUEST | `8edd2622-ddcd-4435-a57e-98a6b4dd342f` | write (with response) | encrypted request frame |
| RESPONSE | `8edd2623-ddcd-4435-a57e-98a6b4dd342f` | read, notify | encrypted response frame (the latest) |

All values are at most 512 bytes (ATT limit). Long reads/writes as in first-time setup: iOS does
them by itself, the Pi handles `offset`; Android requests an MTU of 517 and also handles a lower one.

## Session

Per BLE connection (BlueZ passes the connection as `device` in the `ReadValue`/`WriteValue`
options); at most one session per connection. The Pi notices a disconnect from BlueZ's `Device1`
`Connected` going false (or the device object going away); a bluetoothd restart ends every session.

1. The app subscribes to RESPONSE (notifications), picks `na` = 16 random bytes and writes it to
   SESSION. Any other length fails with *unlikely error* (0x0e). The Pi picks `np` = 16 random
   bytes and starts a new session for this connection (a previous one ends).
2. The app reads SESSION: `0x01 | np` (17 bytes; the first byte is the protocol version). Read
   before any write: 0x08. An app that sees another first byte stops ("werk de app bij").
3. Both derive the session key:
   `Ks = HKDF-SHA256(ikm = UTF-8 bytes of s exactly as in the URL, salt = "zz-narrowcasting", info = "zz-manage-v1" | na | np, 32 bytes)`.

A session ends on disconnect, on a new SESSION write, after 300 s without an accepted request, and
when the secret changes or is deleted (a new sticker or unpairing ends every open session at once).

## Frames

Both directions:

`len (2 bytes, big endian) | ctr (4 bytes, big endian) | ciphertext + tag (16)`, `len` = number of
bytes after the length field. AES-256-GCM with key `Ks`,
nonce `dir (1 byte) | 7 zero bytes | ctr (4 bytes, big endian)` and additional data
`"zz-manage-v1" | dir`. `dir` = `0x01` for requests (app -> Pi), `0x02` for responses (Pi -> app),
so the two directions never share a nonce. Plaintext is UTF-8 JSON.

- Requests: `ctr` starts at 1 and goes up by one per request. The Pi accepts a request only when it
  decrypts with this session's key **and** its `ctr` is higher than the last accepted one, else
  *insufficient authorization* (0x08). That is the replay protection: a frame from an earlier
  session never decrypts (another `na`/`np`), a frame from this session never has a fresh `ctr`.
- Responses: the Pi's own `ctr`, from 1 up. The app accepts only a response that decrypts with its
  session key and has a higher `ctr` than the last one it accepted (a replayed or stale answer from
  a fake Pi fails both, the app picked `na`).
- The Pi keeps the latest response frame per session and notifies it. A RESPONSE read returns the
  latest frame of the reader's own session (by `device`); without a session it fails with 0x08.
  BlueZ sends a notification to every subscribed connection, so a notification may carry another
  phone's frame, and it can be cut off at the MTU: when it does not decrypt under the app's `Ks`,
  the app ignores it and reads RESPONSE (long read). A response that was overwritten before the app
  read it is never needed: `status` reports the outcome of the last `scan`/`connect` (`job`).

### Write errors (ATT)

| Error | When | App |
|---|---|---|
| 0x08 insufficient authorization | no secret stored, no session yet, frame does not decrypt with the session key (wrong or old `s`, old session, the secret changed), `ctr` not fresh | on the first request of a fresh session: wrong Pi or outdated sticker (try the next candidate, then "QR verlopen"); later in a session: one new handshake, then retry once |
| 0x0e unlikely error | malformed frame (length field wrong, shorter than 22 bytes, a long write with a gap or over 512 bytes), bad SESSION write (not 16 bytes, an offset) | bug: start over |

A frame that decrypts but holds no valid request (not JSON, no `op`, unknown `op`) is accepted and
answered encrypted with `ok: false` (`bad_request` / `unknown_op`), so the app learns what an older
Pi does not know. Its `ctr` counts as used.

The Pi raises `org.bluez.Error.NotAuthorized` for 0x08 and `org.bluez.Error.InvalidArguments` for
0x0e. bluetoothd is expected to map the first to *insufficient authorization* and to have no mapping
for the second, so it answers *unlikely error*; to be verified on the Pi with `btmon` (ATT Error
Response code). The apps treat any code other than 0x08 as a bug, so this decides nothing.

## Requests and responses

Request: `{"op": "<name>", ...fields}`. Response:

```json
{"re": 3, "op": "connect", "ok": false, "final": true, "error": "failed",
 "msg": "Verbinden met Kantine mislukt: verkeerd wachtwoord?", "ssid": "Oud netwerk"}
```

| Field | Meaning |
|---|---|
| `re` | `ctr` of the request this answers |
| `op` | the request's op |
| `final` | `false` for progress (`busy`), `true` for the last answer to this request (every request gets exactly one final) |
| `ok` | outcome (in a final); with `false`, `error` is set |
| `error` | `bad_request` (validation, not JSON), `unknown_op`, `busy` (a `scan`/`connect` already runs; `status` is always allowed), `failed`, `no_wifi` (no wifi device) |
| `msg` | Dutch text for the user, or null |

Unknown fields are ignored on both sides.

### `status`

`{"op":"status"}` -> one final:

```json
{"re": 1, "op": "status", "ok": true, "final": true, "v": 1, "ops": ["status", "scan", "connect"],
 "device": "pi-10000000a1b2c3d4", "name": "Kantine links", "agent_version": "v0.9.7",
 "state": "PLAYING", "paired": true, "online": true,
 "network": "wifi", "ssid": "Kantine", "signal": 82, "ip": "192.168.1.42",
 "job": {"op": "connect", "state": "failed", "ssid": "Gast", "msg": "Verbinden met Gast mislukt: verkeerd wachtwoord?"}}
```

| Field | Meaning |
|---|---|
| `v`, `ops` | protocol version (1) and the ops this Pi knows; the app hides what is missing |
| `device` | full device ID |
| `name` | screen name from the CMS (kept in `state.json`), null before the first manifest |
| `agent_version` | as in the heartbeat |
| `state` | what the TV shows: `WIFI_SETUP`, `PAIRING`, `WAITING`, `PLAYING` |
| `paired` | has a device token |
| `online` | on a network and the CMS answers (the agent's offline rule, [`protocol.md`](protocol.md) 5) |
| `network` | `ethernet`, `wifi` or null, as in the heartbeat |
| `ssid`, `signal` | the wifi the Pi is on and its signal 0-100, else null |
| `ip` | local IPv4 address (source address of the default route; the heartbeat uses the route to the CMS host, the same address in practice), else null |
| `job` | the last `scan`/`connect` from a phone: `{"op", "state": "busy"\|"ok"\|"failed", "ssid", "msg"}`, null when none since the agent started |

A long `name` or job `msg` is shortened so the frame stays within 512 bytes.

### `scan`

`{"op":"scan"}` -> `{"final": false, "state": "busy"}`, then the final with the network list:

```json
{"re": 2, "op": "scan", "ok": true, "final": true, "networks": [["Kantine", 82, 1], ["Gast", 20, 0]]}
```

`[ssid, signal 0-100, secured 0/1]`, strongest first, one entry per SSID; the weakest are dropped
until the frame fits in 512 bytes. Outside the setup window there is no hotspot, so the Pi scans
without dropping anything (a few seconds, also while connected to wifi). During the setup window it
is the setup `scan`: the hotspot goes away for about 20 s.

### `connect`

`{"op":"connect","ssid":"Kantine","password":"geheim123","hidden":false}`, same rules as the setup
`connect` (password empty = open network, otherwise 8 to 63 characters; `hidden: true` for a
network that is not in the list). A validation problem is a final `bad_request` with a message.
Otherwise `{"final": false, "state": "busy", "msg": "Verbinden met Kantine..."}`, then the final.

What the Pi does, also while it is online (and so playing):

1. It notes the wifi connection it has now (`old`, may be none) and whether it was online.
2. It saves the network as a new connection `pi-player-new-<ssid>` next to everything it has, with
   an autoconnect priority one above `old`'s and the same-SSID connections' (0 without either). A saved connection with the same
   SSID (e.g. the password changed) is not touched yet: that is how the old password is kept aside.
   Like the setup `connect`, a network that is not in the last scan is saved as hidden.
3. It switches to it. Success = connected with an IPv4 address within 45 s **and** the CMS answers
   (`GET <CMS>/up`, any status) within 30 s after that. Then the new connection stays saved as
   `pi-player-<ssid>`, and the connections with the same SSID it replaces are deleted (also one
   that is not ours, such as Pi Imager's `preconfigured`: its password no longer works). `old`
   stays saved when it is another network.
   Final: `{"ok": true, "ssid": "Kantine", "ip": "192.168.1.57", "online": true}`.
4. Failure (wrong password, no such network, no address, or no CMS while `old` was online): the new
   connection is deleted, so a same-SSID connection still has its old password, and the Pi brings
   `old` up again.
   Final: `ok: false`, `error: "failed"`, `msg` with the reason (`Verbinden met Gast mislukt:
   Verkeerd wachtwoord? Terug naar Kantine.`), `ssid` = the network it is on after the fallback
   (null when none).
5. When `old` did not work either (the Pi was offline, e.g. the password changed), there is nothing
   to go back to: a join that fails leaves it as before (NetworkManager keeps retrying its saved
   networks), and a join without CMS is kept (it is no worse) with `ok: true`, `online: false` and a
   message.

No reboot and no agent restart in any of these: the content keeps playing from `current.mp4`, the
manifest poll and the heartbeat continue on the new network. Over ethernet the same steps only touch
the wifi (there is no `old` wifi to lose); the CMS check then goes over the default route (the
cable), so it does not prove the new wifi reaches the CMS. While the screen shows WIFI_SETUP (the
hotspot is up) `scan` and `connect` are the setup service's jobs (the hotspot drops, a joined network
ends the setup); in the 120 s after it, and otherwise, they are the management jobs above. One job at
a time for both services (`busy`).

## Adding ops later

- A new op gets a new name; a name is never reused with another meaning. An older Pi answers
  `unknown_op` (encrypted) and leaves it out of `status.ops`, so the app hides it.
- New fields can be added to any request or response; both sides ignore unknown fields.
- A change to the handshake, key derivation or framing is version 2: SESSION's first byte `0x02`,
  `info` `zz-manage-v2`. The trust model (the sticker secret) stays; no new characteristic is needed
  for new ops.

## Test vectors

`s = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"`, `na = 000102...0f`, `np = 101112...1f`:

- `Ks = 58cb3bf3146d29f20289f4397eefeb0b283e00efab27c34056d96b894cbfa048`
- request `ctr` 1, `{"op":"status"}`:
  `0023000000016d4aebd66f2afc6e5d9fc7246f979f09d6a1a87cbd131eea5cef3524343cb0`
- response `ctr` 1, `{"re":1,"op":"scan","ok":true,"final":true,"networks":[]}`:
  `004d00000001e9c9fbace569fc061f9e768a3bcd4adf7f064c9c5bf019ac29e316286054658ff8bb44cef05cac73d8c22bf66f10d8b2f7b44103a78bb7bedd0efa4219f3963c9eff9905b6022eeed5`

`python3 -m pi_player.blesetup` checks these vectors, the session and replay rules, the ops, the
setup lock and the advertising switches (`pi_player/blemanage.py`); `python3 -m pi_player.netman`
checks the `connect` switch and fallback against a fake nmcli.
