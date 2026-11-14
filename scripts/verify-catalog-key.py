#!/usr/bin/env python3
"""Mint a short-lived catalog key from the build secret and probe the mirror.

Usage:
    python3 scripts/verify-catalog-key.py

Reads VRSRC_CLIENT_ID and VRSRC_BUILD_SECRET (or FRAME_CYBERDECK_CLIENT_ID /
FRAME_CYBERDECK_BUILD_SECRET) from .env or the environment. Prints only
non-secret metadata: status, baseUri, refreshAfter, expiresAt, key length and
the mirror probe status. The key and the build secret are never printed.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENDPOINT = os.environ.get("FRAME_CYBERDECK_KEY_URL", "https://vrsrc.fyi/api/mirror/app-key")


def load_env() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def value(*names: str) -> str:
    for name in names:
        found = os.environ.get(name, "").strip()
        if found:
            return found
    return ""


def main() -> int:
    load_env()
    client_id = value("FRAME_CYBERDECK_CLIENT_ID", "VRSRC_CLIENT_ID")
    build_secret = value("FRAME_CYBERDECK_BUILD_SECRET", "VRSRC_BUILD_SECRET")
    if not client_id or not build_secret:
        print("Missing VRSRC_CLIENT_ID / VRSRC_BUILD_SECRET in .env or the environment.")
        return 2

    package_version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    body = json.dumps(
        {
            "client_id": client_id,
            "build_secret": build_secret,
            "install_id": str(uuid.uuid4()),
            "app_version": package_version,
        }
    ).encode()
    # Python's urllib TLS fingerprint is challenged by the service's CDN
    # (Cloudflare error 1010); curl over HTTP/2 is not.
    result = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--http2",
            "--user-agent",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            "--header",
            "Content-Type: application/json",
            "--data-binary",
            "@-",
            "--write-out",
            "\n%{http_code}",
            ENDPOINT,
        ],
        input=body.decode(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout.rsplit("\n", 1)
    status = int(output[-1]) if output[-1].isdigit() else 0
    raw = output[0] if len(output) > 1 else result.stdout
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = {"raw": raw[:300]}

    print(f"status: {status}")
    if isinstance(payload, dict):
        print("baseUri:", payload.get("baseUri"))
        print("refreshAfter:", payload.get("refreshAfter"))
        print("expiresAt:", payload.get("expiresAt"))
        print("reason:", payload.get("reason"))
        print("error:", payload.get("error"))
        key = payload.get("apiKey") or ""
        print("apiKey length:", len(key))
    else:
        key = ""
    if status != 200 or not key:
        return 1

    base = (payload.get("baseUri") or "").rstrip("/")
    if not base.startswith("https://"):
        print("mirror skipped: the grant did not return an HTTPS baseUri")
        return 1
    config = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
    try:
        os.chmod(config.name, 0o600)
        config.write(f'header = "X-API-Key: {key}"\n')
        config.close()
        probe = subprocess.run(
            [
                "curl",
                "--http2",
                "--silent",
                "--output",
                os.devnull,
                "--write-out",
                "%{http_code}",
                "--user-agent",
                "rclone/v1.72.1",
                "--config",
                config.name,
                "--range",
                "0-0",
                f"{base}/meta.7z",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        os.unlink(config.name)
    print("mirror status:", probe.stdout.strip())
    return 0 if probe.stdout.strip() in ("200", "206") else 1


if __name__ == "__main__":
    raise SystemExit(main())
