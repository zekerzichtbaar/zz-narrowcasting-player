# Progress — qr-management

## Current state
All five build slices done: spec, CMS (secret, sticker page, `d`, `/setup?s=`), Pi (secret store, BLE
management server), iOS and Android (link routing, session client, management UI). Only user-gated /
hardware acceptance remains (see OVERVIEW DoD annotations).

Invariants in force:
- Secret = 32 random bytes from the CMS, base64url no padding (43 chars); Pi and apps accept
  `[A-Za-z0-9_-]{22,128}`; BLE key material = UTF-8 bytes of `s` as in the URL (never base64-decoded).
- Manifest `management_secret`: valid -> store/replace, `null` -> delete, missing/invalid/204 -> keep,
  401 -> delete with the token. Pi file `<state_dir>/management_secret` 0600, atomic write.
- CMS: `screens.management_secret` (encrypted), `screens.device_id`; `d` = `Screen::stickerId()`.
  Sticker page GET `/schermen/{screen}/beheer-qr`, "Nieuwe QR" = POST same path. `/setup?s=` (no `k`) =
  "Scherm beheren" + Play Store link; `k` wins.
- BLE (docs/ble-management.md is the source of truth): setup + management services always registered;
  one advertised UUID per `blemanage.advert_kind`; sessions end on disconnect/new SESSION/300 s idle/secret change.
- Apps (iOS `ManageClient.swift` = Android `ManageClient.kt`, same rules and Dutch strings): `k` present ->
  setup (a broken or value-less `k` -> no link); `s` without `k` -> management; candidates by advertised name
  with either service UUID, 2 s window, strongest first; 0x08 on SESSION/first request -> next candidate, all
  -> "QR verlopen"; later 0x08 -> one re-handshake + retry; other ATT codes -> bug screen; version != 1 ->
  "werk de app bij"; no management service -> unsupported; `re` registered before the write; rising response
  ctr; undecryptable notification -> one read; mid-job drop -> reconnect + `status.job`; job 180 s, poll 10 s.

Key files:
- Spec: `docs/ble-management.md`, `docs/protocol.md`, `docs/ble-setup.md`.
- Pi: `pi_player/blemanage.py`, `blesetup.py`, `netman.py`, `__main__.py`, `content.py`; `mock_cms.py`.
- CMS (`../zz-narrowcasting`, uncommitted): see slice 1/3 entries.
- iOS kit: `ManageProtocol.swift`, `ManageTransport.swift`, `ManageClient.swift`, `ManageBLETransport.swift`,
  `MockManageTransport.swift`, `Tests/.../ManageTests.swift`; UI `ManageFlowView.swift`, `SetupScreen.swift`.
- Android (`android/app/src/main/java/nl/zekerzichtbaar/narrowcasting/`): `setup/ManageProtocol.kt`,
  `ManageTransport.kt`, `ManageClient.kt`, `ManageBleTransport.kt`, `MockManageTransport.kt`; `ui/ManageFlow.kt`;
  `MainActivity.kt` (`SetupModel` holds setup or manage client). Tests: `src/test/.../ManageTest.kt`,
  `src/androidTest/.../ManageFlowTest.kt`. Mocks: iOS `-mockManage`, Android extra `mockManage=true`, DEMO sticker.

Environment notes:
- CMS: `echo http://localhost:5173 > public/hot; php -d memory_limit=2G vendor/bin/pest; rm public/hot`. Pi: README loop.
- iOS: `cd ios/WifiSetupKit && swift test`; simulator `iPhone 18 Pro` headless; shots in `shots-ios/`.
- Android: `./gradlew testDebugUnitTest assembleDebug`; AVD `narrowcasting_test` headless
  (`emulator -avd narrowcasting_test -no-window -no-audio`), `ANDROID_SERIAL=emulator-5554` for every
  adb/connected call. `connectedDebugAndroidTest` uninstalls the app (and its screenshots): for shots use
  `installDebug installDebugAndroidTest` + `adb shell am instrument -w -e class ...`. Shots in `shots-android/`.

## Slice: 1 — spec + secret delivery
Shipped:
- `docs/ble-management.md` (full management protocol, test vectors), `docs/protocol.md` field +
  401 rule + identity, link from `docs/ble-setup.md`.
- CMS: column + migration, model methods, manifest field in both bodies, regenerate controller and
  route, 8 feature tests, two exact-JSON tests updated, CLAUDE.md bullet.
- Pi: secret persistence in `Content`, `Content.poll()` extracted from `_loop` (same behaviour),
  `pairing.write_private` shared with `store_token`, self-check (stub walk + live mock_cms run:
  stored 0600, rotation replaces, unpair deletes token + secret).
