#!/usr/bin/env bash
# Install or upgrade pi-player on Raspberry Pi OS Lite (Bookworm). Idempotent: re-run = upgrade.
#   sudo ./install.sh
#   wget -qO- https://raw.githubusercontent.com/zekerzichtbaar/zz-narrowcasting-player/master/install.sh | sudo bash
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"
TARBALL="${PI_PLAYER_TARBALL:-https://github.com/zekerzichtbaar/zz-narrowcasting-player/archive/refs/heads/master.tar.gz}"
APP=/opt/pi-player
ETC=/etc/pi-player
STATE=/var/lib/pi-player
UNIT=/etc/systemd/system/pi-player.service

# --- helpers (pure file edits; also exercised against scratch copies) ---------------------

# chrony drop-in from config `ntp_server` (empty = remove it, distro pool only). Prints "changed"
# when the file was written/removed. $1 = server ("" or host), $2 = drop-in path.
apply_ntp() {
  local server="$1" dropin="$2" want
  if [[ -z "$server" ]]; then
    if [[ -e "$dropin" ]]; then rm -f "$dropin"; echo changed; fi
    return 0
  fi
  if [[ ! "$server" =~ ^[A-Za-z0-9.:-]+$ ]]; then
    echo "    ignoring invalid ntp_server '$server'" >&2
    return 0
  fi
  want="# pi-player: written by install.sh from ntp_server in /etc/pi-player/config.toml
server $server iburst prefer"
  if [[ ! -e "$dropin" || "$(cat "$dropin")" != "$want" ]]; then
    install -d -m 0755 "$(dirname "$dropin")"
    printf '%s\n' "$want" > "$dropin"
    echo changed
  fi
}

