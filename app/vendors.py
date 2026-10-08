from urllib.parse import quote, urlsplit, urlunsplit

VENDORS = {
    "tapo": dict(label="TP-Link Tapo / VIGI", port=554, onvif_port=2020,
                 main="rtsp://{auth}{host}:{port}/stream1", sub="rtsp://{auth}{host}:{port}/stream2",
                 hint="Tapo აპში: კამერა → Settings → Advanced Settings → Camera Account. "
                      "აქ ის სახელი/პაროლი შეიყვანეთ (არა TP-Link ანგარიში)."),
    "hikvision": dict(label="Hikvision / HiWatch / EZVIZ", port=554, onvif_port=80,
                      main="rtsp://{auth}{host}:{port}/Streaming/Channels/{ch}01",
                      sub="rtsp://{auth}{host}:{port}/Streaming/Channels/{ch}02",
                      hint="NVR/DVR-ის შემთხვევაში მიუთითეთ არხის ნომერი. EZVIZ-ზე პაროლი = Verification code."),
    "dahua": dict(label="Dahua / IMOU / Amcrest", port=554, onvif_port=80,
                  main="rtsp://{auth}{host}:{port}/cam/realmonitor?channel={ch}&subtype=0",
                  sub="rtsp://{auth}{host}:{port}/cam/realmonitor?channel={ch}&subtype=1",
                  hint="NVR/XVR-ის შემთხვევაში მიუთითეთ არხის ნომერი."),
    "uniview": dict(label="Uniview (UNV)", port=554, onvif_port=80,
                    main="rtsp://{auth}{host}:{port}/unicast/c{ch}/s0/live",
                    sub="rtsp://{auth}{host}:{port}/unicast/c{ch}/s1/live", hint=""),
    "reolink": dict(label="Reolink", port=554, onvif_port=8000,
                    main="rtsp://{auth}{host}:{port}/h264Preview_{ch2}_main",
                    sub="rtsp://{auth}{host}:{port}/h264Preview_{ch2}_sub",
                    hint="Reolink-ზე RTSP ჩართეთ: Settings → Network → Advanced → Port Settings."),
    "axis": dict(label="Axis", port=554, onvif_port=80,
                 main="rtsp://{auth}{host}:{port}/axis-media/media.amp",
                 sub="rtsp://{auth}{host}:{port}/axis-media/media.amp?resolution=640x360", hint=""),
    "xmeye": dict(label="XMEye / Xiongmai DVR (DVRIP)", port=34567, onvif_port=8899,
                  main="dvrip://{auth}{host}:{port}?channel={ch0}&subtype=0",
                  sub="dvrip://{auth}{host}:{port}?channel={ch0}&subtype=1",
                  hint="იაფი ჩინური DVR/კამერები (XMEye აპი). პორტი ჩვეულებრივ 34567."),
    "onvif": dict(label="ONVIF (ავტომატური აღმოჩენა)", port=554, onvif_port=80, main=None, sub=None,
                  hint="გამოიყენეთ ღილაკი „ONVIF ძებნა“ — ნაკადის მისამართები თავად შეივსება."),
    "rtsp": dict(label="სხვა — RTSP მისამართი ხელით", port=554, onvif_port=80, main=None, sub=None,
                 hint="ჩაწერეთ სრული rtsp:// მისამართი. სახელი/პაროლი თავად ჩაემატება, თუ მისამართში არ არის."),
}


def auth_part(user, pw):
    if not user:
        return ""
    return f"{quote(user, safe='')}:{quote(pw or '', safe='')}@"


def inject_creds(url, user, pw):
    if not url or not user:
        return url or ""
    s = urlsplit(url)
    if "@" in s.netloc or not s.netloc:  # already has creds, or not a network URL (ffmpeg:camX#...)
        return url
    return urlunsplit(s._replace(netloc=auth_part(user, pw) + s.netloc))


def stream_urls(cam):
    v = VENDORS.get(cam["vendor"]) or {}
    if v.get("main"):
        ch = int(cam.get("channel") or 1)
        f = dict(auth=auth_part(cam.get("username"), cam.get("password")), host=cam["host"],
                 port=cam.get("port") or v["port"], ch=ch, ch2=f"{ch:02d}", ch0=ch - 1)
        return v["main"].format(**f), (v["sub"].format(**f) if v.get("sub") else "")
    u, p = cam.get("username"), cam.get("password")
    return inject_creds(cam.get("main_url"), u, p), inject_creds(cam.get("sub_url"), u, p)


def mask(url):
    s = urlsplit(url or "")
    if "@" not in s.netloc:
        return url or ""
    userinfo, host = s.netloc.rsplit("@", 1)
    user = userinfo.split(":", 1)[0]
    return urlunsplit(s._replace(netloc=f"{user}:***@{host}"))
