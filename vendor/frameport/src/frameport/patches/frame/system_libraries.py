"""Android's optional shared libraries back in the manifest (GitHub #484, YouTube VR).

An app names the shared Java libraries it uses with `<uses-library>` in its manifest; Android then puts them on the
app's class path. OVRPort's `patch_remove_uses_library` (on by default) drops every `<uses-library>` and
`<uses-native-library>` but libopenxr.google.so, because Quest-only ones (for example Meta's system libraries) would
block the install. That also drops the ones Lepton's Android 11 has, so an app that uses one of them starts without it:
YouTube VR names `org.apache.http.legacy` (Android 9+ gives it to apps targeting API 28+ only on request) and dies at
start with `ClassNotFoundException: org.apache.http.protocol.HttpRequestHandler`.

This patch puts back the entries of the original manifest (read at analysis: `extra.uses_libraries`) that Lepton
provides (`LEPTON_LIBRARIES`, the libraries in its /system/etc/permissions), always with `android:required="false"`
so a library can never stop the install. Builds of apps without such an entry are unchanged."""
from __future__ import annotations

from ...apk import axml
from ..base import ApkContext, Patch, Suggestion, register

MANIFEST = "AndroidManifest.xml"

# Shared libraries Lepton's Android 11 provides (/system/etc/permissions/*.xml in its image). The one place this list
# lives: detect(), apply() and the triage signature's classes (catalog/triage.yaml `system-library-missing`) follow it.
LEPTON_LIBRARIES = (
    "org.apache.http.legacy",
    "android.test.base",
    "android.test.mock",
    "android.test.runner",
    "javax.obex",
    "com.android.location.provider",
    "com.android.mediadrm.signer",
    "com.android.media.remotedisplay",
    "com.android.future.usb.accessory",
    "android.net.ipsec.ike",
)


def wanted(analysis) -> list[str]:
    """The Lepton-provided libraries the original manifest names, in its order."""
    names = (analysis.extra or {}).get("uses_libraries") or []
    return [name for name in names if name in LEPTON_LIBRARIES]


class KeepSystemLibraries(Patch):
    id = "frame.keep_system_libraries"
    title = "Keep Android's shared libraries the app uses"
    description = ("OVRPort removes every <uses-library> entry from the manifest (Quest-only libraries would stop "
                   "the install), including ones the Frame's Android has. An app that needs one of those then can't "
                   "find its classes and closes at start (for example YouTube VR: ClassNotFoundException "
                   "org.apache.http.protocol.HttpRequestHandler, from org.apache.http.legacy). This puts back the "
                   "entries for libraries the Frame provides, marked optional.")
    order = 23
    default_on = True  # suggested only where the original manifest names such a library (detect)

    def detect(self, a):
        libs = wanted(a)
        if libs:
            return Suggestion(True, "The app asks for Android's " + ", ".join(libs) + " library, which OVRPort "
                                    "removes from the manifest; without it the app can't load those classes.")
        return None

    def applies(self, a):
        # OVRPort doesn't run on 2D apps or 32-bit games on the bridge: their manifests keep the entries
        return bool(wanted(a)) and a.vr_kind != "none"

    def apply(self, ctx: ApkContext) -> bool:
        libs = wanted(ctx.analysis)
        if not libs:
            return False
        manifest = ctx.ws.read(MANIFEST)
        fixed = axml.add_uses_libraries(manifest, libs)
        if fixed is None:
            return False
        ctx.ws.put(MANIFEST, fixed)
        added = [lib for lib in libs if lib not in axml.uses_libraries(manifest)]
        ctx.notes.append("shared libraries declared again: " + ", ".join(added))
        return True

    def validate(self, ctx: ApkContext):
        libs = wanted(ctx.analysis)
        if not libs:
            return []
        missing = [lib for lib in libs if lib not in axml.uses_libraries(ctx.ws.read(MANIFEST))]
        if missing:
            return [("Shared libraries", False, "not declared: " + ", ".join(missing))]
        return [("Shared libraries", True, "declared: " + ", ".join(libs))]


register(KeepSystemLibraries)
