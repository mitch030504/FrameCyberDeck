#!/usr/bin/env fish

# Validate FrameCyberDeck's Steam Frame contribution provenance and packaging
# without contacting vrSrc or invoking rclone.
#
# Usage:
#   scripts/frame-contribution-dry-run.fish [package.name]

set -l package_name $argv[1]
if test -z "$package_name"
    set package_name com.Icosa.OpenBrush
end

set -l config_root "$HOME/.config/frame-cyberdeck"
if set -q XDG_CONFIG_HOME
    set config_root "$XDG_CONFIG_HOME/frame-cyberdeck"
end

set -l registry "$config_root/frame-source-registry.json"
if not test -f "$registry"
    echo "ERROR: provenance registry not found: $registry" >&2
    exit 1
end

set -l fields (python3 - "$registry" "$package_name" <<'PY'
import json
import sys

registry_path, package_name = sys.argv[1], sys.argv[2]
with open(registry_path, "r", encoding="utf-8") as fh:
    registry = json.load(fh)

entry = registry.get(package_name)
if not isinstance(entry, dict):
    raise SystemExit(f"ERROR: no provenance record for {package_name}")

source = entry.get("originalSourcePath")
version = entry.get("versionCode")
game_id = entry.get("gameId")
if not source or version is None or not game_id:
    raise SystemExit(f"ERROR: incomplete provenance record for {package_name}")

print(source)
print(version)
print(game_id)
PY
)
or exit $status

set -l source_path $fields[1]
set -l expected_version $fields[2]
set -l game_id $fields[3]

if not test -f "$source_path"
    echo "ERROR: original source no longer exists: $source_path" >&2
    exit 1
end

if not string match -q -r '\.apk$' -- (string lower "$source_path")
    echo "ERROR: dry-run currently validates direct APK provenance only: $source_path" >&2
    exit 1
end

set -l sdk_roots
if set -q ANDROID_HOME
    set -a sdk_roots "$ANDROID_HOME"
end
if set -q ANDROID_SDK_ROOT
    set -a sdk_roots "$ANDROID_SDK_ROOT"
end
set -a sdk_roots /opt/android-sdk "$HOME/Android/Sdk"

set -l aapt ""
for root in $sdk_roots
    if not test -d "$root/build-tools"
        continue
    end
    set -l candidates (find "$root/build-tools" -maxdepth 2 -type f -name aapt 2>/dev/null | sort -V -r)
    if test (count $candidates) -gt 0
        set aapt $candidates[1]
        break
    end
end

if test -z "$aapt"
    echo "ERROR: Android build-tools aapt not found." >&2
    exit 1
end

set -l badging ("$aapt" dump badging "$source_path" 2>/dev/null)
or begin
    echo "ERROR: aapt could not inspect $source_path" >&2
    exit 1
end

set -l actual_package (string match -r "package: name='([^']+)'" -- "$badging" | string replace -r ".*name='([^']+)'.*" '$1' | head -n1)
set -l actual_version (string match -r "versionCode='([0-9]+)'" -- "$badging" | string replace -r ".*versionCode='([0-9]+)'.*" '$1' | head -n1)
set -l app_label (string match -r "application-label:'([^']+)'" -- "$badging" | string replace -r ".*application-label:'([^']+)'.*" '$1' | head -n1)

if test "$actual_package" != "$package_name"
    echo "ERROR: provenance package mismatch: registry=$package_name apk=$actual_package" >&2
    exit 1
end

if test "$actual_version" != "$expected_version"
    echo "ERROR: provenance version mismatch: registry=$expected_version apk=$actual_version" >&2
    exit 1
end

set -l sevenzip "$PWD/resources/bin/linux/7zzs"
if not test -x "$sevenzip"
    set sevenzip "$config_root/bin/7zzs"
end
if not test -x "$sevenzip"
    echo "ERROR: 7zzs not found in repo or FrameCyberDeck config bin." >&2
    exit 1
end

set -l output_root "$HOME/.cache/frame-cyberdeck/contribution-dry-run"
mkdir -p "$output_root"

set -l staging (mktemp -d "$output_root/staging.XXXXXX")
or exit 1

function cleanup --on-event fish_exit
    if test -n "$staging" -a -d "$staging"
        rm -rf "$staging"
    end
end

cp "$source_path" "$staging/" || exit 1

set -l hwid (printf '%s' "(hostname)-(whoami)" | sha256sum | awk '{print $1}')
printf '%s' "$hwid" > "$staging/HWID.txt"

set -l safe_label "$app_label"
if test -z "$safe_label"
    set safe_label "$package_name"
end
set safe_label (string replace -ar '[<>:"/\\|?*]' '_' -- "$safe_label")
set -l prefix (string sub -s 1 -l 1 "$hwid")
set -l archive "$output_root/$safe_label v$expected_version $package_name $prefix PC.zip"

rm -f "$archive"
"$sevenzip" a -tzip "$archive" "$staging/*" >/dev/null
or begin
    echo "ERROR: failed to create dry-run archive." >&2
    exit 1
end

echo "Frame contribution dry-run: PASS"
echo "  Package:          $package_name"
echo "  Version code:     $expected_version"
echo "  Game ID:          $game_id"
echo "  App label:        $app_label"
echo "  Original source:  $source_path"
echo "  Original SHA256:  "(sha256sum "$source_path" | awk '{print $1}')
echo "  Dry-run archive:  $archive"
echo "  Archive SHA256:   "(sha256sum "$archive" | awk '{print $1}')
echo
echo "Archive contents:"
"$sevenzip" l "$archive" | string match -r '(^| )[^ ]+\.apk$|HWID\.txt'
echo
echo "No network upload was attempted."
