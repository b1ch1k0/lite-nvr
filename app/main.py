import asyncio, datetime, hashlib, hmac, ipaddress, json, logging, os, re, secrets, shutil, subprocess, time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from . import auth, db, go2rtc, onvif, poster, ptz, qr, recorder, storage, totp, vendors
from .config import BASE, EVENTS_DIR, GCS_KEY, GO2RTC_RTSP, POSTER_DIR, REC_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))
CAM_RE = re.compile(r"^cam\d+$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILE_RE = re.compile(r"^\d{2}-\d{2}-\d{2}\.mp4$")


@asynccontextmanager
async def lifespan(app):
    db.init()
    os.makedirs(REC_DIR, exist_ok=True)
    os.makedirs(EVENTS_DIR, exist_ok=True)
    os.makedirs(POSTER_DIR, exist_ok=True)
    mgr = recorder.get()
    tasks = [asyncio.create_task(go2rtc.sync_loop()), asyncio.create_task(mgr.run()),
             asyncio.create_task(storage.loop()), asyncio.create_task(poster.loop())]
    yield
    for t in tasks:
        t.cancel()
    await mgr.stop_all()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


class Redirect(Exception):
    def __init__(self, url):
        self.url = url


@app.exception_handler(Redirect)
async def _redir(request, exc):
    return RedirectResponse(exc.url, 303)


def ip(request):
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


_CGNAT = ipaddress.ip_network("100.64.0.0/10")   # Tailscale / carrier VPNs count as local


def is_remote(request):
    """True when the viewer comes over the internet (e.g. via Cloudflare), False on the LAN or a VPN.
    Remote viewers start on the light sub-streams so the site's upload isn't eaten by a full-quality grid."""
    try:
        a = ipaddress.ip_address(ip(request))
    except ValueError:
        return True
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return not (a.is_private or a.is_loopback or a.is_link_local or a in _CGNAT)


def need(request, role=None):
    u = auth.user_from_request(request)
    if not u:
        raise HTTPException(404)
    if role == "admin" and u["role"] != "admin":
        raise HTTPException(403, "უფლება არ გაქვთ")
    return u


def page(request, name, user, **ctx):
    ctx.update(user=user, msg=request.query_params.get("msg"), err=request.query_params.get("err"),
               remote=is_remote(request))
    return templates.TemplateResponse(request, name, ctx)


def back(url, msg=None, err=None):
    q = []
    if msg:
        q.append("msg=" + quote(msg))
    if err:
        q.append("err=" + quote(err))
    return RedirectResponse(url + ("?" + "&".join(q) if q else ""), 303)


# ---------------------------------------------------------------- auth
# ---------------------------------------------------------------- login: hidden address + proof-of-work
# The login page lives only at /s/<random slug> (settings.gate_path). Everything else answers 404 to
# anonymous visitors, so scanners can't even tell a panel is here. The form posts JSON to /_/k together
# with a proof-of-work over a signed, single-use nonce; requests without a JS engine just get 404s.
POW_PREFIX = "0000"   # 16 bits ≈ 65k sha256 in the browser (~0.3–1 s)
_used_nonces = {}


def _secret():
    sec = db.get_setting("app_secret")
    if not sec:
        sec = secrets.token_hex(32)
        db.set_setting("app_secret", sec)
    return sec


def open_login():
    """settings.open_login=1 → the login page is also served at / (plain IP), not only at /s/<slug>."""
    return db.get_setting("open_login") == "1"


def login_url():
    return "/" if open_login() else f"/s/{gate_path()}"


def _login_page(request):
    r = templates.TemplateResponse(request, "login.html", {"user": None, "nonce": _make_nonce(), "pow": POW_PREFIX})
    r.headers["Cache-Control"] = "no-store"
    r.headers["X-Robots-Tag"] = "noindex, nofollow"
    return r


def gate_path(regenerate=False):
    g = db.get_setting("gate_path")
    if not g or regenerate:
        g = secrets.token_urlsafe(12).replace("-", "x").replace("_", "z")
        db.set_setting("gate_path", g)
    return g


def _make_nonce():
    body = f"{int(time.time())}.{secrets.token_hex(8)}"
    sig = hmac.new(_secret().encode(), body.encode(), hashlib.sha256).hexdigest()[:20]
    return f"{body}.{sig}"


def _check_pow(nonce, counter):
    try:
        ts, rnd, sig = nonce.split(".")
        good = hmac.new(_secret().encode(), f"{ts}.{rnd}".encode(), hashlib.sha256).hexdigest()[:20]
        if not hmac.compare_digest(sig, good) or abs(time.time() - int(ts)) > 900:
            return False
    except (ValueError, AttributeError):
        return False
    now = time.time()
    for k in [k for k, t in _used_nonces.items() if now - t > 1000]:
        _used_nonces.pop(k, None)
    if nonce in _used_nonces:
        return False
    if not hashlib.sha256(f"{nonce}:{counter}".encode()).hexdigest().startswith(POW_PREFIX):
        return False
    _used_nonces[nonce] = now
    return True


@app.get("/")
def root(request: Request):
    if auth.user_from_request(request):
        return RedirectResponse("/live", 303)
    if open_login():
        return _login_page(request)
    raise HTTPException(404)


@app.get("/s/{slug}", response_class=HTMLResponse)
def login_page(request: Request, slug: str):
    if not hmac.compare_digest(slug, gate_path()):
        raise HTTPException(404)
    if auth.user_from_request(request):
        return RedirectResponse("/live", 303)
    return _login_page(request)


@app.post("/_/k")
async def gate_auth(request: Request):
    addr = ip(request)
    try:
        j = await request.json()
    except Exception:
        return Response(status_code=404)
    if not isinstance(j, dict) or not _check_pow(str(j.get("n", "")), str(j.get("c", ""))):
        return Response(status_code=404)
    if auth.blocked(addr):
        return JSONResponse({"s": "locked"}, 429)
    username = str(j.get("u", "")).strip()[:64]
    user = auth.verify_pw(username, str(j.get("p", ""))[:256])
    if not user:
        auth.note_fail(addr)
        db.audit(username, addr, "შესვლა ვერ მოხერხდა")
        return JSONResponse({"s": "denied"}, 403)
    if auth.needs_totp(user):
        code = str(j.get("t", ""))[:12]
        if not code.strip():
            return JSONResponse({"s": "2fa", "n": _make_nonce()})   # ask for the 6-digit code
        if not auth.check_totp(user, code):
            auth.note_fail(addr)
            db.audit(username, addr, "ორფაქტორიანი კოდი არასწორია")
            return JSONResponse({"s": "2fa", "bad": 1, "n": _make_nonce()}, 403)
    tok = auth.start_session(user)
    db.audit(username, addr, "შესვლა")
    r = JSONResponse({"s": "ok", "to": "/live"})
    secure = request.headers.get("x-forwarded-proto") == "https"
    r.set_cookie(auth.SESSION_COOKIE, tok, max_age=auth.SESSION_TTL, httponly=True, samesite="lax", secure=secure)
    return r


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")


@app.post("/logout")
def logout(request: Request):
    tok = request.cookies.get(auth.SESSION_COOKIE)
    if tok:
        auth.logout(tok)
    r = RedirectResponse(login_url(), 303)
    r.delete_cookie(auth.SESSION_COOKIE)
    return r


@app.get("/auth/stream")
def auth_stream(request: Request):
    """nginx auth_request target for go2rtc /api/ws and /api/frame.jpeg."""
    u = auth.user_from_request(request)
    if not u:
        return Response(status_code=401)
    q = parse_qs(urlsplit(request.headers.get("x-original-uri", "")).query, keep_blank_values=True)
    srcs = q.get("src", [])
    if len(srcs) != 1 or set(q) - {"src", "width", "height", "w", "h"}:
        return Response(status_code=403)
    name = srcs[0]
    cam = name[: -len(go2rtc.SUB)] if name.endswith(go2rtc.SUB) else name
    return Response(status_code=204 if auth.can(u, cam, "live") else 403)


def _totp_pending_key(uid):
    return f"totp_pending:{uid}"


@app.get("/account", response_class=HTMLResponse)
def account(request: Request):
    u = need(request)
    pending = db.get_setting(_totp_pending_key(u["id"]))
    qr_svg, otp_uri = "", ""
    if pending and not u.get("totp_secret"):
        otp_uri = totp.provisioning_uri(pending, u["username"])
        qr_svg = qr.svg(otp_uri) or ""
    return page(request, "account.html", u, gate=gate_path() if u["role"] == "admin" else "",
                open_login=open_login(), totp_on=bool(u.get("totp_secret")),
                totp_pending=pending, totp_secret=pending, qr_svg=qr_svg, otp_uri=otp_uri)


@app.post("/account/2fa/start")
def account_2fa_start(request: Request):
    u = need(request)
    if not u.get("totp_secret"):
        db.set_setting(_totp_pending_key(u["id"]), totp.new_secret())
    return back("/account", msg="დაასკანერეთ QR კოდი აპლიკაციით და შეიყვანეთ კოდი დასადასტურებლად")


@app.post("/account/2fa/cancel")
def account_2fa_cancel(request: Request):
    u = need(request)
    db.set_setting(_totp_pending_key(u["id"]), "")
    return back("/account", msg="ორფაქტორიანი ავტორიზაციის ჩართვა გაუქმდა")


@app.post("/account/2fa/confirm")
async def account_2fa_confirm(request: Request):
    u = need(request)
    code = (await request.form()).get("code", "")
    pending = db.get_setting(_totp_pending_key(u["id"]))
    if not pending:
        return back("/account", err="ჯერ დააჭირეთ „ჩართვას“")
    if not totp.verify(pending, code):
        return back("/account", err="არასწორი კოდი — შეამოწმეთ, რომ ტელეფონის დრო ზუსტია")
    db.ex("UPDATE users SET totp_secret=? WHERE id=?", (pending, u["id"]))
    db.set_setting(_totp_pending_key(u["id"]), "")
    db.audit(u["username"], ip(request), "ორფაქტორიანი ავტორიზაცია ჩაირთო")
    return back("/account", msg="ორფაქტორიანი ავტორიზაცია ჩაირთო")


@app.post("/account/2fa/disable")
async def account_2fa_disable(request: Request):
    u = need(request)
    f = await request.form()
    if not auth.check_pw(f.get("password", ""), u["pw_hash"]):
        return back("/account", err="პაროლი არასწორია")
    db.ex("UPDATE users SET totp_secret=NULL WHERE id=?", (u["id"],))
    db.set_setting(_totp_pending_key(u["id"]), "")
    db.audit(u["username"], ip(request), "ორფაქტორიანი ავტორიზაცია გამოირთო")
    return back("/account", msg="ორფაქტორიანი ავტორიზაცია გამოირთო")


@app.post("/account/gate")
def account_gate(request: Request):
    u = need(request, "admin")
    gate_path(regenerate=True)
    db.audit(u["username"], ip(request), "შესვლის დამალული მისამართი შეიცვალა")
    return back("/account", msg="ახალი მისამართი შეიქმნა — ძველი აღარ მუშაობს")


@app.post("/account/open-login")
async def account_open_login(request: Request):
    u = need(request, "admin")
    on = (await request.form()).get("on") == "1"
    db.set_setting("open_login", "1" if on else "0")
    db.audit(u["username"], ip(request), "შესვლა IP-ზე " + ("ჩაირთო" if on else "გამოირთო"))
    return back("/account", msg="შესვლის გვერდი ახლა " + ("IP მისამართზეა" if on else "მხოლოდ დამალულ მისამართზეა"))


@app.post("/account")
def account_post(request: Request, old: str = Form(...), new: str = Form(...)):
    u = need(request)
    if not auth.check_pw(old, u["pw_hash"]):
        db.audit(u["username"], ip(request), "პაროლის შეცვლა ვერ მოხერხდა: ძველი პაროლი არასწორია")
        return back("/account", err="ძველი პაროლი არასწორია — პაროლი არ შეცვლილა")
    if len(new) < 8:
        db.audit(u["username"], ip(request), "პაროლის შეცვლა ვერ მოხერხდა: ახალი პაროლი 8 სიმბოლოზე მოკლეა")
        return back("/account", err="ახალი პაროლი მინიმუმ 8 სიმბოლო უნდა იყოს — პაროლი არ შეცვლილა")
    db.ex("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_pw(new), u["id"]))
    db.audit(u["username"], ip(request), "პაროლი შეიცვალა")
    return back("/account", msg="პაროლი შეიცვალა")


# ---------------------------------------------------------------- viewing
@app.get("/live", response_class=HTMLResponse)
def live(request: Request):
    u = need(request)
    ids = set(auth.allowed_cam_ids(u))
    cams = [c for c in db.cameras(enabled_only=True) if c["id"] in ids]
    for c in cams:
        c["has_sub"] = bool(vendors.stream_urls(c)[1])
    return page(request, "live.html", u, cams=cams, modes=db.REC_MODES)


@app.get("/state")
def state(request: Request):
    """Live badges: recording / motion per camera the user may see."""
    u = auth.user_from_request(request)
    if not u:
        return JSONResponse({}, status_code=401)
    ids = set(auth.allowed_cam_ids(u))
    return JSONResponse({k: v for k, v in recorder.get().snapshot_state().items() if k in ids})


@app.get("/cam/{cam_id}", response_class=HTMLResponse)
def cam_view(request: Request, cam_id: str):
    u = need(request)
    c = db.camera(cam_id)
    if not c or not auth.can(u, cam_id, "live"):
        raise HTTPException(404)
    c["has_sub"] = bool(vendors.stream_urls(c)[1])
    return page(request, "cam.html", u, cam=c, can_playback=auth.can(u, cam_id, "playback"),
                can_ptz=bool(c.get("ptz")) and auth.can(u, cam_id, "ptz"))


def _ptz_cam(request, cam_id):
    u = need(request)
    c = db.camera(cam_id)
    if not c or not c.get("ptz") or not auth.can(u, cam_id, "ptz"):
        raise HTTPException(403)
    return u, c


async def _ptz_do(coro):
    try:
        await coro
        return JSONResponse({"ok": True})
    except onvif.OnvifError as e:
        return JSONResponse({"ok": False, "error": str(e)}, 502)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{e.__class__.__name__}: {e}"}, 502)


@app.post("/ptz/{cam_id}/move")
async def ptz_move(request: Request, cam_id: str, x: float = Form(0), y: float = Form(0), z: float = Form(0)):
    _, c = _ptz_cam(request, cam_id)
    recorder.get().suppress_motion(cam_id, 6)  # covers the 1.6 s dead-man stop + refocus/exposure settle
    return await _ptz_do(ptz.move(c, x, y, z))


@app.post("/ptz/{cam_id}/stop")
async def ptz_stop(request: Request, cam_id: str):
    _, c = _ptz_cam(request, cam_id)
    recorder.get().suppress_motion(cam_id, 6)
    return await _ptz_do(ptz.stop(c))


@app.get("/ptz/{cam_id}/presets")
async def ptz_presets(request: Request, cam_id: str):
    _, c = _ptz_cam(request, cam_id)
    try:
        return JSONResponse({"ok": True, "presets": await asyncio.to_thread(ptz.presets_sync, c)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 502)


@app.post("/ptz/{cam_id}/goto")
async def ptz_goto(request: Request, cam_id: str, token: str = Form(...)):
    u, c = _ptz_cam(request, cam_id)
    recorder.get().suppress_motion(cam_id, 15)  # presets can travel far
    db.audit(u["username"], ip(request), f"{c['name']}: PTZ პრესეტზე გადასვლა {token}")
    return await _ptz_do(asyncio.to_thread(ptz.goto_sync, c, token))


@app.post("/ptz/{cam_id}/preset")
async def ptz_set_preset(request: Request, cam_id: str, name: str = Form(...), token: str = Form("")):
    u, c = _ptz_cam(request, cam_id)
    if u["role"] != "admin":
        raise HTTPException(403)
    try:
        tok = await asyncio.to_thread(ptz.set_preset_sync, c, name.strip()[:30] or "preset", token or None)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 502)
    db.audit(u["username"], ip(request), f"{c['name']}: PTZ პრესეტი შეინახა „{name}“ ({tok or token})")
    return JSONResponse({"ok": True, "token": tok or token})


def list_days(cam_id):
    d = os.path.join(REC_DIR, cam_id)
    if not os.path.isdir(d):
        return []
    return sorted([x for x in os.listdir(d) if DAY_RE.match(x) and os.listdir(os.path.join(d, x))], reverse=True)


@app.get("/playback", response_class=HTMLResponse)
def playback(request: Request, cam: str = "", day: str = ""):
    u = need(request)
    cams = [c for c in db.cameras() if auth.can(u, c["id"], "playback")]
    if not cams:
        return page(request, "playback.html", u, cams=[], cam=None, days=[], day="", files=[])
    cam = cam if any(c["id"] == cam for c in cams) else cams[0]["id"]
    days = list_days(cam)
    day = day if day in days else (days[0] if days else "")
    files, events = [], []
    if day:
        d = os.path.join(REC_DIR, cam, day)
        names = sorted([n for n in os.listdir(d) if FILE_RE.match(n)])
        now = time.time()
        midnight = datetime.datetime.strptime(day, "%Y-%m-%d").timestamp()
        for i, n in enumerate(names):
            st = os.stat(os.path.join(d, n))
            start = datetime.datetime.strptime(f"{day} {n[:8]}", "%Y-%m-%d %H-%M-%S").timestamp()
            files.append({"name": n, "label": n[:-4].replace("-", ":"), "mb": st.st_size / 1e6,
                          "t": round(start - midnight, 1), "dur": round(max(1.0, st.st_mtime - start), 1),
                          "live": i == len(names) - 1 and now - st.st_mtime < 20})
        for r in db.q("SELECT * FROM events WHERE cam_id=? AND start>=? AND start<? ORDER BY start",
                      (cam, midnight, midnight + 86400)):
            end = r["end"] or now
            events.append({"id": r["id"], "t": round(r["start"] - midnight, 1), "dur": round(max(1.0, end - r["start"]), 1),
                           "label": datetime.datetime.fromtimestamp(r["start"]).strftime("%H:%M:%S"),
                           "snap": bool(r["snap"]), "score": round((r["score"] or 0) * 100, 1), "open": r["end"] is None})
        for f in files:
            f["motion"] = any(e["t"] < f["t"] + f["dur"] and e["t"] + e["dur"] > f["t"] for e in events)
    c = db.camera(cam) or {}
    return page(request, "playback.html", u, cams=cams, cam=cam, days=days, day=day, files=files, events=events,
                can_dl=auth.can(u, cam, "download"), mode=db.REC_MODES.get(c.get("rec_mode") or "continuous"))


@app.get("/poster/{cam_id}.jpg")
def poster_img(request: Request, cam_id: str):
    u = need(request)
    if not CAM_RE.match(cam_id) or not auth.can(u, cam_id, "live"):
        raise HTTPException(403)
    if not os.path.exists(os.path.join(POSTER_DIR, f"{cam_id}.jpg")):
        raise HTTPException(404)
    return Response(headers={"X-Accel-Redirect": f"/_poster/{cam_id}.jpg", "Cache-Control": "no-cache"},
                    media_type="image/jpeg")


@app.get("/ev/{cam_id}/{ev_id}.jpg")
def event_snap(request: Request, cam_id: str, ev_id: int):
    u = need(request)
    if not CAM_RE.match(cam_id) or not auth.can(u, cam_id, "playback"):
        raise HTTPException(403)
    return Response(headers={"X-Accel-Redirect": f"/_ev/{cam_id}/{int(ev_id)}.jpg"}, media_type="image/jpeg")


@app.get("/rec/{cam_id}/{day}/{fname}")
def rec_file(request: Request, cam_id: str, day: str, fname: str, dl: int = 0):
    u = need(request)
    if not (CAM_RE.match(cam_id) and DAY_RE.match(day) and FILE_RE.match(fname)):
        raise HTTPException(404)
    if not auth.can(u, cam_id, "download" if dl else "playback"):
        raise HTTPException(403)
    if not os.path.isfile(os.path.join(REC_DIR, cam_id, day, fname)):
        raise HTTPException(404)
    h = {"X-Accel-Redirect": f"/_rec/{cam_id}/{day}/{fname}"}
    if dl:
        h["Content-Disposition"] = f'attachment; filename="{cam_id}_{day}_{fname}"'
        db.audit(u["username"], ip(request), f"გადმოწერა {cam_id}/{day}/{fname}")
    return Response(headers=h, media_type="video/mp4")


# ---------------------------------------------------------------- admin: cameras
def cam_form_values(c=None):
    c = c or {}
    return {k: c.get(k) for k in ("id", "name", "vendor", "host", "port", "username", "password", "channel",
                                  "main_url", "sub_url", "onvif_port", "rec_mode", "motion", "motion_sens", "motion_area",
                                  "audio", "enabled", "ptz")}


@app.get("/admin/cameras", response_class=HTMLResponse)
async def cameras_page(request: Request):
    u = need(request, "admin")
    streams = await go2rtc.status() or {}
    recs = recorder.get().recs
    cams = db.cameras()
    for c in cams:
        main, sub = vendors.stream_urls(c)
        c["main_masked"], c["sub_masked"] = vendors.mask(main), vendors.mask(sub)
        c["vendor_label"] = vendors.VENDORS.get(c["vendor"], {}).get("label", c["vendor"])
        st = streams.get(c["id"]) or {}
        c["online"] = go2rtc.is_online(st)
        c["has_consumers"] = bool(st.get("consumers"))
        r = recs.get(c["id"])
        c["rec_state"] = r.state if r else ""
        d = recorder.get().dets.get(c["id"])
        c["det_state"] = d.state if d else ""
        c["viewers"] = [r["username"] for r in db.q(
            "SELECT u.username FROM user_cams uc JOIN users u ON u.id=uc.user_id WHERE uc.cam_id=? AND u.role<>'admin' "
            "ORDER BY u.username", (c["id"],))]
    return page(request, "cameras.html", u, cams=cams, modes=db.REC_MODES)


@app.post("/admin/cameras/{cam_id}/mode")
async def cam_mode(request: Request, cam_id: str):
    """Quick per-camera switches from the camera list / live view."""
    u = need(request, "admin")
    c = db.camera(cam_id)
    if not c:
        raise HTTPException(404)
    f = await request.form()
    mode = f.get("rec_mode", c.get("rec_mode"))
    if mode not in db.REC_MODES:
        return JSONResponse({"ok": False, "error": "უცნობი რეჟიმი"}, 400)
    motion = c.get("motion") or 0
    if "motion" in f:
        motion = 1 if f.get("motion") in ("1", "on", "true") else 0
    set_mode(cam_id, c, mode, motion)
    db.audit(u["username"], ip(request), f"{c['name']}: ჩაწერა={db.REC_MODES[mode]}, მოძრაობის დეტექცია={'ჩართ' if motion else 'გამორთ'}")
    await recorder.get().restart(cam_id)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True, "rec_mode": mode, "motion": motion})
    return back(request.headers.get("referer", "/admin/cameras").split("?")[0], msg=f"{c['name']}: {db.REC_MODES[mode]}")


