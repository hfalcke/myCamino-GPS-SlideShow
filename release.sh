#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-./.venv/bin/python}"
REMOTE="${REMOTE:-origin}"
DMG_PATH="dist/myCamino-GPS-Track-Show.dmg"
COMMIT_MESSAGE=""
ASSUME_YES=0

usage() {
  cat <<'EOF'
Build, commit, and publish a myCamino release.

Usage:
  ./release.sh -m "Commit message"
  ./release.sh --yes -m "Commit message"

Options:
  -m, --message TEXT  Required Git commit message.
  -y, --yes           Run without the confirmation prompt.
  -h, --help          Show this help.

Environment overrides:
  PYTHON=path          Python interpreter used for tests.
  PYINSTALLER=path     Passed through to build_dmg.sh.
  REMOTE=name          Git remote to push to (default: origin).
  MYCAMINO_DEPLOY_HOST Remote SSH destination used by the website publisher.
  MYCAMINO_SSH_KEY     SSH private key used by the website publisher.

The script tests, commits the exact source, builds from that commit, pushes the
Git branch, and atomically publishes the verified DMG as the website's active
download. The local DMG remains in dist/ and is intentionally not committed.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--message)
      [[ $# -ge 2 ]] || { echo "Error: $1 requires a value." >&2; exit 2; }
      COMMIT_MESSAGE="$2"
      shift 2
      ;;
    -y|--yes)
      ASSUME_YES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "${COMMIT_MESSAGE//[[:space:]]/}" ]] || {
  echo "Error: provide a meaningful commit message with -m or --message." >&2
  exit 2
}
[[ -x "$PYTHON" ]] || { echo "Error: Python is not executable: $PYTHON" >&2; exit 1; }
[[ -x "./build_dmg.sh" ]] || { echo "Error: ./build_dmg.sh is missing or not executable." >&2; exit 1; }
[[ -x "./scripts/publish_website_release.sh" ]] || { echo "Error: website release publisher is missing or not executable." >&2; exit 1; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "Error: not inside a Git worktree." >&2; exit 1; }
git remote get-url "$REMOTE" >/dev/null 2>&1 || { echo "Error: Git remote '$REMOTE' does not exist." >&2; exit 1; }

RELEASE_LABEL="$(PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "$PYTHON" -c 'from application_metadata import full_version_label; print(full_version_label())')"
RELEASE_DATE="$(PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "$PYTHON" -c 'from application_metadata import APP_RELEASE_DATE; print(APP_RELEASE_DATE)')"
BUNDLE_VERSION="$(PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "$PYTHON" -c 'from application_metadata import APP_BUNDLE_VERSION; print(APP_BUNDLE_VERSION)')"

BRANCH="$(git branch --show-current)"
[[ -n "$BRANCH" ]] || { echo "Error: cannot release from a detached HEAD." >&2; exit 1; }

for state_ref in MERGE_HEAD REBASE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
  if git rev-parse -q --verify "$state_ref" >/dev/null 2>&1; then
    echo "Error: an unfinished Git operation is active ($state_ref). Finish or abort it first." >&2
    exit 1
  fi
done

echo "Release summary"
echo "  Repository: $ROOT_DIR"
echo "  Branch:     $BRANCH"
echo "  Remote:     $REMOTE ($(git remote get-url "$REMOTE"))"
echo "  Commit:     $COMMIT_MESSAGE"
echo "  Version:    $RELEASE_LABEL"
echo "  Artifact:   $DMG_PATH"
echo
git status --short
echo

if [[ "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "Run tests, commit the source, build the DMG, push, and publish it to the website? [y/N] " answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) echo "Release cancelled."; exit 0 ;;
  esac
fi

echo "==> Checking patch formatting"
git --no-pager diff --check

echo "==> Running test suite"
"$PYTHON" -m unittest discover -s tests

echo "==> Staging all repository changes"
git add --all

if git diff --cached --quiet; then
  echo "==> No source changes to commit; the DMG was built successfully."
else
  git --no-pager diff --cached --check
  git status --short
  echo "==> Creating commit"
  git commit -m "$COMMIT_MESSAGE"
fi

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "Error: tracked or untracked source changes remain after committing." >&2
  echo "The DMG must be built from a clean, identifiable source revision." >&2
  git status --short >&2
  exit 1
fi

echo "==> Building and verifying DMG from $(git rev-parse --short HEAD)"
./build_dmg.sh
[[ -f "$DMG_PATH" ]] || { echo "Error: expected DMG was not created: $DMG_PATH" >&2; exit 1; }
BUNDLE_INFO="dist/myCamino GPS Track Show.app/Contents/Info.plist"
[[ -f "$BUNDLE_INFO" ]] || { echo "Error: built application metadata is missing: $BUNDLE_INFO" >&2; exit 1; }
BUILT_BUNDLE_VERSION="$(plutil -extract CFBundleShortVersionString raw -o - "$BUNDLE_INFO")"
[[ "$BUILT_BUNDLE_VERSION" == "$BUNDLE_VERSION" ]] || {
  echo "Error: built app version is $BUILT_BUNDLE_VERSION, expected $BUNDLE_VERSION." >&2
  exit 1
}
echo "==> Verified built application version $BUILT_BUNDLE_VERSION"

echo "==> Pushing $BRANCH to $REMOTE"
if git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1; then
  if ! git push "$REMOTE" "$BRANCH"; then
    echo "Error: the local commit is safe, but the push failed. Resolve the remote issue and run git push." >&2
    exit 1
  fi
else
  if ! git push --set-upstream "$REMOTE" "$BRANCH"; then
    echo "Error: the local commit is safe, but the push failed. Resolve the remote issue and run git push." >&2
    exit 1
  fi
fi

echo "==> Publishing verified DMG to mycamino.heinofalcke.de"
./scripts/publish_website_release.sh "$DMG_PATH" "$RELEASE_LABEL" "$RELEASE_DATE"

echo "==> Release complete"
echo "Commit: $(git rev-parse --short HEAD)"
echo "DMG:    $ROOT_DIR/$DMG_PATH"
shasum -a 256 "$DMG_PATH"
