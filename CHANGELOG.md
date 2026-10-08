# Changelog

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