def set_mode(cam_id, old, mode, motion):
    since = old.get("mode_since")
    if mode == "motion" and old.get("rec_mode") != "motion":
        since = time.time()
    db.ex("UPDATE cameras SET rec_mode=?, record=?, motion=?, mode_since=? WHERE id=?",
          (mode, 0 if mode == "off" else 1, motion, since, cam_id))


@app.get("/admin/cameras/new", response_class=HTMLResponse)
def cam_new(request: Request, vendor: str = "tapo"):
    u = need(request, "admin")
    vals = cam_form_values({"vendor": vendor, "rec_mode": "continuous", "audio": 1, "enabled": 1, "channel": 1,
                            "motion": 0, "motion_sens": 2, "motion_area": 0.5})
    for k in ("host", "name", "main_url", "sub_url", "username", "onvif_port"):
        if request.query_params.get(k):
            vals[k] = request.query_params[k]
    return page(request, "cam_form.html", u, c=vals, vendors=vendors.VENDORS, new=True, modes=db.REC_MODES)


@app.get("/admin/cameras/{cam_id}", response_class=HTMLResponse)
def cam_edit(request: Request, cam_id: str):
    u = need(request, "admin")
    c = db.camera(cam_id)
    if not c:
        raise HTTPException(404)
    return page(request, "cam_form.html", u, c=cam_form_values(c), vendors=vendors.VENDORS, new=False, modes=db.REC_MODES)


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v, default=None):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return default


