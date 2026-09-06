#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYINSTALLER="${PYINSTALLER:-./.venv/bin/pyinstaller}"
PYTHON="${PYTHON:-./.venv/bin/python}"
APP_ID="${MYCAMINO_APP_ID:-org.mycamino}"
APP_IDENTITY="${APPLE_DISTRIBUTION_IDENTITY:-}"
INSTALLER_IDENTITY="${MAC_INSTALLER_IDENTITY:-}"
PROFILE="${MYCAMINO_PROVISIONING_PROFILE:-}"
APP="dist/myCamino GPS Track Show.app"
PKG="dist/myCamino.pkg"

[[ "$APP_ID" == "org.mycamino" ]] || { echo "Error: Store App ID must be org.mycamino." >&2; exit 1; }
[[ -x "$PYINSTALLER" ]] || { echo "Error: PyInstaller is unavailable: $PYINSTALLER" >&2; exit 1; }
[[ -x "$PYTHON" ]] || { echo "Error: Python is unavailable: $PYTHON" >&2; exit 1; }
[[ -n "$APP_IDENTITY" ]] || { echo "Error: set APPLE_DISTRIBUTION_IDENTITY." >&2; exit 1; }
[[ -n "$INSTALLER_IDENTITY" ]] || { echo "Error: set MAC_INSTALLER_IDENTITY." >&2; exit 1; }
[[ -f "$PROFILE" ]] || { echo "Error: set MYCAMINO_PROVISIONING_PROFILE to a valid profile." >&2; exit 1; }

echo "==> Preparing bundled licenses and helper"
scripts/build_ffmpeg_lgpl.sh
"$PYTHON" scripts/prepare_license_bundle.py \
  --project-root "$ROOT_DIR" \
  --output-dir build/license_bundle \
  --ffmpeg-source build/third-party-sources/ffmpeg-8.1.1.tar.xz
MYCAMINO_DISTRIBUTION=app-store "$PYINSTALLER" --clean --noconfirm GPSTrackShow.spec

echo "==> Building sandboxed myCamino"
MYCAMINO_DISTRIBUTION=app-store \
MYCAMINO_CODESIGN_IDENTITY="$APP_IDENTITY" \
MYCAMINO_ENTITLEMENTS="packaging/app-store/myCamino.entitlements" \
"$PYINSTALLER" --clean --noconfirm "myCamino GPS Track Show.spec"

cp "$PROFILE" "$APP/Contents/embedded.provisionprofile"

echo "==> Signing bundled helpers and application"
find "$APP/Contents" -type f -perm -111 -print0 | while IFS= read -r -d '' executable; do
  [[ "$executable" == "$APP/Contents/MacOS/"* ]] && continue
  codesign --force --options runtime --sign "$APP_IDENTITY" \
    --entitlements packaging/app-store/helper.entitlements "$executable"
done
codesign --force --options runtime --sign "$APP_IDENTITY" \
  --entitlements packaging/app-store/myCamino.entitlements "$APP"

"$PYTHON" scripts/check_app_store_compliance.py "$APP"

echo "==> Creating signed upload package"
productbuild --component "$APP" /Applications --sign "$INSTALLER_IDENTITY" "$PKG"
pkgutil --check-signature "$PKG"
echo "Created $PKG"
