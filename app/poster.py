"""Per-camera background picture: the last frame of the newest finished recording (or a live grab when
the camera isn't recording). Tiles show it while the live stream connects or if it drops."""
import asyncio, datetime, logging, os, time
import httpx
from . import db, go2rtc, vendors
from .config import GO2RTC_API, POSTER_DIR, REC_DIR

log = logging.getLogger("nvr.poster")
EVERY = 60


def newest_finished(cam_id):
    today = datetime.date.today()
    for d in (today, today - datetime.timedelta(days=1)):
        ddir = os.path.join(REC_DIR, cam_id, d.isoformat())
        try:
            names = sorted(n for n in os.listdir(ddir) if n.endswith(".mp4"))
        except FileNotFoundError:
            continue
        for n in reversed(names):
            p = os.path.join(ddir, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if time.time() - st.st_mtime > 5 and st.st_size > 50_000:  # finished (moov written)
                return p
    return None


async def from_recording(src, out):
    p = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-sseof", "-3", "-i", src,
        "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "5", "-y", out,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        return await asyncio.wait_for(p.wait(), 20) == 0 and os.path.getsize(out) > 0
    except (asyncio.TimeoutError, OSError):
        p.kill()
        return False


async def from_live(cam, out):
    name = cam["id"] + (go2rtc.SUB if vendors.stream_urls(cam)[1] else "")
    async with httpx.AsyncClient(base_url=GO2RTC_API, timeout=15) as c:
        r = await c.get("/api/frame.jpeg", params={"src": name, "width": 640})
    if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
        with open(out, "wb") as f:
            f.write(r.content)
        return True
    return False


async def refresh(cam):
    os.makedirs(POSTER_DIR, exist_ok=True)
    final = os.path.join(POSTER_DIR, f"{cam['id']}.jpg")
    tmp = final + ".tmp.jpg"
    src = newest_finished(cam["id"])
    ok = False
    if src and (not os.path.exists(final) or os.path.getmtime(src) > os.path.getmtime(final) - 1):
        ok = await from_recording(src, tmp)
    elif src:
        return  # poster already from the newest recording
    if not ok and (not src or not os.path.exists(final)):
        try:
            ok = await from_live(cam, tmp)
        except Exception:
            ok = False
    if ok:
        os.replace(tmp, final)


async def loop():
    await asyncio.sleep(15)
    while True:
        for cam in db.cameras(enabled_only=True):
            try:
                await refresh(cam)
            except Exception as e:
                log.warning("poster %s: %s", cam["id"], e)
        await asyncio.sleep(EVERY)
