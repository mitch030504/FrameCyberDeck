"""Route statically linked Khronos OpenXR loaders through FrameBridge."""
from __future__ import annotations

from ...analysis import static_openxr
from ...tools import overport
from ..base import ApkContext, Patch, Suggestion, register
from . import artifact
from .adapter import GENERIC, ORIGINAL

SHIM = "libfp_xrruntime.so"


class StaticOpenXRLoader(Patch):
    id = "frame.static_openxr_loader"
    title = "Embedded OpenXR loader: use FrameBridge"
    description = ("Routes a game's statically linked Khronos OpenXR loader through FrameBridge by rewriting its "
                   "runtime manifest paths in place into its own /sdcard (including assembled paths). Disables its "
                   "Android runtime "
                   "broker queries so they fall back to that file. Ships a runtime shim and an unmodified inner "
                   "Khronos loader, which reads the system runtime manifest. Reinstall to write the per-game manifest.")
    requires = ("frame.adapter",)
    order = 9  # provide the inner loader before frame.adapter wraps it
    revision = 2  # 2: the game's loader no longer loads implicit API layers (Valve's layer twice, #254)

    def applies(self, a):
        return bool((a.extra or {}).get("static_openxr_loader"))

    def detect(self, a):
        if self.applies(a):
            return Suggestion(True, "The embedded Khronos OpenXR loader bypasses FrameBridge.")
        return None

    def apply(self, ctx: ApkContext) -> bool:
        ws = ctx.ws
        if ws.abi != "arm64-v8a":
            raise RuntimeError("Embedded OpenXR loader redirection requires arm64")
        libs = (ctx.analysis.extra or {}).get("static_openxr_loader") or []
        if not libs:
            raise RuntimeError("No embedded Khronos OpenXR loader detected; analyze the APK again")
        changed = False
        for name in libs:
            data = ws.read_lib(name)
            fixed, count = static_openxr.rewrite(data)
            if not count and not static_openxr.redirected(data):
                raise RuntimeError(f"{name}: embedded loader manifest paths could not be redirected")
            if count:
                ws.put(ws.lib(name), fixed)
                changed = True
        # Like a32_bridge, also works when the recipe doesn't run OVRPort at all.
        if not ws.has(ws.lib(ORIGINAL)) and not ws.has(ws.lib(GENERIC)):
            inner = overport.ensure_runtime_lib(GENERIC, ws.abi, ctx.reporter).read_bytes()
            ws.put(ws.lib(ORIGINAL), inner)
            ws.put(ws.lib(GENERIC), artifact(ws.abi, GENERIC))
            changed = True
        shim = artifact(ws.abi, SHIM)
        if not ws.has(ws.lib(SHIM)) or ws.read_lib(SHIM) != shim:
            ws.put(ws.lib(SHIM), shim)
            changed = True
        return changed

    def validate(self, ctx: ApkContext):
        ws = ctx.ws
        libs = (ctx.analysis.extra or {}).get("static_openxr_loader") or []
        ok = bool(libs) and all(ws.has(ws.lib(n)) and static_openxr.redirected(ws.read_lib(n))
                               for n in libs)
        ok = ok and ws.has(ws.lib(SHIM)) and ws.read_lib(SHIM) == artifact(ws.abi, SHIM)
        return [("Embedded OpenXR loader uses FrameBridge", ok, ", ".join(libs))]


register(StaticOpenXRLoader)
