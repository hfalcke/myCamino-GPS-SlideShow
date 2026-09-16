#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/mycamino_env.sh"
if [[ "${MYCAMINO_LOCK_HELD:-0}" != "1" ]]; then
  exec python3 -m ops_control.operation_lock bash "${BASH_SOURCE[0]}" "$@"
fi

default_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
repo_root="${1:-${MYCAMINO_REPO_ROOT:-${default_root}}}"
[[ -d "$repo_root/.git" ]] || { echo "Missing myCamino Git checkout: $repo_root" >&2; exit 2; }

branch="$(git -C "$repo_root" branch --show-current)"
[[ "$branch" == "main" ]] || { echo "myCamino website publishing requires branch main; current branch: ${branch:-detached}" >&2; exit 2; }

if ! git -C "$repo_root" diff --cached --quiet; then
  echo "Refusing to mix already-staged changes into the website commit:" >&2
  git -C "$repo_root" diff --cached --name-only >&2
  exit 2
fi

website_python="$repo_root/.venv-web/bin/python"
if [[ ! -x "$website_python" ]]; then
  python3 -m venv "$repo_root/.venv-web"
fi
if ! "$website_python" -c 'import django, markdown, whitenoise' >/dev/null 2>&1; then
  "$repo_root/.venv-web/bin/pip" install -r "$repo_root/website/requirements.txt"
fi

echo "Running myCamino website checks"
"$website_python" "$repo_root/website/manage.py" test siteapp
"$website_python" "$repo_root/website/manage.py" makemigrations --check --dry-run

paths=(
  .gitignore
  website
  .github/workflows/mycamino-site.yml
  scripts/publish_website_release.sh
  ops_control
  control.sh
  scripts/ops
  scripts/deploy
  scripts/tests
  infra
  docs/CONTROL_PANEL.md
)

git -C "$repo_root" diff --check -- "${paths[@]}"
echo "Checking existing outgoing commits before publishing"
git -C "$repo_root" fetch origin main
python3 - "$repo_root" <<'PY'
import subprocess, sys
root = sys.argv[1]
names = subprocess.check_output(['git', '-C', root, 'log', '--format=', '--name-only', 'origin/main..HEAD'], text=True).splitlines()
allowed = ('website/', 'ops_control/', 'scripts/ops/', 'scripts/deploy/', 'scripts/tests/', 'infra/')
single = {'.gitignore', 'control.sh', '.github/workflows/mycamino-site.yml', 'scripts/publish_website_release.sh', 'docs/CONTROL_PANEL.md', 'README.md'}
other = sorted({n for n in names if n and n not in single and not n.startswith(allowed)})
if other:
    print('Existing unpushed commits contain application changes. Review and publish those separately before releasing the website:', file=sys.stderr)
    print('\n'.join(other), file=sys.stderr)
    raise SystemExit(2)
PY

echo "Website-scoped changes"
git -C "$repo_root" status --short -- "${paths[@]}"
git -C "$repo_root" add -- "${paths[@]}"

if git -C "$repo_root" diff --cached --quiet; then
  echo "No website changes need a new commit. Pushing existing main commits."
else
  git -C "$repo_root" diff --cached --check
  git -C "$repo_root" commit -m "Publish myCamino beta website"
fi

git -C "$repo_root" push origin main
echo "Website source pushed. GitHub Actions will publish ghcr.io/hfalcke/mycamino-site:main."
