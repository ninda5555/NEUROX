#!/usr/bin/env bash
# NEUROX single-command bootstrap. Unlike server_setup.sh (which assumes the
# repo is already checked out, since it lives inside it), this one works on a
# totally bare box — it fetches the repo itself, then hands off to
# deploy/server_setup.sh for the full idempotent install + self-check.
#
#   curl -fsSL https://raw.githubusercontent.com/ninda5555/NEUROX/main/deploy/oneshot.sh | sudo bash
#   # or, with the repo already cloned:
#   sudo ./deploy/oneshot.sh
#
# All server_setup.sh env var overrides apply here too (NEUROX_USER,
# NEUROX_APP_DIR, NEUROX_REPO_URL, NEUROX_BRANCH, NEUROX_LOG_FILE).

set -euo pipefail

REPO_URL="${NEUROX_REPO_URL:-https://github.com/ninda5555/NEUROX.git}"
BRANCH="${NEUROX_BRANCH:-main}"
SERVICE_USER="${NEUROX_USER:-ubuntu}"
APP_DIR="${NEUROX_APP_DIR:-/home/$SERVICE_USER/NEUROX}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root (sudo ./oneshot.sh, or pipe into 'sudo bash')." >&2
  exit 1
fi

echo "==> Fetching NEUROX ($BRANCH) to $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
else
  if ! command -v git >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq git
  fi
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

echo "==> Handing off to deploy/server_setup.sh (full install, firewall, services, self-check)"
exec env NEUROX_USER="$SERVICE_USER" NEUROX_APP_DIR="$APP_DIR" \
  NEUROX_REPO_URL="$REPO_URL" NEUROX_BRANCH="$BRANCH" \
  bash "$APP_DIR/deploy/server_setup.sh"
