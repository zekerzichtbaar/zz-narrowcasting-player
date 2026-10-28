# Narrowcasting Android app

The Android counterpart of [`../ios/`](../ios/): it sets up a screen's wifi over Bluetooth. The TV's QR
code (`https://app.zekerzichtbaar.tv/setup?d=..&k=..`) opens the app, which finds the Pi, lists
its networks and sends the chosen one, encrypted with the token from the QR. Same protocol, same
flow and the same Dutch texts as the iOS app. Protocol: [`../docs/ble-setup.md`](../docs/ble-setup.md).

Android has no equivalent of App Clips, so the app has to be installed from the Play Store.
Once it's installed, the QR opens it straight from the camera (App Link). The app also has its own QR
scanner and a "paste the link" field.

| Part | What |
|---|---|
| `setup/Protocol.kt` | Link parsing, AES-GCM frames (HKDF key), STATUS/NETWORKS payloads, validation |
| `setup/SetupClient.kt` | The state machine, a one-to-one port of `SetupClient.swift` |
| `setup/BleTransport.kt` | GATT central; `MockTransport.kt` is the simulated Pi |
| `ui/` | Compose flow: home + QR scanner, network list, password sheet, progress, pairing code |
| `MainActivity.kt` | Entry point (launcher, App Link), `SetupModel` owns the flow across rotation |

Package `nl.zekerzichtbaar.narrowcasting`, minSdk 26 (Android 8), targetSdk 36.

## Build

You need JDK 17 or newer and the Android SDK with platform 37 (`sdkmanager "platforms;android-37.0"`).
Android Studio works as is (open `android/`).

```sh
cd android
./gradlew assembleDebug            # app/build/outputs/apk/debug/app-debug.apk
./gradlew bundleRelease            # app/build/outputs/bundle/release/app-release.aab (Play Store)
```

Release builds are signed with the upload key from `android/keystore.properties` (git-ignored):

```properties
storeFile=/path/to/upload.jks
storePassword=...
keyAlias=upload
keyPassword=...
```

Without that file, a release build is signed with the debug key so it installs locally; Play
rejects such a bundle.

## Test

```sh
./gradlew testDebugUnitTest            # protocol, crypto against the Pi's test vector, state machine, mock
./gradlew connectedDebugAndroidTest    # UI flow against the simulated Pi, on an emulator
```

The emulator's Bluetooth can't reach a Pi. To run against a simulated one, launch with the extra
`mockSetup`:

```sh
adb shell am start -n nl.zekerzichtbaar.narrowcasting/.MainActivity --ez mockSetup true
```

In the mock, password `fout1234` fails and anything else gets the screen online with pairing code `K7Q-4M2`.
Release builds have the same mock behind the demo link
`https://app.zekerzichtbaar.tv/setup?d=DEMO&k=AAECAwQFBgcICQoLDA0ODw` (device IDs are hex, so
`DEMO` is never a real screen). Play review can use it, so keep it working.

## Run on a phone against a real screen

1. Install the debug APK (`./gradlew installDebug` with the phone connected over USB).
2. Put the screen in *Wifi instellen* and open its link: scan the QR with the app's *Scan de QR-code*,
   or with the camera if the App Link is verified (see below). You can also send it over adb:
   `adb shell am start -a android.intent.action.VIEW -d 'https://app.zekerzichtbaar.tv/setup?d=1E2F\&k=<k>'`.
3. Allow *Apparaten in de buurt* (Android 12+), or Location on Android 8 to 11. On Android 8 to 11
   Location must also be switched on, otherwise scans find nothing.

## Release

- **App Link:** the CMS must serve `https://app.zekerzichtbaar.tv/.well-known/assetlinks.json`:

  ```json
  [{"relation": ["delegate_permission/common.handle_all_urls"],
    "target": {"namespace": "android_app", "package_name": "nl.zekerzichtbaar.narrowcasting",
               "sha256_cert_fingerprints": ["<SHA-256 of the Play app signing key>"]}}]
  ```

  Use the fingerprint from Play Console -> Test and release -> App integrity -> App signing (and the
  upload or debug key's fingerprint as a second entry for builds installed outside Play). If it's
  missing or wrong, Android opens the QR in the browser instead of the app.
- **CMS `/setup` page:** on Android it should link to the Play Store listing, since without the app the
  QR lands there. The hotspot route stays as the fallback.
- **Upload with fastlane** (`brew install fastlane`), from `android/`. The Play service account goes in
  `fastlane/.env` (git-ignored) as `PLAY_JSON_KEY_PATH=...`; it's the same Zeker Zichtbaar account as BC SDVB.

  ```sh
  fastlane android verify_auth    # credentials work and the app exists in Play Console
  fastlane android release        # signed bundle, next versionCode, internal track as a draft
  fastlane android listing        # Dutch texts, icon, feature graphic, screenshots (needs a release on Play)
  fastlane android release track:production status:completed version_name:1.1
  ```

  Play Console has to create the app itself: *Create app*, package `nl.zekerzichtbaar.narrowcasting`.
  Until the app has been published once, Play only accepts draft releases, so roll out the first
  one by hand. Console also needs the privacy policy, data safety, content rating and target
  audience forms. In data safety, say the app collects no data. For Bluetooth, say it's used only to set up
  the screen and doesn't derive location (`neverForLocation`). Give review the demo link.
