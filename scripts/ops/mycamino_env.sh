#!/usr/bin/env bash
# Source from myCamino scripts; private values never live in the repository.
mycamino_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export MYCAMINO_CONFIG_DIR="${MYCAMINO_CONFIG_DIR:-${HOME}/.config/mycamino}"
if [[ -f "$MYCAMINO_CONFIG_DIR/panel.env" ]]; then
  set -a
  source "$MYCAMINO_CONFIG_DIR/panel.env"
  set +a
fi
export MYCAMINO_REPO_ROOT="$mycamino_root"
cd "$mycamino_root"
