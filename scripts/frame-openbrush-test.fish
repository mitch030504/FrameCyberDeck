#!/usr/bin/env fish
set -l host $FRAME_CYBERDECK_HOST
if test -z "$host"
    if test (count $argv) -ge 1
        set host $argv[1]
    else
        set host frame
    end
end

set -l root (realpath (dirname (status filename))/..)
set -l cache "$HOME/.cache/frame-cyberdeck/openbrush-test"
set -l apk "$cache/OpenBrush_Quest_2.32.0.apk"
set -l converted "$cache/converted"
set -l url "https://github.com/icosa-foundation/open-brush/releases/download/2.32.0/OpenBrush_Quest_2.32.0.apk"
set -l expected "62d5a1b18cfc55d592409f1cda3ee0f23cd6234c87eaa48f73815b2c1b0842b4"

mkdir -p "$cache"

if not test -f "$apk"
    echo "Downloading official Open Brush Quest 2.32.0 build..."
    curl --fail --location --retry 3 --output "$apk.tmp" "$url"; or exit 1
    mv "$apk.tmp" "$apk"
end

set -l actual (sha256sum "$apk" | string split ' ')[1]
if test "$actual" != "$expected"
    echo "Open Brush APK checksum mismatch."
    echo "Expected: $expected"
    echo "Actual:   $actual"
    rm -f "$apk"
    exit 1
end

echo
echo "=== CONVERTER DOCTOR ==="
python3 "$root/resources/frame-convert.py" doctor; or exit 1

echo
echo "=== CONVERT OPEN BRUSH ==="
rm -rf "$converted"
mkdir -p "$converted"
python3 "$root/resources/frame-convert.py" convert     --input "$apk"     --output "$converted"; or exit 1

echo
echo "=== DEVKIT STATUS ==="
python3 "$root/resources/frame-devkit-bridge.py"     --host "$host"     status; or exit 1

echo
echo "=== DEPLOY ==="
python3 "$root/resources/frame-devkit-bridge.py"     --host "$host"     deploy     --name OpenBrush-2.32.0     --directory "$converted"     --start-command game.apk; or exit 1

echo
echo "Open Brush was converted and uploaded. Launch OpenBrush-2.32.0 from the Frame Steam library."
