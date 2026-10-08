"""Per-camera workers:
 * Rec      — ffmpeg copies go2rtc's restream into clock-aligned MP4 segments (no re-encode of video).
 * Detector — ffmpeg decodes the low-res stream at a few fps into tiny grey frames; numpy frame differencing
              against a running background produces motion events (stored in the events table + snapshot).
Both have a stall watchdog: a hung ffmpeg (seen with RTSP half-closed sockets) is killed and restarted.
"""
import asyncio, collections, datetime, logging, os, signal, time

import httpx
import numpy as np

from . import db, go2rtc
from .config import EVENTS_DIR, GO2RTC_API, GO2RTC_RTSP, REC_DIR

log = logging.getLogger("nvr.rec")
STALL_SEC = 20


def ensure_dirs(cam_id):
    today = datetime.date.today()
    for d in (today, today + datetime.timedelta(days=1)):
        os.makedirs(os.path.join(REC_DIR, cam_id, d.isoformat()), exist_ok=True)


class Worker:
    """ffmpeg supervisor: restart with backoff, stderr ring buffer, stall watchdog."""
    kind = "worker"

    def __init__(self, cam_id):
        self.cam_id = cam_id
        self.proc = None
        self.stopping = False
        self.state = "იწყება"
        self.restarts = 0
        self.since = time.time()
        self.last_progress = time.time()
        self.log = collections.deque(maxlen=25)
        self.task = asyncio.create_task(self.loop())

    def note(self, line):
        self.log.append(time.strftime("%m-%d %H:%M:%S ") + line)

    def cmd(self):
        raise NotImplementedError

    async def consume(self, proc):
        raise NotImplementedError

    async def _stderr(self, proc):
        async for line in proc.stderr:
            self.note(line.decode(errors="replace").rstrip())

    async def _watchdog(self, proc):
        while proc.returncode is None:
            await asyncio.sleep(5)
            if time.time() - self.last_progress > STALL_SEC and proc.returncode is None:
                self.note(f"watchdog: {STALL_SEC}წმ მონაცემი არ მოსულა — ვრესტარტავ")
                proc.kill()
                return

    async def on_exit(self):
        pass

    async def loop(self):
        backoff = 3
        while not self.stopping:
            started = time.time()
            rc = None
            try:
                self.before_start()
                self.proc = await asyncio.create_subprocess_exec(
                    *self.cmd(), stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1 << 20)
                self.last_progress = time.time()
                self.state, self.since = self.running_label, time.time()
                aux = [asyncio.create_task(self._stderr(self.proc)), asyncio.create_task(self._watchdog(self.proc))]
                try:
                    await self.consume(self.proc)
                    rc = await self.proc.wait()
                finally:
                    for t in aux:
                        t.cancel()
            except asyncio.CancelledError:
                if self.proc and self.proc.returncode is None:
                    self.proc.kill()
                raise
            except Exception as e:
                rc = f"{e.__class__.__name__}: {e}"
                if self.proc and self.proc.returncode is None:
                    self.proc.kill()
            await self.on_exit()
            if self.stopping:
                break
            self.restarts += 1
            if time.time() - started > 120:
                backoff = 3
            self.state, self.since = f"კავშირი გაწყდა ({rc}), ხელახლა {backoff}წმ-ში", time.time()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        self.state = "გაჩერებული"

    def before_start(self):
        pass

    async def stop(self):
        self.stopping = True
        p = self.proc
        if p and p.returncode is None:
            p.send_signal(signal.SIGINT)  # ffmpeg finalizes the current MP4 on SIGINT
            try:
                await asyncio.wait_for(p.wait(), 10)
            except asyncio.TimeoutError:
                p.kill()
        self.task.cancel()
        try:
            await self.task
        except (asyncio.CancelledError, Exception):
            pass
        await self.on_exit()


