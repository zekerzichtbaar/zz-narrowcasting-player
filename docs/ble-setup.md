# Wifi setup over Bluetooth (App Clip <-> Pi)

Version 1. The phone sets up the Pi's wifi over Bluetooth LE instead of joining the setup
hotspot. The hotspot and captive portal stay up next to it as the fallback for Android and for
phones without the App Clip.

```
TV (WIFI_SETUP screen)                     iPhone
  QR: https://app.zekerzichtbaar.tv/setup?d=1E2F&k=<token>
                          camera ──────────>  App Clip card -> App Clip opens with that URL
Pi (BlueZ GATT server)  <── BLE ──────────  scans for service + name "ZZ-1E2F", connects
  NETWORKS (read)        ──────────────────> network list
  COMMAND (write)        <────────────────── AES-GCM {"op":"connect","ssid":..,"password":..}
  STATUS (notify)        ──────────────────> busy -> online (+ pairing code) / failed
```

Being in front of the screen is the authorization, the same as with the hotspot password: the
token is only shown on the TV, and every command must be encrypted with it.

## Setup URL (QR on the TV)

`<setup_url>?d=<id>&k=<token>`

| Param | Value |
|---|---|
| `setup_url` | config `setup_url`, default `https://app.zekerzichtbaar.tv/setup` (the App Clip's associated domain) |
| `d` | last 4 characters of the device ID, upper case (`1E2F`); same as the hotspot name `Narrowcast-1E2F` |
| `k` | 16 random bytes, base64url without padding (22 characters). New on every agent start |

On iOS the URL opens the App Clip (or the full app when installed). Anywhere else it opens the
CMS page `/setup`, which explains the hotspot route.

## Advertising

- Service UUID `8edd2610-ddcd-4435-a57e-98a6b4dd342f`
- Local name `ZZ-<id>`, e.g. `ZZ-1E2F` (short so flags + 128-bit UUID + name fit in 31 bytes)
- Advertised while the screen shows wifi setup, and for 120 s after it left that state, so a phone
  that is still connected sees the result and the pairing code.

The app matches on the service UUID and `CBAdvertisementDataLocalNameKey == "ZZ-" + d`
(case-insensitive). Not `peripheral.name`: iOS caches GAP names.

No pairing/bonding: no characteristic requires encryption, so iOS shows no pairing dialog.

## Characteristics

| Name | UUID | Properties | Value |
|---|---|---|---|
| STATUS | `8edd2611-ddcd-4435-a57e-98a6b4dd342f` | read, notify | JSON, see below |
| NETWORKS | `8edd2612-ddcd-4435-a57e-98a6b4dd342f` | read | JSON, see below |
| COMMAND | `8edd2613-ddcd-4435-a57e-98a6b4dd342f` | write (with response) | encrypted frame, see below |

All values are at most 512 bytes (ATT limit). iOS does long reads/writes (above the MTU) by
itself; the Pi handles the `offset`.

### STATUS

```json
{"v":1,"seq":7,"state":"failed","msg":"Verbinden met Kantine mislukt: Verkeerd wachtwoord?",
 "ssid":null,"scan":3,"code":null,"url":null}
```

| Field | Meaning |
|---|---|
| `v` | protocol version (1) |
| `seq` | increases on every change |
| `state` | `ready`: waiting for a network choice. `busy`: working on a command from the phone, `msg` says what (only for commands over Bluetooth; the hotspot restarting or the saved-network retry don't count). `failed`: last attempt failed, `msg` says why; stays until the next command (after a `scan` an already reported error is not repeated). `online`: on the network and the CMS answers, not paired yet. `paired`: online and paired (nothing left to do) |
| `msg` | Dutch text for the user, or null |
| `ssid` | network the Pi joined (`online`/`paired`), else null |
| `scan` | id of the network list; when it changes, read NETWORKS again |
| `code`, `url` | pairing code (`K7Q-4M2`) and CMS pairing page, once known (`online`) |

A notification may be cut off at the MTU: when it doesn't parse as JSON, read the characteristic.

### NETWORKS

```json
[["Kantine",82,1],["Office 5G",54,1],["Gast",20,0]]
```

`[ssid, signal 0-100, secured 0/1]`, strongest first, one entry per SSID, cut off at 512 bytes
(weakest dropped). Empty list `[]` while nothing was scanned yet.

### COMMAND

Frame: `len (2 bytes, big endian) | nonce (12) | ciphertext + tag (16)`, `len` = number of bytes
after the length field. AES-256-GCM with additional data `zz-wifi-setup-v1`.

Key: `HKDF-SHA256(ikm = token bytes, salt = "zz-narrowcasting", info = "zz-wifi-setup-v1", 32 bytes)`.

Plaintext (UTF-8 JSON), one of:

```json
{"op":"connect","ssid":"Kantine","password":"geheim123","hidden":false}
{"op":"scan"}
```

- `connect`: join that network. Password empty = open network; otherwise 8 to 63 characters.
  `hidden: true` for a network that is not in NETWORKS. The Pi answers through STATUS:
  `busy` -> `online` / `paired` or `failed`.
- `scan`: refresh NETWORKS. The Pi 4 can't scan while the hotspot is up, so this drops the hotspot
  for about 20 s; STATUS goes `busy`, then `ready` with a new `scan` id.

Write errors (ATT): a frame that does not decrypt with the token (old QR, agent restarted) fails
with *insufficient authorization* (0x08): scan the QR on the screen again. A bad command (not
JSON, unknown op) fails with *unlikely error* (0x0e). A validation problem (password too short)
is accepted and reported as STATUS `failed` with a message.

## Test without the App Clip

`python3 -m pi_player.blesetup` runs the protocol self-check (framing, crypto, payloads). On a Pi,
`bluetoothctl` -> `menu gatt` -> `list-attributes` shows the service while the setup screen is up.
