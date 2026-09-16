#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$root/scripts/deploy/ansible_env.sh"
if [[ "${MYCAMINO_LOCK_HELD:-0}" != "1" ]]; then
  exec python3 -m ops_control.operation_lock bash "${BASH_SOURCE[0]}" "$@"
fi
run_playbook "$root/infra/ansible/playbooks/mycamino-deploy.yml" "$@"
run_playbook "$root/infra/ansible/playbooks/mycamino-smoke.yml" "$@"
