#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

OVRPORT_APP_VERSION = "1.2.3"
OVRPORT_RUNTIME_VERSION = "3.4.3-23204ea"
OVRPORT_ZIP_URL = "https://github.com/ovrport/app/releases/download/1.2.3/cli-jar.zip"
OVRPORT_ZIP_SHA256 = "3239408ff1e97ad016916825c216dc3016c0ffa7db7c317221ed3f1244441594"

QUEST2FRAME_COMMIT = "607e17067b04b7c521e23e5fc0a626edb3db5940"
FRAME_BRIDGE_URL = (
    "https://raw.githubusercontent.com/MichaelScottsman/Quest2Frame/"
    + QUEST2FRAME_COMMIT
    + "/native/frame_bridge.c"
)
FRAME_BRIDGE_GIT_BLOB_SHA1 = "705078c497e9868c64fba5c67671205bb3494c1f"

OPENXR_REVISION = "f2448a8797c85814aa892efc1ab8707900fbcc78"
OPENXR_HEADERS = {
    "openxr.h": "17e8e334a7ad0c6933b31143330d4771010e393df5d5d9d08566eb15bb171a15",
    "openxr_platform_defines.h": "a458ba5777415f2de518fdcdcc87fcf8651ba0468c9f69a101378672aad80946",
}


def emit(value: dict) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def fail(message: str, code: int = 1) -> None:
    emit({"ok": False, "error": message})
    raise SystemExit(code)


def log(message: str) -> None:
    print(f"[Frame Convert] {message}", file=sys.stderr, flush=True)


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha1(data: bytes) -> str:
    prefix = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(prefix + data).hexdigest()


def download(url: str, destination: pathlib.Path, expected_sha256: str | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    request = urllib.request.Request(url, headers={"User-Agent": "FrameCyberDeck/0.1"})
    log(f"Downloading {url}")
    with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as out:
        shutil.copyfileobj(response, out)

    if expected_sha256:
        actual = sha256(temp)
        if actual != expected_sha256:
            temp.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA-256 mismatch for {destination.name}: expected {expected_sha256}, got {actual}"
            )
    temp.replace(destination)


def run(args: list[str | pathlib.Path], *, cwd: pathlib.Path | None = None) -> str:
    printable = [str(value) for value in args]
    log("$ " + " ".join(printable))
    result = subprocess.run(
        printable,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout.strip():
        for line in result.stdout.splitlines():
            log(line)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({pathlib.Path(printable[0]).name}, exit {result.returncode}): "
            + result.stdout[-3000:]
        )
    return result.stdout


def tools_root() -> pathlib.Path:
    override = os.environ.get("FRAME_CYBERDECK_TOOLS")
    if override:
        return pathlib.Path(override).expanduser().resolve()
    return pathlib.Path.home() / ".local" / "share" / "frame-cyberdeck" / "tools"


def version_key(path: pathlib.Path) -> tuple[int, ...]:
    values = re.findall(r"\d+", path.name)
    return tuple(int(x) for x in values) or (0,)


def sdk_candidates() -> list[pathlib.Path]:
    values: list[pathlib.Path] = []
    for name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        value = os.environ.get(name)
        if value:
            values.append(pathlib.Path(value).expanduser())
    values += [
        pathlib.Path.home() / "Android" / "Sdk",
        pathlib.Path.home() / ".local" / "share" / "android-sdk",
        pathlib.Path("/opt/android-sdk"),
        pathlib.Path("/usr/lib/android-sdk"),
    ]
    unique: list[pathlib.Path] = []
    seen: set[str] = set()
    for value in values:
        resolved = value.expanduser().resolve()
        key = str(resolved)
        if key not in seen:
            unique.append(resolved)
            seen.add(key)
    return unique


def find_sdk() -> pathlib.Path:
    for sdk in sdk_candidates():
        if sdk.is_dir() and (sdk / "build-tools").is_dir():
            return sdk
    raise RuntimeError(
        "Android SDK not found. Set ANDROID_SDK_ROOT or install Android SDK build-tools."
    )


def find_build_tools(sdk: pathlib.Path) -> pathlib.Path:
    candidates = sorted(
        [p for p in (sdk / "build-tools").iterdir() if p.is_dir()],
        key=version_key,
        reverse=True,
    )
    for build in candidates:
        if all((build / name).exists() for name in ("aapt", "zipalign", "apksigner")):
            return build
    raise RuntimeError(
        f"No Android build-tools directory under {sdk / 'build-tools'} contains aapt, zipalign and apksigner."
    )


