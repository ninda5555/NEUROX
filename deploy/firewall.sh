#!/usr/bin/env bash
# ufw baseline for the NEUROX VPS: SSH in, nothing else public.
# The dashboard is never bound to a public interface (see
# deploy/systemd/neurox-dashboard.service — 127.0.0.1 only, reached via
# `tailscale serve`), but this is defense in depth in case that ever changes.
set -euo pipefail

ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw deny 8000/tcp     # dashboard: never public, Tailscale-only
ufw --force enable
ufw status verbose
