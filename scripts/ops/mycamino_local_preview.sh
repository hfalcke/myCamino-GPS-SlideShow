#!/usr/bin/env bash
set -euo pipefail

default_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
action="${1:-status}"
repo_root="${2:-${MYCAMINO_REPO_ROOT:-${default_root}}}"
website_root="$repo_root/website"
venv_root="$repo_root/.venv-web"
state_root="${MYCAMINO_PREVIEW_STATE_ROOT:-/tmp/mycamino-site-preview-${UID}}"
pid_file="$state_root/server.pid"
log_file="$state_root/server.log"
database_path="$state_root/db.sqlite3"
url="http://127.0.0.1:8765/"

[[ -f "$website_root/manage.py" ]] || { echo "Missing myCamino website at $website_root" >&2; exit 2; }
mkdir -p "$state_root"

running_pid() {
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(sed -n '1p' "$pid_file")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o command= | grep -F "website/manage.py runserver 127.0.0.1:8765" >/dev/null
}

ensure_environment() {
  if [[ ! -x "$venv_root/bin/python" ]]; then
    echo "Creating isolated website environment: $venv_root"
    python3 -m venv "$venv_root"
  fi
  if ! "$venv_root/bin/python" -c 'import django, markdown, whitenoise' >/dev/null 2>&1; then
    echo "Installing pinned website dependencies"
    "$venv_root/bin/pip" install -r "$website_root/requirements.txt"
  fi
}

case "$action" in
  start)
    if running_pid; then
      echo "myCamino local preview is already running at $url"
      exit 0
    fi
    rm -f "$pid_file"
    ensure_environment
    DJANGO_DATABASE_PATH="$database_path" \
      DJANGO_DEBUG="true" \
      DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost" \
      "$venv_root/bin/python" "$website_root/manage.py" migrate --noinput
    nohup env \
      DJANGO_DATABASE_PATH="$database_path" \
      DJANGO_DEBUG="true" \
      DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost" \
      "$venv_root/bin/python" "$website_root/manage.py" runserver 127.0.0.1:8765 --noreload \
      >"$log_file" 2>&1 </dev/null &
    echo "$!" > "$pid_file"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if curl -fsS --max-time 2 "${url}health/" >/dev/null 2>&1; then
        echo "myCamino local preview is ready: $url"
        echo "Log: $log_file"
        exit 0
      fi
      sleep 1
    done
    echo "Preview did not become healthy. Recent log:" >&2
    tail -40 "$log_file" >&2 || true
    exit 1
    ;;
  stop)
    if ! running_pid; then
      rm -f "$pid_file"
      echo "myCamino local preview is not running."
      exit 0
    fi
    pid="$(sed -n '1p' "$pid_file")"
    kill "$pid"
    for _ in 1 2 3 4 5; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    rm -f "$pid_file"
    echo "Stopped myCamino local preview."
    ;;
  status)
    if running_pid && curl -fsS --max-time 2 "${url}health/" >/dev/null 2>&1; then
      echo "myCamino local preview is healthy: $url"
      echo "PID: $(sed -n '1p' "$pid_file")"
      echo "Log: $log_file"
      exit 0
    fi
    echo "myCamino local preview is stopped or unhealthy."
    [[ -f "$log_file" ]] && tail -20 "$log_file"
    exit 1
    ;;
  *)
    echo "Usage: $0 {start|stop|status} [myCamino repository]" >&2
    exit 2
    ;;
esac
