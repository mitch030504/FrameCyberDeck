"""APK support for device.foveation; runtime configuration lives in settings.py.

Shared injection needs no additional APK edits in RE4/Deadpool. Batman's tested
OVRPlugin build needs ordinary shared allocation; never apply those offsets to
another build. The optional diagnostic instruments actual coarse fragments.
"""
from __future__ import annotations

import hashlib
import io
import struct

from elftools.elf.elffile import ELFFile

from ...analysis import elf
from . import artifact
from .zink_shader_fix import ENGINE_LIBS, NOT_ENGINE

LAYER = "libVkLayer_fdm_darken.so"
BATMAN_ORIGINAL = "76cafa25c2243c2bc065e30d83d7083b8546153dc6a3c39227235fbabe13c969"
CLIMB_RENDERER = "31a121dbfae692fdc0e54853e0640eed820f07826893fa0d905767c9620023a0"
CLIMB_BRIDGE = "libvrapi_foveation.so"
# CryEngine: the modular renderer (Climb 2) and the monolithic launcher that contains it (The Climb 1)
CRYENGINE_RENDERER = ("libCryRender", "libAndroidLauncher")
STORAGE_WORDS = ((0x21c778, 0x5298000b, 0x5290000b),
                 (0x21c77c, 0x5288000c, 0x5280000c),
                 (0x21df50, 0x52980009, 0x52900009))


def ordinary_storage(data: bytes) -> bytes:
    """Accept only the verified original, or exactly its already-patched form."""
    image = ELFFile(io.BytesIO(data))
    original, patched = bytearray(data), bytearray(data)
    for va, old, new in STORAGE_WORDS:
        seg = next((s for s in image.iter_segments() if s["p_type"] == "PT_LOAD"
                    and s["p_vaddr"] <= va and va + 4 <= s["p_vaddr"] + s["p_filesz"]), None)
        if seg is None:
            raise ValueError("Batman foveation: unsupported OVRPlugin build; allocation fix not applied")
        offset = va - seg["p_vaddr"] + seg["p_offset"]
        if struct.unpack_from("<I", data, offset)[0] not in (old, new):
            raise ValueError("Batman foveation: unsupported OVRPlugin instructions")
        struct.pack_into("<I", original, offset, old)
        struct.pack_into("<I", patched, offset, new)
    if hashlib.sha256(original).hexdigest() != BATMAN_ORIGINAL:
        raise ValueError("Batman foveation: this OVRPlugin version has no verified allocation fix")
    return bytes(patched)


def engine_lib(ws):
    lib = next((n for n in ENGINE_LIBS if ws.has(ws.lib(n))), None)
    if lib:
        return lib
    for name in ws.libs():
        if name.startswith(NOT_ENGINE):
            continue
        data = ws.read_lib(name)
        if (elf.is_elf(data) and {"vkGetInstanceProcAddr", "vkCreateInstance", "eglInitialize"}
                & elf.dyn_symbols(data, False)):
            return name
    return None


def apk_options(ctx):
    """Personal settings never select APK edits. Prepare only verified compatibility builds automatically."""
    from ..settings import foveation_options

    requested = foveation_options(ctx.params)
    if ctx.params.get("support") or requested[0] in ("", "off"):
        ws = ctx.ws
        if ctx.analysis.package == "com.camouflaj.manta":
            if not ws.has(ws.lib("libOVRPlugin.so")):
                return "", 0, False
            try:
                ordinary_storage(ws.read_lib("libOVRPlugin.so"))
            except ValueError:
                ctx.notes.append("unknown Batman OVRPlugin: automatic foveation allocation fix skipped")
                return "", 0, False
        elif ctx.analysis.package == "com.crytek.climb2":
            if (not ws.has(ws.lib("libCryRenderVulkan.so")) or
                    hashlib.sha256(ws.read_lib("libCryRenderVulkan.so")).hexdigest() != CLIMB_RENDERER):
                ctx.notes.append("unknown Climb renderer: automatic foveation bridge skipped")
                return "", 0, False
        else:
            return "", 0, False
        return "tracked", 0, False
    return requested


