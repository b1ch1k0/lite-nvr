"""Minimal ONVIF client (WS-UsernameToken digest) + WS-Discovery. No external deps besides httpx."""
import base64, datetime, hashlib, ipaddress, os, select, socket, time, uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape

import httpx

NS_DEV = "http://www.onvif.org/ver10/device/wsdl"
NS_MEDIA = "http://www.onvif.org/ver10/media/wsdl"
NS_SCHEMA = "http://www.onvif.org/ver10/schema"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
PW_DIGEST = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
B64 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-soap-message-security-1.0#Base64Binary"


class OnvifError(Exception):
    pass


def _txt(el, path):
    f = el.find(path)
    return f.text.strip() if f is not None and f.text else ""


def _rehost(url, host):
    """Cameras often report an internal IP; force the address we actually reach."""
    if not url:
        return url
    s = urlsplit(url)
    netloc = host + (f":{s.port}" if s.port else "")
    if s.username:
        netloc = s.netloc.rsplit("@", 1)[0] + "@" + netloc
    return urlunsplit(s._replace(netloc=netloc))


class Camera:
    def __init__(self, host, port=80, user="", password="", timeout=8):
        self.host, self.port, self.user, self.password = host, int(port), user, password
        self.device_url = f"http://{host}:{self.port}/onvif/device_service"
        self.media_url = None
        self.offset = datetime.timedelta(0)
        self.http = httpx.Client(timeout=timeout)

    def _header(self):
        if not self.user:
            return ""
        nonce = os.urandom(16)
        created = (datetime.datetime.now(datetime.timezone.utc) + self.offset).strftime("%Y-%m-%dT%H:%M:%SZ")
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()).decode()
        return (f'<s:Header><Security s:mustUnderstand="1" xmlns="{WSSE}"><UsernameToken>'
                f'<Username>{escape(self.user)}</Username><Password Type="{PW_DIGEST}">{digest}</Password>'
                f'<Nonce EncodingType="{B64}">{base64.b64encode(nonce).decode()}</Nonce>'
                f'<Created xmlns="{WSU}">{created}</Created></UsernameToken></Security></s:Header>')

    def call(self, url, body, auth=True):
        env = (f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
               f'{self._header() if auth else ""}<s:Body>{body}</s:Body></s:Envelope>')
        try:
            r = self.http.post(url, content=env.encode(),
                               headers={"Content-Type": "application/soap+xml; charset=utf-8"})
        except httpx.HTTPError as e:
            raise OnvifError(f"კავშირი ვერ დამყარდა: {e.__class__.__name__} {e}")
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            raise OnvifError(f"HTTP {r.status_code}: არ არის ONVIF პასუხი")
        fault = root.find(".//{*}Fault")
        if fault is not None:
            reason = " ".join(t.strip() for t in fault.itertext() if t.strip())
            if "NotAuthorized" in reason or r.status_code in (400, 401):
                raise OnvifError("ავტორიზაცია ვერ გაიარა — შეამოწმეთ სახელი/პაროლი. " + reason[:200])
            raise OnvifError(reason[:300])
        return root

    def sync_time(self):
        try:
            root = self.call(self.device_url, f'<GetSystemDateAndTime xmlns="{NS_DEV}"/>', auth=False)
            u = root.find(".//{*}UTCDateTime")
            if u is None:
                return
            cam = datetime.datetime(int(_txt(u, ".//{*}Year")), int(_txt(u, ".//{*}Month")), int(_txt(u, ".//{*}Day")),
                                    int(_txt(u, ".//{*}Hour")), int(_txt(u, ".//{*}Minute")), int(_txt(u, ".//{*}Second")),
                                    tzinfo=datetime.timezone.utc)
            self.offset = cam - datetime.datetime.now(datetime.timezone.utc)
        except (OnvifError, ValueError):
            pass

    def device_info(self):
        root = self.call(self.device_url, f'<GetDeviceInformation xmlns="{NS_DEV}"/>')
        return {k: _txt(root, f".//{{*}}{k}") for k in ("Manufacturer", "Model", "FirmwareVersion", "SerialNumber")}

    def _media(self):
        if self.media_url:
            return self.media_url
        try:
            root = self.call(self.device_url, f'<GetCapabilities xmlns="{NS_DEV}"><Category>Media</Category></GetCapabilities>')
            x = _txt(root, ".//{*}Media/{*}XAddr")
            self.media_url = _rehost(x, self.host) if x else self.device_url
        except OnvifError:
            self.media_url = self.device_url
        if f":{self.port}" not in self.media_url:
            s = urlsplit(self.media_url)
            self.media_url = urlunsplit(s._replace(netloc=f"{self.host}:{self.port}"))
        return self.media_url

    def profiles(self):
        root = self.call(self._media(), f'<GetProfiles xmlns="{NS_MEDIA}"/>')
        out = []
        for p in root.iter():
            if not p.tag.endswith("}Profiles"):
                continue
            tok = p.get("token")
            enc = p.find(".//{*}VideoEncoderConfiguration")
            item = {"token": tok, "name": _txt(p, "{*}Name"), "encoding": "", "width": 0, "height": 0, "fps": ""}
            if enc is not None:
                item["encoding"] = _txt(enc, "{*}Encoding")
                w, h = _txt(enc, ".//{*}Resolution/{*}Width"), _txt(enc, ".//{*}Resolution/{*}Height")
                item["width"], item["height"] = int(w or 0), int(h or 0)
                item["fps"] = _txt(enc, ".//{*}FrameRateLimit")
            out.append(item)
        return out

    def stream_uri(self, token):
        body = (f'<GetStreamUri xmlns="{NS_MEDIA}"><StreamSetup><Stream xmlns="{NS_SCHEMA}">RTP-Unicast</Stream>'
                f'<Transport xmlns="{NS_SCHEMA}"><Protocol>RTSP</Protocol></Transport></StreamSetup>'
                f'<ProfileToken>{escape(token)}</ProfileToken></GetStreamUri>')
        root = self.call(self._media(), body)
        return _rehost(_txt(root, ".//{*}Uri"), self.host)

    def probe_all(self):
        self.sync_time()
        info = self.device_info()
        profiles = self.profiles()
        for p in profiles:
            try:
                p["uri"] = self.stream_uri(p["token"])
            except OnvifError as e:
                p["uri"], p["error"] = "", str(e)
        profiles.sort(key=lambda p: -(p["width"] * p["height"]))
        return {"info": info, "profiles": profiles, "media_url": self.media_url}


