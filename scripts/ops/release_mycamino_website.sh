#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/mycamino_env.sh"
if [[ "${MYCAMINO_LOCK_HELD:-0}" != "1" ]]; then
  exec python3 -m ops_control.operation_lock bash "${BASH_SOURCE[0]}" "$@"
fi

infra_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mycamino_root="${1:-${MYCAMINO_REPO_ROOT:-${infra_root}}}"
github_repo="${MYCAMINO_GITHUB_REPO:-hfalcke/myCamino-GPS-SlideShow}"
workflow="${MYCAMINO_WEBSITE_WORKFLOW:-myCamino website}"
discovery_timeout="${MYCAMINO_BUILD_DISCOVERY_SECONDS:-180}"
poll_interval="${MYCAMINO_BUILD_POLL_SECONDS:-5}"
git_bin="${MYCAMINO_GIT_BIN:-git}"
gh_bin="${MYCAMINO_GH_BIN:-gh}"
publish_script="${MYCAMINO_PUBLISH_SCRIPT:-${infra_root}/scripts/ops/publish_mycamino_site.sh}"
deploy_script="${MYCAMINO_DEPLOY_SCRIPT:-${infra_root}/scripts/deploy/deploy_mycamino_staging.sh}"

[[ -d "$mycamino_root/.git" ]] || { echo "Missing myCamino Git checkout: $mycamino_root" >&2; exit 2; }
[[ -x "$publish_script" ]] || { echo "Missing website publisher: $publish_script" >&2; exit 2; }
[[ -x "$deploy_script" ]] || { echo "Missing website deployment script: $deploy_script" >&2; exit 2; }
command -v "$git_bin" >/dev/null 2>&1 || { echo "git is required: $git_bin" >&2; exit 2; }
command -v "$gh_bin" >/dev/null 2>&1 || { echo "GitHub CLI is required: $gh_bin" >&2; exit 2; }
[[ "$discovery_timeout" =~ ^[1-9][0-9]*$ ]] || { echo "MYCAMINO_BUILD_DISCOVERY_SECONDS must be a positive integer." >&2; exit 2; }
[[ "$poll_interval" =~ ^[1-9][0-9]*$ ]] || { echo "MYCAMINO_BUILD_POLL_SECONDS must be a positive integer." >&2; exit 2; }

branch="$($git_bin -C "$mycamino_root" branch --show-current)"
[[ "$branch" == "main" ]] || { echo "Website release requires branch main; current branch: ${branch:-detached}" >&2; exit 2; }
"$gh_bin" auth status --hostname github.com >/dev/null

remote_before="$($git_bin -C "$mycamino_root" ls-remote origin refs/heads/main | awk 'NR == 1 {print $1}')"

echo "==> Testing, committing, and pushing the myCamino website scope"
"$publish_script" "$mycamino_root"

release_sha="$($git_bin -C "$mycamino_root" rev-parse HEAD)"
remote_after="$($git_bin -C "$mycamino_root" ls-remote origin refs/heads/main | awk 'NR == 1 {print $1}')"
[[ "$remote_after" == "$release_sha" ]] || {
  echo "GitHub main does not match the local release commit." >&2
  echo "Local:  $release_sha" >&2
  echo "Remote: ${remote_after:-missing}" >&2
  exit 1
}

run_line=""
if [[ "$remote_before" != "$release_sha" ]]; then
  echo "==> Waiting for the GitHub website build for ${release_sha:0:12}"
  deadline=$((SECONDS + discovery_timeout))
  while (( SECONDS < deadline )); do
    run_line="$($gh_bin run list \
      --repo "$github_repo" \
      --workflow "$workflow" \
      --commit "$release_sha" \
      --limit 1 \
      --json databaseId,status,conclusion,url \
      --jq '.[0] // empty | [.databaseId, .status, (.conclusion // "pending"), .url] | @tsv')"
    [[ -n "$run_line" ]] && break
    sleep "$poll_interval"
  done
  [[ -n "$run_line" ]] || {
    echo "No GitHub Actions run appeared for commit $release_sha within ${discovery_timeout}s." >&2
    exit 1
  }
else
  echo "==> No new website commit was needed; checking the existing build for main"
  run_line="$($gh_bin run list \
    --repo "$github_repo" \
    --workflow "$workflow" \
    --commit "$release_sha" \
    --status success \
    --limit 1 \
    --json databaseId,status,conclusion,url \
    --jq '.[0] // empty | [.databaseId, .status, (.conclusion // "pending"), .url] | @tsv')"
  [[ -n "$run_line" ]] || { echo "No successful myCamino website build is available." >&2; exit 1; }
fi

IFS=$'\t' read -r run_id run_status run_conclusion run_url <<<"$run_line"
[[ "$run_id" =~ ^[0-9]+$ ]] || { echo "GitHub returned an invalid workflow run identifier." >&2; exit 1; }
echo "GitHub run: $run_url"
if [[ "$run_status" != "completed" || "$run_conclusion" != "success" ]]; then
  echo "==> Waiting for GitHub run $run_id to complete"
  "$gh_bin" run watch "$run_id" --repo "$github_repo" --compact --exit-status --interval 10
fi

remote_ready="$($git_bin -C "$mycamino_root" ls-remote origin refs/heads/main | awk 'NR == 1 {print $1}')"
[[ "$remote_ready" == "$release_sha" ]] || {
  echo "GitHub main changed while the image was building; refusing to deploy an ambiguous :main tag." >&2
  echo "Expected: $release_sha" >&2
  echo "Current:  ${remote_ready:-missing}" >&2
  exit 1
}

echo "==> Pulling, deploying, and smoke-testing the myCamino website"
"$deploy_script"

echo "==> myCamino website release complete"
echo "Commit:  $release_sha"
echo "Build:   $run_url"
echo "Website: https://mycamino.heinofalcke.de/"
