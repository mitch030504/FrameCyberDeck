"""Apps that look for a web browser before opening a page (Plex's sign-in, GitHub #466) say "no browser installed" in
Lepton: its Android has no browser. Lepton itself hands every VIEW intent to Steam's browser
(services.jar `ActivityStarter.execute`: any intent with action VIEW writes `steam://openurl/<data>` to
`/lepton/steam.pipe` and starts nothing), but such an app never fires the intent.

This patch adds a browser to the app's own APK: an exported activity with a VIEW + DEFAULT + BROWSABLE filter for
http/https (no host, so the app's own deep links keep their more specific filters) and its class in an extra
classesN.dex (`native/java-handoff`, artifact `dex/browser-handoff.dex`). The app's browser check now finds it, the
app starts the VIEW intent and Lepton's hand-off sends the page to Steam's browser before any activity is resolved.
If the activity is ever started anyway, it writes the same pipe line itself and closes (Theme.NoDisplay). No second
package is installed (a second APK in a Lepton container corrupts the main app, see DEVICE_NOTES)."""
from __future__ import annotations

import re

from ...apk import axml
from ...core.paths import artifacts_dir
from ..base import ApkContext, Patch, Suggestion, register

ACTIVITY = "org.frameport.handoff.BrowserHandoff"
DEX_CLASS = b"Lorg/frameport/handoff/BrowserHandoff;"
ARTIFACT = "dex/browser-handoff.dex"


class BrowserHandoff(Patch):
    id = "frame.browser_handoff"
    title = "Browser for sign-ins: open pages in Steam's browser"
    description = ("Lepton's Android has no web browser. Apps that check for one before opening a page (for example "
                   "Plex's sign-in) say no browser is installed and stop. This adds a small browser entry to the "
                   "app itself (an activity for http/https links in its manifest and an extra classes.dex): the "
                   "app's check finds it, and the page opens in Steam's browser, where Lepton sends every link. "
                   "Whether the app notices the finished sign-in depends on the app (a code or PIN it polls for "
                   "works; a sign-in that returns to the app through a link doesn't).")
    order = 45
    needs_vr = False  # mostly 2D apps (sign-ins); a VR app's check is fixed the same way
    a32 = True  # Java only

    def applies(self, a):
        return a.vr_kind == "none" or bool((a.extra or {}).get("browser_query"))

    def detect(self, a):
        if (a.extra or {}).get("browser_query"):
            return Suggestion(False, "The app looks for a web browser (its manifest asks Android for apps that open "
                                     "http/https links), and Lepton has none. Turn on if it says no browser is "
                                     "installed, e.g. when signing in.")
        return None

    def apply(self, ctx: ApkContext) -> bool:
        ws = ctx.ws
        dexes = sorted((n for n in ws.names() if re.fullmatch(r"classes\d*\.dex", n)),
                       key=lambda n: int(n[7:-4] or 1))
        if any(DEX_CLASS in ws.read(n) for n in dexes):
            return False
        manifest = axml.add_browser_activity(ws.read("AndroidManifest.xml"), ACTIVITY)
        if manifest is None:
            return False
        ws.put("AndroidManifest.xml", manifest)
        name = f"classes{len(dexes) + 1}.dex" if dexes else "classes.dex"
        ws.put(name, (artifacts_dir() / ARTIFACT).read_bytes())
        ctx.notes.append(f"browser stand-in {ACTIVITY} added ({name}): links open in Steam's browser")
        return True

    def validate(self, ctx: ApkContext):
        own = axml.browser_activities(ctx.ws.read("AndroidManifest.xml"))
        if ACTIVITY in own:
            return [("Browser stand-in", True, "http/https links resolve to the app's stand-in; Lepton sends them "
                                               "to Steam's browser")]
        return [("Browser stand-in", False, "the stand-in activity is missing from the manifest")]


register(BrowserHandoff)
