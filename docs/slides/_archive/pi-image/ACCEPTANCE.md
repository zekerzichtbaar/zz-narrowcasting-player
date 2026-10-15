# Acceptance checklist — pi-image

Code-complete. These three Definition-of-Done items can only be verified by you (CI run, real hardware).
Automation evidence so far: a full `image/build.sh` run in a privileged arm64 `debian:bookworm` container
passed all 34 self-checks (585 MB `.img.xz`); `actionlint` passes on the workflow; the qemu/x86 build
path has never run.

## 1. Tagged release produces a downloadable image

First real run of the qemu/x86 path. Expect 30–90 min on `ubuntu-latest`.

```bash
git add -A && git commit            # commit this task first (see suggested commit)
git push origin master
git tag v0.4.0 && git push origin v0.4.0
```

Or: GitHub → *Actions* → *image* → *Run workflow* (inputs: user, password, CMS URL, wifi country) → publishes a
prerelease named `image-<run_number>`.

Expected: https://github.com/zekerzichtbaar/zz-narrowcasting-player/releases/tag/v0.4.0 has
`pi-player-v0.4.0.img.xz` and `pi-player-v0.4.0.img.xz.sha256`. The job log shows every `ok:` self-check line
and no `FAIL`.

If it fails: most likely spots are binfmt registration (`Register aarch64 binfmt` step, fallback
`tonistiigi/binfmt:qemu-v8.1.5`, tag confirmed to exist) or apt under qemu inside the chroot. Run
`/implement pi-image` with the failing step's log; the fix belongs in `image/build.sh` / `.github/workflows/image.yml`.

- [ ] Release has `.img.xz` + `.sha256`

## 2. Pi 4 without ethernet boots to `WIFI_SETUP`

```bash
curl -LO https://github.com/zekerzichtbaar/zz-narrowcasting-player/releases/download/v0.4.0/pi-player-v0.4.0.img.xz
curl -LO https://github.com/zekerzichtbaar/zz-narrowcasting-player/releases/download/v0.4.0/pi-player-v0.4.0.img.xz.sha256
shasum -a 256 -c pi-player-v0.4.0.img.xz.sha256     # macOS; Linux: sha256sum -c
```

Flash with Raspberry Pi Imager (*Use custom*, answer **No** to OS customisation) or Etcher. Insert in a Pi 4 with
HDMI, **no ethernet cable**, power on.

Expected: Pi OS reboots once by itself (rootfs expand), then within about 30 s of the second boot the screen
shows *Set up wifi* with hotspot SSID, password and QR code. No keyboard, no manual step.

Not expected: login prompt, black screen, `rfkill` / "wifi blocked" message, raspi-config wizard.

- [ ] `WIFI_SETUP` screen shown without manual steps

## 3. After wifi setup: pairing screen + SSH

Scan the QR on a phone, pick your wifi in the captive-portal sheet, enter the password.

Expected: screen switches to the pairing code screen (README section 2). Then from your laptop on the same LAN:

```bash
ssh pi@<pi-ip>        # password: pi-player
passwd                # change it
```

Expected: login succeeds, `systemctl is-active pi-player` prints `active`, `cat /etc/machine-id` is non-empty
(generated at first boot), `ls /etc/ssh/ssh_host_*` shows freshly generated keys.

- [ ] Pairing screen appears
- [ ] SSH login with `pi` / `pi-player` works

## Placeholder choices made during the build (change if you disagree)

- Defaults: `IMAGE_USER=pi`, `IMAGE_PASSWORD=pi-player`, `WIFI_COUNTRY=NL`, `GROW_MB=1536`, `xz -6`.
- Base image pinned: raspios oldstable lite arm64 2026-10-06 (Bookworm), sha256 in `image/build.sh`.
- Workflow: 120-minute job timeout; prerelease title `image-<run_number>`; release notes text.
- README: new section numbered "0" so existing anchors keep working. Rendering not previewed.
