"""App bundles: one Android app in several files (GitHub discussion #482). FramePort takes

  .xapk   APKPure (and FrameDrop): a zip with manifest.json, the APK (or base.apk + config.*.apk splits),
          Android/obb/<package>/*.obb and an icon
  .apks   bundletool's build-apks output (toc.pb, splits/base-master.apk + splits/base-arm64_v8a.apk …, maybe
          standalones/ or universal.apk) and SAI's backups (base.apk, split_*.apk, meta.sai_v*.json)
  .apkm   APKMirror: a zip with info.json, base.apk and split_config.*.apk (older downloads are encrypted for
          APKMirror's own installer: FramePort says so, it can't open them)
  .apkx   the same kind of zip under another name (FrameDrop accepts it like .xapk)
  a folder of split APKs: base.apk + split_config.arm64_v8a.apk … (e.g. pulled with `adb shell pm path`)

The user's file is never changed: a bundle is unpacked into <data>/bundles/<package>/<key>/ as `<package>.apk` and
`obb/<package>/` (the layout quest_dump already reads), keyed by the bundle's contents (moving or renaming the file
finds the same copy). Lepton installs exactly one APK per app and OVRPort converts one APK, so the splits are merged
into the base:
  * splits that only add native libraries or assets (ABI splits: the usual case for Unity/Unreal games, install-time
    asset packs) are merged here, in Python: their lib/ and assets/ entries are added to the base and the base
    manifest loses android:isSplitRequired/requiredSplitTypes (else Android refuses it alone) and Play's
    com.android.vending.splits.required flag (else Play Core's missing-split check stops the app);
  * splits with resources or code (language/density splits, feature modules) need their resource tables merged:
    REAndroid's APKEditor (Apache-2.0, downloaded into tools/ like OVRPort) does that (`merge`).
Only the arm64-v8a ABI split is taken (armeabi-v7a only when there is no 64-bit one: then the 32-bit bridge's rules
apply), x86 ones never. A merged APK is unsigned: every build signs it with the game's own key (prepare_as_is too).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..apk import axml
from ..core.events import Reporter

EXTS = (".xapk", ".apks", ".apkm", ".apkx")
FORMAT = 1  # the unpacked layout; a change unpacks every bundle again (a new key)
INFO = "bundle.json"
ABIS = {"arm64_v8a": "arm64-v8a", "armeabi_v7a": "armeabi-v7a", "armeabi": "armeabi", "x86": "x86",
        "x86_64": "x86_64", "mips": "mips", "mips64": "mips64"}
ABI_PREFERENCE = ("arm64-v8a", "armeabi-v7a", "armeabi")  # what the Frame can run (32-bit: the a32 bridge)
DENSITIES = {"ldpi", "mdpi", "tvdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi", "nodpi", "anydpi"}
TEXTURES = ("astc", "etc2", "etc1", "s3tc", "dxt1", "pvrtc", "atc", "latc", "3dc")  # Quest GPUs: ASTC, then ETC2
# what a split may hold to be merged without touching the resource table
SIMPLE_PREFIXES = ("lib/", "assets/", "META-INF/")
SIMPLE_NAMES = {"AndroidManifest.xml", "stamp-cert-sha256"}
SIGNATURE_FILE = re.compile(r"^META-INF/([^/]+\.(SF|RSA|DSA|EC)|MANIFEST\.MF)$", re.I)
SPLIT_ATTRS = {"isSplitRequired", "requiredSplitTypes", "splitTypes"}
SKIP_FOLDERS = ("instant/", "system/", "archive/")


class BundleError(ValueError):
    """A bundle FramePort can't use (the message says why, in plain words)."""