# Quiet HDMI console: append the kernel cmdline tokens that are missing (a token whose key is
# already present, e.g. loglevel=7, is left as the user set it). Prints "changed" if edited.
CMDLINE_TOKENS=(consoleblank=0 vt.global_cursor_default=0 quiet loglevel=3 logo.nologo)
apply_cmdline() {
  local file="$1" line tok key add=()
  [[ -f "$file" ]] || return 0
  line="$(head -n1 "$file" | tr -d '\r')"
  for tok in "${CMDLINE_TOKENS[@]}"; do
    key="${tok%%=*}"
    if [[ ! " $line " =~ [[:space:]]${key//./\\.}(=[^[:space:]]*)?[[:space:]] ]]; then
      add+=("$tok")
    fi
  done
  (( ${#add[@]} )) || return 0
  [[ -e "$file.pi-player.bak" ]] || cp -p "$file" "$file.pi-player.bak"
  # cmdline.txt must stay one line
  printf '%s %s\n' "$line" "${add[*]}" > "$file.tmp" && mv "$file.tmp" "$file"
  echo changed
}

# HDMI mode from config `display_mode` (e.g. 1920x1080@50): set or replace the kernel's
# video=HDMI-A-1:... token, which kmssink plays in. Empty = leave the cmdline alone.
apply_video_mode() {
  local file="$1" mode="$2" line new
  [[ -f "$file" && -n "$mode" ]] || return 0
  if [[ ! "$mode" =~ ^[0-9]+x[0-9]+@[0-9]+$ ]]; then
    echo "    ignoring invalid display_mode '$mode'" >&2
    return 0
  fi
  line="$(head -n1 "$file" | tr -d '\r')"
  if [[ " $line " =~ [[:space:]]video=HDMI-A-1:[^[:space:]]* ]]; then
    new="${line/${BASH_REMATCH[0]# }/video=HDMI-A-1:$mode}"
  else
    new="$line video=HDMI-A-1:$mode"
  fi
  [[ "$new" != "$line" ]] || return 0
  [[ -e "$file.pi-player.bak" ]] || cp -p "$file" "$file.pi-player.bak"
  printf '%s\n' "$new" > "$file.tmp" && mv "$file.tmp" "$file"
  echo changed
}

[[ "${PI_PLAYER_INSTALL_LIB:-}" == 1 ]] && return 0 2>/dev/null

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi

# Piped from wget (no checkout next to the script): fetch the repo source first.
if [[ ! -d "$SRC/pi_player" ]]; then
  echo "==> downloading $TARBALL"
  SRC="$(mktemp -d)"
  trap 'rm -rf "$SRC"' EXIT
  wget -qO- "$TARBALL" | tar xz -C "$SRC" --strip-components=1
fi

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends chrony python3 \
  python3-pil python3-qrcode fonts-dejavu-core dnsmasq-base \
  python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-libav

echo "==> files"
install -d -m 0755 "$APP"
rm -rf "$APP/pi_player.new"
cp -r "$SRC/pi_player" "$APP/pi_player.new"
find "$APP/pi_player.new" -name '__pycache__' -prune -exec rm -rf {} +
rm -rf "$APP/pi_player"
mv "$APP/pi_player.new" "$APP/pi_player"

install -d -m 0755 "$ETC"
if [[ ! -e "$ETC/config.toml" ]]; then
  install -m 0644 "$SRC/config/config.toml" "$ETC/config.toml"
  echo "    wrote default $ETC/config.toml"
else
  echo "    keeping existing $ETC/config.toml"
  # The WebSocket era default was a 5 min fallback poll; over HTTP the poll is the update signal.
  if grep -q '^manifest_poll = 300$' "$ETC/config.toml"; then
    sed -i 's/^manifest_poll = 300$/manifest_poll = 30/' "$ETC/config.toml"
    echo "    manifest_poll 300 -> 30 (HTTP protocol)"
  fi
fi
install -d -m 0700 "$STATE"

cat > /usr/local/bin/pi-player <<'WRAP'
#!/bin/sh
PYTHONPATH=/opt/pi-player exec /usr/bin/python3 -m pi_player "$@"
WRAP
chmod 0755 /usr/local/bin/pi-player

echo "==> wifi setup (hotspot + captive portal)"
# Hotspot clients resolve every name to the Pi, so phones detect the captive portal.
# 10.42.0.1 = netman.HOTSPOT_IP — keep in sync.
install -d -m 0755 /etc/NetworkManager/dnsmasq-shared.d
cat > /etc/NetworkManager/dnsmasq-shared.d/pi-player-portal.conf <<'DNS'
# pi-player: captive portal — answer every DNS query on the setup hotspot with the Pi itself.
address=/#/10.42.0.1
DNS
# Wifi stays rfkill-blocked on Pi OS until a regulatory country is set (Imager usually does it).
if command -v raspi-config >/dev/null 2>&1 && [[ -z "$(raspi-config nonint get_wifi_country 2>/dev/null || true)" ]]; then
  raspi-config nonint do_wifi_country "${WIFI_COUNTRY:-NL}" || true
  echo "    wifi country set to ${WIFI_COUNTRY:-NL} (override: sudo WIFI_COUNTRY=XX ./install.sh)"
fi
rfkill unblock wifi 2>/dev/null || true

echo "==> time sync (chrony)"
NTP_SERVER="$(PYTHONPATH="$APP" python3 -c 'import sys; from pi_player import config; print(config.load(sys.argv[1])["ntp_server"])' "$ETC/config.toml")"
# Debian's chrony.conf reads conf.d; make sure it still does.
if [[ -f /etc/chrony/chrony.conf ]] && ! grep -q '^confdir /etc/chrony/conf.d' /etc/chrony/chrony.conf; then
  echo 'confdir /etc/chrony/conf.d' >> /etc/chrony/chrony.conf
fi
if [[ -n "$(apply_ntp "$NTP_SERVER" /etc/chrony/conf.d/pi-player.conf)" ]]; then
  systemctl restart chrony 2>/dev/null || true
fi
echo "    ntp_server: ${NTP_SERVER:-<distro pool>}"

echo "==> clean HDMI console"
REBOOT=""
for f in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
  if [[ -f "$f" ]]; then
    DISPLAY_MODE="$(PYTHONPATH="$APP" python3 -c 'import sys; from pi_player import config; print(config.load(sys.argv[1])["display_mode"])' "$ETC/config.toml")"
    if [[ -n "$(apply_cmdline "$f")$(apply_video_mode "$f" "$DISPLAY_MODE")" ]]; then
      REBOOT=1 && echo "    updated $f (backup: $f.pi-player.bak)"
    fi
    break
  fi
done

echo "==> service"
install -m 0644 "$SRC/systemd/pi-player.service" "$UNIT"
systemctl daemon-reload
systemctl enable chrony >/dev/null 2>&1 || true
systemctl enable pi-player.service
systemctl restart pi-player.service

echo "==> done. Logs: journalctl -u pi-player -f"
if [[ -n "$REBOOT" ]]; then
  echo "    kernel cmdline changed: reboot once (console text, HDMI mode): sudo reboot"
fi
if [[ ! -e "$STATE/current.mp4" ]]; then
  echo "    no video yet: copy one to $STATE/current.mp4 (see scripts/make_test_video.sh)"
fi
