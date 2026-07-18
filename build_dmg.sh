#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-./.venv/bin/python}"
PYINSTALLER="${PYINSTALLER:-./.venv/bin/pyinstaller}"
APP_NAME="myCamino GPS Track Show"
DMG_NAME="myCamino-GPS-Track-Show.dmg"
DMG_PATH="dist/${DMG_NAME}"
TMP_ROOT="$(mktemp -d /tmp/mycamino-dmg-root.XXXXXX)"
PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-/tmp/mycamino-pyinstaller-cache}"
export PYINSTALLER_CONFIG_DIR

cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

detach_existing_dmg_mounts() {
  local mount_path
  for mount_path in "/Volumes/${APP_NAME}" "/Volumes/${APP_NAME} "*; do
    [[ -d "$mount_path" ]] || continue
    echo "==> Detaching stale mounted volume: ${mount_path}"
    hdiutil detach "$mount_path" >/dev/null || true
  done
}

echo "==> Building app icon"
"$PYTHON" - <<'PY'
from pathlib import Path
from PIL import Image

source = Image.open("MyCaminoLogo-ohneText.png").convert("RGBA")
bbox = source.getbbox()
if bbox:
    source = source.crop(bbox)
iconset = Path("build/appicon.iconset")
iconset.mkdir(parents=True, exist_ok=True)
for name, size in [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    target = int(size * 0.96)
    scale = min(target / source.width, target / source.height)
    resized = source.resize((max(1, round(source.width * scale)), max(1, round(source.height * scale))), Image.Resampling.LANCZOS)
    canvas.alpha_composite(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
    canvas.save(iconset / name)
PY
iconutil -c icns build/appicon.iconset -o build/MyCaminoLogo-ohneText.icns

echo "==> Checking Python syntax"
"$PYTHON" -m py_compile \
  GPSTrackShowGUI.py \
  GPXEditor.py \
  adventure_parameters.py \
  cocoa_parameter_editor.py \
  json_storage.py \
  GetGeoLocations.py \
  gpx_tracks_table.py \
  GPSTrackShow.py \
  video_audio_normalization.py

if [[ ! -x "vendor/ffmpeg/ffmpeg" ]]; then
  echo "==> Building pinned LGPL FFmpeg for normalized video audio"
  scripts/build_ffmpeg_lgpl.sh
fi

echo "==> Building bundled slide-show player"
"$PYINSTALLER" --noconfirm GPSTrackShow.spec

echo "==> Building standalone GPX editor"
"$PYINSTALLER" --noconfirm "myCamino GPX Editor.spec"

echo "==> Building ${APP_NAME}.app"
"$PYINSTALLER" --noconfirm "${APP_NAME}.spec"

echo "==> Preparing DMG root"
cp -R "dist/${APP_NAME}.app" "$TMP_ROOT/"
cp -R "dist/myCamino GPX Editor.app" "$TMP_ROOT/"
if [[ -f "dist/DMG_README.txt" ]]; then
  cp "dist/DMG_README.txt" "$TMP_ROOT/README.txt"
else
  printf '%s\n' "Drag ${APP_NAME}.app to Applications." > "$TMP_ROOT/README.txt"
fi
ln -s /Applications "$TMP_ROOT/Applications"

detach_existing_dmg_mounts

echo "==> Creating ${DMG_PATH}"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$TMP_ROOT" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "==> Verifying ${DMG_PATH}"
hdiutil verify "$DMG_PATH"

echo "==> Done: ${ROOT_DIR}/${DMG_PATH}"
