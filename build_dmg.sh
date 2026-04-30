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

echo "==> Checking Python syntax"
"$PYTHON" -m py_compile \
  GPSTrackShowGUI.py \
  GPXEditor.py \
  GetGeoLocations.py \
  gpx_tracks_table.py \
  GPSTrackShow.py

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