@app.post("/admin/cameras/save")
async def cam_save(request: Request):
    u = need(request, "admin")
    f = await request.form()
    cam_id = f.get("id") or ""
    vendor = f.get("vendor", "rtsp")
    if vendor not in vendors.VENDORS:
        return back("/admin/cameras", err="უცნობი მწარმოებელი")
    name = (f.get("name") or "").strip() or f.get("host") or "კამერა"
    v = vendors.VENDORS[vendor]
    row = dict(name=name, vendor=vendor, host=(f.get("host") or "").strip(),
               port=_int(f.get("port"), v["port"]), username=(f.get("username") or "").strip(),
               channel=_int(f.get("channel"), 1), main_url=(f.get("main_url") or "").strip(),
               sub_url=(f.get("sub_url") or "").strip(), onvif_port=_int(f.get("onvif_port"), v["onvif_port"]),
               audio=1 if f.get("audio") else 0, enabled=1 if f.get("enabled") else 0,
               ptz=1 if f.get("ptz") else 0,
               motion_sens=min(3, max(1, _int(f.get("motion_sens"), 2))),
               motion_area=min(50.0, max(0.05, _float(f.get("motion_area"), 0.5))))
    mode = f.get("rec_mode") if f.get("rec_mode") in db.REC_MODES else "continuous"
    motion = 1 if f.get("motion") else 0
    pw = f.get("password") or ""
    if v.get("main") and not row["host"]:
        return back(request.headers.get("referer", "/admin/cameras"), err="IP მისამართი სავალდებულოა")
    if not v.get("main") and not row["main_url"]:
        return back(request.headers.get("referer", "/admin/cameras"), err="ძირითადი ნაკადის მისამართი სავალდებულოა")
    if cam_id and db.camera(cam_id):
        old = db.camera(cam_id)
        row["password"] = pw if pw else old["password"]
        sets = ", ".join(f"{k}=?" for k in row)
        db.ex(f"UPDATE cameras SET {sets} WHERE id=?", (*row.values(), cam_id))
        set_mode(cam_id, old, mode, motion)
        go2rtc.mark_dirty(cam_id)
        db.audit(u["username"], ip(request), f"კამერა შეიცვალა {cam_id} ({name})")
        await recorder.get().restart(cam_id)
    else:
        cam_id = db.next_cam_id()
        row["password"] = pw
        cols = ", ".join(["id", "created", *row])
        db.ex(f"INSERT INTO cameras({cols}) VALUES({','.join('?' * (len(row) + 2))})",
              (cam_id, time.time(), *row.values()))
        set_mode(cam_id, {}, mode, motion)
        db.audit(u["username"], ip(request), f"კამერა დაემატა {cam_id} ({name})")
    try:
        await go2rtc.sync_once()
    except Exception:
        go2rtc.kick.set()
    recorder.get().kick.set()
    return back("/admin/cameras", msg=f"შენახულია: {name}")


