"""AndroidManifest.xml fixes: LAUNCHER category, debuggable flag, Meta-only permissions."""
from __future__ import annotations

from ...apk import axml
from ..base import ApkContext, Param, Patch, Suggestion, register

MANIFEST = "AndroidManifest.xml"


class Launcher(Patch):
    id = "frame.launcher"
    a32 = True  # also for 32-bit games on the bridge
    needs_vr = False
    title = "Launcher category (INFO → LAUNCHER)"
    description = ("Lepton only launches an activity with category LAUNCHER (Quest apps often use INFO). "
                   "Symptom when missing: launch.log says 'APP_ACTIVITY is empty' and nothing starts.")
    order = 20
    default_on = True
    package_revisions = {"com.flycast.emulator.vr": 2}  # 2: a launcher on an activity-alias moves to its activity

    def detect(self, a):
        if a.has_info_category:
            return Suggestion(True, "Manifest uses category INFO only; Lepton needs LAUNCHER.")
        return Suggestion(True, "Applied automatically if the manifest needs it.")

    def apply(self, ctx: ApkContext) -> bool:
        fixed = axml.fix_launcher(ctx.ws.read(MANIFEST))
        if fixed:
            ctx.ws.put(MANIFEST, fixed)
        # the only launcher is an <activity-alias>: Lepton starts activities only (GitHub #400)
        moved = axml.launcher_from_alias(ctx.ws.read(MANIFEST))
        if moved:
            ctx.ws.put(MANIFEST, moved)
            ctx.notes.append("launcher moved from an activity-alias to its target activity")
        return bool(fixed or moved)

    def validate(self, ctx):
        manifest = ctx.ws.read(MANIFEST)
        return [("Launchable in Lepton", axml.launcher_on_activity(manifest),
                 "an activity with category LAUNCHER (an activity-alias alone isn't started)")]


class NoDebuggable(Patch):
    id = "frame.nodebug"
    needs_vr = False
    title = "Clear android:debuggable"
    description = ("OVRPort marks apps debuggable; that turns on CheckJNI, which aborts some Unreal games on sloppy "
                   "JNI calls ('JNI DETECTED ERROR', for example 'GetStringUTFChars ... NULL' in Time Stall). Clear "
                   "it for those.")
    order = 21

    def detect(self, a):
        from ..applicability import is_unreal, unreal_version

        if not is_unreal(a):
            return None
        v = unreal_version(a)
        if v and v < (4, 22):
            return Suggestion(True, f"Unreal Engine {v[0]}.{v[1]}: older UE4 makes JNI calls CheckJNI rejects "
                                    "(for example Time Stall aborted on GetStringUTFChars(NULL)).")
        if any(lib.startswith("libmetaxraudio") for lib in a.libs):
            return Suggestion(True, "Unreal build of Meta XR Audio: its telemetry lookup leaves a pending JNI "
                                    "exception that CheckJNI turns into an abort (for example NOPE Challenge).")
        return Suggestion(False, "Enable if the game aborts with 'JNI DETECTED ERROR' (CheckJNI).")

    def applies(self, a):
        return a.engine in ("Unreal", "Other", "CryEngine")

    def apply(self, ctx):
        fixed = axml.set_bool_attr(ctx.ws.read(MANIFEST), "application", "debuggable", False)
        if fixed:
            ctx.ws.put(MANIFEST, fixed)
        return bool(fixed)


class MetaPermissions(Patch):
    id = "frame.meta_permissions"
    title = "Declare Meta-only permissions"
    description = ("Declares com.oculus.permission.* / horizonos.permission.* that the app uses, so Android grants "
                   "them at install (for example the 'use spatial data' permission of mixed-reality games like "
                   "Demeter).")
    order = 22

    def detect(self, a):
        from ..applicability import needs_scene, uses_scene

        if needs_scene(a):
            return Suggestion(True, "Mixed-reality game that needs the room model: its 'use spatial data' "
                                    "permission must be granted (for example Demeter).")
        if uses_scene(a):  # gets the room (scene_emul) too: without the permission it never asks for it
            return Suggestion(True, "Uses Meta's room or anchor APIs: their permissions must be granted, or the "
                                    "game never asks for the room (for example NOPE's MR mode).")
        perms = a.meta_permissions or (a.extra or {}).get("meta_permissions_used") or []
        scene = [p for p in perms if any(k in p for k in ("SCENE", "ANCHOR", "SPATIAL", "BOUNDARY"))]
        if scene:
            names = ", ".join(p.rsplit(".", 1)[-1] for p in scene[:3])
            return Suggestion(True, "Uses Meta scene/spatial data permissions (" + names
                              + "); declared so Android grants spatial data access at install.")
        return None

    def applies(self, a):
        return bool(a.meta_permissions or a.extra.get("meta_permissions_used"))

    def apply(self, ctx):
        fixed = axml.define_meta_permissions(ctx.ws.read(MANIFEST))
        if fixed:
            ctx.ws.put(MANIFEST, fixed[0])
            ctx.notes.append(f"declared {len(fixed[1])} permission(s)")
        return bool(fixed)