PROBE = ('<?xml version="1.0" encoding="UTF-8"?><e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
         'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
         'xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><e:Header><w:MessageID>uuid:{id}</w:MessageID>'
         '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
         '<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>'
         '<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>')


def discover(subnets=(), wait=3.0):
    """WS-Discovery: multicast on the local segment plus unicast probes to every host of given subnets
    (works across routers, e.g. a camera VLAN)."""
    targets = [("239.255.255.250", 3702)]
    for sn in subnets:
        net = ipaddress.ip_network(sn.strip(), strict=False)
        if net.num_addresses > 1024:
            raise ValueError(f"{sn}: ძალიან დიდი ქსელია (მაქს. /22)")
        targets += [(str(h), 3702) for h in net.hosts()]
    msg = PROBE.format(id=uuid.uuid4()).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.bind(("", 0))
    found = {}
    for t in targets:
        try:
            s.sendto(msg, t)
        except OSError:
            pass
    end = time.time() + wait
    while time.time() < end:
        r, _, _ = select.select([s], [], [], max(0, end - time.time()))
        if not r:
            break
        data, addr = s.recvfrom(65535)
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        xaddrs = _txt(root, ".//{*}XAddrs").split()
        scopes = _txt(root, ".//{*}Scopes").split()
        name = hw = ""
        for sc in scopes:
            if "/name/" in sc:
                name = sc.rsplit("/", 1)[-1].replace("%20", " ")
            if "/hardware/" in sc:
                hw = sc.rsplit("/", 1)[-1].replace("%20", " ")
        port = 80
        for x in xaddrs:
            u = urlsplit(x)
            if u.hostname == addr[0] or len(xaddrs) == 1:
                port = u.port or 80
        found[addr[0]] = {"ip": addr[0], "port": port, "name": name, "hardware": hw, "xaddrs": xaddrs}
    s.close()
    return sorted(found.values(), key=lambda d: ipaddress.ip_address(d["ip"]))
