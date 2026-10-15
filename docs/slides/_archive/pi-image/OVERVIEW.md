> **Completed** (pending acceptance: 3 items, see ACCEPTANCE.md) — 2026-10-15

# pi-image — flashable Raspberry Pi image with pi-player preinstalled

## Goal
Produce a ready-to-flash `.img.xz` (Raspberry Pi OS Lite 64-bit Bookworm + pi-player) that
boots straight into the `WIFI_SETUP` screen on first power-on — no SSH install, no network
needed. Built automatically by GitHub Actions and attached to a GitHub Release, so anyone can
flash it with Raspberry Pi Imager / Etcher.

## Scope
- Build script (`image/build.sh` or similar): download the official Pi OS Lite arm64 image,
  verify its checksum, grow the rootfs, loop-mount, chroot via `qemu-user-static` (binfmt),
  run `install.sh` from the checkout, clean up, shrink/compress to `.img.xz`.
- Make `install.sh` chroot-safe (env flag or detection): no `systemctl restart`/`daemon-reload`,
  no `rfkill`/`raspi-config` calls that fail; `systemctl enable` still works.
- Wifi unblocked at first boot **without** `raspi-config` (it does `iw reg set` + `rfkill`,
  impossible in a chroot): replicate its file-level effect — `/var/lib/systemd/rfkill/*:wlan`
  = `0` and the regdomain (`cfg80211.ieee80211_regdom=XX` on the kernel cmdline, or whatever
  the image's own `raspi-config` writes — verify in the first slice). Critical: a blocked wlan
  means no `WIFI_SETUP` screen.
- Fixed user + SSH via Pi OS's official headless mechanism: `userconf.txt` (`user:sha512-hash`)
  and an empty `ssh` file on the boot partition. Pi OS creates the user at first boot and skips
  the `userconfig` wizard itself (which would otherwise fight pi-player over tty1).
- Bake the CMS URL into `/etc/pi-player/config.toml` (build input `CMS_URL`); the repo
  default `http://localhost:8080` is useless on a flashed card.
- GitHub Actions workflow: on tag push (and `workflow_dispatch`) build the image and upload it
  to a Release (plus sha256).
- README section: how to flash, default credentials, how to change the password.

## Non-goals
- pi-gen or a custom OS build.
- Local Docker build on macOS (script must run on any Linux with root; CI is the supported path).
- Over-the-air OS updates / A-B partitions. Upgrades of pi-player stay `install.sh` over SSH.
- Pi models other than Pi 4 (Pi 5/3 may work, not tested).
- Per-device secrets baked into the image (token, pairing) — every flashed card pairs itself.

## Key decisions & constraints
- **Approach: stock image + chroot** (decided). Base = official `raspios_lite_arm64` **Bookworm**
  image, pinned by URL + sha256 in the build script (current Pi OS is Trixie — do not pick
  the "latest" link).
- Chroot hygiene (all known Pi-OS-under-qemu breakers): comment out `/etc/ld.so.preload`
  (`libarmmem` crashes every binary under qemu) and restore it; `/usr/sbin/policy-rc.d` exit
  101 so apt postinst never starts services; copy host `/etc/resolv.conf` in and restore;
  bind-mount `/dev /dev/pts /proc /sys`; boot partition mounted at `/boot/firmware` inside the
  chroot so `apply_cmdline` edits the real `cmdline.txt` (must keep the existing
  `init=...firstboot` token so Pi OS's own rootfs expand still runs).
- Grow the image (`truncate` + `parted resizepart 2` + `resize2fs`) before apt: Lite has too
  little free space for GStreamer + libav. Partition number stays 2, so `PARTUUID` in
  `fstab`/`cmdline.txt` stays valid. `zerofree` the rootfs before `xz`.
- Leave `/etc/machine-id` empty and `/etc/ssh/ssh_host_*` absent (base image state) so each
  device gets its own id and host keys at first boot; assert this in the self-check.
- **Build in GitHub Actions** on `ubuntu-latest` (`qemu-user-static`, `binfmt-support`,
  `losetup`, `parted`/`resize2fs`, `xz`). Image artifact is ~0.5–1 GB compressed → Release
  asset, not a workflow artifact.
- **Fixed user + SSH on** (decided). Username/password are **workflow inputs with a
  documented default**, not repo secrets: the hash sits on the FAT boot partition of every
  distributed image, so a secret buys nothing. Hash with `openssl passwd -6`. Document clearly
  that the default password must be changed after first login.
- Reuse `install.sh` — no second copy of install logic in the build script.
- First boot must keep Pi OS's own rootfs auto-expand working.
- Kernel cmdline tweaks (`apply_cmdline`) apply to the image's boot partition, so no extra
  reboot is needed after flashing.
- `/etc/pi-player/config.toml` in the image = repo default (CMS URL etc.); no per-device state.

## Preconditions & external dependencies
- GitHub repo `zekerzichtbaar/zz-narrowcasting-player` with Actions enabled and permission to
  create Releases (`contents: write`).
- Default username + password (workflow inputs with defaults). `CMS_URL` =
  `https://narrowcasting.zekerzichtbaar.nl` (decided; build default).
- A Raspberry Pi 4 + SD card + HDMI screen for the final hardware test.

## Building blocks
- `install.sh` chroot mode
- Image build script (download/verify, grow, mount, chroot, user/SSH, wifi country, unmount, compress)
- GitHub Actions workflow + Release upload
- README flashing docs

## Definition of Done
- [x] `install.sh` runs without errors inside a chroot (no `systemctl restart`/`rfkill` failures) and still behaves identically on a live Pi.
- [x] Build script exists, pins base image URL + sha256, and produces `pi-player-<version>.img.xz` + `.sha256` on Linux as root.
- [x] In the built image (verified by mounting it in CI): `/opt/pi-player` present, `pi-player.service` enabled, GStreamer + python3 deps installed, `userconf.txt` + `ssh` on the boot partition, rfkill state files `0`, regdomain set, cmdline tokens applied with `init=...firstboot` intact, `ld.so.preload` restored, no `policy-rc.d`, `machine-id` empty, no ssh host keys, `cms_url` = build input.
- [x] `.github/workflows/` builds the image on tag push and `workflow_dispatch` and uploads it to a GitHub Release.
- [x] README has a "Flash the image" section with default credentials and password-change instruction.
- [ ] A tagged release has actually produced a downloadable image. `[user-gated]`
- [ ] Flashed SD card in a Pi 4 with no ethernet boots to the `WIFI_SETUP` screen (hotspot SSID + QR) without manual steps (Pi OS reboots once by itself to expand the rootfs; that is fine). `[user-gated]`
- [ ] After wifi setup on that Pi: pairing screen appears and SSH login with the default user works. `[user-gated]`

## Open questions
- Default username/password values (proposal: `pi` / `pi-player`). `CMS_URL` decided:
  `https://narrowcasting.zekerzichtbaar.nl`.
- `WIFI_COUNTRY` as build input (default NL)?
- Hostname: every card boots as `raspberrypi` (mDNS collisions with several screens on one
  LAN). Accept, or derive `pi-player-<serial>` at first boot? Not needed for the DoD.
