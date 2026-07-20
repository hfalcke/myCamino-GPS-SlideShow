#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DMG_PATH="${1:-${ROOT_DIR}/dist/myCamino-GPS-Track-Show.dmg}"
LABEL="${2:-Beta — $(date '+%d %B %Y')}"
RELEASE_DATE="${3:-$(date '+%Y-%m-%d')}"
REMOTE_HOST="${MYCAMINO_DEPLOY_HOST:-deploy@staging.fedipass.org}"
SSH_KEY="${MYCAMINO_SSH_KEY:-/Users/falcke/.ssh/fedipass_staging_ed25519}"

[[ -f "$DMG_PATH" ]] || { echo "Missing DMG: $DMG_PATH" >&2; exit 1; }
SHA256="$(shasum -a 256 "$DMG_PATH" | awk '{print $1}')"
SIZE="$(stat -f '%z' "$DMG_PATH")"
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
REMOTE_NAME="myCamino-GPS-Track-Show-${STAMP}.dmg"
REMOTE_TMP="/var/lib/mycamino/releases/.${REMOTE_NAME}.upload"
REMOTE_FINAL="/var/lib/mycamino/releases/${REMOTE_NAME}"

echo "Publishing ${LABEL} (${SIZE} bytes, ${SHA256})"
scp -i "$SSH_KEY" "$DMG_PATH" "${REMOTE_HOST}:${REMOTE_TMP}"
ssh -i "$SSH_KEY" "$REMOTE_HOST" bash -s -- "$REMOTE_TMP" "$REMOTE_FINAL" "$REMOTE_NAME" "$SHA256" "$SIZE" "$LABEL" "$RELEASE_DATE" <<'REMOTE'
set -euo pipefail
tmp="$1" final="$2" name="$3" expected_sha="$4" expected_size="$5" label="$6" release_date="$7"
actual_sha="$(sha256sum "$tmp" | awk '{print $1}')"
actual_size="$(stat -c '%s' "$tmp")"
[[ "$actual_sha" == "$expected_sha" && "$actual_size" == "$expected_size" ]]
mv "$tmp" "$final"
cd /opt/mycamino/site
docker compose exec -T web python manage.py register_release "/releases/$name" --label "$label" --date "$release_date"
ln -sfn "$name" /var/lib/mycamino/releases/.latest.dmg.next
mv -Tf /var/lib/mycamino/releases/.latest.dmg.next /var/lib/mycamino/releases/latest.dmg
REMOTE
echo "Published https://mycamino.heinofalcke.de/downloads/myCamino-GPS-Track-Show.dmg"
