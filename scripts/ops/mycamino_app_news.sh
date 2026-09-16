#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$root/scripts/ops/mycamino_env.sh"
exec python3 -m ops_control.cli news "$@"