@dataclass
class Part:
    """One APK of a bundle."""

    name: str  # member name in the bundle (or the file name in a split folder)
    package: str | None
    version_code: int | None
    split: str | None  # None = a base (or complete) APK
    entries: list[str] = field(default_factory=list)
    size: int = 0

    @property
    def config(self) -> str | None:
        """'arm64_v8a' of config.arm64_v8a / feature.config.arm64_v8a; None for a base or a feature/asset split."""
        s = self.split or ""
        if s.startswith("config."):
            return s[7:]
        return s.split(".config.", 1)[1] if ".config." in s else None

    @property
    def module(self) -> str:
        """The module a split belongs to: 'base' for config.* splits, the feature's name for feature.config.*."""
        s = self.split or ""
        if not s or s.startswith("config."):
            return "base"
        return s.split(".config.", 1)[0]

    @property
    def kind(self) -> str:
        if self.split is None:
            return "base"
        c = self.config
        if c is None:
            return "feature"
        if c in ABIS:
            return "abi"
        if c in DENSITIES:
            return "density"
        if c in TEXTURES or c.startswith("tier_"):
            return "texture"
        return "language"

    @property
    def simple(self) -> bool:
        """Only native libraries/assets: merged without APKEditor."""
        return all(n.startswith(SIMPLE_PREFIXES) or n in SIMPLE_NAMES or n.endswith("/") for n in self.entries)


@dataclass
class Plan:
    """What a bundle holds and what FramePort makes of it."""

    source: Path
    package: str
    version_code: int | None
    base: Part
    splits: list[Part]
    skipped: list[tuple[str, str]]  # (part, why)
    obbs: list[tuple[str, str]]  # (member/file, path below Android/obb/<package>/)
    ignored: list[str]  # files of the bundle that go nowhere (Android/data/…)
    key: str
    title: str | None = None  # a name the bundle gives (xapk manifest.json "name", apkm info.json "app_name")
    used: str | None = None  # how prepare() merged it when that differs from `method` (a fallback)

    @property
    def needs_resource_merge(self) -> bool:
        return any(not p.simple for p in self.splits)

    @property
    def method(self) -> str:
        if not self.splits:
            return "base"
        return "apkeditor" if self.needs_resource_merge else "libs"

    def cache_dir(self) -> Path:
        from ..core.paths import bundles_dir

        return bundles_dir() / _safe(self.package) / self.key

    @property
    def apk_path(self) -> Path:
        return self.cache_dir() / f"{_safe(self.package)}.apk"

    @property
    def data_dir(self) -> Path | None:
        return self.cache_dir() / "obb" / _safe(self.package) if self.obbs else None


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "app"


# ------------------------------------------------------------------------------------------------ detection
def is_bundle_file(path: Path) -> bool:
    path = Path(path)
    return path.suffix.lower() in EXTS and path.is_file()


def _apk_files(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in folder.iterdir() if p.suffix.lower() == ".apk" and p.is_file())
    except OSError:
        return []


def _manifest_of(z: zipfile.ZipFile) -> bytes | None:
    try:
        return z.read("AndroidManifest.xml")
    except KeyError:
        return None


def _part(name: str, z: zipfile.ZipFile, size: int) -> Part | None:
    data = _manifest_of(z)
    if data is None:
        return None
    try:
        attrs = axml.manifest_attrs(data)
    except Exception:  # noqa: BLE001 - not a binary manifest
        return None
    vc = attrs.get("versionCode")
    return Part(name, attrs.get("package") if isinstance(attrs.get("package"), str) else None,
                vc if isinstance(vc, int) and not isinstance(vc, bool) else None,
                attrs.get("split") if isinstance(attrs.get("split"), str) and attrs.get("split") else None,
                [i.filename for i in z.infolist()], size)


def split_folder(folder: Path) -> list[Path] | None:
    """The APKs of a split set lying loose in a folder (base.apk + split_config.*.apk …): None when the folder's
    APKs aren't one (a single APK, an APK and its alternate build, two games)."""
    apks = _apk_files(Path(folder))
    if len(apks) < 2:
        return None
    parts = []
    for p in apks:
        try:
            with zipfile.ZipFile(p) as z:
                part = _part(p.name, z, p.stat().st_size)
        except (zipfile.BadZipFile, OSError):
            part = None
        if part:
            parts.append((p, part))
    bases = [part for _, part in parts if part.split is None]
    splits = [part for _, part in parts if part.split is not None]
    if len(bases) != 1 or not splits or any(s.package != bases[0].package for s in splits):
        return None
    return [p for p, _ in parts]