def ndk_candidates(sdk: pathlib.Path) -> list[pathlib.Path]:
    values: list[pathlib.Path] = []
    for name in ("ANDROID_NDK_ROOT", "ANDROID_NDK_HOME"):
        value = os.environ.get(name)
        if value:
            values.append(pathlib.Path(value).expanduser())

    # Android Studio / sdkmanager side-by-side layout.
    ndk_root = sdk / "ndk"
    if ndk_root.is_dir():
        values += sorted(
            [p for p in ndk_root.iterdir() if p.is_dir()],
            key=version_key,
            reverse=True,
        )

    # Common Arch/CachyOS AUR package layouts.
    values += [
        pathlib.Path("/opt/android-ndk"),
        pathlib.Path("/usr/lib/android-ndk"),
        sdk / "ndk-bundle",
    ]

    unique: list[pathlib.Path] = []
    seen: set[str] = set()
    for value in values:
        resolved = value.expanduser().resolve()
        key = str(resolved)
        if key not in seen and resolved.is_dir():
            unique.append(resolved)
            seen.add(key)
    return unique


def find_ndk_toolchain(sdk: pathlib.Path) -> pathlib.Path:
    for ndk in ndk_candidates(sdk):
        prebuilt = ndk / "toolchains" / "llvm" / "prebuilt"
        if not prebuilt.is_dir():
            continue
        for host in ("linux-x86_64", "linux-aarch64"):
            toolchain = prebuilt / host
            if (toolchain / "bin" / "clang").is_file():
                return toolchain
        for toolchain in prebuilt.iterdir():
            if (toolchain / "bin" / "clang").is_file():
                return toolchain
    raise RuntimeError(
        "Android NDK not found. Install an NDK side-by-side package or set ANDROID_NDK_ROOT."
    )


def find_java() -> pathlib.Path:
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = pathlib.Path(java_home) / "bin" / "java"
        if candidate.is_file():
            return candidate
    value = shutil.which("java")
    if value:
        return pathlib.Path(value)
    raise RuntimeError("Java is required. Install a JDK (Quest2Frame recommends Java 21).")


def ensure_ovrport(root: pathlib.Path) -> pathlib.Path:
    folder = root / "ovrport-cli"
    jar = folder / "overportcli-1.2.3-all.jar"
    if jar.is_file():
        return jar

    archive = root / "downloads" / "ovrport-cli-1.2.3.zip"
    if not archive.is_file() or sha256(archive) != OVRPORT_ZIP_SHA256:
        download(OVRPORT_ZIP_URL, archive, OVRPORT_ZIP_SHA256)

    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(folder)

    matches = list(folder.rglob("overportcli-1.2.3-all.jar"))
    if not matches:
        raise RuntimeError("Official OVR Port CLI archive did not contain overportcli-1.2.3-all.jar")
    if matches[0] != jar:
        shutil.copy2(matches[0], jar)
    return jar


