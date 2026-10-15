# pi-image — progress

## Current state
- **Done (automation):** `install.sh` chroot mode, `image/build.sh` (build, self-check, compress),
  `.github/workflows/image.yml` (build + Release), README section 0 "Flash the image".
- **Open (user-gated only):** first tagged Release run in CI, Pi 4 boot to WIFI_SETUP, pairing + SSH.
- Key files:
  - `install.sh` — `CHROOT` flag (`PI_PLAYER_CHROOT=1` or `systemd-detect-virt --chroot`) skips
    daemon-reload/restart, `rfkill`, `raspi-config`; `apply_wifi_country` helper (chroot only).
  - `image/build.sh` — env inputs `CMS_URL` `IMAGE_USER` `IMAGE_PASSWORD` `WIFI_COUNTRY` `VERSION`
    `OUT_DIR` `CACHE_DIR` `GROW_MB`; empty values fall back to defaults (`pi` / `pi-player` /
    `https://narrowcasting.zekerzichtbaar.nl` / `NL`). Output `pi-player-$VERSION.img.xz` + `.sha256`.
  - `.github/workflows/image.yml` — tag `v*` → Release for the tag (create `--verify-tag`, or
    `upload --clobber` if it exists); `workflow_dispatch` (inputs user/password/cms_url/
    wifi_country) → prerelease `image-<run_number>`, VERSION `dev-<run>-<sha7>`. Inputs pass via
    env (empty on tag pushes → build.sh defaults, single source of defaults). Base `.img.xz`
    cached by `BASE_SHA256` (parsed from build.sh) in `$RUNNER_TEMP/base`. Binfmt: check
    `/proc/sys/fs/binfmt_misc/qemu-aarch64`, then `update-binfmts`, `systemd-binfmt` restart,
    `tonistiigi/binfmt:qemu-v8.1.5` fallback.
  - `.gitignore` — `image/out/`.
- Base image pinned: `raspios_oldstable_lite_arm64-2026-10-06` (Bookworm), sha256 `9e938c5a…c1af`.
- Base-image invariants: wifi block = NM `WirelessEnabled=false` (no `*:wlan` rfkill files);
  `ld.so.preload` absent; machine-id empty, no host keys; cmdline has
  `init=/usr/lib/raspberrypi-sys-mods/firstboot`. Self-check asserts all DoD item 3 points.
- Build uses per-partition loop devices (`losetup -o/--sizelimit`), grow via `sfdisk -N 2`.
- qemu/x86 path (CI) has **never run yet** — the first CI run is its test.

## Slice: 1 — install.sh chroot mode + image/build.sh
Shipped:
- `install.sh` chroot mode and `apply_wifi_country` helper (tested idempotent + invalid country).
- `image/build.sh`: pinned Bookworm base download/verify, grow, mount, chroot hygiene
  (preload/policy-rc.d/resolv.conf/binds/qemu), runs `install.sh` with `PI_PLAYER_CHROOT=1`,
  bakes `cms_url`, `apt-get clean` + empty apt lists, empties machine-id / removes host keys,
  `userconf.txt` (`openssl passwd -6`) + `ssh`, `zerofree`, read-only self-check of every DoD
  item 3 point (fails the build), `xz -T0`, sha256.
- `.gitignore`: `image/out/`.
Key decisions:
- Wifi unblock replicates raspi-config's chroot behaviour (NM state + cmdline regdom), discovered
  by reading the image's `/usr/bin/raspi-config`; DoD "rfkill state files 0" is vacuous for this
  base (none exist), so NM `WirelessEnabled=true` is checked additionally.
- `sfdisk` instead of `parted` (fewer deps). Base pinned to the `oldstable` (Bookworm) channel.
Notes / leftovers:
- `cmdline.txt.pi-player.bak` (from `apply_cmdline`) ends up on the boot partition; harmless.
- install.sh prints "reboot once" / "no video yet" in chroot mode; cosmetic.
- qemu (x86) path untested locally — watch the first CI run.
Verification: `bash -n` both scripts; `shellcheck` (koalaman/shellcheck:stable) clean; full build
in privileged arm64 Debian container → exit 0, all 34 self-checks ok.
Placeholder choices: default `IMAGE_USER=pi`, `IMAGE_PASSWORD=pi-player` (OVERVIEW proposal),
`WIFI_COUNTRY=NL`, `GROW_MB=1536`, `xz -6`.
Runtime-unverified: image not yet booted on a Pi 4 (user-gated DoD items).

## Slice: 2 — GitHub Actions workflow + README flashing docs
Shipped:
- `.github/workflows/image.yml`: tag `v*` + `workflow_dispatch`, `contents: write`, host deps,
  aarch64 binfmt registration with fallbacks, base-image cache, `sudo --preserve-env=... image/build.sh`,
  chown of outputs, Release publish via `gh` (tag → release; dispatch → prerelease `image-<run>`).
- README: new section "0. Flash the image" (download, sha256 verify on Linux/macOS, Imager "Use
  custom" + answer No to OS customisation, Etcher, first-boot auto-reboot, default `pi`/`pi-player`,
  `passwd`, hostname note, CI + local build instructions). Contents list updated; numbering of
  sections 1–9 untouched so existing anchors stay valid.
Key decisions:
- Workflow passes inputs as env and relies on build.sh's `${VAR:-default}` for tag pushes, so
  defaults live in build.sh (duplicated only as dispatch-form defaults).
- Dispatch builds publish as prereleases `image-<run_number>` on `$GITHUB_SHA`.
- `install.sh` / `image/build.sh` unchanged.
Notes / leftovers:
- First CI run exercises the qemu path for the first time; likely failure points: binfmt
  registration, qemu speed (timeout 120 min), runner disk space (`df` is logged).
- `tonistiigi/binfmt:qemu-v8.1.5` tag assumed to exist (fallback only).
- Open questions from OVERVIEW (hostname collisions) not addressed; documented the manual fix.
Verification: `rhysd/actionlint:1.7.7` (incl. shellcheck on run blocks) → exit 0.
Placeholder choices: README section numbered "0" (to keep existing anchors), release notes text,
prerelease title format, 120 min job timeout, binfmt fallback image tag.
Runtime-unverified: workflow never run on GitHub; README rendering not previewed.
