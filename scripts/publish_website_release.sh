#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DMG_PATH="${1:-${ROOT_DIR}/dist/myCamino-GPS-Track-Show.dmg}"
METADATA_PYTHON="${PYTHON:-${ROOT_DIR}/.venv/bin/python}"
[[ -x "$METADATA_PYTHON" ]] || { echo "Python is not executable: $METADATA_PYTHON" >&2; exit 1; }
DEFAULT_LABEL="$(PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "$METADATA_PYTHON" -c 'from application_metadata import full_version_label; print(full_version_label())')"
DEFAULT_RELEASE_DATE="$(PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "$METADATA_PYTHON" -c 'from application_metadata import APP_RELEASE_DATE; print(APP_RELEASE_DATE)')"
LABEL="${2:-${DEFAULT_LABEL}}"
RELEASE_DATE="${3:-${DEFAULT_RELEASE_DATE}}"
REMOTE_HOST="${MYCAMINO_DEPLOY_HOST:-deploy@staging.fedipass.org}"
SSH_KEY="${MYCAMINO_SSH_KEY:-/Users/falcke/.ssh/fedipass_staging_ed25519}"
PRUNER="${ROOT_DIR}/scripts/prune_website_releases.sh"

[[ -f "$DMG_PATH" ]] || { echo "Missing DMG: $DMG_PATH" >&2; exit 1; }
[[ -f "$SSH_KEY" ]] || { echo "Missing SSH key: $SSH_KEY" >&2; exit 1; }
[[ -f "$PRUNER" ]] || { echo "Missing release-pruning helper: $PRUNER" >&2; exit 1; }
command -v scp >/dev/null 2>&1 || { echo "scp is required." >&2; exit 1; }
command -v ssh >/dev/null 2>&1 || { echo "ssh is required." >&2; exit 1; }
command -v base64 >/dev/null 2>&1 || { echo "base64 is required." >&2; exit 1; }
SHA256="$(shasum -a 256 "$DMG_PATH" | awk '{print $1}')"
SIZE="$(stat -f '%z' "$DMG_PATH")"
LABEL_B64="$(printf '%s' "$LABEL" | base64 | tr -d '\n')"
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
REMOTE_NAME="myCamino-GPS-Track-Show-${STAMP}.dmg"
REMOTE_TMP="/var/lib/mycamino/releases/.${REMOTE_NAME}.upload"
REMOTE_FINAL="/var/lib/mycamino/releases/${REMOTE_NAME}"
REMOTE_PRUNER="/var/lib/mycamino/releases/.prune-${STAMP}.sh"

echo "Publishing ${LABEL} (${SIZE} bytes, ${SHA256})"
scp -i "$SSH_KEY" "$DMG_PATH" "${REMOTE_HOST}:${REMOTE_TMP}"
scp -i "$SSH_KEY" "$PRUNER" "${REMOTE_HOST}:${REMOTE_PRUNER}"
ssh -i "$SSH_KEY" "$REMOTE_HOST" bash -s -- "$REMOTE_TMP" "$REMOTE_FINAL" "$REMOTE_NAME" "$SHA256" "$SIZE" "$LABEL_B64" "$RELEASE_DATE" "$REMOTE_PRUNER" <<'REMOTE'
set -euo pipefail
tmp="$1" final="$2" name="$3" expected_sha="$4" expected_size="$5" label_b64="$6" release_date="$7" pruner="$8"
metadata_activated=0
reconcile_on_exit() {
  status="$?"
  trap - EXIT HUP INT TERM
  rm -f -- "$pruner"
  if [[ "$metadata_activated" -eq 1 ]]; then
    ln -sfn "$name" /var/lib/mycamino/releases/.latest.dmg.next
    mv -Tf /var/lib/mycamino/releases/.latest.dmg.next /var/lib/mycamino/releases/latest.dmg
  fi
  exit "$status"
}
trap reconcile_on_exit EXIT HUP INT TERM
label="$(printf '%s' "$label_b64" | base64 --decode)"
actual_sha="$(sha256sum "$tmp" | awk '{print $1}')"
actual_size="$(stat -c '%s' "$tmp")"
[[ "$actual_sha" == "$expected_sha" && "$actual_size" == "$expected_size" ]]
mv "$tmp" "$final"
chmod 0644 "$final"
cd /opt/mycamino/site
previous_dmg=""
if [[ -e /var/lib/mycamino/releases/latest.dmg ]]; then
  previous_dmg="$(readlink -f /var/lib/mycamino/releases/latest.dmg)"
fi
echo "Registering release metadata and computing the server-side checksum ..."
# The SSH script itself arrives on standard input. docker compose exec also
# attaches standard input unless redirected, which would consume every line
# below this command and silently skip activation while returning success.
register_output="$(docker compose exec -T web python manage.py register_release "/releases/$name" --label "$label" --date "$release_date" </dev/null)"
metadata_activated=1
ln -sfn "$name" /var/lib/mycamino/releases/.latest.dmg.next
mv -Tf /var/lib/mycamino/releases/.latest.dmg.next /var/lib/mycamino/releases/latest.dmg
[[ "$(readlink -f /var/lib/mycamino/releases/latest.dmg)" == "$final" ]]
printf '%s\n' "$register_output"
bash "$pruner" /var/lib/mycamino/releases "$final" "$previous_dmg"
echo "Release activation complete: $name"
REMOTE
ACTIVE_SHA_LINE="$(ssh -i "$SSH_KEY" "$REMOTE_HOST" "sha256sum /var/lib/mycamino/releases/latest.dmg")"
ACTIVE_SHA="${ACTIVE_SHA_LINE%% *}"
[[ "$ACTIVE_SHA" == "$SHA256" ]] || {
  echo "Release publication failed: latest.dmg has SHA-256 ${ACTIVE_SHA:-unavailable}, expected $SHA256." >&2
  exit 1
}
echo "Verified active website DMG: ${ACTIVE_SHA}"
echo "Published https://mycamino.heinofalcke.de/downloads/myCamino-GPS-Track-Show.dmg"
