"""Signing (apksigner via the managed JRE) and alignment checks.

OVRPort creates one keystore per package (password "password", alias "key"). The same key must sign every later
build of that package, or Android refuses the update and saves are lost on reinstall.
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path

from ..tools import overport as overport_tool
from ..tools import toolchain

KS_PASS = "password"
KS_ALIAS = "key"
PAGE_ALIGN = 16384


def _apksigner() -> str:
    jar = toolchain.apksigner_jar()
    if not jar:
        raise RuntimeError("apksigner is not installed; run `frameport tools install`")
    return str(jar)


def ensure_keystore(package: str) -> Path:
    """The package's signing key. OVRPort creates it on its first patch; apps built without OVRPort (ordinary
    Android apps) get one made here with the same layout (password "password", alias "key")."""
    ks = overport_tool.keystore(package)
    if ks.exists():
        return ks
    java = toolchain.java_path()
    keytool = java.with_name("keytool.exe" if java and java.name.endswith(".exe") else "keytool") if java else None
    if not keytool or not keytool.exists():
        raise RuntimeError(f"no signing key for {package} and no keytool to create one (run `frameport tools install`)")
    import subprocess

    p = subprocess.run([str(keytool), "-genkeypair", "-keystore", str(ks), "-storetype", "JKS", "-storepass", KS_PASS,
                        "-keypass", KS_PASS, "-alias", KS_ALIAS, "-keyalg", "RSA", "-keysize", "2048",
                        "-validity", "10000", "-dname", f"CN={package}, O=FramePort"],
                       capture_output=True, text=True, timeout=120)
    if p.returncode or not ks.exists():
        raise RuntimeError("couldn't create a signing key: " + (p.stdout + p.stderr)[-500:])
    return ks


def sign(unsigned: Path, final: Path, package: str) -> None:
    """Align (4 bytes; .so to 16 KiB pages) and sign v1+v2+v3. apksigner performs the alignment itself."""
    ks = ensure_keystore(package)
    final.unlink(missing_ok=True)
    p = toolchain.run_java([
        "-jar", _apksigner(), "sign", "--ks", str(ks), "--ks-pass", f"pass:{KS_PASS}", "--ks-key-alias", KS_ALIAS,
        "--key-pass", f"pass:{KS_PASS}", "--alignment-preserved", "false", "--lib-page-alignment", str(PAGE_ALIGN),
        "--v1-signing-enabled", "true", "--v2-signing-enabled", "true", "--v3-signing-enabled", "true",
        "--out", str(final), str(unsigned),
    ])
    if p.returncode:
        raise RuntimeError("apksigner sign failed: " + (p.stdout + p.stderr)[-1500:])
    final.with_name(final.name + ".idsig").unlink(missing_ok=True)


def verify(apk: Path) -> tuple[bool, str]:
    p = toolchain.run_java(["-jar", _apksigner(), "verify", "--print-certs", str(apk)])
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def cert_digest(apk: Path) -> str | None:
    ok, text = verify(apk)
    for line in text.splitlines():
        if "certificate SHA-256 digest" in line:
            return line.rsplit(":", 1)[1].strip()
    return None


def alignment_problems(apk: Path) -> list[str]:
    """Stored entries must be 4-byte aligned; stored .so files page (16 KiB) aligned (what zipalign -c -P 16 4
    checks)."""
    bad = []
    with open(apk, "rb") as f, zipfile.ZipFile(apk) as z:
        for info in z.infolist():
            if info.compress_type != zipfile.ZIP_STORED or info.is_dir():
                continue
            f.seek(info.header_offset)
            header = f.read(30)
            name_len, extra_len = struct.unpack_from("<HH", header, 26)
            data_off = info.header_offset + 30 + name_len + extra_len
            need = PAGE_ALIGN if info.filename.endswith(".so") else 4
            if data_off % need:
                bad.append(f"{info.filename} at {data_off} (needs {need})")
    return bad