- mock_cms: per-token secret, served in both bodies, `POST /admin/management-qr[?device_id][&enabled]`.

Key decisions:
- CMS local `main` was 3 (+1 unfetched) commits behind `origin/main` (wifi_setup work by a
  colleague touching the same files); fast-forwarded `b7a3766` -> `072601b` (clean tree, ff-only)
  before editing. No commit, no push.
- Lazy generation in the manifest for screens paired before this, instead of a migration backfill.
- Regenerate is for any user of the organization (not superadmin-only), mpv screens only.
- mock_cms never answers 204 while it serves a secret (like the real CMS); `enabled=0` restores the
  older-CMS 204 path, which the content self-check uses.

Notes/leftovers:
- CMS changes are uncommitted; upstream moves fast: check the migration timestamp
  `2026_10_28_120000` against upstream before committing.
- `npm run check` (vp, format-checks CLAUDE.md in CI) not run: no node_modules here.
- The QR slice needs `d` in the CMS: `screens` has no `device_id`; `pairing_codes.device_id` is
  pruned a day after the code expires; `device_info.serial` only exists when the player sends it.
- Locking the setup characteristics outside the setup window is a `blesetup.py` change for the BLE
  slice; "First-time setup is unchanged" is its regression check.
- "The Pi agent run against mock_cms" was verified with `Content.poll()` over real HTTP against a
  live mock_cms, not the full agent (GStreamer/NetworkManager unavailable on macOS).

Placeholder choices:
- Toast texts in `ManagementQrController` ("Nieuwe QR gemaakt. De oude sticker van "X" werkt binnen
  een paar seconden niet meer." / offline variant).
- Route slug `/schermen/{screen}/beheer-qr`, route name `screens.management-qr`.
- App messages proposed in the spec: "QR verlopen", "werk de app bij", "Verbinden met X...".
- Service/characteristic UUIDs `8edd2620..2623-ddcd-4435-a57e-98a6b4dd342f`, timeouts (45 s join,
  30 s CMS, 300 s session idle, ~2 s candidate scan).

## Slice: 2 — Pi BLE management server
Shipped:
- `pi_player/blemanage.py`: HKDF session key, frames (len|ctr|ct+tag, dir nonce/AAD), `Manager`
  (SESSION/REQUEST/RESPONSE per device, replay via rising ctr, 0x08/0x0e errors, idle/rotation/
  disconnect end), ops `status`/`scan`/`connect` with progress + one final, 512-byte fitting,
  `status_fields`, `advert_kind`. Self-check: spec vectors, replay (same frame, earlier session,
  captured `connect` frame -> no wifi job), wrong `s`, rotation/deletion, idle, two phones, ops in
  both routes, busy sharing, sizes.
- `pi_player/blesetup.py`: management service next to setup in one GATT app; setup STATUS/NETWORKS/
  COMMAND locked outside the window (`setup_read`/`setup_write`), STATUS notify suppressed; advertising
  kind setup/manage/none with unregister->register switch; Alias; Device1 disconnect watch; self-check
  for lock + advertising transitions.
- `pi_player/netman.py`: `switch()` with fallback, `connect_outcome`, `wait_cms`, `wifi_signal`,
  `parse_active`, `parse_ip4`; `WifiSetup.manage()` + `_manage()`; fake-nmcli self-check (switch
  online, wrong password, no CMS, changed password offline incl. `preconfigured`, same SSID online,
  offline join without CMS) and the hotspot hold after a connect.
- `__main__.py` wiring; docs (`ble-management.md` connect steps, errors, advertising switch, ip,
  ethernet caveat, routing; `ble-setup.md` lock note); README (doc link, self-check loop, log line).

Key decisions:
- Same-SSID password change via a second profile instead of modify+restore: no secret read-back
  (`nmcli -s`), the old password can't be lost mid-switch. A kept join deletes same-SSID profiles,
  also ones not ours (`preconfigured`), which `pi-player reset` will not bring back.
- `status.ip` = default-route source address (no DNS lookup on the GLib thread).
- Jobs routed by `wifi.wanted`: in the 120 s linger (no hotspot) they take the management path.
- A management connect that ends on any wifi (new or old after fallback) sets the wifi loop's
  `hold`, so a WIFI_SETUP that began during a long fallback (WAITING screens after 30 s offline)
  does not start the hotspot over the restored network.
- 0x0e from management = `org.bluez.Error.InvalidArguments` (expected unmapped -> unlikely);
  setup still raises `Failed` as before (possibly 0x80 on real BlueZ; apps treat non-8 as a bug).

Notes/leftovers:
- Management only runs when `setup_url` is set and BLE deps exist (same gate as the setup service).
- With ethernet up, the CMS check after a wifi switch goes over the cable (documented).
- The ATT error mapping, advertisement swap gap and Alias are runtime-unverified (below).

Placeholder choices:
- Dutch strings: "Ongeldig verzoek.", "Deze speler heeft geen wifi.", "De speler is nog bezig met een
  vorige opdracht.", "Netwerken zoeken…", "Verbinden met X…", "Wifi-instelling gestopt.", "Geen
  wifi-adapter gevonden.", "Er ging iets mis op de speler.", "Verbinden met X mislukt: de server is
  niet bereikbaar", " Terug naar X.", "Verbonden met X, maar de server is niet bereikbaar (geen
  internet?)".
- Profile name `pi-player-new-<ssid>`; limits `NAME_MAX` 64, `MSG_MAX` 120, `JOB_MAX` 150 s.

Runtime-unverified: real BlueZ registration of both services and the advertisement swap (~0.5 s gap);
Alias `ZZ-<id>` and nothing readable in nRF Connect; ATT codes (0x08 / 0x0e) via `btmon`; a real
nmcli switch + fallback on a Pi; whether the BLE link survives a wifi switch on the shared radio;
the D-Bus glue (only smoke-tested against a fake `dbus` module).


## Slice: 3 — CMS sticker page, `d`, `/setup?s=`
Shipped:
- `screens.device_id` (migration with backfill from the latest surviving claimed pairing code), set in
  `Screen::claim()` / `pairWith()`, refreshed from the mpv heartbeat's `device_id` (validated
  `[A-Za-z0-9._-]{1,64}`); `Screen::stickerId()`, `managementUrl()`, `STICKER_URL`.