def is_bundle(path: Path) -> bool:
    """A bundle file, or a folder holding one split APK set."""
    path = Path(path)
    return is_bundle_file(path) or (path.is_dir() and split_folder(path) is not None)


# ------------------------------------------------------------------------------------------------ reading
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")


def _open(path: Path) -> zipfile.ZipFile:
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError as exc:
        raise BundleError(f"{path.name} can't be read: {exc}") from exc
    if head not in ZIP_MAGIC:
        # APKMirror's encrypted .apkm (until 2021): a libsodium secretstream file, byte 1 = the password hash
        # algorithm (1 or 2); github.com/souramoo/unapkm documents the header
        if path.suffix.lower() == ".apkm" or (len(head) > 1 and head[1] in (1, 2)):
            raise BundleError(
                f"{path.name} is a protected APKMirror bundle: older .apkm files are encrypted so that only the "
                "APKMirror Installer app can open them, and FramePort can't. Download the app again from "
                "APKMirror (current .apkm files are plain bundles) or get it as an .apk/.xapk.")
        raise BundleError(f"{path.name} isn't an app bundle FramePort can read (not a zip archive)")
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise BundleError(f"{path.name} is damaged or incomplete (not a readable zip archive): {exc}") from exc
    if any(i.flag_bits & 0x1 for i in z.infolist()):
        z.close()
        raise BundleError(f"{path.name} is password-protected: FramePort can't unpack it")
    return z


def _json(z: zipfile.ZipFile, name: str) -> dict:
    try:
        data = json.loads(z.read(name).decode("utf-8-sig"))
    except (KeyError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _inner_part(z: zipfile.ZipFile, info: zipfile.ZipInfo) -> Part | None:
    try:
        with z.open(info) as f, zipfile.ZipFile(f) as inner:
            return _part(info.filename, inner, info.file_size)
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError):
        return None


def _abi_rank(p: Part) -> int:
    abi = ABIS.get(p.config or "")
    return ABI_PREFERENCE.index(abi) if abi in ABI_PREFERENCE else len(ABI_PREFERENCE)


def _base_libs(p: Part) -> set[str]:
    return {n.split("/")[1] for n in p.entries if n.startswith("lib/") and n.count("/") >= 2}


def _choose(parts: list[Part], pick_base_by_name: tuple[str, ...] = ()) -> tuple[Part, list[Part],
                                                                                 list[tuple[str, str]]]:
    """(base, splits to merge, skipped parts with the reason) of a bundle's APKs."""
    complete = [p for p in parts if p.split is None and p.package]
    if not complete:
        raise BundleError("the bundle has no base APK (only split APKs)")
    packages = {p.package for p in complete}
    skipped: list[tuple[str, str]] = []
    # a universal/standalone APK is complete on its own (bundletool's universal mode, pre-Lollipop standalones)
    names = {p.name.lower().replace("\\", "/"): p for p in complete}
    base = next((names[n] for n in names if n.rsplit("/", 1)[-1] == "universal.apk"), None)
    if base is None:
        standalones = sorted((p for p in complete if "standalones/" in p.name.lower().replace("\\", "/")),
                             key=lambda p: (_standalone_rank(p.name), -p.size))
        base = next((p for p in standalones if _standalone_rank(p.name) < len(ABI_PREFERENCE)), None)
    if base is not None:
        skipped += [(p.name, "not needed: the bundle has a complete APK") for p in parts if p is not base]
        return base, [], skipped
    bases = [p for p in complete if "standalones/" not in p.name.lower()]
    if len({p.package for p in bases}) > 1 or len(packages) > 1 and len(bases) > 1:
        raise BundleError("the bundle holds more than one app: " + ", ".join(sorted(p for p in packages if p)))
    base = next((p for p in bases if p.name.lower().rsplit("/", 1)[-1] in pick_base_by_name), None) or max(
        bases, key=lambda p: p.size)
    skipped += [(p.name, "another complete APK of the bundle") for p in bases if p is not base]
    splits = [p for p in parts if p.split is not None]
    chosen: list[Part] = []
    for p in splits:
        if p.package and p.package != base.package:
            skipped.append((p.name, f"belongs to {p.package}"))
            continue
        if p.version_code and base.version_code and p.version_code != base.version_code:
            skipped.append((p.name, f"another version ({p.version_code}, the base is {base.version_code})"))
            continue
        chosen.append(p)
    out: list[Part] = []
    # per module: one ABI (arm64-v8a, else 32-bit ARM only when the module has no 64-bit code), one texture format
    for module in dict.fromkeys(p.module for p in chosen):
        mine = [p for p in chosen if p.module == module]
        abi = sorted((p for p in mine if p.kind == "abi"), key=_abi_rank)
        has_arm64 = module == "base" and "arm64-v8a" in _base_libs(base)
        take_abi = abi[0] if abi and _abi_rank(abi[0]) < len(ABI_PREFERENCE) else None
        if take_abi and has_arm64 and ABIS[take_abi.config] != "arm64-v8a":
            take_abi = None
        tex = sorted((p for p in mine if p.kind == "texture"),
                     key=lambda p: TEXTURES.index(p.config) if p.config in TEXTURES else len(TEXTURES))
        for p in mine:
            if p.kind == "abi" and p is not take_abi:
                skipped.append((p.name, f"{ABIS[p.config]} code (the Frame runs arm64-v8a)"))
            elif p.kind == "texture" and tex and p is not tex[0]:
                skipped.append((p.name, f"{p.config} textures ({tex[0].config} is used)"))
            else:
                out.append(p)
    return base, out, skipped