class Rec(Worker):
    kind = "rec"
    running_label = "იწერება"

    def __init__(self, cam, seg_sec):
        self.audio = bool(cam["audio"])
        self.seg_sec = seg_sec
        self.sig = (self.audio, seg_sec)
        super().__init__(cam["id"])

    def before_start(self):
        ensure_dirs(self.cam_id)

    def cmd(self):
        out = os.path.join(REC_DIR, self.cam_id, "%Y-%m-%d", "%H-%M-%S.mp4")
        a = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "48k", "-ac", "1"] if self.audio else ["-an"]
        return ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "warning",
                "-progress", "pipe:1", "-stats_period", "2",
                "-rtsp_transport", "tcp", "-timeout", "10000000", "-fflags", "+genpts",
                "-i", f"{GO2RTC_RTSP}/{self.cam_id}",
                "-map", "0:v:0", "-c:v", "copy", *a,
                "-f", "segment", "-segment_time", str(self.seg_sec), "-segment_atclocktime", "1",
                "-reset_timestamps", "1", "-strftime", "1", "-segment_format", "mp4",
                "-segment_format_options", "movflags=+faststart", out]

    async def consume(self, proc):
        last = None
        async for line in proc.stdout:
            if line.startswith(b"out_time_us=") or line.startswith(b"total_size="):
                if line != last:
                    self.last_progress = time.time()
                    last = line


class Detector(Worker):
    kind = "motion"
    running_label = "აქტიური"
    W, H, FPS = 160, 90, 5
    POST_SEC = 8          # event ends after this much stillness
    THRESH = {1: 40, 2: 25, 3: 15}  # per-pixel grey-level change; higher sensitivity = lower threshold

    def __init__(self, cam, src):
        self.src = src
        self.thr = self.THRESH.get(int(cam.get("motion_sens") or 2), 25)
        self.area = max(0.05, float(cam.get("motion_area") or 0.5)) / 100.0
        self.sig = det_sig(cam, src)
        self.active = False
        self.event_id = None
        self.ev_score = 0.0
        self.level = 0.0
        self.last_motion = 0.0
        self.suppress_until = 0.0   # PTZ moving: the whole view shifts, that's not motion
        super().__init__(cam["id"])

    def cmd(self):
        return ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error",
                "-rtsp_transport", "tcp", "-timeout", "10000000",
                "-i", f"{GO2RTC_RTSP}/{self.src}", "-an",
                "-vf", f"fps={self.FPS},scale={self.W}:{self.H}:flags=area,format=gray",
                "-f", "rawvideo", "pipe:1"]

    async def consume(self, proc):
        n = self.W * self.H
        bg = None
        hist = collections.deque(maxlen=3)
        frames = 0
        last_db = 0.0
        while True:
            try:
                buf = await proc.stdout.readexactly(n)
            except asyncio.IncompleteReadError:
                return
            self.last_progress = now = time.time()
            f = np.frombuffer(buf, np.uint8).astype(np.float32)
            frames += 1
            if bg is None:
                bg = f.copy()
                noise = np.zeros(n, np.float32)
                continue
            if now < self.suppress_until:
                bg = f.copy()
                hist.clear()
                self.level = 0.0
                continue
            f_mean, bg_mean = float(f.mean()), float(bg.mean())
            raw = np.abs(f - bg) > self.thr
            # lights switched on/off, IR day/night switch, auto-exposure jump: the whole picture
            # changes brightness at once — restart the background instead of raising an event
            if (abs(f_mean - bg_mean) > 18 and raw.mean() > 0.35) or raw.mean() > 0.6:
                bg = f.copy()
                hist.clear()
                self.level = 0.0
                self.note(f"განათების ცვლილება ({bg_mean:.0f}→{f_mean:.0f}) — ფონი განახლდა")
                continue
            # compensate small global brightness drift before comparing
            changed = np.abs((f - f_mean + bg_mean) - bg) > self.thr
            # pixels that change almost all the time (on-screen clock, fan, flicker) are ignored
            noise += 0.02 * (changed - noise)
            valid = noise < 0.25
            frac = float((changed & valid).mean())
            # background adapts slowly, and even slower where things move (keeps slow walkers visible)
            alpha = np.where(changed, 0.01, 0.08).astype(np.float32)
            bg += alpha * (f - bg)
            self.level = frac
            hist.append(frac >= self.area)
            if frames < self.FPS * 3:
                continue  # warm-up
            if sum(hist) >= 2:
                self.last_motion = now
                self.ev_score = max(self.ev_score, frac)
                if not self.active:
                    await self.start_event(now)
                elif now - last_db > 5:
                    last_db = now
                    db.ex("UPDATE events SET end=?, score=? WHERE id=?", (now, self.ev_score, self.event_id))
            elif self.active and now - self.last_motion > self.POST_SEC:
                await self.end_event()

    async def start_event(self, now):
        self.active = True
        self.ev_score = self.level
        start = now - 1.0
        self.event_id = db.ex("INSERT INTO events(cam_id,start,end,score) VALUES(?,?,NULL,?)",
                              (self.cam_id, start, self.ev_score)).lastrowid
        asyncio.create_task(self.snapshot(self.event_id))
        get().on_motion(self.cam_id, True)

    async def end_event(self):
        if not self.active:
            return
        self.active = False
        db.ex("UPDATE events SET end=?, score=? WHERE id=?", (self.last_motion, self.ev_score, self.event_id))
        self.event_id = None
        get().on_motion(self.cam_id, False)

    async def on_exit(self):
        await self.end_event()

    async def snapshot(self, ev_id):
        try:
            d = os.path.join(EVENTS_DIR, self.cam_id)
            os.makedirs(d, exist_ok=True)
            async with httpx.AsyncClient(base_url=GO2RTC_API, timeout=10) as c:
                r = await c.get("/api/frame.jpeg", params={"src": self.src})
            if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                with open(os.path.join(d, f"{ev_id}.jpg"), "wb") as fh:
                    fh.write(r.content)
                db.ex("UPDATE events SET snap=1 WHERE id=?", (ev_id,))
        except Exception as e:
            self.note(f"snapshot: {e}")


