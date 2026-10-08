# Security

**Report a vulnerability** privately via GitHub → *Security* → *Report a vulnerability* (please don't open a public issue).

## How the panel protects itself
- The login page exists only at a random URL (`/s/<random>`, `sudo nvrctl link`). Every other URL returns the same
  plain nginx 404 to anonymous visitors, so scanners can't tell an NVR is running.
- Login needs a proof-of-work over a signed, single-use nonce (≈0.2–1 s in a browser); scripts without JavaScript get 404.
- 10 failed logins from one IP → 10-minute block. Passwords are stored with scrypt.
- Live streams, snapshots and recordings are authorised per camera by the app (nginx `auth_request` / `X-Accel-Redirect`);
  go2rtc and the app listen on 127.0.0.1 only.
- Camera passwords are stored in `/var/lib/nvr/nvr.db` (mode 750, user `nvr`) — protect server access accordingly.

## Recommendations
- Keep the panel on a LAN/VPN. If you publish it to the internet, put HTTPS in front (e.g. Caddy, nginx + certbot).
- Use a separate camera VLAN; give cameras no internet access.