- Sticker page (Blade, `ManagementQrController::show`): inline BaconQrCode SVG, `ZZ-<d>`, name,
  instruction, "Afdrukken" (print CSS hides everything but the 60 mm sticker), "Nieuwe QR" form with
  `confirm()`; "De QR verschijnt zodra..." without a valid `d`; `Cache-Control: no-store`.
  `store()` now redirects to the sticker page with a session status (no Inertia toast).
- Menu item "Beheer-QR (sticker)" (mpv screens, every user of the organization) opening it in a new tab.
- `/setup` sticker branch ("Scherm beheren", App Clip line, Play Store link), `k` wins; same page for
  any `s` (no validity oracle).
- Tests: exact URL (model + response contains the SVG rendered from that URL), other org 404, guest,
  Chromium 404, no `d`, `d` derivation dataset, regenerate changes the QR, device_id on claim/re-pair,
  heartbeat fill, migration backfill, `/setup?s=` and `k`+`s` never echo values. 581/581.
- Docs: CMS CLAUDE.md bullet, `docs/protocol.md` heartbeat note on `device_id`.

Key decisions:
- `d` from a `device_id` column fed by the heartbeat rather than `device_info.serial`: the player
  never sends `serial`, but sends `device_id` in every heartbeat (protocol 4.1), token-authenticated.
- Sticker as a separate Blade page instead of a dialog in `/schermen`: `/schermen` polls, so props
  would ship every secret every few seconds; Blade is also testable without a Vite build.
- `mock_cms.py` unchanged: `device_id` lives only in the CMS; the Pi-side dev flow does not use it.
- Verified the rendered QR decodes to the exact URL (`rsvg-convert` + OpenCV).

Notes/leftovers:
- `bacon/bacon-qr-code` is used directly but only required transitively (via Fortify); consider adding
  it to the CMS `composer.json` when committing.
- "Do not log query strings of `/setup`": the app logs no request URLs, but Sentry error events carry
  the request URL, and Forge's nginx access log logs query strings. Deploy-side follow-up (nginx
  `log_format` / Sentry `before_send` scrub) if wanted.
- TSX change (one menu item) is type-unverified: no node_modules (Wayfinder action `show` generated fine).
- `npm run check` (vp lint + format) not run on `pages/screens/index.tsx` or the two new CMS CLAUDE.md
  bullets (CLAUDE.md formatting failed CI before): run it before committing.
- Migration timestamps `2026_10_28_120000` / `130000`: check against upstream before committing.

Placeholder choices:
- Copy: page title "Beheer-QR van "X"", intro, "De QR verschijnt zodra de speler verbinding heeft
  gemaakt...", sticker hint "Scan met je telefoon om de wifi en status van dit scherm te beheren.",
  confirm text, status messages, menu label "Beheer-QR (sticker)", `/setup` "Scherm beheren" copy and
  the three Android steps.
