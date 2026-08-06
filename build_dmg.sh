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
PREVIOUS_BUILD_ROOT="$(mktemp -d /tmp/mycamino-previous-builds.XXXXXX)"
FFMPEG_SOURCE_ARCHIVE="build/third-party-sources/ffmpeg-8.1.1.tar.xz"
LICENSE_BUNDLE="build/license_bundle"
PYINSTALLER_CACHE_OWNED=0
if [[ -z "${PYINSTALLER_CONFIG_DIR:-}" ]]; then
  PYINSTALLER_CONFIG_DIR="$(mktemp -d /tmp/mycamino-pyinstaller-cache.XXXXXX)"
  PYINSTALLER_CACHE_OWNED=1
fi
export PYINSTALLER_CONFIG_DIR
VERIFY_MOUNT=""

cleanup() {
  if [[ -n "$VERIFY_MOUNT" && -d "$VERIFY_MOUNT" ]]; then
    hdiutil detach "$VERIFY_MOUNT" >/dev/null || true
  fi
  rm -rf "$TMP_ROOT"
  rm -rf "$PREVIOUS_BUILD_ROOT"
  if [[ "$PYINSTALLER_CACHE_OWNED" -eq 1 && -d "$PYINSTALLER_CONFIG_DIR" ]]; then
    rm -rf "$PYINSTALLER_CONFIG_DIR"
  fi
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

move_previous_build_product() {
  local product="$1"
  local source="dist/${product}"
  [[ -e "$source" ]] || return 0
  echo "==> Moving previous build product aside: ${source}"
  mv "$source" "$PREVIOUS_BUILD_ROOT/${product}"
}

echo "==> Building app icon"
"$PYTHON" - <<'PY'
from pathlib import Path
from PIL import Image

source = Image.open("MyCaminoLogo-ohneText.png").convert("RGBA")
bbox = source.getbbox()
if bbox:
    source = source.crop(bbox)
size = 1024
canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
target = int(size * 0.96)
scale = min(target / source.width, target / source.height)
resized = source.resize(
    (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
    Image.Resampling.LANCZOS,
)
canvas.alpha_composite(
    resized, ((size - resized.width) // 2, (size - resized.height) // 2)
)
output = Path("build/MyCaminoLogo-ohneText.icns")
output.parent.mkdir(parents=True, exist_ok=True)
canvas.save(output, format="ICNS")
PY

echo "==> Checking Python syntax"
"$PYTHON" -m py_compile \
  application_metadata.py \
  GPSTrackShowGUI.py \
  GPXEditor.py \
  adventure_parameters.py \
  cocoa_parameter_editor.py \
  json_storage.py \
  GetGeoLocations.py \
  gpx_import.py \
  gpx_point_editing.py \
  gpx_routing.py \
  media_track_builder.py \
  gpx_tracks_table.py \
  GPSTrackShow.py \
  video_audio_normalization.py \
  license_resources.py \
  scripts/prepare_license_bundle.py

echo "==> Fetching and verifying corresponding FFmpeg source"
scripts/build_ffmpeg_lgpl.sh --source-only

if [[ ! -x "vendor/ffmpeg/ffmpeg" ]]; then
  echo "==> Building pinned LGPL FFmpeg for normalized video audio"
  scripts/build_ffmpeg_lgpl.sh
fi

echo "==> Preparing licenses and corresponding source"
"$PYTHON" scripts/prepare_license_bundle.py \
  --ffmpeg-source "$FFMPEG_SOURCE_ARCHIVE"

echo "==> Building bundled slide-show player"
move_previous_build_product "GPSTrackShow"
"$PYINSTALLER" --clean --noconfirm GPSTrackShow.spec

echo "==> Building standalone GPX editor"
move_previous_build_product "myCamino GPX Editor"
move_previous_build_product "myCamino GPX Editor.app"
"$PYINSTALLER" --clean --noconfirm "myCamino GPX Editor.spec"

echo "==> Building ${APP_NAME}.app"
move_previous_build_product "$APP_NAME"
move_previous_build_product "${APP_NAME}.app"
"$PYINSTALLER" --clean --noconfirm "${APP_NAME}.spec"

echo "==> Preparing DMG root"
cp -R "dist/${APP_NAME}.app" "$TMP_ROOT/"
cp -R "dist/myCamino GPX Editor.app" "$TMP_ROOT/"
cp "DMG_README.txt" "$TMP_ROOT/README.txt"
cp "$LICENSE_BUNDLE/app_resources/licenses/myCamino/GPL-3.0.txt" \
  "$TMP_ROOT/License — GPL-3.0.txt"
cp "$LICENSE_BUNDLE/app_resources/licenses/myCamino/Third-Party Notices.txt" \
  "$TMP_ROOT/Third-Party Notices.txt"
cp "$LICENSE_BUNDLE/app_resources/licenses/myCamino/Source Code Information.txt" \
  "$TMP_ROOT/Source Code Information.txt"
cp -R "$LICENSE_BUNDLE/app_resources/licenses" "$TMP_ROOT/Licenses"
cp "$LICENSE_BUNDLE"/source/myCamino-source-*.tar.gz "$TMP_ROOT/"
cp "$LICENSE_BUNDLE/source/ffmpeg-8.1.1.tar.xz" "$TMP_ROOT/"
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

echo "==> Mounting DMG and checking distributed license resources"
VERIFY_MOUNT="$(hdiutil attach -nobrowse -readonly "$DMG_PATH" | awk '/\/Volumes\// {sub(/^.*\/Volumes\//, "/Volumes/"); print; exit}')"
[[ -n "$VERIFY_MOUNT" && -d "$VERIFY_MOUNT" ]] || {
  echo "Error: could not determine the mounted DMG path." >&2
  exit 1
}
for required in \
  "$VERIFY_MOUNT/License — GPL-3.0.txt" \
  "$VERIFY_MOUNT/Third-Party Notices.txt" \
  "$VERIFY_MOUNT/Source Code Information.txt" \
  "$VERIFY_MOUNT/myCamino GPS Track Show.app/Contents/Resources/licenses/myCamino/GPL-3.0.txt" \
  "$VERIFY_MOUNT/myCamino GPX Editor.app/Contents/Resources/licenses/myCamino/GPL-3.0.txt"; do
  [[ -f "$required" ]] || {
    echo "Error: required DMG resource is missing: $required" >&2
    exit 1
  }
done
find "$VERIFY_MOUNT" -maxdepth 1 -name 'myCamino-source-*.tar.gz' -type f | grep -q . || {
  echo "Error: the corresponding myCamino source archive is missing." >&2
  exit 1
}
[[ -f "$VERIFY_MOUNT/ffmpeg-8.1.1.tar.xz" ]] || {
  echo "Error: the corresponding FFmpeg source archive is missing." >&2
  exit 1
}
hdiutil detach "$VERIFY_MOUNT" >/dev/null
VERIFY_MOUNT=""

echo "==> Done: ${ROOT_DIR}/${DMG_PATH}"
