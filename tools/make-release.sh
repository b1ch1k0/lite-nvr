#!/bin/bash
# Builds dist/nvr.tar.gz — the archive install.sh downloads from GitHub Releases.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/__init__.py)
mkdir -p dist
tar --exclude=__pycache__ --transform "s,^,nvr-$VERSION/," -czf dist/nvr.tar.gz \
    app bin deploy setup.sh install.sh requirements.txt LICENSE README.md README.ka.md CHANGELOG.md
sha256sum dist/nvr.tar.gz | tee dist/nvr.tar.gz.sha256
