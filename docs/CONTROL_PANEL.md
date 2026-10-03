# myCamino Control

From the myCamino repository, run:

```bash
./control.sh
```

The launcher starts or reopens the authenticated panel at
`http://127.0.0.1:8788/`. It runs independently of FediOps (port 8787).
Use `./control.sh status`, `./control.sh stop`, or `./control.sh restart` to
manage the panel. Closing the browser does not stop it. Set
`MYCAMINO_CONTROL_NO_OPEN=1` to start without opening a browser.

## Operations

### Code

- **Commit and Push myCamino Code** asks for a commit message, runs application
  tests, stages all non-ignored repository changes, commits them, and pushes the
  current branch to GitHub. This includes existing staged files, new files,
  documentation, website changes, and any earlier pending commits. Review the
  checkout and pending-commit lists before confirming. It does not build a DMG
  or deploy the website.
- **Build Local DMG** runs `build_dmg.sh` against the current notebook source,
  including uncommitted changes. The output is
  `dist/myCamino-GPS-Track-Show.dmg`. It replaces local build products without
  committing, pushing, changing the version, or publishing the download.
  A progress page opens immediately, showing the current build stage, a
  stage-based progress bar, elapsed time, and combined output/errors refreshed
  every second. Stages are not equally long, so the bar is not a time estimate.
  Turn off **Follow new output** to read earlier lines without auto-scrolling.
  Closing or refreshing the page does not stop or repeat the build. Return via
  **Recent local builds** on the home page. The panel retains the last ten
  builds and the latest 250,000 output characters per build in memory for the
  current panel session. Its permanent action log records only the outcome.
- **Release App DMG to Website** asks for the version (`X.Y.Z`), release date,
  and commit message. It uses `release.sh` to update `application_metadata.py`,
  run tests, commit the exact source, build/verify the DMG, push Git, and publish
  the download with matching version/date metadata. Both app version constants
  are updated together. The form shows the current version and defaults the
  date to today; edit the version for a new numbered release. This changes the
  live download, not the website's application container. Release retention
  preserves the active and previous DMGs.

These actions require confirmation and share the website deployment lock.
Local DMG builds have live progress; other operations return output when
finished, so keep their results tab open. A failure
stops later steps but does not undo a version edit or a completed local commit.
A failed push preserves the commit; a failed upload may occur after the source
has already been pushed. Check the result before retrying. Never force-push to
work around a rejected push.

The release script can also update the version from the terminal:

```bash
./release.sh --version 0.9.2 --date 2026-10-03 -m "Prepare version 0.9.2"
```

Without the new flags it keeps the existing metadata, as before. `--version`
without `--date` uses today's date. Merely opening the panel or changing form
fields does not change the program version. Direct `release.sh` and
`build_dmg.sh` invocations also share the operation lock.

### Website and feedback

- **Release myCamino Website (All Steps)** checks and publishes website and
  operator code, waits for the matching GitHub image build, then deploys and
  smoke-tests only the website service. It does not rebuild the desktop app.
- Separate commit/push, build-status, deploy, start, stop, and status controls
  remain available. A second button confirms modifying operations.
- Local preview controls use port 8765 and an isolated preview database.
- GitHub report controls are read-only. “New” means open issues retaining the
  `needs-triage` label. Review and remove that label on GitHub to clear the queue.
  **List All Open myCamino Reports & Requests** also includes already-reviewed
  open issues, ordered by most recent activity. Neither list includes private
  website contact messages.
- **Check myCamino GitHub Build** shows the five latest website image builds
  and their status (running, succeeded, or failed). It does not start a build,
  deploy the website, or check the desktop DMG packaging.
- **Downloads** shows 30 requests per page, newest first. A request records
  authorized access, not proof that an entire DMG transfer completed.
- **Beta data** shows registrations, contact messages, and the download log;
  CSV exports retain their Excel-compatible encoding and dates.
- **Application news** supports drafts, editing, publishing, withdrawing, and
  slug-confirmed deletion. Publishing changes the live application news feed.