@app.post("/admin/cameras/{cam_id}/delete")
async def cam_delete(request: Request, cam_id: str, wipe: str = Form("")):
    u = need(request, "admin")
    c = db.camera(cam_id)
    if not c:
        raise HTTPException(404)
    db.ex("DELETE FROM cameras WHERE id=?", (cam_id,))
    await recorder.get().restart(cam_id)
    go2rtc.kick.set()
    db.ex("DELETE FROM events WHERE cam_id=?", (cam_id,))
    if CAM_RE.match(cam_id):
        shutil.rmtree(os.path.join(EVENTS_DIR, cam_id), ignore_errors=True)
        try:
            os.remove(os.path.join(POSTER_DIR, f"{cam_id}.jpg"))
        except OSError:
            pass
    if wipe and CAM_RE.match(cam_id):
        shutil.rmtree(os.path.join(REC_DIR, cam_id), ignore_errors=True)
    db.audit(u["username"], ip(request), f"კამერა წაიშალა {cam_id} ({c['name']})" + (" + ჩანაწერები" if wipe else ""))
    return back("/admin/cameras", msg=f"წაიშალა: {c['name']}")


@app.post("/admin/cameras/test")
async def cam_test(request: Request):
    need(request, "admin")
    f = dict(await request.form())
    cam = {k: f.get(k) for k in ("vendor", "host", "port", "username", "password", "channel", "main_url", "sub_url")}
    if not cam["password"] and f.get("id"):
        old = db.camera(f["id"])
        if old:
            cam["password"] = old["password"]
    try:
        cam["port"] = _int(cam["port"])
        main, sub = vendors.stream_urls(cam)
    except Exception as e:
        return JSONResponse({"ok": False, "results": [{"url": "", "error": str(e)}]})
    live = {}
    if f.get("id"):
        streams = await go2rtc.status() or {}
        for name in (f["id"], f["id"] + go2rtc.SUB):
            for pu in go2rtc.producer_urls(streams.get(name)):
                live[pu] = name
    out = []
    for kind, url in (("ძირითადი", main), ("დამხმარე", sub)):
        if not url:
            continue
        if url in live:
            res = await ffprobe(f"{GO2RTC_RTSP}/{live[url]}")
            if res.get("ok"):
                res["info"] += " (არსებული კავშირით, go2rtc)"
        else:
            res = await ffprobe(url)
        out.append({"kind": kind, "url": vendors.mask(url), **res})
    return JSONResponse({"ok": all(r.get("ok") for r in out) and bool(out), "results": out})


