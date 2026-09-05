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

The repository-level `release.sh` automatically invokes
`scripts/publish_website_release.sh` after the source branch has been pushed.
The publisher uploads to a temporary name, verifies size and SHA-256 on the
server, registers the release, and only then switches `latest.dmg`. After that
successful switch it keeps the active DMG and the DMG that was active directly
before the switch, and removes all other timestamped DMG files. Historical
release metadata and download records remain in Django; unexpected files are
never pruned.

Application version and release-date metadata live in
`application_metadata.py`. Update that file for a new program version before
running `release.sh`. The release script reads those values, verifies the
built macOS bundle version, and explicitly supplies the release label and date
to the website publisher. The values therefore appear consistently in the
GUI, macOS bundle, and website release record.
## Application news and updates

The macOS applications read the public, versioned feed at
`/api/app-news/v1/` at most once per day. Manage messages through the FediOps
Control **Application News** page or the `app_news` management command. Keep a
slug stable because clients use it as the read/unread identifier; set **Kind**
to Update and provide **App version** for a release notice. Draft and
future-dated items are not returned.

```bash
python website/manage.py app_news list --all
python website/manage.py app_news put version-1-0 --title "myCamino 1.0" \
  --summary "Version 1.0 is available." --kind update --app-version 1.0
python website/manage.py app_news publish version-1-0
```

The feed request contains no installation identifier or project data. Do not
add per-client tokens, tracking parameters, or analytics to this endpoint.