Except for the live local-build page, long operations return their result in
the same page. Other pages remain
available while they run. Modifying operations are serialized across the panel
and website-release CLI; wait for a running operation before starting another.
Each operation has an explanation on the home page, confirmation page, and
results page. Read-only checks are identified separately from modifying actions.

The Code release action uses `./release.sh`. Website publishing
does not stage desktop application files or the user guides automatically.
It refuses already-staged changes and refuses to push existing outgoing commits
that contain application work. Review and publish those commits separately when
they are ready. No existing unpushed commits were published during panel migration.

## Private configuration

Everything needed for routine operations lives outside Git in
`~/.config/mycamino/` (override with `MYCAMINO_CONFIG_DIR`):

| File | Purpose |
| --- | --- |
| `panel.env` | Local port and SSH connection settings |
| `inventory.yml` | Ansible host and SSH identity |
| `deployment.yml` | Application paths, legal details, SMTP settings, network name |
| `secrets.vault.yml` | Encrypted Django, SMTP, administrator, and registry credentials |
| `vault-password.txt` | Private key for decrypting the configuration |
| `migration-backup.vault` | Encrypted recovery copy of the former private FediPass files |

The configuration directory is accessible only to its owner; files use mode
0600. Back up the encrypted files and vault password securely. The migration
preserves existing credentials rather than rotating them. The notebook's old
plain-text vault-named file was not a source of encrypted storage; the new
myCamino secrets are encrypted with Ansible Vault.

Install the independent deployment environment with:

```bash
scripts/deploy/setup_ops.sh
```

Edit encrypted values without putting plaintext in Git:

```bash
.venv-ops/bin/ansible-vault edit \
  --vault-password-file ~/.config/mycamino/vault-password.txt \
  ~/.config/mycamino/secrets.vault.yml
```

The examples under `infra/examples/` document fresh installations. For the
one-time migration only, `scripts/deploy/migrate_private_config.py` reads the
old infrastructure checkout, prepares and verifies new private files, and—with
`--finalize`—removes old application-owned values. It retains the three gateway
values in FediPass configuration. Do not rerun a completed migration to overwrite
subsequent configuration edits. The encrypted backup contains a JSON mapping of
original relative file paths to base64-encoded contents; it is for selective
recovery, not wholesale replacement of newer infrastructure settings.

Logs and the private launcher session live in
`~/.local/state/mycamino-control/` (`MYCAMINO_CONTROL_STATE` overrides it).
The action log records action identifiers, times, and outcomes, without export
contents, news text, emails, or credentials. Existing FediOps logs stay in place.

## Shared infrastructure contract

FediPass infrastructure owns Caddy, TLS, network provisioning, and the protected
release mount. The agreed connection is:

- Hostname: `mycamino.heinofalcke.de`.
- Website container: `mycamino-site`, port `8003`.
- External Docker network: `fedipass_staging_public`.
- Server Compose directory: `/opt/mycamino/site`.
- Data directory: `/var/lib/mycamino`.
- Caddy mount: `/var/lib/mycamino/releases` → `/srv/mycamino-releases`, read-only.
- Download authorization: `/internal/authorize-download/`, followed by serving
  `latest.dmg` through Caddy's protected public download route.

The shared gateway keeps its infrastructure-owned playbook and existing Compose
file location, even though that file currently sits under `/opt/mycamino/site`.
The independent deployment checks the existing gateway and network; it does not
reload or recreate Caddy. Fix a missing gateway connection in `fedipass-infra`.
Changing the hostname, backend port, network, or release mount requires updating
both sides of this documented contract. Ordinary releases need only myCamino.

The website still owns its database, administrator, retention/export schedules,
and DMG publication. Release retention keeps the active DMG and one predecessor.
No data relocation or public URL change is part of the control-panel split.

## Checks

```bash
python3 -m unittest discover -s scripts/tests
bash scripts/deploy/deploy_mycamino_staging.sh --syntax-check
```

Tests use temporary repositories and mock remote mutations. The HTTP tests bind
temporary loopback ports. The syntax check reads private configuration but does
not deploy. Use the panel's read-only controls for live checks.
