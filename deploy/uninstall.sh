#!/bin/bash
# Removes the NVR services and program files. Recordings and the database are KEPT unless --purge.
#   sudo /opt/nvr/deploy/uninstall.sh [--purge]
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
PURGE=0; [ "${1:-}" = --purge ] && PURGE=1
systemctl disable --now nvr nvr-go2rtc 2>/dev/null || true
rm -f /etc/systemd/system/nvr.service /etc/systemd/system/nvr-go2rtc.service
rm -rf /etc/systemd/system/nvr.service.d
rm -f /etc/nginx/sites-enabled/nvr /etc/nginx/sites-available/nvr /etc/nginx/conf.d/nvr.conf
rm -f /usr/local/bin/nvrctl /usr/bin/nvrctl
systemctl daemon-reload; nginx -t 2>/dev/null && systemctl reload nginx || true
rm -rf /opt/nvr
if [ "$PURGE" = 1 ]; then
  rm -rf /var/lib/nvr
  mountpoint -q /srv/nvr && echo "/srv/nvr is a separate disk — left mounted; wipe it yourself if needed" || rm -rf /srv/nvr
  userdel nvr 2>/dev/null || true
  echo "NVR removed, data purged."
else
  echo "NVR removed. Kept: /var/lib/nvr (settings, users, cameras) and /srv/nvr (recordings)."
  echo "To delete them too:  sudo rm -rf /var/lib/nvr /srv/nvr && sudo userdel nvr"
fi
