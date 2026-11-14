"""FramePort running on the Steam Frame itself (Desktop Mode, the Linux ARM64 bundle): it manages the same device over
SSH to 127.0.0.1, so everything else (agent, installs, launch tests) works unchanged. Developer Mode has to be on for
sshd; FramePort authorizes its own key for the local user, no pairing needed."""
from __future__ import annotations

import functools
import getpass
import os
import platform
from pathlib import Path

LOCAL_NAME = "This Frame (experimental)"  # FramePort running on the Frame itself


@functools.lru_cache(maxsize=1)
def on_frame() -> bool:
    """SteamOS on ARM64 (the Steam Frame)."""
    if platform.machine().lower() not in ("aarch64", "arm64"):
        return False
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(line.strip().lower() in ("id=steamos", 'id="steamos"') for line in text.splitlines())


def authorize_self(public_key: str) -> bool:
    """Add FramePort's public key to this user's ~/.ssh/authorized_keys (once). True if it was added."""
    ssh = Path.home() / ".ssh"
    keys = ssh / "authorized_keys"
    ssh.mkdir(mode=0o700, exist_ok=True)
    have = keys.read_text(encoding="utf-8", errors="replace") if keys.exists() else ""
    if public_key.strip() in have:
        return False
    with open(keys, "a", encoding="utf-8") as f:
        f.write(("" if not have or have.endswith("\n") else "\n") + public_key.strip() + " frameport-local\n")
    os.chmod(keys, 0o600)
    return True


def local_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "steamos"
