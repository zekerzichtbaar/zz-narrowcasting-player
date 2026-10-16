#!/usr/bin/env bash
# Build a ready-to-flash pi-player image from the official Raspberry Pi OS Lite arm64 (Bookworm)
# image: grow it, chroot in (qemu-user-static on non-arm64 hosts), run install.sh, add the
# headless user + SSH, self-check, compress. Linux + root only (CI: .github/workflows).
#
#   sudo image/build.sh
#   sudo CMS_URL=https://cms.example IMAGE_USER=pi IMAGE_PASSWORD=secret WIFI_COUNTRY=NL \
#        VERSION=1.2.3 OUT_DIR=/tmp/out image/build.sh
#
# Output: $OUT_DIR/pi-player-$VERSION.img.xz + .img.xz.sha256
# Host tools: curl, xz, sha256sum, sfdisk/partx/losetup (util-linux), e2fsck/resize2fs
# (e2fsprogs), zerofree, openssl; on x86 hosts also qemu-user-static + binfmt-support.
set -euo pipefail

# Pinned base: the last Bookworm line ("oldstable"; current Pi OS is Trixie). Bump both together.
BASE_URL=https://downloads.raspberrypi.com/raspios_oldstable_lite_arm64/images/raspios_oldstable_lite_arm64-2026-10-06/2026-10-06-raspios-bookworm-arm64-lite.img.xz
BASE_SHA256=9e938c5a6981c3f498b3b564c527f3e1fd58ef20bbf703be0eea748384a6c1af

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMS_URL="${CMS_URL:-https://app.zekerzichtbaar.tv}"
IMAGE_USER="${IMAGE_USER:-pi}"
IMAGE_PASSWORD="${IMAGE_PASSWORD:-pi-player}"
WIFI_COUNTRY="${WIFI_COUNTRY:-NL}"
GROW_MB="${GROW_MB:-1536}"   # room for GStreamer + libav; Pi OS expands to the card at first boot
OUT_DIR="${OUT_DIR:-$REPO/image/out}"
CACHE_DIR="${CACHE_DIR:-$OUT_DIR}"
VERSION="${VERSION:-$(git -C "$REPO" describe --tags --always --dirty 2>/dev/null || echo dev)}"

NAME="pi-player-$VERSION"
IMG="$OUT_DIR/$NAME.img"
BASE_XZ="$CACHE_DIR/$(basename "$BASE_URL")"
MNT="$OUT_DIR/mnt"
LOOP_ROOT="" LOOP_BOOT=""

