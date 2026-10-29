Completed (pending acceptance: 9 items, see ACCEPTANCE.md)

# Manage a paired screen from the phone via a QR sticker

## Goal
Today the phone app can only do first-time wifi setup, authorized by an ephemeral QR on the TV.
We want a printed QR sticker on each Pi that opens the app (or the iOS App Clip) and lets whoever
holds it manage that screen over Bluetooth: see its status and change its wifi, also when the Pi
is offline (wifi password changed) and without a power cycle. The sticker secret is owned by the
CMS, so it can be invalidated and reprinted at any time.

## Scope
- CMS (`../zz-narrowcasting`): a per-screen management secret, shown as a printable QR on the
  screen's page, with "new QR" that invalidates the old one.
- CMS -> Pi delivery of that secret over the authenticated manifest; the Pi persists it so it
  works offline.
- Pi: BLE GATT management mode that is available while paired (not only in WIFI_SETUP),
  authenticated with the sticker secret.
- Management ops v1: status/diagnostics (current SSID, signal, IP, online/paired, agent version,
  screen name), scan, connect to another network (also while online).
- Protocol designed to grow (more ops later) without a new trust model.
- iOS app + App Clip and Android app: open the sticker URL into a management screen.
- `docs/ble-setup.md` (or a new `docs/ble-management.md`) and `docs/protocol.md` updated;
  `mock_cms.py` serves the new manifest field.

## Non-goals
- NFC tags (the URL in a QR is enough; an NFC tag holding the same URL works for free but is
  not designed or tested).
- Management ops beyond wifi + status (restart, identify, rotation, ...): later tasks.
- App login / CMS user accounts in the app. Holding the sticker is the authorization.
- Changing the first-time setup flow (ephemeral TV QR, hotspot fallback): it stays as is.
- Burning anything into the Pi's OTP.
- Audit trail of phone changes in the CMS (see Open questions).

## Key decisions & constraints
- **Trust model:** physical access to the sticker = authorization, like the TV QR today but
  persistent. Acceptable because the sticker sits on the Pi itself, and touching the Pi already
  means full control (the SD card holds `device_token`). The residual risk (a photographed
  sticker used later from BLE range) is handled by CMS invalidation.
- **The CMS owns the secret.** It is generated per `Screen` (random, >= 128 bit), stored
  retrievably (Laravel `encrypted` cast) so the QR can be shown again, and replaced by
  "new QR". It is *not* derived from `device_token`: the CMS only stores `sha256(device_token)`
  (`app/Models/Screen.php`), and using that hash as a key would let a DB leak mint access.
- **Delivery:** the manifest carries the secret (or a key derived from it) to the paired Pi. The
  Pi stores it in its state dir (0600) for offline use. Invalidation only reaches a Pi that is
  online; an offline Pi accepts the old sticker until its next manifest. On 401 (unpaired) the Pi
  deletes the secret; a re-paired screen gets a new secret and needs a new sticker.
- **URL:** reuse the path both apps already handle: `https://app.zekerzichtbaar.tv/setup`
  (iOS `appclips:`/`applinks:` entitlements, Android App Link `android:path="/setup"`), with a
  new param, e.g. `?d=<id4>&s=<secret>`, so no new App Clip experience or intent filter is
  needed. `k=` stays the ephemeral first-setup token. Without the app: iOS opens the App Clip,
  Android lands on the CMS `/setup` page (Play Store link).
- **No plaintext over the air in management mode:** unlike first-time setup (STATUS/NETWORKS are
  readable unauthenticated), management status, diagnostics and network lists are only returned
  encrypted. A generic BLE scanner app must learn nothing beyond the advertised name.
- **Replay protection:** the key is static, so a sniffed command frame must not work twice. Use a
  Pi-issued challenge/nonce per session (no reliance on the Pi clock: Pi 4 has no RTC).
- **Switching networks while online** must not strand the Pi: if the new network fails, it falls
  back to the network it had and reports `failed`.
- **No hotspot in management mode**, so `scan` does not need to drop anything (the ~20 s hotspot
  drop in v1 only applies to WIFI_SETUP).
- BLE advertising today only runs in WIFI_SETUP + 120 s (`pi_player/blesetup.py`, `LINGER`,
  `_advertise`). Management needs advertising while a management secret is present.
- Shared protocol code lives in `ios/WifiSetupKit` and the Android equivalent; keep both at
  parity and keep `python3 -m pi_player.blesetup` self-check covering the new crypto/framing.

## Preconditions & external dependencies
- Access to the CMS repo `../zz-narrowcasting` and a way to deploy it (the Pi side is useless
  until the CMS serves the secret; `mock_cms.py` covers local testing).
- A real Pi 4 with Bluetooth, plus an iPhone and an Android phone, for the end-to-end checks.
- App Store / Play releases of the apps (App Clip update) to ship it. No new App Store Connect
  App Clip experience should be needed, since the `/setup` path is reused.

