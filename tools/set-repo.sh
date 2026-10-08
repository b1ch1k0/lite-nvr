#!/bin/bash
# Replaces the __GITHUB_REPO__ / __AUTHOR__ placeholders before the first push:
#   ./tools/set-repo.sh myuser/nvr "Your Name"
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -ge 2 ] || { echo "usage: $0 owner/repo \"Author Name\""; exit 1; }
grep -rl --exclude-dir=.git --exclude=set-repo.sh -e __GITHUB_REPO__ -e __AUTHOR__ . | xargs sed -i "s#__GITHUB_REPO__#$1#g; s#__AUTHOR__#$2#g"
grep -rn --exclude-dir=.git --exclude=set-repo.sh -e __GITHUB_REPO__ -e __AUTHOR__ . || echo "placeholders replaced"