log() { echo "==> $*"; }
die() { echo "build.sh: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root (sudo)"
[[ "$(uname -s)" == Linux ]] || die "Linux only (CI is the supported path)"
[[ "$IMAGE_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || die "invalid IMAGE_USER '$IMAGE_USER'"
[[ "$WIFI_COUNTRY" =~ ^[A-Z]{2}$ ]] || die "invalid WIFI_COUNTRY '$WIFI_COUNTRY'"
[[ "$CMS_URL" =~ ^https?://[^[:space:]\"\|]+$ ]] || die "invalid CMS_URL '$CMS_URL'"
for t in curl xz sha256sum sfdisk partx losetup e2fsck resize2fs zerofree openssl chroot; do
  command -v "$t" >/dev/null || die "missing host tool: $t"
done
QEMU=""
if [[ "$(uname -m)" != aarch64 ]]; then
  [[ -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]] || die "no aarch64 binfmt (install qemu-user-static binfmt-support)"
  QEMU="$(command -v qemu-aarch64-static || true)"
fi

# --- loop devices per partition (offsets, no partition scan: works without udev, e.g. Docker) ---
part_loop() { # $1 = partition number, rest = extra losetup flags; prints the loop device
  local n="$1" start size; shift
  read -r start size < <(partx -g -n "$n" -o START,SECTORS "$IMG")
  losetup -f --show "$@" -o $((start * 512)) --sizelimit $((size * 512)) "$IMG"
}
unmount_all() {
  if mountpoint -q "$MNT"; then umount -R "$MNT" || umount -R -l "$MNT"; fi
  [[ -z "$LOOP_BOOT" ]] || losetup -d "$LOOP_BOOT" 2>/dev/null || true
  [[ -z "$LOOP_ROOT" ]] || losetup -d "$LOOP_ROOT" 2>/dev/null || true
  LOOP_ROOT="" LOOP_BOOT=""
}
trap unmount_all EXIT
mount_image() { # $1 = "rw" or "ro"
  local ro=()
  [[ "$1" == ro ]] && ro=(-r)
  LOOP_ROOT="$(part_loop 2 "${ro[@]}")"
  LOOP_BOOT="$(part_loop 1 "${ro[@]}")"
  mkdir -p "$MNT"
  mount -o "$1" "$LOOP_ROOT" "$MNT"
  mount -o "$1" "$LOOP_BOOT" "$MNT/boot/firmware"
}

# --- base image ------------------------------------------------------------------------------
mkdir -p "$OUT_DIR" "$CACHE_DIR"
if [[ ! -f "$BASE_XZ" ]] || ! echo "$BASE_SHA256  $BASE_XZ" | sha256sum -c --quiet - 2>/dev/null; then
  log "downloading $BASE_URL"
  curl -fL --retry 3 -o "$BASE_XZ.part" "$BASE_URL"
  mv "$BASE_XZ.part" "$BASE_XZ"
fi
echo "$BASE_SHA256  $BASE_XZ" | sha256sum -c --quiet - || die "base image checksum mismatch"

log "decompressing + growing by ${GROW_MB} MB"
xz -dc "$BASE_XZ" > "$IMG"
truncate -s "+${GROW_MB}M" "$IMG"
echo ", +" | sfdisk -q --no-reread --no-tell-kernel -N 2 "$IMG"   # partition 2 stays 2: PARTUUID valid
LOOP_ROOT="$(part_loop 2)"
e2fsck -fy "$LOOP_ROOT" >/dev/null || [[ $? -le 1 ]] || die "e2fsck failed"
resize2fs "$LOOP_ROOT" >/dev/null
losetup -d "$LOOP_ROOT"; LOOP_ROOT=""

# --- chroot ----------------------------------------------------------------------------------
log "mounting + preparing chroot"
mount_image rw
CMDLINE_ORIG="$(head -n1 "$MNT/boot/firmware/cmdline.txt")"
PRELOAD_SUM=absent
if [[ -e "$MNT/etc/ld.so.preload" ]]; then
  PRELOAD_SUM="$(sha256sum < "$MNT/etc/ld.so.preload")"
  cp -p "$MNT/etc/ld.so.preload" "$MNT/etc/ld.so.preload.pi-player-build"
  sed -i 's/^[^#]/#&/' "$MNT/etc/ld.so.preload"   # libarmmem crashes binaries under qemu
fi
mv "$MNT/etc/resolv.conf" "$MNT/etc/resolv.conf.pi-player-build"
cp -L /etc/resolv.conf "$MNT/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' > "$MNT/usr/sbin/policy-rc.d"
chmod 0755 "$MNT/usr/sbin/policy-rc.d"
[[ -z "$QEMU" ]] || cp "$QEMU" "$MNT/usr/bin/qemu-aarch64-static"
for d in /dev /dev/pts /proc /sys; do mount --bind "$d" "$MNT$d"; done

SRC_IN=/tmp/pi-player-src
mkdir -p "$MNT$SRC_IN"
cp -r "$REPO/install.sh" "$REPO/pi_player" "$REPO/config" "$REPO/systemd" "$MNT$SRC_IN/"

log "running install.sh in the chroot"
chroot "$MNT" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 \
  PI_PLAYER_CHROOT=1 WIFI_COUNTRY="$WIFI_COUNTRY" bash "$SRC_IN/install.sh"

log "baking cms_url = $CMS_URL"
sed -i "s|^cms_url = .*|cms_url = \"$CMS_URL\"|" "$MNT/etc/pi-player/config.toml"
grep -qx "cms_url = \"$CMS_URL\"" "$MNT/etc/pi-player/config.toml" || die "cms_url not set"

# Apply the user now instead of via userconf.txt: at first boot userconfig.service would run
# cancel-rename, which does `systemctl start getty@tty1`, and that cancels pi-player's queued
# start job (Conflicts=getty@tty1) -> login prompt instead of the player. Pi OS's own script
# renames uid 1000, sets the password, disables userconfig; systemctl start is a no-op in a chroot.
log "user '$IMAGE_USER' (applied in the chroot, not at first boot)"
chroot "$MNT" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 \
  /usr/lib/userconf-pi/userconf "$IMAGE_USER" "$(openssl passwd -6 -stdin <<<"$IMAGE_PASSWORD")"

log "cleaning up the chroot"
chroot "$MNT" apt-get clean
rm -rf "$MNT/var/lib/apt/lists/"* "$MNT$SRC_IN"
for d in /sys /proc /dev/pts /dev; do umount "$MNT$d"; done
rm -f "$MNT/usr/sbin/policy-rc.d" "$MNT/usr/bin/qemu-aarch64-static"
mv -f "$MNT/etc/resolv.conf.pi-player-build" "$MNT/etc/resolv.conf"
if [[ -e "$MNT/etc/ld.so.preload.pi-player-build" ]]; then
  mv -f "$MNT/etc/ld.so.preload.pi-player-build" "$MNT/etc/ld.so.preload"
fi
# Per-device identity is created at first boot: empty machine-id, no ssh host keys.
: > "$MNT/etc/machine-id"
[[ -L "$MNT/var/lib/dbus/machine-id" ]] || rm -f "$MNT/var/lib/dbus/machine-id"
rm -f "$MNT"/etc/ssh/ssh_host_*

log "ssh (boot partition)"
: > "$MNT/boot/firmware/ssh"

unmount_all
log "zerofree"
LOOP_ROOT="$(part_loop 2)"
zerofree "$LOOP_ROOT"
losetup -d "$LOOP_ROOT"; LOOP_ROOT=""

# --- self-check (read-only) ------------------------------------------------------------------
log "self-check"
mount_image ro
FAIL=0
check() { # $1 = description, rest = test command
  local what="$1"; shift
  if "$@"; then echo "    ok   $what"; else echo "    FAIL $what"; FAIL=1; fi
}
pkg_installed() { # dpkg status paragraph for $1 says "install ok installed"
  awk -v p="$1" 'BEGIN { RS = ""; FS = "\n" }
    { pk = 0; ok = 0
      for (i = 1; i <= NF; i++) { if ($i == "Package: " p) pk = 1; if ($i == "Status: install ok installed") ok = 1 }
      if (pk && ok) found = 1 }
    END { exit !found }' "$MNT/var/lib/dpkg/status"
}
rfkill_wlan_zero() {
  local f
  for f in "$MNT"/var/lib/systemd/rfkill/*:wlan; do
    [[ -e "$f" ]] || continue
    [[ "$(cat "$f")" == 0 ]] || return 1
  done
}
cmdline_has() { [[ " $CMDLINE " == *" $1 "* ]]; }
preload_restored() {
  if [[ "$PRELOAD_SUM" == absent ]]; then [[ ! -e "$MNT/etc/ld.so.preload" ]]
  else [[ "$(sha256sum < "$MNT/etc/ld.so.preload")" == "$PRELOAD_SUM" ]]; fi
}
no_host_keys() { ! compgen -G "$MNT/etc/ssh/ssh_host_*" >/dev/null; }
user_ok() {
  grep -q "^$IMAGE_USER:x:1000:" "$MNT/etc/passwd" && grep -Eq "^$IMAGE_USER:\\\$6\\\$[^:]+:" "$MNT/etc/shadow"
}

CMDLINE="$(head -n1 "$MNT/boot/firmware/cmdline.txt")"
CMDLINE_TOKENS=()
eval "$(PI_PLAYER_INSTALL_LIB=1 bash -c ". '$REPO/install.sh'; declare -p CMDLINE_TOKENS")"

check "/opt/pi-player present" test -f "$MNT/opt/pi-player/pi_player/__main__.py"
check "pi-player.service enabled" test -L "$MNT/etc/systemd/system/multi-user.target.wants/pi-player.service"
for p in python3 python3-gi python3-pil python3-qrcode gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav gstreamer1.0-gl chrony dnsmasq-base; do
  check "package $p" pkg_installed "$p"
done
check "user $IMAGE_USER (uid 1000, sha512 password)" user_ok
check "userconfig.service disabled" test ! -e "$MNT/etc/systemd/system/multi-user.target.wants/userconfig.service"
check "no userconf.txt" test ! -e "$MNT/boot/firmware/userconf.txt"
check "ssh on boot partition" test -e "$MNT/boot/firmware/ssh"
check "rfkill wlan state files 0" rfkill_wlan_zero
check "NetworkManager WirelessEnabled=true" grep -qx 'WirelessEnabled=true' "$MNT/var/lib/NetworkManager/NetworkManager.state"
check "regdomain $WIFI_COUNTRY on cmdline" cmdline_has "cfg80211.ieee80211_regdom=$WIFI_COUNTRY"
for t in "${CMDLINE_TOKENS[@]}"; do check "cmdline $t" cmdline_has "$t"; done
check "cmdline init=...firstboot intact" cmdline_has "init=/usr/lib/raspberrypi-sys-mods/firstboot"
check "cmdline root= unchanged" cmdline_has "$(grep -o 'root=[^ ]*' <<<"$CMDLINE_ORIG")"
check "cmdline.txt is one line" test "$(wc -l < "$MNT/boot/firmware/cmdline.txt")" -eq 1
check "ld.so.preload restored" preload_restored
check "no policy-rc.d" test ! -e "$MNT/usr/sbin/policy-rc.d"
check "no qemu binary left" test ! -e "$MNT/usr/bin/qemu-aarch64-static"
check "resolv.conf restored" test ! -e "$MNT/etc/resolv.conf.pi-player-build"
check "machine-id empty" test ! -s "$MNT/etc/machine-id"
check "no ssh host keys" no_host_keys
check "cms_url = $CMS_URL" grep -qx "cms_url = \"$CMS_URL\"" "$MNT/etc/pi-player/config.toml"
unmount_all
[[ $FAIL -eq 0 ]] || die "self-check failed ($IMG kept for inspection)"

# --- compress --------------------------------------------------------------------------------
log "compressing $NAME.img.xz"
rm -f "$IMG.xz"
xz -T0 -6 "$IMG"
(cd "$OUT_DIR" && sha256sum "$NAME.img.xz" > "$NAME.img.xz.sha256")
rmdir "$MNT" 2>/dev/null || true
log "done: $OUT_DIR/$NAME.img.xz"
cat "$OUT_DIR/$NAME.img.xz.sha256"
