"""Minimal TOTP (RFC 6238) — no third-party dependency. Used for optional two-factor login."""
import base64, hashlib, hmac, os, struct, time, urllib.parse

DIGITS = 6
PERIOD = 30


def new_secret():
    # 20 random bytes → base32, the format authenticator apps expect
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _code(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    num = struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF
    return str(num % (10 ** DIGITS)).zfill(DIGITS)


def verify(secret, code, window=1):
    """True if `code` matches the current 30s step (±`window` steps for clock drift)."""
    code = (code or "").strip().replace(" ", "")
    if not secret or not code.isdigit():
        return False
    now = int(time.time()) // PERIOD
    for d in range(-window, window + 1):
        if hmac.compare_digest(_code(secret, now + d), code):
            return True
    return False


def provisioning_uri(secret, username, issuer="NVR"):
    label = urllib.parse.quote(f"{issuer}:{username}")
    params = urllib.parse.urlencode({"secret": secret, "issuer": issuer, "digits": DIGITS, "period": PERIOD})
    return f"otpauth://totp/{label}?{params}"
