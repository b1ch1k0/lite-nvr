#!/bin/bash
# Applies/updates the NVR on this host. Idempotent: re-run to upgrade.
# Called by ../setup.sh (from a release tarball) and by the RPM %post (--no-copy).
#   --port N               nginx listen port (default: keep current, else 80)
#   --remove-default-site  drop nginx's distro default site (Debian/Ubuntu)
#   --no-copy              files are already in /opt/nvr (RPM)
set -euo pipefail
trap 'echo "install.sh: failed at line $LINENO" >&2' ERR
cd "$(dirname "$0")/.."
PORT=""; REMOVE_DEFAULT=0; COPY=1
while [ $# -gt 0 ]; do case "$1" in
  --port) PORT="$2"; shift 2 ;; --remove-default-site) REMOVE_DEFAULT=1; shift ;; --no-copy) COPY=0; shift ;;
  *) echo "unknown option $1"; exit 1 ;; esac; done

. /etc/os-release
case " ${ID} ${ID_LIKE:-} " in
  *" debian "*|*" ubuntu "*) FAMILY=debian; WEBUSER=www-data; NGX=/etc/nginx/sites-available/nvr ;;
  *" rhel "*|*" fedora "*|*" centos "*) FAMILY=rhel; WEBUSER=nginx; NGX=/etc/nginx/conf.d/nvr.conf ;;
  *) echo "unsupported distro: $ID"; exit 1 ;;
esac
if [ -z "$PORT" ]; then   # keep the current port on upgrades (file is absent on a fresh install)
  PORT=$( { sed -n 's/^ *listen \([0-9]*\) default_server;.*/\1/p' "$NGX" 2>/dev/null || true; } | head -1)
  PORT=${PORT:-80}
fi

# --- user and directories
id nvr >/dev/null 2>&1 || useradd --system --home-dir /var/lib/nvr --shell /usr/sbin/nologin nvr
install -d -o nvr -g nvr -m 750 /var/lib/nvr /srv/nvr
chown nvr:nvr /srv/nvr; chmod 750 /srv/nvr
install -d -o nvr -g nvr -m 750 /srv/nvr/rec /srv/nvr/events /srv/nvr/posters
usermod -aG nvr "$WEBUSER"

# --- application files
install -d -m 755 /opt/nvr /opt/nvr/bin
if [ "$COPY" = 1 ]; then
  rm -rf /opt/nvr/app && cp -r app /opt/nvr/app
  install -m 755 bin/nvr-run bin/nvrctl /opt/nvr/bin/
  cp requirements.txt /opt/nvr/
  rm -rf /opt/nvr/deploy && cp -r deploy /opt/nvr/deploy
  install -m 644 deploy/nvr-go2rtc.service deploy/nvr.service /etc/systemd/system/
  # sudo's secure_path on RHEL doesn't include /usr/local/bin
  if [ "$FAMILY" = rhel ]; then ln -sf /opt/nvr/bin/nvrctl /usr/bin/nvrctl; else ln -sf /opt/nvr/bin/nvrctl /usr/local/bin/nvrctl; fi
fi
# go2rtc moved from /usr/local/bin (older installs) to /opt/nvr/bin
[ -x /opt/nvr/bin/go2rtc ] || { [ -x /usr/local/bin/go2rtc ] && cp /usr/local/bin/go2rtc /opt/nvr/bin/go2rtc; } || true
[ -x /opt/nvr/bin/go2rtc ] || { echo "go2rtc missing in /opt/nvr/bin — run setup.sh"; exit 1; }

# --- python environment: RPM ships /opt/nvr/lib, script installs use a venv
if [ -d /opt/nvr/lib ]; then
  PY=$(cat /opt/nvr/python); PP=/opt/nvr/lib
else
  PYBIN=python3
  if [ "$FAMILY" = rhel ] && command -v python3.11 >/dev/null; then PYBIN=python3.11; fi
  if [ ! -x /opt/nvr/venv/bin/python ] || ! /opt/nvr/venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    rm -rf /opt/nvr/venv; "$PYBIN" -m venv /opt/nvr/venv
  fi
  /opt/nvr/venv/bin/pip install -q --upgrade pip
  /opt/nvr/venv/bin/pip install -q -r /opt/nvr/requirements.txt
  PY=/opt/nvr/venv/bin/python; PP=""
fi
chown -R root:root /opt/nvr

# --- database + go2rtc config (real YAML, so go2rtc can patch it itself)
( cd /opt/nvr && runuser -u nvr -- env NVR_DATA=/var/lib/nvr PYTHONPATH="$PP" "$PY" -c \
  "from app import db, go2rtc; db.init(); go2rtc.write_yaml(go2rtc.desired())" )

# --- nginx
sed "s/listen 80 default_server;/listen ${PORT} default_server;/; s/listen \[::\]:80 default_server;/listen [::]:${PORT} default_server;/" \
    deploy/nginx-nvr.conf > "$NGX"
if [ "$FAMILY" = debian ]; then
  ln -sf "$NGX" /etc/nginx/sites-enabled/nvr
  [ "$REMOVE_DEFAULT" = 1 ] && rm -f /etc/nginx/sites-enabled/default
fi

# --- SELinux (RHEL family): let nginx proxy to the app/go2rtc and read recordings
if command -v getenforce >/dev/null && [ "$(getenforce)" != Disabled ]; then
  setsebool -P httpd_can_network_connect 1
  semanage fcontext -a -t httpd_sys_content_t '/srv/nvr(/.*)?' 2>/dev/null || semanage fcontext -m -t httpd_sys_content_t '/srv/nvr(/.*)?'
  restorecon -R /srv/nvr
  if [ "$PORT" != 80 ]; then semanage port -a -t http_port_t -p tcp "$PORT" 2>/dev/null || semanage port -m -t http_port_t -p tcp "$PORT"; fi
fi
# --- firewalld
if systemctl is-active -q firewalld 2>/dev/null; then
  firewall-cmd -q --permanent --add-port="${PORT}/tcp" && firewall-cmd -q --reload
fi

nginx -t
systemctl daemon-reload
systemctl enable -q --now nvr-go2rtc nvr nginx
systemctl restart nvr-go2rtc nvr
systemctl reload nginx
echo "INSTALLED (port $PORT)"
