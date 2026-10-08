#!/bin/bash
# Builds the RPM on a Rocky/Alma/RHEL 9 machine (or container):  ./packaging/build-rpm.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/__init__.py)
case "$(uname -m)" in x86_64) GA=amd64 ;; aarch64) GA=arm64 ;; *) echo "unsupported arch"; exit 1 ;; esac
command -v rpmbuild >/dev/null || dnf -y -q install rpm-build python3.11 python3.11-pip
TOP=$(mktemp -d); mkdir -p "$TOP"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}
tar --transform "s,^,nvr-$VERSION/," -czf "$TOP/SOURCES/nvr-$VERSION.tar.gz" app bin deploy requirements.txt LICENSE
curl -fsSL -o "$TOP/SOURCES/go2rtc_linux_$GA" "https://github.com/AlexxIT/go2rtc/releases/download/v1.9.14/go2rtc_linux_$GA"
cp packaging/rpm/nvr.spec "$TOP/SPECS/"
rpmbuild --define "_topdir $TOP" --define "nvr_version $VERSION" --define "go2rtc_arch $GA" -bb "$TOP/SPECS/nvr.spec"
mkdir -p dist && cp "$TOP"/RPMS/*/*.rpm dist/ && rm -rf "$TOP"
ls -la dist/*.rpm