async def ffprobe(url):
    if url.startswith("dvrip://"):
        return {"ok": True, "info": "DVRIP — შემოწმდება შენახვის შემდეგ (go2rtc)"}
    cmd = ["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-timeout", "8000000",
           "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate", "-of", "json", url]
    try:
        p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        so, se = await asyncio.wait_for(p.communicate(), 15)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "დრო ამოიწურა (15წმ) — კამერა არ პასუხობს"}
    if p.returncode != 0:
        err = se.decode(errors="replace").strip().replace(url, "<url>")
        if "401" in err:
            err = "401 Unauthorized — სახელი/პაროლი არასწორია"
        return {"ok": False, "error": err[-400:] or "უცნობი შეცდომა"}
    streams = json.loads(so or b"{}").get("streams", [])
    parts = []
    for s in streams:
        if s.get("codec_type") == "video":
            fps = s.get("avg_frame_rate", "0/1").split("/")
            fr = round(int(fps[0]) / int(fps[1])) if len(fps) == 2 and int(fps[1] or 0) else "?"
            parts.append(f"ვიდეო {s.get('codec_name')} {s.get('width')}x{s.get('height')} {fr}fps")
        elif s.get("codec_type") == "audio":
            parts.append(f"აუდიო {s.get('codec_name')}")
    codecs = {s.get("codec_name") for s in streams if s.get("codec_type") == "video"}
    warn = ""
    if "hevc" in codecs:
        warn = " ⚠ H.265 — ბრაუზერში ცოცხალი ჩვენება შეიძლება არ იმუშაოს"
    if "mjpeg" in codecs:
        return {"ok": False, "error": ", ".join(parts) + " — MJPEG ნაკადი არ გამოდგება (ცოცხლად არ ჩანს, ჩაწერაც მძიმეა). აირჩიეთ H.264 ნაკადი."}
    return {"ok": True, "info": ", ".join(parts) + warn}


