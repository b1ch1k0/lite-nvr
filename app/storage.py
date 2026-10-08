"""Local retention + offload of finished segments to Google Cloud Storage or an SFTP box via rclone."""
import asyncio, datetime, logging, os, re, shutil, subprocess, time
from . import db
from .config import EVENTS_DIR, GCS_KEY, RCLONE_CONF, REC_DIR, SFTP_KEY

log = logging.getLogger("nvr.storage")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
state = {"last_run": None, "ok": None, "msg": "ჯერ არ გაშვებულა", "local_bytes": 0, "deleted": 0, "pruned": 0}
kick = asyncio.Event()


def write_rclone_conf(s):
    t = s["offload_target"]
    if t == "gcs":
        conf = (f"[remote]\ntype = google cloud storage\nservice_account_file = {GCS_KEY}\n"
                "bucket_policy_only = true\n")
    elif t == "sftp":
        conf = (f"[remote]\ntype = sftp\nhost = {s['sftp_host']}\nport = {int(s['sftp_port'] or 22)}\n"
                f"user = {s['sftp_user']}\nkey_file = {SFTP_KEY}\nshell_type = unix\n")
    else:
        return None
    with open(RCLONE_CONF, "w") as f:
        f.write(conf)
    os.chmod(RCLONE_CONF, 0o600)
    return conf


def remote_path(s):
    if s["offload_target"] == "gcs":
        return "remote:" + "/".join(p.strip("/") for p in (s["gcs_bucket"], s["gcs_prefix"]) if p.strip("/"))
    return "remote:" + (s["sftp_path"] or ".")


def rclone(args, timeout=3 * 3600):
    cmd = ["rclone", "--config", RCLONE_CONF, *args]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def ensure_sftp_key():
    if not os.path.exists(SFTP_KEY):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "nvr-offload", "-f", SFTP_KEY], check=True)
    with open(SFTP_KEY + ".pub") as f:
        return f.read().strip()


def test_remote():
    s = db.settings()
    if not write_rclone_conf(s):
        return False, "დისტანციური შენახვა გამორთულია"
    rp = remote_path(s)
    rc, out = rclone(["mkdir", rp], timeout=60)
    if rc == 0:
        rc, out = rclone(["lsf", rp, "--max-depth", "1"], timeout=60)
    return rc == 0, (out or "OK")[-1500:]


def local_cleanup(s):
    deleted = 0
    keep_days = int(s["retention_days"] or 7)
    cutoff = (datetime.date.today() - datetime.timedelta(days=keep_days)).isoformat()
    if not os.path.isdir(REC_DIR):
        return 0
    for cam in os.listdir(REC_DIR):
        cdir = os.path.join(REC_DIR, cam)
        if not os.path.isdir(cdir):
            continue
        for day in os.listdir(cdir):
            if DAY_RE.match(day) and day < cutoff:
                shutil.rmtree(os.path.join(cdir, day), ignore_errors=True)
                deleted += 1
    # emergency: keep min_free_pct free, delete oldest segments first
    min_free = float(s["min_free_pct"] or 10)
    du = shutil.disk_usage(REC_DIR)
    if du.free / du.total * 100 < min_free:
        files = []
        for root, _, names in os.walk(REC_DIR):
            for n in names:
                if n.endswith(".mp4"):
                    p = os.path.join(root, n)
                    files.append((os.path.basename(root) + n, p))
        files.sort()
        for _, p in files[:-1]:
            du = shutil.disk_usage(REC_DIR)
            if du.free / du.total * 100 >= min_free + 2:
                break
            try:
                os.remove(p)
                deleted += 1
            except OSError:
                pass
    return deleted


PAD_SEC = 10     # keep the neighbouring segment when motion starts/ends this close to its edge
SETTLE_SEC = 90  # only judge segments that finished at least this long ago


def seg_start(day, name):
    return datetime.datetime.strptime(f"{day} {name[:8]}", "%Y-%m-%d %H-%M-%S").timestamp()


