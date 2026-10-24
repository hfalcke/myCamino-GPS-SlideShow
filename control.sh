#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$root/scripts/ops/mycamino_env.sh"
# Reload panel code on every ordinary launch. Explicit `start` remains useful
# when the existing process must be reopened without interrupting it.
exec python3 -m ops_control.launcher "${1:-restart}"
