import sqlite3, threading, time
from .config import DB_PATH

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'viewer', active INTEGER NOT NULL DEFAULT 1, created REAL);
CREATE TABLE IF NOT EXISTS user_cams(
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  cam_id TEXT REFERENCES cameras(id) ON DELETE CASCADE, PRIMARY KEY(user_id, cam_id));
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, expires REAL);
CREATE TABLE IF NOT EXISTS cameras(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, vendor TEXT NOT NULL, host TEXT, port INTEGER,
  username TEXT, password TEXT, channel INTEGER DEFAULT 1, main_url TEXT, sub_url TEXT,
  onvif_port INTEGER, record INTEGER DEFAULT 1, audio INTEGER DEFAULT 1,
  enabled INTEGER DEFAULT 1, sort INTEGER DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS audit(ts REAL, username TEXT, ip TEXT, action TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, cam_id TEXT NOT NULL, start REAL NOT NULL, end REAL, score REAL DEFAULT 0,
  snap INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS events_cam_start ON events(cam_id, start);
"""

# column, type — added to cameras when missing (upgrade path for existing installs)
CAM_COLUMNS = [
    ("rec_mode", "TEXT"),                    # off | continuous | motion
    ("motion", "INTEGER DEFAULT 0"),          # detect motion (always on in rec_mode=motion)
    ("motion_sens", "INTEGER DEFAULT 2"),     # 1 low, 2 medium, 3 high
    ("motion_area", "REAL DEFAULT 0.5"),      # % of frame that must change
    ("mode_since", "REAL"),                   # when rec_mode last became 'motion'
    ("ptz", "INTEGER DEFAULT 0"),             # ONVIF PTZ controls shown in the camera view
]
REC_MODES = {"off": "გამორთული", "continuous": "24/7 უწყვეტი", "motion": "მხოლოდ მოძრაობისას"}

DEFAULTS = {
    "retention_days": "7", "min_free_pct": "10", "segment_minutes": "5",
    "offload_target": "none", "offload_mode": "copy", "offload_interval_min": "10",
    "remote_retention_days": "30", "bwlimit": "",
    "gcs_bucket": "", "gcs_prefix": "nvr",
    "sftp_host": "", "sftp_port": "22", "sftp_user": "", "sftp_path": "nvr",
}


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        _local.c = c
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    have = {r["name"] for r in c.execute("PRAGMA table_info(cameras)")}
    for col, typ in CAM_COLUMNS:
        if col not in have:
            c.execute(f"ALTER TABLE cameras ADD COLUMN {col} {typ}")
    c.execute("UPDATE cameras SET rec_mode = CASE WHEN record=1 THEN 'continuous' ELSE 'off' END "
              "WHERE rec_mode IS NULL OR rec_mode NOT IN ('off','continuous','motion')")
    # events left open by a crash/restart
    c.execute("UPDATE events SET end = start + 10 WHERE end IS NULL")
    c.commit()


def q(sql, args=()):
    return conn().execute(sql, args).fetchall()


def q1(sql, args=()):
    return conn().execute(sql, args).fetchone()


def ex(sql, args=()):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur


def get_setting(key):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else DEFAULTS.get(key, "")


def settings():
    s = dict(DEFAULTS)
    for r in q("SELECT key, value FROM settings"):
        s[r["key"]] = r["value"]
    return s


def set_setting(key, value):
    ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, str(value)))


def cameras(enabled_only=False):
    sql = "SELECT * FROM cameras" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY sort, created"
    return [dict(r) for r in q(sql)]


def camera(cam_id):
    r = q1("SELECT * FROM cameras WHERE id=?", (cam_id,))
    return dict(r) if r else None


def next_cam_id():
    ids = {r["id"] for r in q("SELECT id FROM cameras")}
    n = 1
    while f"cam{n}" in ids:
        n += 1
    return f"cam{n}"


def audit(username, ip, action):
    ex("INSERT INTO audit VALUES(?,?,?,?)", (time.time(), username, ip, action))
    ex("DELETE FROM audit WHERE ts < ?", (time.time() - 180 * 86400,))


def motion_enabled(cam):
    return cam.get("rec_mode") == "motion" or bool(cam.get("motion"))
