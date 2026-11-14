"""Valve's devkit pairing: with Developer Mode on, the Frame's steamos-devkit-service (TCP 32000) accepts an SSH public
key at POST /register while Steam is in pairing mode (Settings → Developer → Pair new host; otherwise it refuses at
once), asks in Steam to approve it, and Valve's root hook installs the key for the steamos user and enables sshd.
Every connection goes from the PC to the Frame, so no firewall on the PC is involved: the fallback when the Frame
can't reach FramePort's setup server. The service only accepts ssh-rsa keys
(connection.devkit_key)."""
from __future__ import annotations

import re
import urllib.error
import urllib.request

from .connection import devkit_public_key

PORT = 32000
APPROVE_TIMEOUT = 30  # Valve's approve hook waits this long for the answer in the headset


class PairingRefused(RuntimeError):  # not a ConnectionError: the Frame is reachable, explain() keeps the message
    pass


def available(host: str, timeout: float = 5) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{PORT}/login-name", timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def register(host: str) -> None:
    """Send FramePort's RSA key; returns once it was approved in the headset and installed, raises PairingRefused."""
    req = urllib.request.Request(f"http://{host}:{PORT}/register", data=devkit_public_key().encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=APPROVE_TIMEOUT + 30) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")  # Valve's service mixes the hook's log lines into it
        m = re.search(r'"error":\s*"([^"]*)"', body)
        msg = (m.group(1) if m else (body.strip() or str(exc))).replace("\\n", " ").strip()
        if "pairing mode" in msg.lower():  # Steam takes requests only while "Pair new host" is open
            msg = "on the Frame open Settings → Developer → Pair new host first, then click Connect again"
        elif "timeout" in msg.lower():
            msg = f"it wasn't approved in the headset within {APPROVE_TIMEOUT} seconds"
        elif "steam is not running" in msg.lower():
            msg = "Steam isn't running on the Frame"
        raise PairingRefused(f"The Frame didn't pair: {msg}") from None
    except OSError as exc:
        raise PairingRefused(f"Couldn't reach the Frame's pairing service (Developer Mode on?): {exc}") from None
    if "Registered" not in body:
        raise PairingRefused(f"The Frame didn't pair: {body.strip()}")