# ---------------------------------------------------------------- admin: ONVIF / auto-detect
# ports worth probing on an unknown camera: RTSP, the common ONVIF service ports, and vendor SDK ports
DETECT_PORTS = {
    554: "RTSP", 8554: "RTSP",
    80: "ONVIF / HTTP", 8000: "ONVIF (Hikvision/Reolink)", 2020: "ONVIF (Tapo)",
    8899: "ONVIF (XMEye)", 8080: "ONVIF", 37777: "Dahua SDK", 34567: "XMEye (DVRIP)",
}
# vendors tried over RTSP on an open 554, in order; label from VENDORS
DETECT_RTSP_VENDORS = ["hikvision", "dahua", "tapo", "uniview", "reolink", "axis"]
# ONVIF service ports tried, most specific first
DETECT_ONVIF_PORTS = [2020, 8899, 8000, 80, 8080]


async def _port_open(host, port, timeout=0.8):
    try:
        fut = asyncio.open_connection(host, port)
        r, w = await asyncio.wait_for(fut, timeout)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


@app.post("/admin/onvif/detect")
async def onvif_detect(request: Request, host: str = Form(...), username: str = Form(""), password: str = Form("")):
    need(request, "admin")
    host = host.strip()
    if not host:
        return JSONResponse({"ok": False, "error": "შეიყვანეთ IP"})
    user, pw = username.strip(), password
    # 1) which ports are open
    results = await asyncio.gather(*[_port_open(host, p) for p in DETECT_PORTS])
    open_ports = {p for p, ok in zip(DETECT_PORTS, results) if ok}
    findings = []
    # 2) ONVIF — first service port that answers gives us real stream URIs + profiles
    onvif_hit = None
    for port in DETECT_ONVIF_PORTS:
        if port not in open_ports:
            continue
        try:
            cam = onvif.Camera(host, port, user, pw)
            res = await asyncio.to_thread(cam.probe_all)
            onvif_hit = {"method": "onvif", "port": port, **res}
            break
        except onvif.OnvifError:
            continue
        except Exception:
            continue
    # 3) RTSP — only when ONVIF didn't already hand us exact URLs. Try each vendor's standard path on 554,
    #    one at a time (cheap cameras drop extra RTSP sessions), reporting those that return real video.
    if not onvif_hit and (554 in open_ports or 8554 in open_ports):
        rport = 554 if 554 in open_ports else 8554
        for vkey in DETECT_RTSP_VENDORS:
            v = vendors.VENDORS[vkey]
            cam = {"vendor": vkey, "host": host, "port": rport, "username": user, "password": pw, "channel": 1}
            main, sub = vendors.stream_urls(cam)
            r = await ffprobe(main)
            if r.get("ok"):
                findings.append({"method": "rtsp", "vendor": vkey, "label": v["label"], "port": rport,
                                 "info": r.get("info", ""), "main": vendors.mask(main), "sub": vendors.mask(sub)})
    return JSONResponse({"ok": True, "host": host,
                         "open": [{"port": p, "label": DETECT_PORTS[p]} for p in sorted(open_ports)],
                         "onvif": onvif_hit, "rtsp": findings})


