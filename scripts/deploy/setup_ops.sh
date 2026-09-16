#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python3 -m venv "$root/.venv-ops"
"$root/.venv-ops/bin/pip" install -r "$root/infra/requirements.txt"
