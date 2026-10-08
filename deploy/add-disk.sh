#!/bin/bash
# Turns an EMPTY disk into the recording disk: GPT + ext4 (label nvrdisk), moves existing recordings,
# mounts at /srv/nvr via fstab (UUID, nofail) and makes nvr.service wait for it.
#   sudo ./deploy/add-disk.sh /dev/sdb
set -euo pipefail
DEV="${1:-}"
[ "$(id -u)" = 0 ] || { echo "გაუშვით sudo-თი"; exit 1; }
[ -b "$DEV" ] || { echo "გამოყენება: sudo $0 /dev/sdX   (lsblk აჩვენებს დისკებს)"; exit 1; }
[ "$(lsblk -ndo TYPE "$DEV")" = disk ] || { echo "$DEV დისკი არ არის (მიუთითეთ მთელი დისკი, მაგ. /dev/sdb)"; exit 1; }
if [ -n "$(lsblk -no MOUNTPOINT "$DEV" | tr -d '[:space:]')" ]; then echo "$DEV ან მისი დანაყოფი მიმაგრებულია — გაჩერება"; exit 1; fi
if [ -n "$(wipefs -n "$DEV" 2>/dev/null)" ] || [ "$(lsblk -no NAME "$DEV" | wc -l)" -gt 1 ]; then
  echo "$DEV ცარიელი არ არის (დანაყოფი/ფაილური სისტემა აქვს). უსაფრთხოებისთვის არ ვეხები."; exit 1
fi
mountpoint -q /srv/nvr && { echo "/srv/nvr უკვე ცალკე დისკზეა"; exit 1; }
echo "$DEV ($(lsblk -ndo SIZE "$DEV")) ფორმატირდება და გახდება ჩაწერის დისკი. გაგრძელება? [y/N]"; read -r a; [ "$a" = y ] || exit 1
parted -s "$DEV" mklabel gpt mkpart nvrdisk ext4 1MiB 100%
partprobe "$DEV"; sleep 2
PART=$(lsblk -lnpo NAME "$DEV" | sed -n 2p)
mkfs.ext4 -q -L nvrdisk -m 1 "$PART"
UUID=$(blkid -s UUID -o value "$PART")
TMP=$(mktemp -d); mount -o noatime "$PART" "$TMP"
[ -d /srv/nvr ] && rsync -aHAX /srv/nvr/ "$TMP"/ || true
systemctl stop nvr 2>/dev/null || true
[ -d /srv/nvr ] && rsync -aHAX --delete /srv/nvr/ "$TMP"/
umount "$TMP"; rmdir "$TMP"
[ -d /srv/nvr ] && mv /srv/nvr /srv/nvr.old
install -d -o nvr -g nvr -m 750 /srv/nvr
cp /etc/fstab "/etc/fstab.bak-$(date +%Y%m%d-%H%M%S)"
echo "UUID=$UUID /srv/nvr ext4 defaults,noatime,nofail 0 2" >> /etc/fstab
systemctl daemon-reload; mount /srv/nvr; chown nvr:nvr /srv/nvr; chmod 750 /srv/nvr
mkdir -p /etc/systemd/system/nvr.service.d
printf "[Unit]\nRequiresMountsFor=/srv/nvr\n" > /etc/systemd/system/nvr.service.d/disk.conf
systemctl daemon-reload; systemctl start nvr
old=$(find /srv/nvr.old -type f 2>/dev/null | wc -l); new=$(find /srv/nvr -type f | wc -l)
echo "ფაილები: ძველი $old, ახალი $new"
if [ "$new" -ge "$old" ]; then rm -rf /srv/nvr.old; echo "ძველი ასლი წაიშალა"; else echo "⚠ შეამოწმეთ /srv/nvr.old ხელით"; fi
df -h /srv/nvr | tail -1
