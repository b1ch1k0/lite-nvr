"""ONVIF PTZ: continuous pan/tilt/zoom with a server-side dead-man stop, presets."""
import asyncio, logging, time
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape

from . import db, onvif

log = logging.getLogger("nvr.ptz")
NS = "http://www.onvif.org/ver20/ptz/wsdl"
TT = "http://www.onvif.org/ver10/schema"
DEADMAN_SEC = 1.6   # the browser repeats "move" every 0.5 s while a button is held
_cams = {}          # cam_id -> (signature, onvif.Camera, ptz_url, profile_token)
_stoppers = {}      # cam_id -> asyncio.Task


def _client(cam):
    sig = (cam["host"], cam.get("onvif_port"), cam.get("username"), cam.get("password"))
    cached = _cams.get(cam["id"])
    if cached and cached[0] == sig:
        return cached[1:]
    port = int(cam.get("onvif_port") or 80)
    c = onvif.Camera(cam["host"], port, cam.get("username") or "", cam.get("password") or "")
    c.sync_time()
    root = c.call(c.device_url, f'<GetCapabilities xmlns="{onvif.NS_DEV}"><Category>PTZ</Category></GetCapabilities>')
    x = onvif._txt(root, ".//{*}PTZ/{*}XAddr")
    if not x:
        raise onvif.OnvifError("კამერა PTZ-ს არ უჭერს მხარს")
    s = urlsplit(x)
    url = urlunsplit(s._replace(netloc=f"{cam['host']}:{port}"))
    prof = c.profiles()
    token = prof[0]["token"] if prof else "000"
    _cams[cam["id"]] = (sig, c, url, token)
    return c, url, token


def _call(cam, body):
    c, url, token = _client(cam)
    return c.call(url, body.replace("{TOKEN}", escape(token)))


def move_sync(cam, x, y, z):
    pt = f'<PanTilt xmlns="{TT}" x="{x:.2f}" y="{y:.2f}"/>' if (x or y) else ""
    zm = f'<Zoom xmlns="{TT}" x="{z:.2f}"/>' if z else ""
    _call(cam, f'<ContinuousMove xmlns="{NS}"><ProfileToken>{{TOKEN}}</ProfileToken><Velocity>{pt}{zm}</Velocity></ContinuousMove>')


def stop_sync(cam):
    _call(cam, f'<Stop xmlns="{NS}"><ProfileToken>{{TOKEN}}</ProfileToken><PanTilt>true</PanTilt><Zoom>true</Zoom></Stop>')


def presets_sync(cam):
    root = _call(cam, f'<GetPresets xmlns="{NS}"><ProfileToken>{{TOKEN}}</ProfileToken></GetPresets>')
    out = []
    for p in root.iter():
        if p.tag.endswith("}Preset"):
            out.append({"token": p.get("token"), "name": onvif._txt(p, "{*}Name") or p.get("token")})
    return out


def goto_sync(cam, token):
    _call(cam, f'<GotoPreset xmlns="{NS}"><ProfileToken>{{TOKEN}}</ProfileToken><PresetToken>{escape(token)}</PresetToken></GotoPreset>')


def set_preset_sync(cam, name, token=None):
    tok = f"<PresetToken>{escape(token)}</PresetToken>" if token else ""
    root = _call(cam, f'<SetPreset xmlns="{NS}"><ProfileToken>{{TOKEN}}</ProfileToken><PresetName>{escape(name)}</PresetName>{tok}</SetPreset>')
    return onvif._txt(root, ".//{*}PresetToken")


async def _deadman(cam):
    await asyncio.sleep(DEADMAN_SEC)
    try:
        await asyncio.to_thread(stop_sync, cam)
    except Exception as e:
        log.warning("deadman stop %s: %s", cam["id"], e)


def _rearm(cam):
    t = _stoppers.pop(cam["id"], None)
    if t:
        t.cancel()
    _stoppers[cam["id"]] = asyncio.create_task(_deadman(cam))


async def move(cam, x, y, z):
    x, y, z = (max(-1.0, min(1.0, float(v))) for v in (x, y, z))
    await asyncio.to_thread(move_sync, cam, x, y, z)
    _rearm(cam)


async def stop(cam):
    t = _stoppers.pop(cam["id"], None)
    if t:
        t.cancel()
    await asyncio.to_thread(stop_sync, cam)
