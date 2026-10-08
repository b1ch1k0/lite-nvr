#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  NVR installer (run from an extracted release or a git checkout)
#  Ubuntu 22.04/24.04 · Debian 12 · Rocky/Alma/RHEL 9 — x86_64 or arm64
#
#  sudo ./setup.sh [--port 80] [--tz Europe/Berlin] [--admin admin] [--keep-default-site]
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"
PORT=80; TZONE=""; ADMIN="admin"; KEEP_DEFAULT=0
GO2RTC_VERSION=v1.9.14   # the bundled live player is patched against this version
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --tz) TZONE="$2"; shift 2 ;;
    --admin) ADMIN="$2"; shift 2 ;;
    --keep-default-site) KEEP_DEFAULT=1; shift ;;
    -h|--help) sed -n 2,8p "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 1 ;;
  esac
done
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run as root: sudo ./setup.sh"
. /etc/os-release
case " ${ID} ${ID_LIKE:-} " in
  *" debian "*|*" ubuntu "*) FAMILY=debian ;;
  *" rhel "*|*" centos "*|*" fedora "*) FAMILY=rhel; EL=$(rpm -E %rhel) ;;
  *) die "unsupported distribution: $PRETTY_NAME" ;;
esac
case "$(uname -m)" in
  x86_64) ARCH=amd64; grep -qw sse4_2 /proc/cpuinfo || die "CPU lacks SSE4.2 (needed by numpy)" ;;
  aarch64) ARCH=arm64 ;;
  *) die "unsupported CPU architecture: $(uname -m)" ;;
esac

say "1/5 packages ($PRETTY_NAME)"
if [ "$FAMILY" = debian ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ffmpeg nginx python3-venv python3-pip rclone sqlite3 curl ca-certificates \
      openssh-client rsync parted >/dev/null
else
  [ "$EL" -ge 9 ] || die "RHEL family 9 or newer required"
  dnf -y -q install dnf-plugins-core epel-release >/dev/null
  dnf config-manager --set-enabled crb >/dev/null 2>&1 || true
  rpm -q rpmfusion-free-release >/dev/null 2>&1 || \
    dnf -y -q install "https://mirrors.rpmfusion.org/free/el/rpmfusion-free-release-${EL}.noarch.rpm" >/dev/null
  PYPKG=python3.11; [ "$EL" -ge 10 ] && PYPKG=python3
  dnf -y -q install ffmpeg nginx "$PYPKG" "$PYPKG-pip" sqlite rclone curl tar rsync parted openssh-clients \
      policycoreutils-python-utils util-linux >/dev/null
fi

say "2/5 go2rtc $GO2RTC_VERSION"
install -d -m 755 /opt/nvr/bin
if ! /opt/nvr/bin/go2rtc -version 2>/dev/null | grep -q "${GO2RTC_VERSION#v}"; then
  curl -fsSL -o /opt/nvr/bin/go2rtc.new \
    "https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/go2rtc_linux_${ARCH}"
  chmod 755 /opt/nvr/bin/go2rtc.new && mv /opt/nvr/bin/go2rtc.new /opt/nvr/bin/go2rtc
fi

say "3/5 timezone"
[ -n "$TZONE" ] && timedatectl set-timezone "$TZONE"
echo "    $(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone)"

say "4/5 NVR"
ARGS=(--port "$PORT")
if [ "$FAMILY" = debian ] && [ "$KEEP_DEFAULT" = 0 ] && [ "$PORT" = 80 ]; then
  others=""
  for f in /etc/nginx/sites-enabled/*; do
    b=$(basename "$f"); [ -e "$f" ] && [ "$b" != default ] && [ "$b" != nvr ] && others="$others $b"
  done
  [ -z "$others" ] || die "nginx already serves other sites ($others) — use --port 8080 or --keep-default-site"
  ARGS+=(--remove-default-site)
fi
./deploy/install.sh "${ARGS[@]}"

say "5/5 admin account and login link"
CRED=/root/nvr-admin.txt
users=$(runuser -u nvr -- sqlite3 /var/lib/nvr/nvr.db "select count(*) from users" 2>/dev/null || echo 0)
if [ "$users" = 0 ]; then
  /opt/nvr/bin/nvrctl reset-admin "$ADMIN" > "$CRED"; chmod 600 "$CRED"
fi
LINK=$(/opt/nvr/bin/nvrctl link)
if [ -f "$CRED" ]; then sed -i '/^login: /d' "$CRED"; echo "login: $LINK" >> "$CRED"; fi
echo
echo "════════════════════════════════════════════════════════════"
echo "  NVR is ready"
echo "  login:    $LINK"
if [ "$users" = 0 ]; then
  sed -n 's/^admin: /  user:     /p; s/^password: /  password: /p' "$CRED"
  echo "  (saved to $CRED, readable by root only)"
else
  echo "  existing users kept — forgot the password?  sudo nvrctl reset-admin"
fi
echo "  Every other URL answers 404 — bookmark the link.  sudo nvrctl link"
echo "════════════════════════════════════════════════════════════"
