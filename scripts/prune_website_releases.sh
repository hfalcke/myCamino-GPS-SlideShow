#!/usr/bin/env bash
set -euo pipefail

RELEASE_DIR="${1:?Usage: prune_website_releases.sh RELEASE_DIR ACTIVE_DMG [PREVIOUS_DMG]}"
ACTIVE_DMG="${2:?Usage: prune_website_releases.sh RELEASE_DIR ACTIVE_DMG [PREVIOUS_DMG]}"
PREVIOUS_DMG="${3:-}"
[[ -d "$RELEASE_DIR" ]] || { echo "Release directory does not exist: $RELEASE_DIR" >&2; exit 1; }
[[ -f "$ACTIVE_DMG" ]] || { echo "Active DMG does not exist: $ACTIVE_DMG" >&2; exit 1; }

RELEASE_DIR="$(cd "$RELEASE_DIR" && pwd -P)"
ACTIVE_DIR="$(cd "$(dirname "$ACTIVE_DMG")" && pwd -P)"
ACTIVE_DMG="${ACTIVE_DIR}/$(basename "$ACTIVE_DMG")"
[[ "$ACTIVE_DIR" == "$RELEASE_DIR" ]] || {
  echo "Active DMG must be directly inside the release directory." >&2
  exit 1
}
[[ "$(basename "$ACTIVE_DMG")" =~ ^myCamino-GPS-Track-Show-[0-9]{8}T[0-9]{6}Z\.dmg$ ]] || {
  echo "Active DMG does not have a managed release filename." >&2
  exit 1
}

if [[ -n "$PREVIOUS_DMG" ]]; then
  [[ -f "$PREVIOUS_DMG" ]] || { echo "Previous DMG does not exist: $PREVIOUS_DMG" >&2; exit 1; }
  PREVIOUS_DIR="$(cd "$(dirname "$PREVIOUS_DMG")" && pwd -P)"
  PREVIOUS_DMG="${PREVIOUS_DIR}/$(basename "$PREVIOUS_DMG")"
  [[ "$PREVIOUS_DIR" == "$RELEASE_DIR" ]] || {
    echo "Previous DMG must be directly inside the release directory." >&2
    exit 1
  }
  [[ "$(basename "$PREVIOUS_DMG")" =~ ^myCamino-GPS-Track-Show-[0-9]{8}T[0-9]{6}Z\.dmg$ ]] || {
    echo "Previous DMG does not have a managed release filename." >&2
    exit 1
  }
fi

# Delete only completed artifacts with the exact publisher-generated name.
# Managed names cannot contain newlines.
while IFS= read -r candidate; do
  base="$(basename "$candidate")"
  [[ "$base" =~ ^myCamino-GPS-Track-Show-[0-9]{8}T[0-9]{6}Z\.dmg$ ]] || continue
  [[ "$candidate" == "$ACTIVE_DMG" ]] && continue
  [[ -n "$PREVIOUS_DMG" && "$candidate" == "$PREVIOUS_DMG" ]] && continue
  rm -- "$candidate"
  echo "Removed superseded release: $base"
done < <(
  find "$RELEASE_DIR" -maxdepth 1 -type f \
    -name 'myCamino-GPS-Track-Show-*.dmg' -print
)
