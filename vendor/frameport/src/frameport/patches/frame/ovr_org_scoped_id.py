"""Answers Meta's org-scoped user id request (`ovr_User_GetOrgScopedID`), which OVRPort's platform loader never answers.

OVRPort's libovrplatformloader.so (runtimes 3.4.3-23204ea and -aa54c3f) exports `ovr_User_GetOrgScopedID`,
`ovr_Message_GetOrgScopedID` and `ovr_OrgScopedID_GetID` as `mov x0, xzr; ret`: the request id is 0 and no message
ever comes back. Meta's C# wrapper (Oculus.Platform `Request<T>.OnComplete`) then logs "An unknown error occurred.
Request failed." and never calls the callback, so a game that waits for it stays on its loading screen: Eleven Table
Tennis (GitHub #452: Users.GetLoggedInUser -> `<BuildIdentity>b__0` -> Users.GetOrgScopedID -> `<BuildIdentity>b__1`,
which goes on with Users.GetUserProof), Cortopia's Escaping Wonderland (#360) and TMNT: Empire City (#422).

The fix lives in the language-pack library (native/langpack, `libfp_langpack.so`, the loader's first DT_NEEDED with
the loader's own exports hidden): with the marker byte `@FPORGID@` set it answers the request with a fresh request id
and a successful `ovrMessage_User_GetOrgScopedID` (0x18F0B01B) whose `ovr_OrgScopedID_GetID` is a 64-bit id derived
from the user id the game passes (a hash: stable, because frame.ovr_stable_user keeps the user id fixed per game and
FramePort user). When no other patch linked the library, language packs stay off in it (`@FPLANGS@` = '0'), so the
build behaves like the plain loader for every other call. `ovr_User_GetUserProof` (Eleven's next request) is answered
by OVRPort itself (a queued message with a nonce string), so it is left alone.
"""
from __future__ import annotations

from ...analysis import elf
from ..base import ApkContext, Patch, Suggestion, register
from . import artifact
from .langpack import EXPORTS, LIB, LOADER, ORG_MARK, with_metadata, with_org_id, without_langs

# games seen waiting for this answer whose analysis has no own flag for it (no re-analysis needed for a known game)
WAITING_GAMES = {
    "quest.eleven.forfunlabs": "Eleven Table Tennis",  # GitHub #452
    "com.CortopiaStudios.draxPrototype": "TMNT: Empire City",  # GitHub #422 (also cortopia_org_id below)
}


class OvrOrgScopedId(Patch):
    id = "frame.ovr_org_scoped_id"
    title = "Answer Meta's org-scoped user id request"
    description = ("OVRPort's platform library never answers Meta's org-scoped user id request "
                   "(ovr_User_GetOrgScopedID returns 'no request'), so Meta's Unity wrapper logs \"An unknown error "
                   "occurred. Request failed.\" and a game that waits for the id stays on its loading screen (e.g. "
                   "Eleven Table Tennis, Escaping Wonderland). Adds the small platform library FramePort also uses "
                   "for language packs and answers that request with a fixed id derived from the game's user id. "
                   "Every other platform call goes to OVRPort's library unchanged.")
    order = 35  # after frame.langpacks / asset_files / asset_files_stub: links the library only if none of them did
    default_on = True  # suggested only where a game is known to wait for the answer (detect)

    def applies(self, a):
        return LOADER in a.libs and "arm64-v8a" in a.abis

    def detect(self, a):
        if not self.applies(a):
            return None
        if (a.extra or {}).get("cortopia_org_id"):
            return Suggestion(True, "The game's Meta start-up waits for an org-scoped user id that OVRPort's "
                                    "platform library never answers (stuck loading screen).")
        name = WAITING_GAMES.get(getattr(a, "package", "") or "")
        if name:
            return Suggestion(True, f"{name} waits for Meta's org-scoped user id, which OVRPort's platform "
                                    "library never answers (stuck loading screen).")
        return None

    def apply(self, ctx: ApkContext) -> bool:
        ws = ctx.ws
        if ws.abi != "arm64-v8a" or not ws.has(ws.lib(LOADER)):
            return False
        loader = ws.read_lib(LOADER)
        if LIB not in elf.needed(loader):  # no language-pack / asset-file patch linked the library: do it here
            patched, hidden = elf.hide_exports(loader, EXPORTS)
            if "ovr_User_GetOrgScopedID" not in hidden:
                ctx.notes.append("the platform loader has no ovr_User_GetOrgScopedID: nothing to replace")
                return False
            ws.put(ws.lib(LOADER), elf.add_needed(patched, LIB))
            lib = with_metadata(artifact(ws.abi, LIB), (ctx.analysis.version or "").strip())
            ws.put(ws.lib(LIB), without_langs(lib))  # only the org-scoped id: language packs pass through
        lib = ws.read_lib(LIB)
        flagged = with_org_id(lib)
        if flagged == lib:
            return False
        ws.put(ws.lib(LIB), flagged)
        ctx.notes.append(f"Meta's org-scoped user id request answered by {LIB}")
        return True

    def validate(self, ctx: ApkContext):
        ws = ctx.ws
        if not ws.has(ws.lib(LOADER)):
            return []
        linked = LIB in elf.needed(ws.read_lib(LOADER))
        enabled = ws.has(ws.lib(LIB)) and ORG_MARK + b"1" in ws.read_lib(LIB)
        return [("Org-scoped user id answered (platform library linked)", linked and enabled, LIB)]


register(OvrOrgScopedId)