## Building blocks
- CMS: `Screen` management secret column + migration, generate/regenerate action, QR + print
  view on the screen page, manifest field.
- Protocol spec: management mode (advertising, characteristics, session challenge, encrypted
  responses, ops `status`, `scan`, `connect`, versioning for future ops).
- Pi: secret persistence (state dir), manifest handling, BLE management server next to the
  existing setup server, NetworkManager switch-with-fallback.
- `mock_cms.py`: serve the new manifest field.
- iOS `WifiSetupKit`: URL parsing for `s=`, management transport/session, management UI
  (status + change network); App and Clip routing.
- Android: same as iOS.
- Docs.

## Definition of Done
- [x] CMS shows a printable management QR per paired screen
  GIVEN a paired screen, WHEN an admin of its organization opens it in the CMS, THEN a QR with `https://app.zekerzichtbaar.tv/setup?d=<id4>&s=<secret>` and a print option are shown; admins of other organizations can't see it
- [ ] The Pi learns the secret and keeps it offline — not verified: needs a real Pi cut off from the network and restarted; headless checks pass (mock_cms run: stored 0600 in the state dir, rotation replaces, unpair deletes)
  GIVEN a paired Pi, WHEN it fetches the manifest and is then cut off from the network and restarted, THEN the sticker still opens management
- [ ] "New QR" invalidates the old sticker — not verified: needs a deployed CMS + online Pi + phone; headless checks pass (Pi rotation ends sessions, wrong `s` -> 0x08; iOS + Android clients show "QR verlopen" against the mock)
  GIVEN an online Pi, WHEN the admin generates a new QR, THEN within one manifest poll the old QR is rejected by the Pi ("QR verlopen") and the new one works
- [ ] Status and diagnostics on the phone — not verified: needs real BLE between a Pi and a phone; iOS Simulator + Android emulator show all fields against the mock Pi (shots-ios/, shots-android/)
  GIVEN a paired Pi, WHEN the sticker is scanned, THEN the app shows screen name, current network + signal, IP, online/paired state and player version
- [ ] Switch to another network while online — not verified: needs a real Pi on wifi; netman fake-nmcli self-check covers switch + fallback, both apps show joined / failed-with-fallback against the mock
  GIVEN an online Pi, WHEN a different network with the right password is chosen, THEN the Pi joins it and stays online in the CMS; WHEN the password is wrong, THEN the Pi stays on (or returns to) its old network and the app shows the failure
- [ ] Fix a changed wifi password without a power cycle — not verified: needs a real Pi whose wifi password changed; netman self-check covers the offline join, both apps' mock tests cover the offline status -> joined flow
  GIVEN a paired Pi playing its cached video while offline because the wifi password changed, WHEN the sticker is scanned and the new password entered, THEN the Pi is online again without a reboot
- [ ] Nothing leaks without the secret — not verified: needs real BlueZ on a Pi + a generic scanner (nRF Connect); headless checks pass (locks, 0x08 for wrong `s`, Alias set)
  GIVEN a generic BLE scanner app or a wrong/old `s=`, WHEN it connects to a paired Pi, THEN it can read no SSID, IP or network list and every command is rejected
- [x] Replayed commands are rejected
  GIVEN a captured `connect` frame from an earlier session, WHEN it is written again, THEN the Pi rejects it
- [ ] Sticker works without the app installed — not verified: needs a real iPhone (App Clip from camera) and an Android phone without the app; CMS `/setup?s=` page test passes
  GIVEN an iPhone without the app, WHEN the sticker is scanned with the camera, THEN the App Clip opens into management; GIVEN an Android phone without the app, THEN the CMS `/setup` page opens with a Play Store link
- [ ] First-time setup is unchanged — not verified: needs real phones + an unpaired Pi; existing iOS ClipFlowUITests and Android SetupFlowTest/unit tests still pass
  GIVEN an unpaired Pi on the WIFI_SETUP screen, WHEN its TV QR is scanned, THEN setup works exactly as before (iOS and Android)
- [ ] End-to-end on real hardware `[user-gated]`
  GIVEN a real Pi with a printed sticker, an iPhone and an Android phone, WHEN each scans it and switches the Pi to another network, THEN both succeed and the CMS shows the Pi online on the new network

## Open questions
- Audit: should the heartbeat report "wifi changed via phone at T" so the CMS shows it? Cheap,
  and useful against a misused sticker; not in scope unless decided.
- Collisions: `d` is only the last 4 characters of the device ID (BLE name `ZZ-<id4>`). Two Pis
  in range with the same suffix: the app should try the next candidate when the secret is
  rejected, or the advertisement/URL should carry more of the ID. Decide in the protocol slice.
- Should management stay available while the Pi is in WIFI_SETUP (hotspot up)? Probably yes
  (the sticker is then the fastest route), but it shares the radio with the hotspot.
