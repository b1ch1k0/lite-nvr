"""Keeps go2rtc streams in sync with the cameras table via its API (no restarts, no churn)."""
import asyncio, json, logging
import httpx
from . import db, vendors
from .config import GO2RTC_API, GO2RTC_YAML

log = logging.getLogger("nvr.go2rtc")
SUB = "__sub"
kick = asyncio.Event()
_warned = set()
_pushed = {}     # name -> src we know go2rtc runs
force = set()    # names to re-push (camera edited in the panel)


def mark_dirty(cam_id):
    force.update({cam_id, cam_id + SUB})


def desired():
    out = {}
    for c in db.cameras(enabled_only=True):
        main, sub = vendors.stream_urls(c)
        if main:
            out[c["id"]] = main
        if sub:
            out[c["id"] + SUB] = sub
    return out


def _q(s):
    return json.dumps(s, ensure_ascii=False)  # JSON string == valid YAML double-quoted scalar


def write_yaml(streams):
    """Block-style YAML: go2rtc patches this file itself on API changes, so it must be real YAML."""
    lines = ["api:", '  listen: "127.0.0.1:1984"', "rtsp:", '  listen: "127.0.0.1:8554"',
             "webrtc:", '  listen: ""', "srtp:", '  listen: ""', "rtmp:", '  listen: ""',
             "log:", "  level: warn",
             # named transcode presets (go2rtc refuses sources containing spaces via its API, so
             # parameters live here and sources reference them: ffmpeg:camX#video=h264lite#width=640)
             "ffmpeg:",
             '  h264lite: "-c:v libx264 -preset superfast -tune zerolatency -profile:v main -pix_fmt yuv420p'
             ' -r 12 -g 24 -b:v 450k -maxrate 600k -bufsize 1200k"',
             "streams:"]
    for name, src in streams.items():
        lines.append(f"  {name}:")
        lines.append(f"    - {_q(src)}")
    tmp = GO2RTC_YAML + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    import os
    os.replace(tmp, GO2RTC_YAML)


def producer_urls(info):
    return [p.get("url") for p in ((info or {}).get("producers") or [])]


async def sync_once():
    want = desired()
    write_yaml(want)
    async with httpx.AsyncClient(base_url=GO2RTC_API, timeout=5) as c:
        cur = (await c.get("/api/streams")).json() or {}
        for name in list(cur):
            if name not in want:
                await c.delete("/api/streams", params={"src": name})
        for name, src in want.items():
            urls = producer_urls(cur.get(name))
            if name not in force and name in cur:
                # go2rtc reports some sources differently (ffmpeg: is shown as an internal rtsp url, active
                # dvrip/onvif producers have no url) — so also trust a stream we pushed ourselves, and an
                # url-less one go2rtc loaded from our yaml at start. Never touch a working stream needlessly.
                if urls == [src] or _pushed.get(name) == src or (name not in _pushed and None in urls):
                    _pushed[name] = src
                    continue
            force.discard(name)
            if name in cur:
                await c.delete("/api/streams", params={"src": name})
            r = await c.put("/api/streams", params={"name": name, "src": src})
            _pushed[name] = src
            if r.status_code >= 300 and (name, src) not in _warned:
                _warned.add((name, src))
                log.warning("go2rtc PUT %s -> %s %s", name, r.status_code, r.text[:200])


async def status():
    try:
        async with httpx.AsyncClient(base_url=GO2RTC_API, timeout=3) as c:
            return (await c.get("/api/streams")).json() or {}
    except Exception:
        return None


def is_online(info):
    """True when go2rtc actually receives data from the camera."""
    for p in (info or {}).get("producers") or []:
        if p.get("remote_addr") or p.get("bytes_recv") or p.get("recv"):
            return True
    return False


async def sync_loop():
    while True:
        try:
            await sync_once()
        except Exception as e:
            log.warning("go2rtc sync failed: %s", e)
        try:
            await asyncio.wait_for(kick.wait(), 30)
        except asyncio.TimeoutError:
            pass
        kick.clear()