- Sticker size 60 mm, QR quiet zone 2 modules, light-grey page style; Play Store URL
  `https://play.google.com/store/apps/details?id=nl.zekerzichtbaar.narrowcasting` (app may not be live yet).

Runtime-unverified: the real print dialog/print layout (screen rendering checked in Chrome from a
rendered copy, print media not emulated); the `/schermen` menu item in a running CMS (no Vite build);
the authenticated page in a live CMS (feature tests only).


## Slice: 4 — iOS management
Shipped:
- Kit: `ManageLink` / `ScreenLink` (k wins, broken k never falls through), `ManageCrypto` (HKDF, frames,
  directions), requests/responses/status payloads (lossy, unknown fields ignored), `ManageTransport`
  protocol, `ManageBLETransport` (CoreBluetooth, continuations failed on disconnect), `MockManageTransport`
  + `MockManagePi` (real crypto, Pi rules, knobs: drop link mid-connect, malformed, no notify, version,
  no service), `ManageClient` state machine (rules in the header).
- UI: `ManageFlowView` (searching, connecting, Bluetooth, status, network list + reused `PasswordSheet`,
  working, joined, failed with fallback, QR verlopen, update app, unsupported, error + retry), `ManageScreen`,
  `ScreenLinkView`; App (URL, universal link, scanner, typed link) and Clip route through `ScreenLink`.
- Tests: 36 new (55 total): spec vectors byte for byte (Ks, request seal, response open), directions,
  link parsing, candidates by RSSI, all-reject expired, locked Pi, outdated, unsupported, malformed + retry,
  scan + switch, wrong password fallback, changed password offline, link drop mid-job (ok and failed via
  `status.job`), idle re-handshake, rotation -> expired, rising ctr + captured connect rejected, replayed
  response ignored, foreign notification -> one read, missed notifications -> read, ops hidden, demo.
  Swift-sealed connect opened by `blemanage.unseal`. `ManageFlowUITests` (status -> wrong password ->
  fallback -> switch) next to the unchanged `ClipFlowUITests`.
- `ios/README.md` (management, `-mockManage`, DEMO sticker); `project.yml` scheme arg `-mockManage`.

Key decisions:
- Management is a separate client/transport, not an extension of `SetupTransport`: the transport moves
  bytes, the client owns candidates/handshake/retry so the mock tests exercise the real rules.
- `NetworkListView` untouched (UI test IDs); only `NetworkRow` went from private to internal for reuse.
- 0x0e (and any non-0x08 ATT code) shows an error screen with "Opnieuw proberen" instead of looping.
- A `connect` whose write never got acknowledged is reported as "verbinding viel weg" unless the next
  status shows that job still busy (then it is followed to its end).

Notes/leftovers:
- Android at parity is the next slice.
- A failed phone job stays visible on the status screen ("Laatste wifi-wijziging") until the agent restarts.

Placeholder choices:
- Copy: "Scherm zoeken…", "Ga dichter bij de speler staan…", "Verbinden met het scherm…", "Het scherm
  wisselt van netwerk…", "Verbonden met X", "Het scherm is online." / "…maar de server is nog niet
  bereikbaar.", "Dat lukte niet", "Naar status (X)", "QR verlopen" + `ManageClient.expiredMessage`,
  `droppedMessage`, `bugMessage`, "Werk de app bij…", "Beheer nog niet beschikbaar…", Bluetooth texts,
  status labels (Wifi, Kabel, IP-adres, Toont: Content / Wacht op content / Koppelcode / Wifi instellen,
  Gekoppeld, Spelerversie, Apparaat, "Offline: de server is niet bereikbaar"), list header/footer
  ("Lukt het niet, dan gaat het scherm terug…"), HomeView / Clip unknown-link / paste-alert text now
  mention the sticker.
- `-mockManage` / `MOCK_MANAGE`, DEMO sticker URL with the spec's vector secret, mock Pi data
  ("Kantine links", Kantine 82 %, 192.168.1.42, v0.9.7; switch -> 74 %, 192.168.1.57).
- Timings: 2 s candidate window, 20 s not-found, 15 s connect, 1.5 s read fallback, 8 s response wait,
  10 s poll, 180 s job, 60 s scan, 1 s rescan delay. A separate `unsupported` phase.