def det_sig(cam, src):
    return (src, Detector.THRESH.get(int(cam.get("motion_sens") or 2), 25),
            max(0.05, float(cam.get("motion_area") or 0.5)) / 100.0)


class Manager:
    def __init__(self):
        self.recs = {}
        self.dets = {}
        self.kick = asyncio.Event()

    def on_motion(self, cam_id, active):
        pass  # hook for notifications

    def suppress_motion(self, cam_id, seconds=4.0):
        d = self.dets.get(cam_id)
        if d:
            d.suppress_until = max(d.suppress_until, time.time() + seconds)

    async def reconcile(self):
        seg_min = max(1, min(60, int(db.get_setting("segment_minutes") or 5)))
        cams = db.cameras(enabled_only=True)
        want_rec, want_det = {}, {}
        from . import vendors
        for c in cams:
            ensure_dirs(c["id"])
            mode = c.get("rec_mode") or "continuous"
            if mode == "continuous":
                want_rec[c["id"]] = (c, seg_min * 60)
            elif mode == "motion":
                want_rec[c["id"]] = (c, 60)  # short segments so motion-only pruning is fine-grained
            if db.motion_enabled(c):
                has_sub = bool(vendors.stream_urls(c)[1])
                want_det[c["id"]] = (c, c["id"] + (go2rtc.SUB if has_sub else ""))
        for cid in list(self.recs):
            w = want_rec.get(cid)
            if w is None or self.recs[cid].sig != (bool(w[0]["audio"]), w[1]):
                await self.recs.pop(cid).stop()
        for cid, (c, seg) in want_rec.items():
            if cid not in self.recs:
                self.recs[cid] = Rec(c, seg)
        for cid in list(self.dets):
            w = want_det.get(cid)
            if w is None or self.dets[cid].sig != det_sig(*w):
                await self.dets.pop(cid).stop()
        for cid, (c, src) in want_det.items():
            if cid not in self.dets:
                self.dets[cid] = Detector(c, src)

    async def restart(self, cam_id):
        for pool in (self.recs, self.dets):
            w = pool.pop(cam_id, None)
            if w:
                await w.stop()
        self.kick.set()

    def snapshot_state(self):
        out = {}
        for c in db.cameras():
            r, d = self.recs.get(c["id"]), self.dets.get(c["id"])
            out[c["id"]] = {
                "mode": c.get("rec_mode") or "continuous",
                "recording": bool(r and r.state == "იწერება" and time.time() - r.last_progress < STALL_SEC),
                "motion": bool(d and d.active),
                "detector": bool(d),
                "level": round(d.level * 100, 2) if d else None,
            }
        return out

    async def run(self):
        await asyncio.sleep(3)  # let go2rtc sync first
        while True:
            try:
                await self.reconcile()
            except Exception as e:
                log.exception("reconcile: %s", e)
            try:
                await asyncio.wait_for(self.kick.wait(), 20)
            except asyncio.TimeoutError:
                pass
            self.kick.clear()

    async def stop_all(self):
        for pool in (self.recs, self.dets):
            for w in list(pool.values()):
                await w.stop()


manager = None


def get():
    global manager
    if manager is None:
        manager = Manager()
    return manager
