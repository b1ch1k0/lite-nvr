#!/usr/bin/env bash
# NVR one-line installer:
#   curl -fsSL https://raw.githubusercontent.com/b1ch1k0/lite-nvr/main/install.sh | sudo bash
#   curl -fsSL https://raw.githubusercontent.com/b1ch1k0/lite-nvr/main/install.sh | sudo bash -s -- --port 8080 --tz Europe/Berlin
# Env: NVR_VERSION=v1.0.0 (default: latest release)  NVR_TARBALL_URL=<url> (mirror / testing)
set -euo pipefail
REPO="b1ch1k0/lite-nvr"
VERSION="${NVR_VERSION:-latest}"
[ "$(id -u)" = 0 ] || { echo "Please run with sudo:  curl -fsSL … | sudo bash"; exit 1; }
command -v curl >/dev/null || { echo "curl is required"; exit 1; }
command -v tar >/dev/null || { (command -v dnf >/dev/null && dnf -y -q install tar) || (apt-get update -qq && apt-get install -y -qq tar); }
if [ -n "${NVR_TARBALL_URL:-}" ]; then URL="$NVR_TARBALL_URL"
elif [ "$VERSION" = latest ]; then URL="https://github.com/$REPO/releases/latest/download/nvr.tar.gz"
else URL="https://github.com/$REPO/releases/download/$VERSION/nvr.tar.gz"; fi
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
echo "==> downloading $URL"
curl -fsSL "$URL" -o "$TMP/nvr.tar.gz"
tar -xzf "$TMP/nvr.tar.gz" -C "$TMP"
cd "$TMP"/nvr-*/ 2>/dev/null || cd "$TMP"/nvr/
exec ./setup.sh "$@"
