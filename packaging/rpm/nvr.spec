# Self-contained RPM: app + vendored Python deps (for python3.11) + go2rtc binary.
# Runtime needs EPEL + RPM Fusion (ffmpeg). Build: packaging/build-rpm.sh
%global debug_package %{nil}
# pip wheels ship pre-built, auditwheel-patched .so files: rpm's strip/byte-compile steps corrupt them
%global __os_install_post %{nil}
%global __brp_mangle_shebangs %{nil}
%global _build_id_links none
# vendored wheels carry their own private libs (numpy.libs/…) — never turn them into system requirements
%global __requires_exclude_from ^/opt/nvr/lib/.*$
%global __provides_exclude_from ^/opt/nvr/lib/.*$
%undefine __brp_python_bytecompile
%global _python_bytecompile_errors_terminate_build 0
%define go2rtc_version 1.9.14

Name:           nvr
Version:        %{nvr_version}
Release:        1%{?dist}
Summary:        Lightweight self-hosted NVR: live view, recording, motion detection, ONVIF/PTZ
License:        MIT
URL:            https://github.com/b1ch1k0/lite-nvr
Source0:        nvr-%{version}.tar.gz
Source1:        go2rtc_linux_%{go2rtc_arch}
ExclusiveArch:  x86_64 aarch64
BuildRequires:  python3.11 python3.11-pip
Requires:       python3.11 nginx ffmpeg sqlite policycoreutils-python-utils util-linux rsync parted
Recommends:     rclone
Requires(pre):  shadow-utils

%description
NVR — a small video surveillance server built on go2rtc and a Python web panel:
ONVIF discovery, Tapo/VIGI/Hikvision/Dahua/Uniview/Reolink/Axis/XMEye templates, live grid,
PTZ control, continuous or motion-only recording, motion events with snapshots, users with
per-camera rights, retention and offload to Google Cloud Storage or SFTP, hidden login URL.

%prep
%setup -q -n nvr-%{version}

%build
python3.11 -m pip install --no-cache-dir --target vendor -r requirements.txt

%install
install -d %{buildroot}/opt/nvr/bin %{buildroot}/opt/nvr/lib %{buildroot}%{_unitdir} %{buildroot}%{_bindir}
cp -r app deploy requirements.txt %{buildroot}/opt/nvr/
cp -r vendor/* %{buildroot}/opt/nvr/lib/
install -m 755 bin/nvr-run bin/nvrctl %{buildroot}/opt/nvr/bin/
install -m 755 %{SOURCE1} %{buildroot}/opt/nvr/bin/go2rtc
echo python3.11 > %{buildroot}/opt/nvr/python
install -m 644 deploy/nvr.service deploy/nvr-go2rtc.service %{buildroot}%{_unitdir}/
ln -s /opt/nvr/bin/nvrctl %{buildroot}%{_bindir}/nvrctl

%pre
getent passwd nvr >/dev/null || useradd --system --home-dir /var/lib/nvr --shell /usr/sbin/nologin nvr

%post
/opt/nvr/deploy/install.sh --no-copy >/var/log/nvr-install.log 2>&1 || { cat /var/log/nvr-install.log; exit 0; }
if [ "$(runuser -u nvr -- sqlite3 /var/lib/nvr/nvr.db 'select count(*) from users' 2>/dev/null)" = 0 ]; then
  /opt/nvr/bin/nvrctl reset-admin admin > /root/nvr-admin.txt && chmod 600 /root/nvr-admin.txt
  echo "login: $(/opt/nvr/bin/nvrctl link)" >> /root/nvr-admin.txt
  echo "NVR installed. Login link, user and password: /root/nvr-admin.txt"
fi

%preun
if [ $1 -eq 0 ]; then systemctl disable --now nvr nvr-go2rtc >/dev/null 2>&1 || true; rm -f /etc/nginx/conf.d/nvr.conf; fi

%postun
if [ $1 -eq 0 ]; then systemctl daemon-reload; systemctl try-reload-or-restart nginx >/dev/null 2>&1 || true; fi

%files
/opt/nvr
%{_unitdir}/nvr.service
%{_unitdir}/nvr-go2rtc.service
%{_bindir}/nvrctl

%changelog
* Thu Oct 08 2026 NVR maintainers - 1.0.0-1
- First public release
