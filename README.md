<div align="center">

# 📹 lite-nvr

**A lightweight, self-hosted network video recorder for Linux.**
Live view, recording, motion detection, ONVIF discovery and PTZ — in one small install.

[![CI](https://github.com/b1ch1k0/lite-nvr/actions/workflows/ci.yml/badge.svg)](https://github.com/b1ch1k0/lite-nvr/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/b1ch1k0/lite-nvr)](https://github.com/b1ch1k0/lite-nvr/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Ubuntu](https://img.shields.io/badge/Ubuntu-22.04%20%7C%2024.04-E95420?logo=ubuntu&logoColor=white)
![Rocky](https://img.shields.io/badge/Rocky%20%7C%20Alma%20%7C%20RHEL-9-10B981?logo=rockylinux&logoColor=white)

[Install](#-install) · [Features](#-features) · [First steps](#-first-steps) · [nvrctl](#-nvrctl) · [Security](SECURITY.md) · [ქართულად](README.ka.md)

</div>

---

Runs comfortably on **2 vCPU / 2 GB RAM** with six cameras. Video is never re-encoded for recording:
streams are copied straight to MP4, so the CPU stays free for motion detection.

## 🚀 Install

**Ubuntu / Debian / Rocky / Alma / RHEL 9 — one line:**

```bash
curl -fsSL https://raw.githubusercontent.com/b1ch1k0/lite-nvr/main/install.sh | sudo bash
```

Options go after `bash -s --`:

```bash
curl -fsSL https://raw.githubusercontent.com/b1ch1k0/lite-nvr/main/install.sh | sudo bash -s -- --port 8080 --tz Europe/Berlin
```

| option | default | |
|---|---|---|
| `--port N` | `80` | use another port if 80 is taken or nginx already hosts other sites |
| `--tz Zone/City` | system | recordings are named in local time |
| `--admin NAME` | `admin` | first administrator |
| `--keep-default-site` | — | don't remove nginx's default site (Debian/Ubuntu) |

At the end the installer prints the **login link**, user and password (also saved in `/root/nvr-admin.txt`):

```
════════════════════════════════════════════════════════════
  NVR is ready
  login:    http://192.168.1.50/s/Xq7pL2mN9vR4tK8w
  user:     admin
  password: ****************
  Every other URL answers 404 — bookmark the link.  sudo nvrctl link
════════════════════════════════════════════════════════════
```

<details>
<summary><b>RPM package (Rocky / Alma / RHEL 9)</b></summary>

```bash
sudo dnf -y install epel-release dnf-plugins-core && sudo dnf config-manager --set-enabled crb
sudo dnf -y install https://mirrors.rpmfusion.org/free/el/rpmfusion-free-release-9.noarch.rpm   # ffmpeg
sudo dnf -y install https://github.com/b1ch1k0/lite-nvr/releases/latest/download/nvr-1.1.0-1.el9.x86_64.rpm
sudo cat /root/nvr-admin.txt
```
The RPM is self-contained (Python dependencies and go2rtc included), no pip at install time.
</details>

<details>
<summary><b>From a git checkout</b></summary>

```bash
git clone https://github.com/b1ch1k0/lite-nvr.git && cd lite-nvr
sudo ./setup.sh --tz Europe/Berlin
```
</details>

<details>
<summary><b>Behind Cloudflare / a reverse proxy (remote access)</b></summary>

To reach it from the internet, point a domain at the box through Cloudflare (or any proxy).
See **[`deploy/nginx-cloudflare.conf.example`](deploy/nginx-cloudflare.conf.example)** for the real-visitor-IP,
no-store caching and origin-TLS snippets. Remote viewers automatically fall back to the light sub-streams.
</details>

**Requirements:** x86_64 (SSE4.2) or arm64 · 2 CPU · 2 GB RAM · internet during install.
A separate disk for recordings is recommended — see [Recording disk](#-recording-disk).

## ✨ Features

| | |
|---|---|
| **Cameras** | **auto-detect** (port-scan + ONVIF/RTSP vendor probe from one IP) · ONVIF discovery (multicast + unicast subnet scan across VLANs) · templates for Tapo/VIGI, Hikvision/HiWatch/EZVIZ, Dahua/IMOU/Amcrest, Uniview, Reolink, Axis, XMEye (DVRIP) · any RTSP URL · NVR/DVR channels · connection test before saving |
| **Live** | grid with *high* (main) or *low* (sub-stream) quality per browser · **tap a tile to enlarge** (animated, switches to the main stream and disconnects the rest) · adaptive jitter buffer · last recorded frame as poster · recording / motion badges |
| **Remote** | viewers over the internet default to the light sub-streams (saves uplink); LAN stays full quality — remembered separately |
| **PTZ** | ONVIF pan/tilt/zoom, speed, presets, keyboard arrows; the camera stops by itself if the browser disconnects |
| **Recording** | per camera: off / 24-7 / motion-only · MP4 segments without re-encoding · watchdog restarts stalled streams |
| **Motion** | detection on the sub-stream (≈3 % CPU per camera) · ignores light switches, IR day/night, on-screen clocks · events with snapshots |
| **Playback** | 24 h + 1 h timeline with motion marks · event thumbnails · 0.5–8× speed · download |
| **Users** | admin / operator / viewer · per-camera access · **optional two-factor login (TOTP)** · audit log |
| **Storage** | retention in days · low-disk guard · offload to **Google Cloud Storage** or an **SFTP** box (rclone) |
| **Security** | hidden login URL, everything else 404 · proof-of-work login · rate limiting · optional login on the plain IP · see [SECURITY.md](SECURITY.md) |
| **Look** | terminal / neon “cyber” theme · responsive, works on the phone (iPhone Safari autoplay) |

> **UI language:** the web interface is currently in **Georgian** 🇬🇪. An English translation is on the roadmap — PRs welcome.

## 🧭 First steps

1. Open the login link and sign in.
2. **Cameras → ONVIF search** (or **+ Add camera** with a vendor template).
   *Tapo:* create a *Camera Account* in the Tapo app first and use ONVIF port `2020`.
3. **Users** → add people and tick the cameras each one may see.
4. **Storage** → retention days, optional cloud/SFTP offload.
5. Change your password and keep the login link: *your name (top right)*.

## 🛠 nvrctl

```bash
sudo nvrctl link          # show the hidden login link
sudo nvrctl new-link      # issue a new link (old one stops working)
sudo nvrctl reset-admin   # forgot the password → new random one
sudo nvrctl status        # services, disk usage, version
```

## 💽 Recording disk

Turn an **empty** disk into the recording disk (refuses disks that contain anything):

```bash
lsblk
sudo /opt/nvr/deploy/add-disk.sh /dev/sdb
```

## 🔄 Update · 🗑 Uninstall

```bash
# update: run the installer again — cameras, users and recordings are kept
curl -fsSL https://raw.githubusercontent.com/b1ch1k0/lite-nvr/main/install.sh | sudo bash

sudo /opt/nvr/deploy/uninstall.sh            # remove program, keep data
sudo /opt/nvr/deploy/uninstall.sh --purge    # remove everything
```

## 🧱 How it works

```
cameras ──RTSP/ONVIF/DVRIP──▶ go2rtc (127.0.0.1) ──▶ ffmpeg recorders ──▶ /srv/nvr/rec/<cam>/<day>/*.mp4
                                     │          └──▶ motion detectors ──▶ events + snapshots
browser ◀── nginx :80 ◀── auth ── web panel (FastAPI, 127.0.0.1:8090) ── SQLite /var/lib/nvr
```

| path | |
|---|---|
| `/opt/nvr` | program |
| `/var/lib/nvr` | database, go2rtc config |
| `/srv/nvr` | recordings, motion snapshots |

Services: `nvr`, `nvr-go2rtc`, `nginx` · logs: `journalctl -u nvr -f`

## 🙏 Credits

Built on [go2rtc](https://github.com/AlexxIT/go2rtc) by Alexey Khit (MIT). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## 📄 License

[MIT](LICENSE)
