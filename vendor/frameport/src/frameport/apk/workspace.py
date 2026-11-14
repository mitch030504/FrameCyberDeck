"""Staged edits to an APK: read entries lazily, collect replacements/additions, write a new zip."""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

SIGNATURE_FILE = re.compile(r"^META-INF/([^/]+\.(SF|RSA|DSA|EC)|MANIFEST\.MF)$", re.I)


class ApkWorkspace:
    def __init__(self, apk: Path):
        self.apk = Path(apk)
        self._zip = zipfile.ZipFile(self.apk)
        self.infos = {i.filename: i for i in self._zip.infolist()}
        self.replace: dict[str, bytes] = {}
        self.add: dict[str, bytes] = {}
        self.rename: dict[str, str] = {}  # old name -> new name (content unchanged unless replaced)
        self.remove: set[str] = set()
        abi = next((a for a in ("arm64-v8a", "armeabi-v7a")
                    if any(n.startswith(f"lib/{a}/") for n in self.infos)), None)
        self.abi = abi
        self.libdir = f"lib/{abi}/" if abi else ""

    def close(self) -> None:
        self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- reads (see staged state)
    def names(self) -> set[str]:
        current = {self.rename.get(n, n) for n in self.infos if n not in self.remove}
        return current | set(self.add)

    def has(self, name: str) -> bool:
        return name in self.names()

    def read(self, name: str) -> bytes:
        if name in self.add:
            return self.add[name]
        original = next((o for o, new in self.rename.items() if new == name), name)
        if original in self.replace:
            return self.replace[original]
        return self._zip.read(original)

    def size(self, name: str) -> int:
        """Uncompressed size of an entry (staged content if replaced)."""
        if name in self.add:
            return len(self.add[name])
        original = next((o for o, new in self.rename.items() if new == name), name)
        if original in self.replace:
            return len(self.replace[original])
        return self.infos[original].file_size

    def contains(self, name: str, needles: tuple[bytes, ...], chunk: int = 4 << 20) -> bool:
        """Whether an entry contains any of `needles`, streamed in chunks (big entries aren't read into memory)."""
        if name in self.add:
            return any(n in self.add[name] for n in needles)
        original = next((o for o, new in self.rename.items() if new == name), name)
        if original in self.replace:
            return any(n in self.replace[original] for n in needles)
        keep = max(len(n) for n in needles) - 1
        tail = b""
        with self._zip.open(original) as f:
            while block := f.read(chunk):
                window = tail + block
                if any(n in window for n in needles):
                    return True
                tail = window[-keep:] if keep else b""
        return False

    def lib(self, name: str) -> str:
        return self.libdir + name

    def libs(self) -> list[str]:
        return sorted(n[len(self.libdir):] for n in self.names() if n.startswith(self.libdir) and n.endswith(".so"))

    def read_lib(self, name: str) -> bytes:
        return self.read(self.lib(name))

    # ---------------------------------------------------------------- writes (staged)
    def put(self, name: str, data: bytes) -> None:
        original = next((o for o, new in self.rename.items() if new == name), name)
        if original in self.infos and original not in self.remove:
            self.replace[original] = data
        else:
            self.add[name] = data

    def move(self, old: str, new: str) -> None:
        if new in self.infos and new not in self.rename:
            self.remove.add(new)
        self.rename[old] = new

    def changed(self) -> bool:
        return bool(self.replace or self.add or self.rename or self.remove)

    def write(self, out: Path) -> Path:
        """Write the staged APK (unsigned: old signature files are dropped); keeps each entry's compression."""
        infos = list(self.infos.values())
        lib_compress = next((i.compress_type for i in infos if i.filename.startswith("lib/")), zipfile.ZIP_DEFLATED)
        with zipfile.ZipFile(out, "w", allowZip64=True) as dst:
            for info in infos:
                name = info.filename
                if info.is_dir() or SIGNATURE_FILE.match(name) or name in self.remove:
                    continue
                if name in self.add and name not in self.rename:  # replaced by an added entry of the same name
                    continue
                target = self.rename.get(name, name)
                zi = zipfile.ZipInfo(target, date_time=info.date_time)
                zi.compress_type, zi.external_attr = info.compress_type, info.external_attr
                if name in self.replace:
                    dst.writestr(zi, self.replace[name])
                else:
                    with (self._zip.open(info) as fin,
                          dst.open(zi, "w", force_zip64=info.file_size > 0x7FFFFFFF) as fout):
                        shutil.copyfileobj(fin, fout, 1 << 22)
            for name, data in self.add.items():
                zi = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                zi.compress_type = lib_compress if name.startswith("lib/") else zipfile.ZIP_DEFLATED
                zi.external_attr = 0o644 << 16
                dst.writestr(zi, data)
        return out