@app.post("/admin/detect/add")
async def detect_add(request: Request):
    """Add a camera found by RTSP vendor detection — stored by vendor template, creds kept out of the URL."""
    u = need(request, "admin")
    f = await request.form()
    vendor = (f.get("vendor") or "").strip()
    if vendor not in vendors.VENDORS:
        return JSONResponse({"ok": False, "error": "უცნობი ვენდორი"})
    cam_id = db.next_cam_id()
    host = (f.get("host") or "").strip()
    name = (f.get("name") or "").strip() or f"{vendors.VENDORS[vendor]['label']} {host}"
    port = _int(f.get("port"), vendors.VENDORS[vendor]["port"])
    db.ex("INSERT INTO cameras(id,name,vendor,host,port,username,password,channel,onvif_port,"
          "record,audio,enabled,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (cam_id, name, vendor, host, port, (f.get("username") or "").strip(), f.get("password") or "",
           _int(f.get("channel"), 1), vendors.VENDORS[vendor]["onvif_port"], 1, 1, 1, time.time()))
    set_mode(cam_id, {}, "continuous", 0)
    db.audit(u["username"], ip(request), f"კამერა დაემატა ავტომატურად {cam_id} ({name}, {vendor})")
    try:
        await go2rtc.sync_once()
    except Exception:
        go2rtc.kick.set()
    recorder.get().kick.set()
    return JSONResponse({"ok": True, "id": cam_id})


@app.get("/admin/onvif", response_class=HTMLResponse)
def onvif_page(request: Request):
    return page(request, "onvif.html", need(request, "admin"))


@app.post("/admin/onvif/scan")
async def onvif_scan(request: Request, subnets: str = Form("")):
    need(request, "admin")
    nets = [s for s in re.split(r"[,\s]+", subnets) if s]
    try:
        found = await asyncio.to_thread(onvif.discover, nets, 3.0)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)})
    known = {c["host"] for c in db.cameras()}
    for d in found:
        d["added"] = d["ip"] in known
    return JSONResponse({"ok": True, "found": found})


@app.post("/admin/onvif/probe")
async def onvif_probe(request: Request, host: str = Form(...), port: int = Form(80),
                      username: str = Form(""), password: str = Form("")):
    need(request, "admin")
    try:
        cam = onvif.Camera(host.strip(), port, username.strip(), password)
        res = await asyncio.to_thread(cam.probe_all)
    except onvif.OnvifError as e:
        return JSONResponse({"ok": False, "error": str(e)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{e.__class__.__name__}: {e}"})
    return JSONResponse({"ok": True, **res})


@app.post("/admin/onvif/add")
async def onvif_add(request: Request):
    u = need(request, "admin")
    f = await request.form()
    cam_id = db.next_cam_id()
    host = (f.get("host") or "").strip()
    name = (f.get("name") or "").strip() or host
    main, sub = (f.get("main_url") or "").strip(), (f.get("sub_url") or "").strip()
    if not main:
        return JSONResponse({"ok": False, "error": "ძირითადი ნაკადი არ არის არჩეული"})
    db.ex("INSERT INTO cameras(id,name,vendor,host,port,username,password,channel,main_url,sub_url,onvif_port,"
          "record,audio,enabled,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (cam_id, name, "onvif", host, 554, (f.get("username") or "").strip(), f.get("password") or "", 1,
           main, sub if sub != main else "", _int(f.get("onvif_port"), 80), 1, 1, 1, time.time()))
    set_mode(cam_id, {}, "continuous", 0)
    db.audit(u["username"], ip(request), f"კამერა დაემატა ONVIF-ით {cam_id} ({name})")
    try:
        await go2rtc.sync_once()
    except Exception:
        go2rtc.kick.set()
    recorder.get().kick.set()
    return JSONResponse({"ok": True, "id": cam_id})


# ---------------------------------------------------------------- admin: users
@app.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request):
    u = need(request, "admin")
    users = [dict(r) for r in db.q("SELECT * FROM users ORDER BY username")]
    cams = {c["id"]: c["name"] for c in db.cameras()}
    for x in users:
        x["cams"] = [cams.get(r["cam_id"], r["cam_id"]) for r in
                     db.q("SELECT cam_id FROM user_cams WHERE user_id=?", (x["id"],))]
    return page(request, "users.html", u, users=users, roles=auth.ROLES, cams=db.cameras())


@app.get("/admin/users/{uid}", response_class=HTMLResponse)
def user_edit(request: Request, uid: int):
    u = need(request, "admin")
    x = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    if not x:
        raise HTTPException(404)
    mine = {r["cam_id"] for r in db.q("SELECT cam_id FROM user_cams WHERE user_id=?", (uid,))}
    return page(request, "user_form.html", u, x=dict(x), roles=auth.ROLES, cams=db.cameras(), mine=mine)


def admins_left(excluding):
    return db.q1("SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1 AND id<>?", (excluding,))["n"]


