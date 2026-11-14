"""Type on the Frame from this computer's keyboard: the agent's `_keyboard` session creates a virtual keyboard on the
Frame (Linux uinput) and this side streams key presses to it as JSON lines over one SSH channel. Keys are sent as
physical keys (Linux key codes), so modifiers, shortcuts and key repeat behave like a keyboard plugged into the Frame;
pasted text is typed with the Frame's US layout."""
from __future__ import annotations

import json
import posixpath
import threading

from ..core import applog

# Flet/Flutter key names, normalised (lowercase, no spaces) -> Linux input key codes (input-event-codes.h)
_NAMED = {
    "escape": 1, "esc": 1, "backspace": 14, "tab": 15, "enter": 28, "return": 28, "space": 57, " ": 57,
    "controlleft": 29, "control": 29, "ctrl": 29, "controlright": 97, "shiftleft": 42, "shift": 42,
    "shiftright": 54, "altleft": 56, "alt": 56, "altright": 100, "altgraph": 100, "metaleft": 125, "meta": 125,
    "metaright": 126, "capslock": 58, "numlock": 69, "scrolllock": 70, "printscreen": 99, "pause": 119,
    "contextmenu": 127, "home": 102, "arrowup": 103, "up": 103, "pageup": 104, "arrowleft": 105, "left": 105,
    "arrowright": 106, "right": 106, "end": 107, "arrowdown": 108, "down": 108, "pagedown": 109, "insert": 110,
    "delete": 111, "numpadenter": 96, "numpadadd": 78, "numpadsubtract": 74, "numpadmultiply": 55,
    "numpaddivide": 98, "numpaddecimal": 83, "numpadequal": 117,
    "audiovolumemute": 113, "audiovolumedown": 114, "audiovolumeup": 115,
    "mediaplaypause": 164, "mediatracknext": 163, "mediatrackprevious": 165, "mediastop": 166,
}
_ROWS = (("1234567890-=", "!@#$%^&*()_+", 2), ("qwertyuiop[]", "QWERTYUIOP{}", 16),
         ("asdfghjkl;'`", 'ASDFGHJKL:"~', 30), ("\\zxcvbnm,./", "|ZXCVBNM<>?", 43))
_CHARS: dict[str, int] = {}
for _plain, _shifted, _first in _ROWS:
    for _i, (_a, _b) in enumerate(zip(_plain, _shifted, strict=True)):
        _CHARS[_a] = _CHARS[_b] = _first + _i
_FKEYS = {f"f{n}": c for n, c in zip(range(1, 13), (59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 87, 88), strict=True)}
_NUMPAD = {f"numpad{d}": c for d, c in zip("1234567890", (79, 80, 81, 75, 76, 77, 71, 72, 73, 82), strict=True)}
_warned: set[str] = set()


def linux_key(name: str) -> int | None:
    """Linux key code for a Flet key name ("A", "Enter", "Arrow Left", "Shift Left", "F5", "Key A", "Digit 1", "!")."""
    if not name:
        return None
    if not name.strip():
        return 57  # " " = space (normalising below would leave nothing)
    if len(name) == 1 and name in _CHARS:
        return _CHARS[name.lower()] if name.isalpha() else _CHARS[name]
    norm = name.lower().replace(" ", "").replace("_", "")
    if norm.startswith("key") and len(norm) == 4:  # physical names: "Key A"
        norm = norm[3:]
    elif norm.startswith("digit") and len(norm) == 6:  # "Digit 1"
        norm = norm[5:]
    if len(norm) == 1 and norm in _CHARS:
        return _CHARS[norm]
    code = _NAMED.get(norm) or _FKEYS.get(norm) or _NUMPAD.get(norm)
    if code is None and name not in _warned:
        _warned.add(name)
        applog.log.info("Type on Frame: no key code for %r", name)
    return code


class KeyboardRefused(RuntimeError):
    pass


class KeyboardSession:
    """One virtual keyboard on the Frame for as long as this session is open (the agent removes it, releasing any
    held key, when the SSH channel closes)."""

    def __init__(self, frame, timeout: float = 15):
        self.frame = frame
        self._lock = threading.Lock()
        self.closed = False
        frame.ensure_agent()
        remote = posixpath.join(frame.home, ".local/share/frameport/agent/frameport_agent.py")
        self._stdin, self._stdout, _err = frame.client.exec_command(f"python3 {remote} _keyboard", timeout=timeout)
        ready = json.loads(self._stdout.readline() or "{}")
        if not ready.get("ready"):
            self.close()
            raise KeyboardRefused(ready.get("error") or "the Frame didn't start the virtual keyboard")
        self._stdout.channel.settimeout(None)

    def _send(self, msg: dict) -> None:
        with self._lock:
            if self.closed:
                return
            self._stdin.write(json.dumps(msg) + "\n")
            self._stdin.flush()

    def key(self, name: str, action: str = "down") -> bool:
        """action: down | up | repeat. False when the key has no Linux key code."""
        code = linux_key(name)
        if code is None:
            return False
        self._send({"k": code, "v": {"down": 1, "up": 0, "repeat": 2}[action]})
        return True

    def text(self, text: str) -> str:
        """Type text with the Frame's US layout; returns the characters it couldn't type."""
        self._send({"text": text})
        reply = json.loads(self._stdout.readline() or "{}")
        return reply.get("skipped", "")

    def close(self) -> None:
        with self._lock:
            self.closed = True
            try:
                self._stdin.channel.shutdown_write()  # EOF: the agent releases held keys and removes the device
                self._stdin.close()
            except Exception:  # noqa: BLE001 - the connection may be gone already
                pass
