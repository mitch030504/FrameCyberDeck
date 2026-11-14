"""QuestCraft's launcher library (pojlib) downloads its Java runtime only when pojlib.API.hasConnection() is true, and
that checks for a Wi-Fi network. Lepton's Android reports its network as Ethernet, so the check fails, the download is
skipped and Minecraft can't start ("libjvm.so not found"; GitHub #18, #468). hasConnection() returns true instead
(in-place classes.dex edit, see vivox_audio_route); without any network the download itself fails as before."""
from __future__ import annotations

from ...apk.dex import Dex
from ..base import ApkContext, Patch, Suggestion, register

API = "Lpojlib/API;"
METHOD = "hasConnection"
QUESTCRAFT = "com.qcxr.qcxr"  # analyses from before the pojlib_connection field (no forced re-analysis for one game)


def patch_dex(dex: Dex) -> list[str]:
    """pojlib.API methods that now return true; [] if nothing applies."""
    done = []
    for idx, code in dex.class_methods(API):
        if dex.method(idx)[1] == METHOD and dex.return_type(idx) == "Z" and \
                dex.return_early(code, "Z", boolean_value=True):
            done.append(METHOD)
    return done


class PojlibConnection(Patch):
    id = "frame.pojlib_connection"
    title = "QuestCraft: count the Frame's network as online"
    description = ("QuestCraft downloads its Java runtime only when Android reports a Wi-Fi connection; the Frame's "
                   "Android reports Ethernet, so the download is skipped and Minecraft can't start. The connection "
                   "check answers yes instead (an in-place edit of classes.dex).")
    order = 44
    needs_vr = False

    def applies(self, a):
        return bool((a.extra or {}).get("pojlib_connection")) or a.package == QUESTCRAFT

    def detect(self, a):
        if self.applies(a):
            return Suggestion(True, "QuestCraft only downloads its Java runtime on Wi-Fi; the Frame's Android reports "
                                    "its network as Ethernet.")
        return None

    def apply(self, ctx: ApkContext) -> bool:
        changed = False
        for name in sorted(n for n in ctx.ws.names() if n.startswith("classes") and n.endswith(".dex")):
            data = ctx.ws.read(name)
            if API.encode() not in data:
                continue
            dex = Dex(data)
            if patch_dex(dex):
                ctx.ws.put(name, dex.finish())
                ctx.notes.append(f"{name}: pojlib API.{METHOD} returns true")
                changed = True
        return changed


register(PojlibConnection)