def apply(ctx):
    mode, _radius, test = apk_options(ctx)
    if mode not in ("tracked", "fixed"):
        return False
    ws = ctx.ws
    if ws.abi != "arm64-v8a":
        raise ValueError("Shared foveation currently supports arm64-v8a games only")
    changed = False
    if ctx.analysis.package == "com.camouflaj.manta":
        data = ws.read_lib("libOVRPlugin.so")
        fixed = ordinary_storage(data)
        if fixed != data:
            ws.put(ws.lib("libOVRPlugin.so"), fixed)
            ctx.notes.append("verified Batman ordinary shared-image allocation fix")
            changed = True
    climb = ctx.analysis.package == "com.crytek.climb2"
    if climb:
        if (not ws.has(ws.lib("libCryRenderVulkan.so")) or
                hashlib.sha256(ws.read_lib("libCryRenderVulkan.so")).hexdigest() != CLIMB_RENDERER):
            raise ValueError("The Climb 2 foveation: unsupported renderer build")
        bridge = elf.add_needed(artifact(ws.abi, CLIMB_BRIDGE), LAYER)
        if not ws.has(ws.lib("libvrapi.so")) or ws.read_lib("libvrapi.so") != bridge:
            ws.put(ws.lib("libvrapi.so"), bridge)
            changed = True
        ctx.notes.append("Climb private bridge: stereo gaze, subpass and held-focus compatibility")
    if test or climb:
        lib = "libvrapi.so" if climb else engine_lib(ws)
        if not lib:
            raise ValueError("Foveation test: no supported renderer library found")
        data = ws.read_lib(lib)
        if LAYER not in elf.needed(data):
            ws.put(ws.lib(lib), elf.add_needed(data, LAYER, first=False))  # see diagnostic_support
            changed = True
        layer = artifact(ws.abi, LAYER)
        if not ws.has(ws.lib(LAYER)) or ws.read_lib(LAYER) != layer:
            ws.put(ws.lib(LAYER), layer)
            changed = True
        if test:
            ctx.notes.append("coarse-fragment darkening diagnostic; not for benchmarking")
    return changed


def validate(ctx):
    mode, _radius, test = apk_options(ctx)
    checks = []
    if mode in ("tracked", "fixed") and ctx.analysis.package == "com.camouflaj.manta":
        data = ctx.ws.read_lib("libOVRPlugin.so")
        checks.append(("Batman shared allocation fix", ordinary_storage(data) == data, "verified OVRPlugin"))
    if mode in ("tracked", "fixed") and (test or ctx.analysis.package == "com.crytek.climb2"):
        lib = "libvrapi.so" if ctx.analysis.package == "com.crytek.climb2" else engine_lib(ctx.ws)
        checks.append(("Foveation darkening layer loaded", bool(lib) and ctx.ws.has(ctx.ws.lib(LAYER))
                       and LAYER in elf.needed(ctx.ws.read_lib(lib)), lib or "no renderer"))
    return checks


def diagnostic_support(ws):
    """The diagnostic constructor is dormant without FRAMEPORT_FOVEATION_TEST=1.

    The layer file always ships: frame.adapter links every arm64 FrameBridge build against it, and without the file
    FrameBridge doesn't load at all (The Climb 1 on a dev build: `xrInitializeLoaderKHR failed: OpenXR result -7`,
    GitHub #432). In the engine library it goes after the existing DT_NEEDED entries: the layer exports
    vkEnumerate{Instance,Device}{Extension,Layer}Properties for the Vulkan loader, and linked first, an engine that
    imports those from libvulkan.so directly gets the layer's (no extensions, VK_ERROR_LAYER_NOT_PRESENT) instead.
    That is what stopped CryEngine (The Climb 1/2 on 1.3.0, GitHub #432/#449)."""
    changed = False
    layer = artifact(ws.abi, LAYER)
    if not ws.has(ws.lib(LAYER)) or ws.read_lib(LAYER) != layer:
        ws.put(ws.lib(LAYER), layer)
        changed = True
    lib = engine_lib(ws) or "libopenxr_loader_generic.so"
    if lib.startswith(CRYENGINE_RENDERER):
        # CryEngine's renderer (Climb 2: libCryRender*; The Climb 1: its monolithic libAndroidLauncher.so) stays
        # untouched; Climb 2's private VrApi bridge loads the layer itself.
        return changed
    data = ws.read_lib(lib)
    if LAYER not in elf.needed(data):
        target = ws.lib(lib)
        updated = elf.add_needed(data, LAYER, first=False)
        if target in ws.add:
            ws.add[target] = updated
        else:
            ws.put(target, updated)
        changed = True
    return changed
