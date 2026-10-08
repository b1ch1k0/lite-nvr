import hashlib, hmac, os, secrets, time
from . import db, totp

SESSION_COOKIE = "nvr_s"
SESSION_TTL = 7 * 86400
ROLES = {
    "admin": "ადმინი — ყველაფერი",
    "operator": "ოპერატორი — ცოცხლად, ჩანაწერები, გადმოწერა, PTZ მართვა",
    "viewer": "მნახველი — მხოლოდ ცოცხლად",
}
ROLE_ACTIONS = {
    "admin": {"live", "playback", "download", "ptz"},
    "operator": {"live", "playback", "download", "ptz"},
    "viewer": {"live"},
}
_fails = {}


def hash_pw(pw):
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw, stored):
    try:
        _, s, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(s), n=2 ** 14, r=8, p=1).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


def blocked(ip):
    now = time.time()
    lst = [t for t in _fails.get(ip, []) if now - t < 600]
    _fails[ip] = lst
    return len(lst) >= 10


def note_fail(ip):
    _fails.setdefault(ip, []).append(time.time())


def verify_pw(username, pw):
    """Check username + password only. Returns the user row (dict) or None."""
    u = db.q1("SELECT * FROM users WHERE username=? AND active=1", (username,))
    if not u or not check_pw(pw, u["pw_hash"]):
        return None
    return dict(u)


def needs_totp(user):
    return bool(user and user.get("totp_secret"))


def check_totp(user, code):
    return totp.verify(user.get("totp_secret"), code)


def start_session(user):
    tok = secrets.token_urlsafe(32)
    db.ex("DELETE FROM sessions WHERE expires < ?", (time.time(),))
    db.ex("INSERT INTO sessions VALUES(?,?,?)", (tok, user["id"], time.time() + SESSION_TTL))
    return tok


def login(username, pw):
    """Password-only login (no 2FA). Kept for callers that don't do two-factor."""
    u = verify_pw(username, pw)
    return start_session(u) if u else None


def logout(tok):
    db.ex("DELETE FROM sessions WHERE token=?", (tok,))


def user_from_request(request):
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return None
    r = db.q1("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
              "WHERE s.token=? AND s.expires>? AND u.active=1", (tok, time.time()))
    return dict(r) if r else None


def allowed_cam_ids(user):
    if user["role"] == "admin":
        return [c["id"] for c in db.cameras()]
    return [r["cam_id"] for r in db.q("SELECT cam_id FROM user_cams WHERE user_id=?", (user["id"],))]


def can(user, cam_id, action):
    if not user or action not in ROLE_ACTIONS.get(user["role"], set()):
        return False
    if user["role"] == "admin":
        return db.camera(cam_id) is not None
    return db.q1("SELECT 1 FROM user_cams WHERE user_id=? AND cam_id=?", (user["id"], cam_id)) is not None