class StartActivity(Patch):
    id = "frame.start_activity"
    needs_vr = False
    title = "Start straight in VR (skip the 2D launcher)"
    description = ("Some apps open a flat Android launcher that then starts a separate VR activity (for example "
                   "WiiCompiled). Shown with Lepton's flat window, that window stays in view after the VR part "
                   "starts. This makes the VR activity the one Lepton starts (its LAUNCHER category), so the app "
                   "opens in VR with no flat window. Everything only the launcher does (choosing or importing "
                   "content) is skipped: turn it off for a run when you need the launcher, and turn off \"Show the "
                   "app's Android window\".")
    order = 22
    params = [Param("activity", "str", "", "Activity to start (empty: the app's VR activity)")]

    def applies(self, a):
        return bool((a.extra or {}).get("vr_activity"))

    def detect(self, a):
        vr = (a.extra or {}).get("vr_activity")
        if not vr:
            return None
        return Suggestion(False, f"The app opens a 2D launcher; its VR part is {vr.rsplit('.', 1)[-1]}. Turn on to "
                                 "start that directly once the app is set up.")

    def apply(self, ctx: ApkContext) -> bool:
        manifest = ctx.ws.read(MANIFEST)
        # the original APK's VR activity (analysis): after OVRPort's conversion every MAIN activity has the VR
        # category, so the converted manifest no longer tells them apart
        activity = ((ctx.params or {}).get("activity") or (ctx.analysis.extra or {}).get("vr_activity")
                    or axml.vr_activity(manifest))
        if not activity:
            ctx.notes.append("no VR activity found")
            return False
        fixed = axml.set_start_activity(manifest, activity)
        if fixed:
            ctx.ws.put(MANIFEST, fixed)
            ctx.notes.append(f"starts {activity}")
        return bool(fixed)

    def validate(self, ctx):
        manifest = ctx.ws.read(MANIFEST)
        names = axml.Axml(manifest).strings()
        starts = [n for el, n, items in axml.component_filters(manifest) if el in ("activity", "activity-alias")
                  and any(k == "category" and names[i] == axml.LAUNCHER for k, _, i in items)]
        wanted = (ctx.params or {}).get("activity") or (ctx.analysis.extra or {}).get("vr_activity") or ""
        ok = len(starts) == 1 and wanted.rsplit(".", 1)[-1] == starts[0].rsplit(".", 1)[-1]
        return [("Only the VR activity is a launcher", ok, ", ".join(starts))]


class MainProcess(Patch):
    id = "frame.main_process"
    a32 = True
    needs_vr = False
    title = "Start the game in the app's main process"
    description = ("Some apps run their game activity in a process of its own (android:process, e.g. Halo CE VR's "
                   "\":halo_game\"), started from a 2D launcher activity. Lepton starts the game activity directly "
                   "and watches only the app's main process, which then never exists: about a minute in, Lepton "
                   "decides the app \"did not start\" and closes it, at whatever point the game has reached. This "
                   "runs those activities in the main process (only their android:process value changes).")
    order = 21
    default_on = True

    def applies(self, a):
        return bool((a.extra or {}).get("separate_process"))

    def detect(self, a):
        found = (a.extra or {}).get("separate_process")
        if not found:
            return None
        return Suggestion(True, f"{found[0].rsplit('.', 1)[-1]} runs in its own process, which Lepton doesn't "
                                "watch: it would close the game about a minute in.")

    def apply(self, ctx: ApkContext) -> bool:
        manifest = ctx.ws.read(MANIFEST)
        fixed = axml.set_main_process(manifest, axml.separate_process_activities(manifest))
        if fixed:
            ctx.ws.put(MANIFEST, fixed)
        return bool(fixed)

    def validate(self, ctx):
        left = axml.separate_process_activities(ctx.ws.read(MANIFEST))
        return [("Game activities run in the main process", not left, ", ".join(left) or "all")]


register(Launcher)
register(NoDebuggable)
register(MetaPermissions)
register(StartActivity)
register(MainProcess)