@app.post("/admin/users/save")
async def user_save(request: Request):
    u = need(request, "admin")
    f = await request.form()
    uid = _int(f.get("id"))
    role = f.get("role", "viewer")
    if role not in auth.ROLES:
        return back("/admin/users", err="უცნობი როლი")
    active = 1 if f.get("active") else 0
    pw = f.get("password") or ""
    cam_ids = [c for c in f.getlist("cams") if db.camera(c)]
    if uid:
        x = db.q1("SELECT * FROM users WHERE id=?", (uid,))
        if not x:
            raise HTTPException(404)
        if x["role"] == "admin" and (role != "admin" or not active) and admins_left(uid) == 0:
            return back(f"/admin/users/{uid}", err="ბოლო აქტიურ ადმინს ვერ ჩამოართმევთ უფლებას")
        db.ex("UPDATE users SET role=?, active=? WHERE id=?", (role, active, uid))
        if pw:
            if len(pw) < 8:
                db.audit(u["username"], ip(request), f"{x['username']}: პაროლის შეცვლა ვერ მოხერხდა (8 სიმბოლოზე მოკლე)")
                return back(f"/admin/users/{uid}", err="პაროლი მინიმუმ 8 სიმბოლო უნდა იყოს — პაროლი არ შეცვლილა")
            db.ex("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_pw(pw), uid))
            db.ex("DELETE FROM sessions WHERE user_id=?", (uid,))
            db.audit(u["username"], ip(request), f"{x['username']}: პაროლი შეიცვალა ადმინის მიერ")
        if not active:
            db.ex("DELETE FROM sessions WHERE user_id=?", (uid,))
        uname = x["username"]
    else:
        uname = (f.get("username") or "").strip()
        if not re.match(r"^[A-Za-z0-9._@-]{3,40}$", uname):
            return back("/admin/users", err="სახელი: 3-40 ლათინური სიმბოლო")
        if len(pw) < 8:
            return back("/admin/users", err="პაროლი მინიმუმ 8 სიმბოლო")
        if db.q1("SELECT 1 FROM users WHERE username=?", (uname,)):
            return back("/admin/users", err="ასეთი მომხმარებელი უკვე არსებობს")
        uid = db.ex("INSERT INTO users(username,pw_hash,role,active,created) VALUES(?,?,?,?,?)",
                    (uname, auth.hash_pw(pw), role, active, time.time())).lastrowid
    db.ex("DELETE FROM user_cams WHERE user_id=?", (uid,))
    for c in cam_ids:
        db.ex("INSERT INTO user_cams VALUES(?,?)", (uid, c))
    db.audit(u["username"], ip(request), f"მომხმარებელი {uname}: როლი={role}, აქტიური={active}, კამერები={','.join(cam_ids) or '-'}")
    return back("/admin/users", msg=f"შენახულია: {uname}")


@app.post("/admin/users/{uid}/2fa-reset")
def user_2fa_reset(request: Request, uid: int):
    u = need(request, "admin")
    x = db.q1("SELECT username FROM users WHERE id=?", (uid,))
    if not x:
        raise HTTPException(404)
    db.ex("UPDATE users SET totp_secret=NULL WHERE id=?", (uid,))
    db.set_setting(_totp_pending_key(uid), "")
    db.audit(u["username"], ip(request), f"ორფაქტორიანი ავტორიზაცია გაუუქმდა: {x['username']}")
    return back("/admin/users", msg=f"2FA გაუუქმდა: {x['username']}")


@app.post("/admin/users/{uid}/delete")
def user_delete(request: Request, uid: int):
    u = need(request, "admin")
    x = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    if not x:
        raise HTTPException(404)
    if x["id"] == u["id"]:
        return back("/admin/users", err="საკუთარ თავს ვერ წაშლით")
    if x["role"] == "admin" and admins_left(uid) == 0:
        return back("/admin/users", err="ბოლო ადმინს ვერ წაშლით")
    db.ex("DELETE FROM users WHERE id=?", (uid,))
    db.audit(u["username"], ip(request), f"მომხმარებელი წაიშალა {x['username']}")
    return back("/admin/users", msg=f"წაიშალა: {x['username']}")


# ---------------------------------------------------------------- admin: storage
@app.get("/admin/storage", response_class=HTMLResponse)
def storage_page(request: Request):
    u = need(request, "admin")
    s = db.settings()
    du = shutil.disk_usage(REC_DIR)
    pub = storage.ensure_sftp_key() if s["offload_target"] == "sftp" else ""
    return page(request, "storage.html", u, s=s, du=du, st=storage.state, sftp_pub=pub,
                gcs_key=os.path.exists(GCS_KEY))


@app.post("/admin/storage")
async def storage_save(request: Request):
    u = need(request, "admin")
    f = await request.form()
    old_seg = db.get_setting("segment_minutes")
    for k in db.DEFAULTS:
        if k in f:
            db.set_setting(k, str(f.get(k)).strip())
    key = (f.get("gcs_key_json") or "").strip()
    if key:
        try:
            j = json.loads(key)
            assert j.get("type") == "service_account" and j.get("private_key")
        except Exception:
            return back("/admin/storage", err="Service account JSON არასწორია")
        with open(GCS_KEY, "w") as fh:
            fh.write(key)
        os.chmod(GCS_KEY, 0o600)
    if db.get_setting("offload_target") == "sftp":
        storage.ensure_sftp_key()
    if db.get_setting("segment_minutes") != old_seg:
        recorder.get().kick.set()
    db.audit(u["username"], ip(request), "შენახვის პარამეტრები შეიცვალა")
    return back("/admin/storage", msg="შენახულია")


@app.post("/admin/storage/test")
async def storage_test(request: Request):
    need(request, "admin")
    ok, out = await asyncio.to_thread(storage.test_remote)
    return JSONResponse({"ok": ok, "out": out})


@app.post("/admin/storage/run")
def storage_run(request: Request):
    need(request, "admin")
    storage.kick.set()
    return back("/admin/storage", msg="გაშვებულია — შედეგი რამდენიმე წუთში გამოჩნდება")


# ---------------------------------------------------------------- admin: status
@app.get("/admin/status", response_class=HTMLResponse)
async def status_page(request: Request):
    u = need(request, "admin")
    streams = await go2rtc.status()
    recs = recorder.get().recs
    cams = db.cameras()
    dets = recorder.get().dets
    for c in cams:
        c["rec"] = recs.get(c["id"])
        c["det"] = dets.get(c["id"])
    with open("/proc/loadavg") as fh:
        load = fh.read().split()[:3]
    mem = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            k, v = line.split(":")
            mem[k] = int(v.split()[0]) // 1024
    du = shutil.disk_usage(REC_DIR)
    logs = [dict(r) for r in db.q("SELECT * FROM audit ORDER BY ts DESC LIMIT 100")]
    return page(request, "status.html", u, cams=cams, go2rtc_ok=streams is not None, load=load, mem=mem,
                du=du, st=storage.state, logs=logs, now=time.time())




def fmt_ts(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "—"


def fmt_gb(b):
    return f"{b / 1e9:.1f} GB"


templates.env.filters["ts"] = fmt_ts
templates.env.filters["gb"] = fmt_gb
