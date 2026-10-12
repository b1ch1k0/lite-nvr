# Changelog

## 1.1.1 — 2026-10-12

Fixed
- **4:3 cameras no longer look squarish** — grid tiles and the enlarged (tap-to-zoom) view now fill the 16:9 frame (crop a little) instead of pillarboxing a 4:3 camera with black side bars. The full uncropped frame is still shown on the standalone single-camera page. On a narrow portrait phone the full frame is kept (cover would crop 4:3 to a sliver); rotate to landscape for the edge-to-edge wide view.

Improved
- **Non-standard-port / protocol cameras** — the "Other — manual" vendor now accepts any protocol and port (`rtsp` / `rtsps` / `http` / `onvif` / `dvrip`) with an example; the Hikvision hint explains that a forwarded/external NVR uses its external RTSP port in the port field; the port field is relabelled "port (RTSP)" to distinguish it from the web/ONVIF port.

## 1.1.0 — 2026-10-09

New
- **Cyber UI** — the login page and the whole panel got a terminal / neon theme (digital-rain login, scanline overlays, glitch title).
- **Two-factor login (TOTP)** — optional per user; QR enrollment (Google Authenticator / Authy / …), admin can reset a locked-out user. Pure-Python TOTP, QR via `segno`.
- **Auto-detect camera** — on the ONVIF page, enter an IP + credentials and it port-scans the common camera ports (RTSP 554/8554, ONVIF 80/2020/8000/8899, Hikvision/Dahua SDK) and probes ONVIF + vendor RTSP paths, then adds what it finds in one click.
- **Enlarge a tile by tapping it** — animated zoom (works on desktop and iPhone, no Fullscreen API); the enlarged camera switches to its main stream and every other tile disconnects so nothing else uses the uplink.
- **Remote low-quality mode** — viewers coming over the internet (non-LAN IP) default to the light sub-streams in the grid and on the full view, with a one-tap switch to the main stream; LAN stays full quality. Choice remembered separately for local and remote.
- **Login on the plain IP** — optional (`/account`): serve the login page at `http://<ip>/` as well as the hidden URL.

Improved
- **Mobile** — responsive pass across every page; iOS Safari now autoplays the live tiles (no tap-to-play overlay); 16 px form fields (no zoom-on-focus); wide tables scroll.
- **Live tiles** — smaller by default (3+ per row), audio muted by default, poster (last-recording frame) shown only while connecting/stalled so the live clock and the poster clock never overlap.
- `segno` added to requirements (QR codes).


## 1.0.0 — 2026-10-08
First public release.
- Live grid (high / low quality per browser), single-camera view, PTZ (ONVIF pan/tilt/zoom, presets, dead-man stop)
- Cameras: ONVIF discovery (multicast + unicast subnet scan), vendor templates (Tapo/VIGI, Hikvision, Dahua/IMOU, Uniview, Reolink, Axis, XMEye/DVRIP), any RTSP, light transcoded sub-streams
- Recording per camera: off / 24-7 / motion-only (1-min segments, pruned around motion); stall watchdog
- Motion detection on sub-streams with lighting-change rejection, OSD-clock masking, snapshots, timeline
- Users: admin / operator / viewer + per-camera access, audit log
- Storage: retention, low-disk guard, offload to Google Cloud Storage or SFTP (rclone), helper to add a recording disk
- Security: hidden login URL (everything else 404), proof-of-work login, rate limiting, nginx auth_request on streams
- Installers: Ubuntu/Debian and Rocky/Alma/RHEL 9 (`setup.sh`, one-line `install.sh`), RPM package
