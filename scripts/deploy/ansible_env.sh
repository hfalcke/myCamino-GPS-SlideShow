#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$root/scripts/ops/mycamino_env.sh"
export ANSIBLE_HOME="$MYCAMINO_CONFIG_DIR/ansible-state"
export ANSIBLE_LOCAL_TEMP="$MYCAMINO_CONFIG_DIR/ansible-tmp"
export ANSIBLE_SSH_CONTROL_PATH_DIR="$MYCAMINO_CONFIG_DIR/ssh-control"
export ANSIBLE_NOCOLOR=1
mkdir -p "$ANSIBLE_HOME" "$ANSIBLE_LOCAL_TEMP" "$ANSIBLE_SSH_CONTROL_PATH_DIR"
ansible_bin="$root/.venv-ops/bin/ansible-playbook"
[[ -x "$ansible_bin" ]] || { echo 'Run scripts/deploy/setup_ops.sh first.' >&2; exit 2; }
common_args=(-i "$MYCAMINO_CONFIG_DIR/inventory.yml" -e "@$MYCAMINO_CONFIG_DIR/deployment.yml" -e "@$MYCAMINO_CONFIG_DIR/secrets.vault.yml" --vault-password-file "$MYCAMINO_CONFIG_DIR/vault-password.txt")
run_playbook() { "$ansible_bin" "${common_args[@]}" "$@"; }