def prune_motion_only():
    """For cameras in 'motion' mode delete finished segments that overlap no motion event.
    Only segments recorded after the camera switched to motion mode are considered."""
    removed = 0
    now = time.time()
    for c in db.cameras():
        if c.get("rec_mode") != "motion":
            continue
        since = c.get("mode_since") or 0
        cdir = os.path.join(REC_DIR, c["id"])
        if not os.path.isdir(cdir):
            continue
        for day in sorted(os.listdir(cdir)):
            if not DAY_RE.match(day):
                continue
            ddir = os.path.join(cdir, day)
            names = sorted(n for n in os.listdir(ddir) if n.endswith(".mp4"))
            for n in names[:-1]:  # never the segment being written
                p = os.path.join(ddir, n)
                try:
                    st, end = seg_start(day, n), os.path.getmtime(p)
                except (ValueError, OSError):
                    continue
                if st < since or end > now - SETTLE_SEC:
                    continue
                hit = db.q1("SELECT 1 FROM events WHERE cam_id=? AND start < ? AND COALESCE(end, ?) > ?",
                            (c["id"], end + PAD_SEC, now, st - PAD_SEC))
                if not hit:
                    try:
                        os.remove(p)
                        removed += 1
                    except OSError:
                        pass
    return removed


def events_cleanup(s):
    keep = int(s["retention_days"] or 7)
    cutoff = time.time() - keep * 86400
    for r in db.q("SELECT id, cam_id FROM events WHERE start < ? AND snap=1", (cutoff,)):
        try:
            os.remove(os.path.join(EVENTS_DIR, r["cam_id"], f"{r['id']}.jpg"))
        except OSError:
            pass
    db.ex("DELETE FROM events WHERE start < ?", (cutoff,))


def dir_size(path):
    total = 0
    for root, _, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def run_once():
    s = db.settings()
    msgs, ok = [], True
    state["pruned"] = prune_motion_only()
    state["deleted"] = local_cleanup(s)
    events_cleanup(s)
    if write_rclone_conf(s):
        rp = remote_path(s)
        seg = int(s["segment_minutes"] or 5)
        verb = "move" if s["offload_mode"] == "move" else "copy"
        args = [verb, REC_DIR, rp, "--min-age", f"{max(seg + 2, 4)}m", "--transfers", "2", "--checkers", "4",
                "--include", "*.mp4", "--log-level", "NOTICE"]
        if s["bwlimit"].strip():
            args += ["--bwlimit", s["bwlimit"].strip()]
        rc, out = rclone(args)
        ok = rc == 0
        msgs.append(f"{verb} → {rp}: " + ("OK" if ok else "შეცდომა") + (f"\n{out[-800:]}" if out else ""))
        rdays = int(s["remote_retention_days"] or 0)
        if ok and rdays > 0:
            rc, out = rclone(["delete", rp, "--min-age", f"{rdays}d", "--include", "*.mp4"])
            if rc != 0:
                ok = False
                msgs.append("ძველის წაშლა ღრუბელში: " + out[-400:])
            elif s["offload_target"] == "sftp":
                rclone(["rmdirs", rp, "--leave-root"])
    else:
        msgs.append("დისტანციური შენახვა გამორთულია — მხოლოდ ლოკალურად.")
    state.update(last_run=time.time(), ok=ok, msg="\n".join(msgs), local_bytes=dir_size(REC_DIR))


async def loop():
    await asyncio.sleep(20)
    while True:
        try:
            await asyncio.to_thread(run_once)
        except Exception as e:
            log.exception("storage run")
            state.update(last_run=time.time(), ok=False, msg=f"შეცდომა: {e}")
        try:
            interval = max(2, int(db.get_setting("offload_interval_min") or 10))
        except ValueError:
            interval = 10
        try:
            await asyncio.wait_for(kick.wait(), interval * 60)
        except asyncio.TimeoutError:
            pass
        kick.clear()
