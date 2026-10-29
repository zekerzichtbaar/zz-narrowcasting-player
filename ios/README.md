# Narrowcasting iOS app + App Clip

Sets up a screen's wifi over Bluetooth: the TV's QR code opens the App Clip "Scherm instellen",
which finds the Pi, lists its networks and sends the chosen network encrypted with the token
from the QR. Protocol: [`../docs/ble-setup.md`](../docs/ble-setup.md).

The QR sticker on a paired player (`/setup?d=..&s=..`, no `k`) opens management instead: the
screen's status and switching its wifi, also while it is online. Protocol:
[`../docs/ble-management.md`](../docs/ble-management.md). `ScreenLink` picks the flow (`k` wins).

| Part | What |
|---|---|
| `WifiSetupKit/` | Swift package. `WifiSetupKit`: protocol, crypto, CoreBluetooth transport, mock Pi, `SetupClient` state machine. `WifiSetupUI`: the shared SwiftUI flow |
| `Clip/` | App Clip `nl.zekerzichtbaar.narrowcasting.Clip`, opened by the QR URL |
| `App/` | Full app `nl.zekerzichtbaar.narrowcasting`: same flow, plus a QR scanner (Apple requires the full app to offer the clip's feature) |
| `ClipUITests/` | XCUITest walking the clip against the mock Pi |

## Generate and build

The Xcode project is generated (and git-ignored): `brew install xcodegen`, then

```sh
cd ios
xcodegen generate
open Narrowcasting.xcodeproj
```

Signing: `DEVELOPMENT_TEAM` at the top of `project.yml`. App Clips and Associated Domains need a
paid team; a free Personal Team can't sign them.

## Test

```sh
cd ios/WifiSetupKit && swift test        # protocol, crypto vs the Pi's test vector, state machine
cd ios && xcodebuild -project Narrowcasting.xcodeproj -scheme NarrowcastingClip \
  -destination 'platform=iOS Simulator,name=iPhone 18 Pro' test   # UI flow against the mock Pi
```

The Simulator has no Bluetooth: launch with `-mockSetup` (scheme option, off by default) or env
`MOCK_SETUP=1` for a simulated Pi. In the mock, password `fout1234` fails and anything else gets the
screen online with pairing code `K7Q-4M2`. `-mockManage` (env `MOCK_MANAGE=1`) opens a sticker link
against a simulated paired Pi with the real management crypto; there too `fout1234` fails (and falls
back to "Kantine"). A Simulator with the full app installed won't launch the clip; uninstall the app
first.

Release builds have the same mock behind the demo link
`https://app.zekerzichtbaar.tv/setup?d=DEMO&k=AAECAwQFBgcICQoLDA0ODw` (device IDs are hex, so `DEMO` is
never a real screen). App Review uses it, so keep it working. The sticker equivalent,
`https://app.zekerzichtbaar.tv/setup?d=DEMO&s=0123456789abcdefghijklmnopqrstuvwxyzABCDEFG`, opens
management against the simulated Pi.

## Run on an iPhone against a real screen

1. Run the `NarrowcastingClip` scheme on the device. The scheme sets `_XCAppClipURL` to a sample
   link; replace its `d`/`k` with the screen's (`d` = the 4 characters after `Narrowcast-`, `k`
   from the QR on the TV), or use step 2.
2. To open the clip from the TV's QR code before App Store approval: on the iPhone, Settings ->
   Developer -> App Clips Testing -> Local Experiences -> Register. URL prefix
   `https://app.zekerzichtbaar.tv/setup`, bundle ID `nl.zekerzichtbaar.narrowcasting.Clip`, any title.
   Then scan the QR with the Camera app.

## Release

- The CMS serves `/.well-known/apple-app-site-association` with `appclips.apps` =
  `["<TEAM>.nl.zekerzichtbaar.narrowcasting.Clip"]` and `applinks` for `/setup`.
- App Store Connect: upload the app (the clip ships inside it), then App Clip -> Advanced App Clip
  Experiences -> add `https://app.zekerzichtbaar.tv/setup` (a URL prefix, so every screen's
  `?d=..&k=..` matches). QR codes are a physical invocation: keep the clip under 15 MB.
