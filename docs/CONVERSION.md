# Conversion

Frame CyberDeck converts a compatible Quest APK into a Frame build, stages its
data, and deploys it through SteamOS Devkit. Conversion is not a guarantee that
a title works; compatibility is per title and per build.

## Pipeline

1. Inspect the source APK and preserve package, version, and title metadata.
2. Apply the selected engine's build-stage patches and FrameBridge adapter.
3. Add the matching shims and recipe settings when the title has a recipe.
4. Align, sign, and verify the converted APK.
5. Stage the APK and its data in the layout the Devkit deployment expects.
6. Deploy, register the managed title, and make it available to launch or
   uninstall.

The original source APK is never overwritten.

## Engines

Open **Other Settings → Steam Frame Conversion** to choose the engine.

| Setting | Behavior |
| --- | --- |
| **Automatic (default)** | Uses the FramePort engine when its files, venv, and tools are ready. Otherwise uses the built-in converter. If a FramePort conversion fails, `auto` logs the failure and falls back to the built-in converter for that attempt. |
| **FramePort engine** | Requires a prepared engine. Does not silently fall back; a failure is reported. |
| **Built-in converter** | Uses the pinned FrameBridge adapter and OVRPort CLI 1.2.5. Suitable when the FramePort environment cannot be prepared. |

### FramePort engine

The vendored engine is FramePort 1.4.1 at commit
`72c71fbbd303886992ab62cbc1aea6287100f935`, under `vendor/frameport/`. It runs
FramePort's build-stage pipeline, including its recipes, shims, and patch sets.
It does not add FramePort's install-stage features to this app's deployment
path.

Prepare it once:

1. Open **Other Settings → Steam Frame Conversion**.
2. Select **FramePort engine** or leave **Automatic**.
3. Choose **Prepare FramePort engine** and wait for Java, OVRPort, and apksigner
   to report installed.
4. Use **Refresh engine status** after a failed or interrupted prepare.

Preparation creates:

```text
<userData>/frameport-engine/
  venv/     Python 3.11+ virtual environment and pinned requirements
  home/     FramePort home and tool state
  builds/   Scratch build output
```

Requirements:

- 64-bit Python 3.11+ with `venv` and pip.
- Network access for the first prepare (Python packages and conversion tools).
- Free disk space for the environment, tools, and converted output.

The engine downloads its own Java, OVRPort (the latest release resolved by its
toolchain), and apksigner. It does not use the host JDK or Android SDK.

### Built-in converter

The built-in path requires:

- Python 3.
- JDK 21 or newer.
- Android SDK build-tools containing `aapt`, `zipalign`, and `apksigner`.
- `ANDROID_SDK_ROOT` and `JAVA_HOME` when the tools are not in standard
  locations.

It uses OVRPort CLI 1.2.5 and a prebuilt FrameBridge adapter pinned to the
FramePort revision and verified against FramePort's SHA-256 manifest. It does
not include FramePort's IL2CPP patch stage, which needs Cpp2IL/.NET per title.

## Recipes and shims

FramePort's catalog at the pin contains 291 entries. Frame CyberDeck distills
194 actionable recipes into `resources/frameport-recipes.json`. Entries with
neither adapter settings nor an alternate OVRPort patch set are omitted because
there is nothing for the converter to apply.

For a title with a recipe, conversion can:

- Merge adapter settings into the APK's settings library, including
  engine-bug fixes such as pose timing, controller vibration, and shader fixes.
- Select the recipe's alternate OVRPort patch set.
- Add the matching XR shim, Vulkan shim, OpenGL ES shader-fix layer, or GL flow
  where the recipe requires it.

Recipe suggestions for scale and refresh rate do not override your saved
choices. A recipe is a tested configuration, not a guarantee that the title
works on every Frame runtime or build.

## Refresh rate

The conversion setting offers:

- Runtime default (0)
- 72, 80, 90, 96, 108, 120, or 144 Hz

Runtime default is the safest choice. A requested rate is used only when the
Frame runtime reports that mode as supported. Setting Runtime default also
clears a refresh rate previously stored in the title's recipe, so an older
conversion does not keep the old rate.

For development, the saved setting can be overridden:

```fish
set -x FRAME_CYBERDECK_REFRESH_RATE 90
```

Unsupported values are ignored and the runtime default is used.

## Outputs and data

The converted output contains the signed APK and the release's data files. Both
Quest data layouts are supported:

- Classic `*.<package>.obb` files.
- Raw bundle files inside a package-named folder, such as Unity asset-bundle
  releases.

Staging preserves the folder structure so raw-bundle titles install with their
full data. Managed installs track package, version, title, and the original
source path.

## Provenance

For managed Frame installs, Frame CyberDeck records which original Quest
payload produced the installed build. Contribution workflows use the original
APK or folder, including matching OBB files.

- Installing a converted APK alone does not create OBB provenance.
- A single-APK install made before provenance was recorded must be reinstalled
  from the original source before contributing.
- Original local ZIP files are retained after upload.

A local validation tool checks package/version identity, provenance, HWID
metadata, and archive creation without uploading:

```fish
fish scripts/frame-contribution-dry-run.fish com.example.game
```

## Install-stage limits

The FramePort engine is a **build-stage** engine in this application. Frame
CyberDeck does not apply FramePort's install-stage hardware video decode,
device-side settings, or per-game room provisioning. The deployment path uses
its own `launch.sh`, Lepton environment, and Devkit staging.

Room capture is available separately through the on-Frame agent, but captured
rooms are not copied into individual MR title installs. See
[ROOM_SCAN.md](ROOM_SCAN.md).

## Failure handling

- A missing or incomplete FramePort environment is reported before conversion.
- An explicit FramePort failure does not fall back; switch to `auto` or the
  built-in converter if you want a fallback.
- A failed conversion does not overwrite the source APK.
- Deployment failures leave the managed title state as reported by Devkit; retry
  after correcting pairing, Lepton, or storage problems.

For prerequisites and troubleshooting, see [INSTALL.md](INSTALL.md). For
current limits, see [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).