def ensure_adapter(root: pathlib.Path, sdk: pathlib.Path) -> pathlib.Path:
    adapter_dir = root / "frame-adapter"
    output = adapter_dir / "libopenxr_loader_generic.so"
    if output.is_file():
        return output

    source = adapter_dir / "frame_bridge.c"
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        request = urllib.request.Request(
            FRAME_BRIDGE_URL, headers={"User-Agent": "FrameCyberDeck/0.1"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
        actual_blob = git_blob_sha1(data)
        if actual_blob != FRAME_BRIDGE_GIT_BLOB_SHA1:
            raise RuntimeError(
                f"Quest2Frame adapter source mismatch: expected git blob {FRAME_BRIDGE_GIT_BLOB_SHA1}, got {actual_blob}"
            )
        source.write_bytes(data)

    include = adapter_dir / "include" / "openxr"
    include.mkdir(parents=True, exist_ok=True)
    for name, expected in OPENXR_HEADERS.items():
        path = include / name
        if not path.is_file() or sha256(path) != expected:
            url = (
                "https://raw.githubusercontent.com/KhronosGroup/OpenXR-SDK/"
                + OPENXR_REVISION
                + "/include/openxr/"
                + name
            )
            download(url, path, expected)

    toolchain = find_ndk_toolchain(sdk)
    clang = toolchain / "bin" / "clang"
    sysroot = toolchain / "sysroot"

    run(
        [
            clang,
            "--target=aarch64-linux-android29",
            f"--sysroot={sysroot}",
            "-shared",
            "-fPIC",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wl,-Bsymbolic",
            "-Wl,-soname,libopenxr_loader_generic.so",
            "-I",
            include.parent,
            source,
            "-ldl",
            "-llog",
            "-o",
            output,
        ]
    )
    if not output.is_file():
        raise RuntimeError("Frame adapter compilation completed without producing the shared library")
    return output


def resolve_apk(source: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    source = source.expanduser().resolve()
    if source.is_file():
        if source.suffix.lower() != ".apk":
            raise RuntimeError(f"Expected an APK or extracted folder, got {source}")
        return source, source.parent
    if not source.is_dir():
        raise RuntimeError(f"Input does not exist: {source}")

    apks = [p for p in source.rglob("*.apk") if len(p.relative_to(source).parts) <= 3]
    if not apks:
        raise RuntimeError("No APK found in input folder")
    root_apks = [p for p in apks if p.parent == source]
    named = [p for p in apks if p.name.lower() in ("game.apk", "base.apk")]
    preferred = (named or root_apks or apks)
    if len(apks) > 1 and not named and len(root_apks) != 1:
        raise RuntimeError(
            "Multiple APKs found. Split/multi-APK packages are not supported by the Frame converter yet."
        )
    return preferred[0], source


def settings_bytes(scale: float, controllers: bool, foveation: bool) -> bytes:
    if scale < 50 or scale > 200:
        raise RuntimeError("Resolution scale must be between 50 and 200 percent")
    text = (
        f"scale={scale / 100.0:.3f}\n"
        f"controller_fix={1 if controllers else 0}\n"
        f"foveation_fix={1 if foveation else 0}\n"
    )
    return text.encode("utf-8")


def is_frame_converted(apk: pathlib.Path) -> bool:
    try:
        with zipfile.ZipFile(apk) as zf:
            names = set(zf.namelist())
        prefix = "lib/arm64-v8a/"
        return (
            prefix + "libframe_settings.so" in names
            and prefix + "libopenxr_loader_original.so" in names
            and prefix + "libopenxr_loader_generic.so" in names
        )
    except zipfile.BadZipFile:
        return False


def copy_obbs(source_root: pathlib.Path, destination: pathlib.Path) -> int:
    obbs = [p for p in source_root.rglob("*.obb") if p.is_file()]
    if not obbs:
        return 0
    obb_dir = destination / "obb"
    obb_dir.mkdir(parents=True, exist_ok=True)
    names: set[str] = set()
    for obb in obbs:
        if obb.name in names:
            raise RuntimeError(f"Duplicate OBB filename found: {obb.name}")
        names.add(obb.name)
        shutil.copy2(obb, obb_dir / obb.name)
    return len(obbs)


def convert(
    input_path: pathlib.Path,
    output_dir: pathlib.Path,
    *,
    scale: float,
    controllers: bool,
    foveation: bool,
) -> dict:
    source_apk, source_root = resolve_apk(input_path)
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    game_apk = output_dir / "game.apk"

    if is_frame_converted(source_apk):
        log("APK already contains Frame conversion markers; copying without repatching.")
        shutil.copy2(source_apk, game_apk)
        obb_count = copy_obbs(source_root, output_dir)
        return {
            "ok": True,
            "converted": False,
            "alreadyConverted": True,
            "gameApk": str(game_apk),
            "directory": str(output_dir),
            "obbCount": obb_count,
            "sha256": sha256(game_apk),
        }

    root = tools_root()
    sdk = find_sdk()
    build = find_build_tools(sdk)
    java = find_java()
    jar = ensure_ovrport(root)
    adapter = ensure_adapter(root, sdk)

    badging = run([build / "aapt", "dump", "badging", source_apk])
    package_match = re.search(r"package: name='([^']+)'", badging)
    if not package_match:
        raise RuntimeError("Could not determine Android package name with aapt")
    package = package_match.group(1)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", package):
        raise RuntimeError(f"Invalid Android package name reported by aapt: {package}")

    version_code_match = re.search(r"versionCode='(\d+)'", badging)
    version_name_match = re.search(r"versionName='([^']*)'", badging)
    label_match = re.search(r"application-label:'([^']*)'", badging)
    version_code = int(version_code_match.group(1)) if version_code_match else 0
    version_name = version_name_match.group(1) if version_name_match else ""
    application_label = label_match.group(1) if label_match else package

    with zipfile.ZipFile(source_apk) as source_zip:
        names = source_zip.namelist()
        if not any(name.startswith("lib/arm64-v8a/") for name in names):
            raise RuntimeError("APK does not contain ARM64 libraries")

    workspace = root / "ovrport-workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    work = pathlib.Path(tempfile.mkdtemp(prefix="frame-cyberdeck-convert-"))
    try:
        patch_dir = work / "ovrport"
        patch_dir.mkdir(parents=True)
        log(f"Converting {source_apk.name} with OVR Port runtime {OVRPORT_RUNTIME_VERSION}")
        run(
            [
                java,
                "-jar",
                jar,
                "patch",
                f"--input={source_apk}",
                f"--output={patch_dir}",
                "--output-name=ovrport.apk",
                f"--workspace={workspace}",
                f"--version={OVRPORT_RUNTIME_VERSION}",
            ]
        )

        patched = patch_dir / "ovrport.apk"
        if not patched.is_file():
            matches = list(patch_dir.rglob("ovrport.apk"))
            if not matches:
                raise RuntimeError("OVR Port did not produce ovrport.apk")
            patched = matches[0]

        unsigned = work / "unsigned.apk"
        prefix = "lib/arm64-v8a/"
        loader = prefix + "libopenxr_loader_generic.so"

        with zipfile.ZipFile(patched, "r") as src, zipfile.ZipFile(
            unsigned, "w", zipfile.ZIP_DEFLATED
        ) as dst:
            if loader not in src.namelist():
                raise RuntimeError(
                    "OVR Port output has no libopenxr_loader_generic.so; this APK/profile is incompatible"
                )
            for item in src.infolist():
                if item.filename == loader or item.filename.startswith("META-INF/"):
                    continue
                dst.writestr(item, src.read(item.filename))
            dst.writestr(prefix + "libopenxr_loader_original.so", src.read(loader))
            dst.write(adapter, loader)
            dst.writestr(
                prefix + "libframe_settings.so",
                settings_bytes(scale, controllers, foveation),
            )

        aligned = work / "aligned.apk"
        run([build / "zipalign", "-f", "-P", "16", "4", unsigned, aligned])

        key = workspace / "signatures" / f"{package}.keystore"
        if not key.is_file():
            raise RuntimeError(
                f"OVR Port did not create the expected signing key: {key}"
            )

        run(
            [
                build / "apksigner",
                "-J--enable-native-access=ALL-UNNAMED",
                "sign",
                "--ks",
                key,
                "--ks-pass",
                "pass:password",
                "--out",
                game_apk,
                aligned,
            ]
        )
        run(
            [
                build / "apksigner",
                "-J--enable-native-access=ALL-UNNAMED",
                "verify",
                "--verbose",
                game_apk,
            ]
        )

        obb_count = copy_obbs(source_root, output_dir)
        result = {
            "ok": True,
            "converted": True,
            "alreadyConverted": False,
            "package": package,
            "versionCode": version_code,
            "versionName": version_name,
            "applicationLabel": application_label,
            "gameApk": str(game_apk),
            "directory": str(output_dir),
            "obbCount": obb_count,
            "sha256": sha256(game_apk),
            "ovrportAppVersion": OVRPORT_APP_VERSION,
            "ovrportRuntimeVersion": OVRPORT_RUNTIME_VERSION,
        }
        (output_dir / "frame-conversion.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def doctor() -> dict:
    result: dict[str, object] = {"ok": True}
    try:
        sdk = find_sdk()
        result["sdk"] = str(sdk)
        result["buildTools"] = str(find_build_tools(sdk))
        result["ndkToolchain"] = str(find_ndk_toolchain(sdk))
    except Exception as exc:
        result["ok"] = False
        result["androidError"] = str(exc)
    try:
        result["java"] = str(find_java())
    except Exception as exc:
        result["ok"] = False
        result["javaError"] = str(exc)
    result["toolsRoot"] = str(tools_root())
    result["ovrportCached"] = (
        tools_root() / "ovrport-cli" / "overportcli-1.2.3-all.jar"
    ).is_file()
    result["adapterCached"] = (
        tools_root() / "frame-adapter" / "libopenxr_loader_generic.so"
    ).is_file()
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Frame CyberDeck Quest APK converter")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor")

    c = sub.add_parser("convert")
    c.add_argument("--input", required=True)
    c.add_argument("--output", required=True)
    c.add_argument("--scale", type=float, default=100.0)
    c.add_argument("--no-controller-fix", action="store_true")
    c.add_argument("--no-foveation-fix", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "doctor":
            emit(doctor())
            return
        if args.command == "convert":
            emit(
                convert(
                    pathlib.Path(args.input),
                    pathlib.Path(args.output),
                    scale=args.scale,
                    controllers=not args.no_controller_fix,
                    foveation=not args.no_foveation_fix,
                )
            )
            return
        fail(f"Unknown command: {args.command}")
    except SystemExit:
        raise
    except Exception as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
