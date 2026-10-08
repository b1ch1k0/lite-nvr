#!/bin/bash
# First publication to GitHub (run once, from the repo root):
#   ./tools/publish.sh <owner/repo> "<Author Name>" <author@email> [ssh-key]
# Create the EMPTY repository on GitHub first (no README/license). Pushing needs either your normal
# GitHub SSH access or a deploy key with write access (pass its private key path as 4th argument).
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -ge 3 ] || { sed -n 2,5p "$0"; exit 1; }
REPO="$1"; NAME="$2"; EMAIL="$3"; KEY="${4:-}"
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/__init__.py)
./tools/set-repo.sh "$REPO" "$NAME"
[ -d .git ] || git init -q -b main
git config user.name "$NAME"; git config user.email "$EMAIL"
[ -n "$KEY" ] && git config core.sshCommand "ssh -i $KEY -o IdentitiesOnly=yes"
git add -A
git commit -q -m "NVR $VERSION" || true
git remote get-url origin >/dev/null 2>&1 || git remote add origin "git@github.com:$REPO.git"
git push -u origin main
git tag -f "v$VERSION" && git push -f origin "v$VERSION"
echo
echo "Pushed. GitHub Actions now builds the release (tarball + RPM): https://github.com/$REPO/actions"
echo "When it is green, anyone can install with:"
echo "  curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | sudo bash"
