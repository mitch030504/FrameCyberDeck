"""GAZZLERS (com.BanzaiStudios.GAZZLERS, GitHub #404): "Exception: Current Platform is not supported" at start.

The game picks its headset platform (Runtime/PlatformDeterminer, enum Platform: None 0, Meta 1, PICO 2, PSVR2 3,
Steam 4) from the Android device name. In 1.4.1.7857 (Cpp2IL + disassembly) GetPlatform is a plain branch to
GetAndroidPlatform, which does `m = SystemInfo.deviceModel.ToLower()`: m.Contains(<first literal>) -> PICO (2),
else m.Contains(<second literal>) -> Meta (1), else `throw new Exception("Current Platform is not supported")`.
Lepton reports manufacturer Valve, model Lepton (write-once ro.* properties that a LEPTON_GFXRECON_* boot property
line can't replace, docs/DEVICE_NOTES.md "Per-game headset identity"), so neither matches and the throw kills
RecenterHandlerFactory.Create and PlatformInitializerFactory.Create (GameInitializer.Initialize): the game never
gets past its start.

GetAndroidPlatform -> `mov w0, #1; ret` (Platform.Meta): the Quest build's own path, the one it takes on a Quest.
Only for this game: the method is looked up by name, so another build simply gets no change."""
from __future__ import annotations

from ..base import register
from .unity_text_input import Il2cppReturnPatch

PACKAGE = "com.BanzaiStudios.GAZZLERS"
DETERMINER = "Runtime/PlatformDeterminer.cs"
RET_META = bytes.fromhex("20008052c0035fd6")  # mov w0, #1 (Platform.Meta) ; ret


class GazzlersQuestPlatform(Il2cppReturnPatch):
    id = "frame.gazzlers_quest_platform"
    title = "GAZZLERS: take the Quest platform path"
    description = ("GAZZLERS decides between its Meta Quest and PICO code from Android's device name and stops with "
                   "\"Current Platform is not supported\" on the Frame, whose Android reports itself as Valve "
                   "Lepton. Rewrites PlatformDeterminer.GetAndroidPlatform -> Meta in libil2cpp.so (found with "
                   "Cpp2IL), so the game starts as it does on a Quest.")
    order = 49
    targets = {DETERMINER: {"GetAndroidPlatform": RET_META}}
    check_name = "GAZZLERS platform"

    def applies(self, a):
        return a.package == PACKAGE and "libil2cpp.so" in a.libs and "arm64-v8a" in a.abis

    def detect(self, a):
        return None  # catalog recipe only


register(GazzlersQuestPlatform)
