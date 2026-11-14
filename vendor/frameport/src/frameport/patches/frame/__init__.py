"""Steam Frame fixes applied to the OVRPort output (stage "apk"). One module per fix; each registers itself."""
from __future__ import annotations

from ...core.paths import artifacts_dir


def artifact(abi: str, name: str) -> bytes:
    path = artifacts_dir() / abi / name
    if not path.exists():
        raise FileNotFoundError(f"missing prebuilt artifact {abi}/{name} (see native/README.md)")
    return path.read_bytes()