Runtime-unverified: all of `ManageBLETransport` (no Bluetooth in the Simulator): subscribe-before-SESSION,
long reads/writes, `CBATTError` -> 0x08/0x0e mapping, `CBError` as disconnect, `didModifyServices`,
RSSI ordering on real advertisements; camera scanning of a sticker; App Clip invocation from a printed QR.
Verified in the Simulator: Clip via `-mockManage` (UI test, screenshots) and the full App via
`simctl openurl` of the DEMO sticker URL without mock flags (`shots-ios/app-demo-sticker-url.png`).

## Slice: 5 — Android management
Shipped:
- `setup/ManageProtocol.kt`: `ManageLink` (secret never in `toString`), `ScreenLink` (k wins, a broken or
  value-less `k` never falls through), `ManageCrypto` (HKDF, frames, directions, unsigned 32-bit ctr as `Long`),
  `ManageRequest` (password not in `toString`), lossy `ManageResponse` (wrong-typed field = missing), `ManageStatus`.
- `ManageTransport.kt` (suspend contract, `recheck()`), `ManageBleTransport.kt` (two ScanFilters, advertised
  name, max RSSI, 2 s window, 4-scans-per-30 s throttle, op queue, MTU 517, require management service,
  connect returns after the RESPONSE CCCD write), `MockManageTransport.kt` (`MockManagePi` = port of the Swift
  mock, synchronous notify inside the write), `ManageClient.kt` (port of `ManageClient.swift`, StateFlows).
- UI `ui/ManageFlow.kt` (searching, connecting, Bluetooth incl. LOCATION_OFF, status, network list + reused
  `PasswordSheet`, working, joined, failed with fallback, QR verlopen, update app, unsupported, error + retry);
  routing in `MainActivity` (App Link, extra `mockManage`, DEMO sticker) and `Home.kt` (scanner, typed link).
- `Protocol.kt`: query parsing and network-row decoding shared (`queryItems`, `value`, `deviceId`,
  `WifiNetwork.fromRows`), setup parsing unchanged. `NetworkRow` (+ optional current-network check),
  `PasswordSheet`, `signalIcon`, `BluetoothProblemView` (+ `managing`), `rememberBluetoothFixes` (key +
  recheck lambda) made internal; setup behaviour and test tags unchanged.
- Tests: 38 new unit tests (65 total): spec vectors byte for byte, directions, unsigned ctr, link parsing,
  payloads, every iOS `ManageClientTests` case + "answer before the write callback" (mutation-checked: fails
  when `re` is registered after the write). Kotlin-sealed connect opened by `blemanage.unseal`.
  `ManageFlowTest` (4 instrumented: status -> wrong password -> fallback -> switch, DEMO sticker App Link,
  typed sticker link, broken `k` -> home) next to the unchanged `SetupFlowTest` (8/8 pass on the emulator).
- `android/README.md`: management, `mockManage`, DEMO sticker, Kotlin -> Python check.

Key decisions:
- `ManageClient` owns a child `SupervisorJob` scope per start; `stop()` cancels it. `CancellationException`
  is always rethrown. "Establishing" = runner job active (no flag to race on cancellation).
- Waiters complete at the end of `apply`, so an awaiting caller sees the updated status (Swift resumes later too).
- GATT status mapping: 0x08 -> rejected, 0x0e -> malformed, 133 / 257 (Android stack errors, not ATT) ->
  disconnected (reconnect), anything else -> bug screen. A 10 s hung GATT op = lost link.
- Resume and permission results call `recheckBluetooth()` (idempotent), never `start()`, so no second establish loop.

Notes/leftovers:
- Only user-gated acceptance remains (OVERVIEW DoD items annotated `not verified` with the hardware reason).

Placeholder choices:
- Copy adapted from iOS: Bluetooth off "Zet Bluetooth aan. Het scherm wordt via Bluetooth beheerd." (no
  Bedieningspaneel), outdated "...Werk de app bij in de Play Store..." (not App Store); home / unknown-link /
  scanner / paste texts mention "de QR-sticker op de speler" / "of de sticker" as on iOS.
- Icons: `ic_warning` for outdated / unsupported / error (iOS uses arrow.down.app / triangle), new `ic_check`
  (current network) and `ic_chevron_left` ("Status" back) Material Symbols paths; status sections as
  upper-case muted headers over cards.

Runtime-unverified: all of `ManageBleTransport` (the emulator has no Bluetooth to a Pi): scan filters and
advertised-name matching, RSSI window, MTU/long reads and writes, CCCD subscribe, GATT status mapping,
`onServiceChanged`, throttle; permission / Bluetooth-off screens on a phone; App Link verification from a
printed sticker. Verified on the emulator against the mock: the full management UI (shots-android/).
