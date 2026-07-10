#!/usr/bin/env bash
# NEUROX cloud deployment — idempotent setup for a fresh Ubuntu 22.04 VPS.
# Run as root:  curl -fsSL .../server_setup.sh | bash   OR   sudo ./server_setup.sh
#
# What it does:
#   1. Installs Python 3.11+, git, build deps, ufw, curl.
#   2. Clones/updates the repo at /opt/neurox.
#   3. Creates the venv, installs requirements.txt.
#   4. Copies config.example.yaml -> config.yaml if missing (never overwrites).
#   5. Installs Tailscale (private, no public exposure — CLAUDE.md's static-IP
#      rule is about the order-placement API, which V1 does not wire).
#   6. Installs + enables the systemd services (dashboard + scheduler).
#   7. Applies the firewall (deny public 8000, allow SSH).
#
# Safe to re-run: every step checks before acting.

set -euo pipefail

REPO_URL="${NEUROX_REPO_URL:-https://github.com/ninda5555/NEUROX.git}"
BRANCH="${NEUROX_BRANCH:-claude/nse-trading-assistant-setup-hly9x3}"
APP_DIR="/opt/neurox"
SERVICE_USER="${NEUROX_USER:-neurox}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root (sudo ./server_setup.sh)." >&2
  exit 1
fi

echo "==> [1/7] System packages"
apt-get update -qq
apt-get install -y -qq software-properties-common curl git ufw build-essential \
  python3.11 python3.11-venv python3.11-dev >/dev/null

echo "==> [2/7] Service account"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  echo "    created system user '$SERVICE_USER'"
else
  echo "    user '$SERVICE_USER' already exists"
fi

echo "==> [3/7] Repository at $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

echo "==> [4/7] Python virtual environment"
sudo -u "$SERVICE_USER" python3.11 -m venv "$APP_DIR/.venv"
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> [5/7] config.yaml"
if [ ! -f "$APP_DIR/config.yaml" ]; then
  sudo -u "$SERVICE_USER" cp "$APP_DIR/config.example.yaml" "$APP_DIR/config.yaml"
  echo "    created config.yaml — EDIT IT before continuing (fyers.app_id / secret_key)."
else
  echo "    config.yaml already present — left untouched."
fi

echo "==> [6/7] Tailscale (private dashboard access, no public exposure)"
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
else
  echo "    tailscale already installed"
fi

echo "==> [7/7] systemd services + firewall"
cp "$APP_DIR/deploy/systemd/neurox-dashboard.service" /etc/systemd/system/
cp "$APP_DIR/deploy/systemd/neurox-scheduler.service" /etc/systemd/system/
# Services run as SERVICE_USER, not root.
sed -i "s/^User=.*/User=$SERVICE_USER/" /etc/systemd/system/neurox-dashboard.service
sed -i "s/^User=.*/User=$SERVICE_USER/" /etc/systemd/system/neurox-scheduler.service
systemctl daemon-reload
systemctl enable neurox-dashboard.service neurox-scheduler.service

bash "$APP_DIR/deploy/firewall.sh"

echo ""
echo "============================================================"
echo " Base install done. Next steps (manual, one time):"
echo "============================================================"
echo "  1. Edit config.yaml:"
echo "       sudo -u $SERVICE_USER nano $APP_DIR/config.yaml"
echo "     Fill in fyers.app_id and fyers.secret_key."
echo ""
echo "  2. Join Tailscale (prints a login URL to open once):"
echo "       tailscale up"
echo ""
echo "  3. Run the one-time bootstrap (history download + training, 1-3 hrs):"
echo "       sudo -u $SERVICE_USER bash -lc 'cd $APP_DIR && source .venv/bin/activate && ./bootstrap.sh'"
echo ""
echo "  4. Start the services:"
echo "       systemctl start neurox-dashboard neurox-scheduler"
echo "       systemctl status neurox-dashboard neurox-scheduler"
echo ""
echo "  5. Expose the dashboard on your private tailnet ONLY (the app itself"
echo "     stays bound to 127.0.0.1 — 'tailscale serve' is what bridges it,"
echo "     plain 'tailscale up' alone does NOT make a 127.0.0.1 service"
echo "     reachable from other devices):"
echo "       tailscale serve --bg http://127.0.0.1:8000"
echo "     (one-time: enable 'HTTPS Certificates' for your tailnet at"
echo "      https://login.tailscale.com/admin/dns — free, no domain needed)"
echo ""
echo "  6. On your phone: install the Tailscale app, sign into the same"
echo "     account, then open the URL 'tailscale serve status' prints"
echo "     (looks like https://<this-machine>.<your-tailnet>.ts.net)"
echo "============================================================"