def _standalone_rank(name: str) -> int:
    """bundletool's standalones/standalone-arm64_v8a_xxhdpi.apk: rank of its ABI (len = none the Frame runs)."""
    low = name.lower()
    for i, abi in enumerate(ABI_PREFERENCE):
        if abi.replace("-", "_") in low:
            return i
    return len(ABI_PREFERENCE)


def _obb_path(member: str, package: str, expansions: dict[str, str]) -> str | None:
    """Where a bundle file goes below Android/obb/<package>/ (None: not game data)."""
    target = expansions.get(member, member).replace("\\", "/").lstrip("/")
    parts = target.split("/")
    if len(parts) >= 4 and parts[0].lower() == "android" and parts[1].lower() == "obb":
        return "/".join(parts[3:])
    if parts[-1].lower().endswith(".obb"):
        return parts[-1]
    return None


def _key(entries: list[tuple]) -> str:
    h = hashlib.sha256(f"frameport-bundle-{FORMAT}".encode())
    for e in sorted(entries):
        h.update(repr(e).encode())
    return h.hexdigest()[:16]


def inspect(path: Path) -> Plan:
    """Read a bundle (or a split folder) without unpacking it: what goes where."""
    path = Path(path)
    if path.is_dir():
        return _inspect_folder(path)
    with _open(path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        manifest = _json(z, "manifest.json")  # xapk
        info = _json(z, "info.json")  # apkm
        expansions = {}
        for e in manifest.get("expansions") or []:
            if isinstance(e, dict) and isinstance(e.get("file"), str):
                expansions[e["file"]] = str(e.get("install_path") or e["file"])
        parts = []
        for i in infos:
            # bundletool's instant-app, system-image and archived variants aren't for installing
            if i.filename.lower().endswith(".apk") and not i.filename.lower().startswith(SKIP_FOLDERS):
                part = _inner_part(z, i)
                if part:
                    parts.append(part)
        if not parts:
            raise BundleError(f"{path.name} has no APK inside")
        base, splits, skipped = _choose(parts, ("base.apk", "base-master.apk"))
        obbs, ignored = [], []
        for i in infos:
            if i.filename.lower().endswith(".apk"):
                continue
            rel = _obb_path(i.filename, base.package, expansions)
            if rel:
                obbs.append((i.filename, rel))
            elif i.filename.replace("\\", "/").lower().startswith("android/"):
                ignored.append(i.filename)
        key = _key([(i.filename, i.CRC, i.file_size) for i in infos])
        title = manifest.get("name") or info.get("app_name")
    return Plan(path, base.package, base.version_code, base, splits, skipped, obbs, ignored, key,
                title if isinstance(title, str) and title.strip() else None)


def _inspect_folder(folder: Path) -> Plan:
    files = split_folder(folder)
    if not files:
        raise BundleError(f"{folder.name} isn't a set of split APKs")
    parts = []
    entries = []
    for p in files:
        with zipfile.ZipFile(p) as z:
            part = _part(p.name, z, p.stat().st_size)
        if part:
            parts.append(part)
            st = p.stat()
            entries.append((p.name, st.st_size, st.st_mtime_ns))
    base, splits, skipped = _choose(parts, ("base.apk",))
    return Plan(folder, base.package, base.version_code, base, splits, skipped, [], [], _key(entries))


# ------------------------------------------------------------------------------------------------ unpacking
def prepared(plan: Plan) -> bool:
    return (plan.cache_dir() / INFO).is_file() and plan.apk_path.is_file()


def prepare(path: Path, reporter: Reporter | None = None) -> Plan:
    """Unpack a bundle into its cache folder (once: an unchanged bundle is reused) and return its plan."""
    plan = inspect(path)
    if prepared(plan):
        return plan
    final = plan.cache_dir()
    tmp = final.with_name(final.name + f".tmp-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        _check_space(plan, tmp)
        parts_dir = tmp / ".parts"
        parts_dir.mkdir()
        files = _extract_parts(plan, parts_dir, reporter)
        out = tmp / plan.apk_path.name
        if reporter:
            reporter.stage("Unpacking the app bundle", f"Putting {plan.source.name} together")
        if plan.method == "base":
            shutil.move(str(files[plan.base.name]), out)
        elif plan.method == "libs":
            merge_simple(files[plan.base.name], [files[p.name] for p in plan.splits], out)
        else:
            from ..tools import apkeditor

            if reporter:
                reporter.log(f"merging {len(plan.splits)} split APKs with APKEditor")
            try:
                apkeditor.merge([files[plan.base.name]] + [files[p.name] for p in plan.splits], out, tmp / ".work")
                _fix_manifest_in_place(out, tmp / ".fixed.apk")
            except Exception as exc:  # noqa: BLE001 - no Java/network, or APKEditor failed
                # translations and other screen densities only: the base still has the default resources
                if any(p.kind in ("feature", "abi") and not p.simple for p in plan.splits):
                    raise BundleError(f"the split APKs of {plan.source.name} couldn't be merged: {exc}") from exc
                if reporter:
                    reporter.log(f"APKEditor unavailable ({exc}); merging only native libraries and assets: "
                                 "translations/screen-density resources of the splits are left out")
                merge_simple(files[plan.base.name], [files[p.name] for p in plan.splits], out)
                plan.used = "libs-only"
        shutil.rmtree(parts_dir, ignore_errors=True)
        shutil.rmtree(tmp / ".work", ignore_errors=True)
        _extract_obbs(plan, tmp / "obb" / _safe(plan.package), reporter)
        record = {"format": FORMAT, "source": str(plan.source), "package": plan.package,
                  "version_code": plan.version_code, "method": plan.used or plan.method, "base": plan.base.name,
                  "splits": [p.name for p in plan.splits], "skipped": plan.skipped,
                  "obbs": [rel for _, rel in plan.obbs], "ignored": plan.ignored, "title": plan.title}
        (tmp / INFO).write_text(json.dumps(record, indent=2))
        shutil.rmtree(final, ignore_errors=True)
        tmp.rename(final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if reporter:
        for name, why in plan.skipped:
            reporter.log(f"{plan.source.name}: left out {name}: {why}")
        for name in plan.ignored:
            reporter.log(f"{plan.source.name}: {name} isn't used (FramePort sends game data to Android/obb only)")
    _prune(plan)
    return plan


def _check_space(plan: Plan, where: Path) -> None:
    need = plan.base.size + sum(p.size for p in plan.splits) * 2
    if not plan.source.is_dir():
        with zipfile.ZipFile(plan.source) as z:
            sizes = {i.filename: i.file_size for i in z.infolist()}
        need += sum(sizes.get(m, 0) for m, _ in plan.obbs)
    try:
        free = shutil.disk_usage(where).free
    except OSError:
        return
    if free < need + (256 << 20):
        raise BundleError(f"not enough free space to unpack {plan.source.name}: it needs about "
                          f"{need / 2**30:.1f} GB in {where.parent.parent.parent}")


def _copy(src, dst, total: int, label: str, reporter: Reporter | None, done: list[int]) -> None:
    while block := src.read(4 << 20):
        dst.write(block)
        done[0] += len(block)
        if reporter:
            reporter.check_cancel()
            reporter.progress(min(done[0] / max(total, 1), 1.0), label)


def _extract_parts(plan: Plan, dest: Path, reporter: Reporter | None) -> dict[str, Path]:
    wanted = [plan.base, *plan.splits]
    out = {}
    if plan.source.is_dir():
        for p in wanted:
            out[p.name] = plan.source / p.name
        if plan.method == "base":  # (never: a split folder has splits) keep the user's file
            shutil.copyfile(out[plan.base.name], dest / "base.apk")
            out[plan.base.name] = dest / "base.apk"
        return out
    total = sum(p.size for p in wanted)
    done = [0]
    if reporter:
        reporter.stage("Unpacking the app bundle", f"Unpacking {plan.source.name}")
    with zipfile.ZipFile(plan.source) as z:
        for i, p in enumerate(wanted):
            target = dest / f"{i:03d}.apk"
            with z.open(p.name) as src, open(target, "wb") as dst:
                _copy(src, dst, total, f"{plan.source.name}: {p.name.rsplit('/', 1)[-1]}", reporter, done)
            out[p.name] = target
    return out


def _extract_obbs(plan: Plan, dest: Path, reporter: Reporter | None) -> None:
    if not plan.obbs:
        return
    with zipfile.ZipFile(plan.source) as z:
        sizes = {i.filename: i.file_size for i in z.infolist()}
        total = sum(sizes.get(m, 0) for m, _ in plan.obbs)
        done = [0]
        if reporter:
            reporter.stage("Unpacking the app bundle", "Unpacking the game data")
        root = dest.resolve()
        for member, rel in plan.obbs:
            target = (dest / rel).resolve()
            if root not in target.parents:  # a "../" name in someone's zip
                raise BundleError(f"{plan.source.name} has a file outside its own folder: {member}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, open(target, "wb") as dst:
                _copy(src, dst, total, f"{plan.source.name}: {rel}", reporter, done)


# ------------------------------------------------------------------------------------------------ merging
def fix_manifest(manifest: bytes) -> bytes:
    """A base manifest that works without its splits: no isSplitRequired/requiredSplitTypes/splitTypes, and Play's
    com.android.vending.splits.required = false."""
    out = axml.remove_attrs(manifest, "application", SPLIT_ATTRS) or manifest
    out = axml.remove_attrs(out, "manifest", SPLIT_ATTRS) or out
    return axml.set_meta_data_bool(out, "com.android.vending.splits.required", False) or out


def merge_simple(base: Path, splits: list[Path], out: Path) -> Path:
    """Base + splits that only add native libraries/assets → one unsigned APK (each entry keeps its compression and
    date; the base's entries win over a split's of the same name)."""
    with zipfile.ZipFile(base) as zb, zipfile.ZipFile(out, "w", allowZip64=True) as dst:
        seen = set()

        def copy(z: zipfile.ZipFile, info: zipfile.ZipInfo, data: bytes | None = None) -> None:
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type, zi.external_attr = info.compress_type, info.external_attr
            seen.add(info.filename)
            if data is not None:
                dst.writestr(zi, data)
                return
            with z.open(info) as fin, dst.open(zi, "w", force_zip64=info.file_size > 0x7FFFFFFF) as fout:
                shutil.copyfileobj(fin, fout, 1 << 22)

        for info in zb.infolist():
            if info.is_dir() or SIGNATURE_FILE.match(info.filename):
                continue
            if info.filename == "AndroidManifest.xml":
                copy(zb, info, fix_manifest(zb.read(info)))
            else:
                copy(zb, info)
        for split in splits:
            with zipfile.ZipFile(split) as zs:
                for info in zs.infolist():
                    name = info.filename
                    if info.is_dir() or name in seen or not name.startswith(("lib/", "assets/")):
                        continue
                    copy(zs, info)
    return out


def _fix_manifest_in_place(apk: Path, scratch: Path) -> None:
    """APKEditor's merge already drops the split attributes; make sure (and set Play's flag) without re-merging."""
    with zipfile.ZipFile(apk) as z:
        manifest = z.read("AndroidManifest.xml")
    fixed = fix_manifest(manifest)
    if fixed == manifest:
        return
    with zipfile.ZipFile(apk) as zin, zipfile.ZipFile(scratch, "w", allowZip64=True) as dst:
        for info in zin.infolist():
            if info.is_dir() or SIGNATURE_FILE.match(info.filename):
                continue
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type, zi.external_attr = info.compress_type, info.external_attr
            if info.filename == "AndroidManifest.xml":
                dst.writestr(zi, fixed)
                continue
            with zin.open(info) as fin, dst.open(zi, "w", force_zip64=info.file_size > 0x7FFFFFFF) as fout:
                shutil.copyfileobj(fin, fout, 1 << 22)
    os.replace(scratch, apk)


def is_signed(apk: Path) -> bool:
    """The APK has a v2+ signing block or v1 signature files (a merged bundle has neither)."""
    try:
        with zipfile.ZipFile(apk) as z:
            if any(SIGNATURE_FILE.match(n) and not n.upper().endswith("MANIFEST.MF") for n in z.namelist()):
                return True
            cd = z.start_dir  # the signing block ends right before the central directory
        with open(apk, "rb") as f:
            f.seek(max(cd - 16, 0))
            return f.read(16) == b"APK Sig Block 42"
    except (OSError, zipfile.BadZipFile, AttributeError):
        return False


# ------------------------------------------------------------------------------------------------ the cache
def _prune(plan: Plan) -> None:
    """Older unpacked copies of the same app that no library entry uses any more."""
    from ..core import library

    used = set()
    for g in library.games():
        if g.get("apk"):
            used.add(Path(g["apk"]).parent.name)
    folder = plan.cache_dir().parent
    try:
        others = [d for d in folder.iterdir() if d.is_dir() and d.name != plan.key]
    except OSError:
        return
    for d in others:
        if d.name not in used and ".tmp-" not in d.name:
            shutil.rmtree(d, ignore_errors=True)


def owned(apk: Path | str | None) -> bool:
    """The path is inside FramePort's bundle cache (an unpacked bundle, not the user's own file)."""
    if not apk:
        return False
    from ..core.paths import bundles_dir

    try:
        return bundles_dir().resolve() in Path(apk).resolve().parents
    except OSError:
        return False


def remove_unpacked(apk: Path | str | None) -> None:
    """Delete the unpacked copy an APK path of the cache belongs to (the game left the library)."""
    if not owned(apk):
        return
    from ..core.paths import bundles_dir

    root = bundles_dir().resolve()
    folder = Path(apk).resolve().parent
    if folder.parent.parent == root:
        shutil.rmtree(folder, ignore_errors=True)
        try:
            folder.parent.rmdir()
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------ as a game
def source_game(path: Path, reporter: Reporter | None = None):
    """The bundle as a SourceGame (unpacked first): the unpacked APK + OBB folder, the bundle's own name and
    folder."""
    from ..core.models import SourceGame
    from .quest_dump import display_name, find_data

    path = Path(path)
    plan = prepare(path, reporter)
    data, files = plan.data_dir, None
    if data is None:  # OBBs lying next to the bundle / in the split folder, like next to an APK
        data, files = find_data(path if path.is_dir() else path.parent, plan.package, plan.version_code)
    if path.is_dir():  # a folder of split APKs is a game folder like any other
        return SourceGame(display_name(path.name), plan.apk_path, data, path, data_files=files, bundle=path)
    return SourceGame(display_name(path.stem), plan.apk_path, data, path.parent, data_files=files, bundle=path)
