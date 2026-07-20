# myCamino beta website

This standalone Django service shares only the existing public Docker network
with the FediPass Caddy container. It has its own image, Compose project,
SQLite database, release directory and deployment lifecycle.

## Local development

```bash
python3 -m venv .venv-web
./.venv-web/bin/pip install -r website/requirements.txt
./.venv-web/bin/python website/manage.py migrate
./.venv-web/bin/python website/manage.py runserver
```

Console email is the development default. Production deployment checks fail
until the legal operator name, postal address and email are configured.

## Production paths

- Compose project: `/opt/mycamino/site`
- Database: `/var/lib/mycamino/database/db.sqlite3`
- Releases: `/var/lib/mycamino/releases`
- Public container network: `fedipass_staging_public`

Caddy authenticates the public DMG route against Django, rewrites it to
`latest.dmg`, and serves the file with HTTP range support. The release
directory has no unprotected public route.
