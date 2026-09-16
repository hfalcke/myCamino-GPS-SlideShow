#!/usr/bin/env python3
"""One-time private migration from FediPass; never required by normal operations.

Prepare and verify first. --finalize removes application-owned values from the
old settings only after their new encrypted copies have been checked.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shlex
import tempfile

import yaml
from ansible.parsing.vault import VaultLib, VaultSecret

GATEWAY = {'mycamino_host', 'mycamino_app_root', 'mycamino_data_root'}
SHARED = ['smtp_host', 'smtp_port', 'smtp_use_tls', 'smtp_use_ssl', 'ghcr_username', 'ghcr_token']
SECRET_NAMES = {'mycamino_django_secret_key', 'mycamino_form_ip_salt',
                'mycamino_smtp_password', 'mycamino_smtp_username', 'mycamino_admin_username',
                'mycamino_admin_email', 'mycamino_admin_password', 'mycamino_ghcr_token', 'mycamino_ghcr_username'}


def atomic(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def strip_keys(raw, keys):
    text = raw.decode()
    tree = yaml.compose(text)
    lines = text.splitlines(keepends=True)
    for key, value in reversed(tree.value):
        if key.value in keys:
            end = value.end_mark.line + (1 if value.end_mark.column else 0)
            del lines[key.start_mark.line:end]
    return ''.join(lines).encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-infra', type=Path, required=True)
    parser.add_argument('--destination', type=Path, default=Path.home()/'.config/mycamino')
    parser.add_argument('--source-vault-password', type=Path)
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    dest = args.destination.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest.chmod(0o700)
    source = args.source_infra.resolve()
    paths = {name: source/name for name in ['infra/ansible/group_vars/staging.yml',
             'infra/ansible/group_vars/staging.vault.yml', 'infra/ansible/inventory/staging.yml',
             'secrets/fediops-control.local.env']}
    password = dest/'vault-password.txt'
    if not password.exists():
        if args.finalize:
            raise SystemExit('Prepare the configuration before finalizing.')
        atomic(password, secrets.token_urlsafe(48).encode()+b'\n')
    vault = VaultLib([('default', VaultSecret(password.read_bytes().strip()))])
    old_vault = None
    if args.source_vault_password:
        old_vault = VaultLib([('default', VaultSecret(args.source_vault_password.read_bytes().strip()))])
    originals = {name: p.read_bytes() for name,p in paths.items()}
    plain = {}
    for name, data in originals.items():
        if data.startswith(b'$ANSIBLE_VAULT;'):
            if not old_vault:
                raise SystemExit('Source vault is encrypted; supply --source-vault-password.')
            data = old_vault.decrypt(data)
        plain[name] = data
    combined = {}
    for name in list(paths)[:2]:
        combined.update(yaml.safe_load(plain[name]) or {})
    selected = {k:v for k,v in combined.items() if k.startswith('mycamino_')}
    for name in SHARED:
        if name not in combined:
            raise SystemExit('Missing required deployment key: '+name)
        selected['mycamino_'+name] = combined[name]
    selected['deploy_user'] = combined.get('deploy_user', 'deploy')
    selected['mycamino_public_network'] = combined['docker_network_name']+'_public'
    missing = (SECRET_NAMES | GATEWAY | {'mycamino_image', 'mycamino_operator_name', 'mycamino_operator_address', 'mycamino_operator_email'}) - selected.keys()
    if missing:
        raise SystemExit('Missing required myCamino keys: '+', '.join(sorted(missing)))
    inventory = yaml.safe_load(plain['infra/ansible/inventory/staging.yml'])
    staging = inventory.get('all', {}).get('children', {}).get('staging', inventory.get('staging', {}))
    hosts = staging.get('hosts', {})
    if len(hosts) != 1:
        raise SystemExit('Expected exactly one staging host; resolve inventory explicitly.')
    host_name, host_values = next(iter(hosts.items()))
    host_values = {**inventory.get('all', {}).get('vars', {}), **staging.get('vars', {}), **(host_values or {})}
    host = host_values.get('ansible_host', host_name)
    user = host_values.get('ansible_user', selected['deploy_user'])
    key = host_values.get('ansible_ssh_private_key_file')
    if not key:
        raise SystemExit('Inventory must supply ansible_ssh_private_key_file.')
    key = str(Path(key).expanduser())
    if not Path(key).is_file():
        raise SystemExit('The configured SSH key is not a readable file.')
    new_inventory = {'all': {'children': {'staging': {'hosts': {'mycamino-server': {
        'ansible_host': host, 'ansible_user': user, 'ansible_ssh_private_key_file': key}}}}}}
    public = {k:v for k,v in selected.items() if k not in SECRET_NAMES}
    private = {k:v for k,v in selected.items() if k in SECRET_NAMES}
    expected = {'deployment.yml': public, 'secrets.vault.yml': private, 'inventory.yml': new_inventory}
    for name, data in expected.items():
        p = dest/name
        if p.exists():
            raw = p.read_bytes()
            if name.endswith('.vault.yml'):
                if not raw.startswith(b'$ANSIBLE_VAULT;'):
                    raise SystemExit('Destination secrets are not encrypted.')
                raw = vault.decrypt(raw)
            if yaml.safe_load(raw) != data:
                raise SystemExit('Destination differs; refusing to overwrite '+name)
        elif args.finalize:
            raise SystemExit('Missing prepared file: '+name)
        else:
            raw = yaml.safe_dump(data, sort_keys=True).encode()
            atomic(p, vault.encrypt(raw) if name.endswith('.vault.yml') else raw)
    panel = '\n'.join(k+'='+shlex.quote(str(v)) for k,v in {
        'MYCAMINO_CONTROL_PORT':8788, 'MYCAMINO_SSH_HOST':host, 'MYCAMINO_SSH_USER':user,
        'MYCAMINO_SSH_KEY':key, 'MYCAMINO_DEPLOY_HOST':user+'@'+host}.items())+'\n'
    panel_file = dest/'panel.env'
    if not panel_file.exists():
        atomic(panel_file, panel.encode())
    elif panel_file.read_text() != panel:
        raise SystemExit('Existing panel settings differ; refusing to replace them.')
    # Verify round-trip encryption before any source cleanup.
    assert yaml.safe_load(vault.decrypt((dest/'secrets.vault.yml').read_bytes())) == private
    backup = dest/'migration-backup.vault'
    if not backup.exists():
        packed = {name: base64.b64encode(raw).decode() for name,raw in originals.items()}
        atomic(backup, vault.encrypt(json.dumps(packed).encode()))
    saved = json.loads(vault.decrypt(backup.read_bytes()))
    if any(base64.b64decode(saved.get(name, '')) != raw for name,raw in originals.items()):
        raise SystemExit('Source has changed since backup; refusing automatic cleanup.')
    print('Verified independent configuration and encrypted recovery copy in '+str(dest))
    if args.finalize:
        remove = {k for k in combined if k.startswith('mycamino_')} - GATEWAY
        for name in list(paths)[:2]:
            cleaned = strip_keys(plain[name], remove)
            if originals[name].startswith(b'$ANSIBLE_VAULT;'):
                cleaned = old_vault.encrypt(cleaned)
            atomic(paths[name], cleaned)
        panel_name = 'secrets/fediops-control.local.env'
        lines = plain[panel_name].decode().splitlines(keepends=True)
        cleaned = ''.join(line for line in lines if not line.lstrip().removeprefix('export ').startswith('MYCAMINO_'))
        atomic(paths[panel_name], cleaned.encode())
        print('Removed application settings from FediPass; retained its gateway settings. No service was restarted.')


if __name__ == '__main__':
    main()
