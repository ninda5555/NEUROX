#!/usr/bin/env bash
# SSH hardening: key-only auth. Called by server_setup.sh (idempotent), or
# run standalone: sudo ./deploy/harden_ssh.sh
#
# Lockout guard: password auth is disabled ONLY if at least one non-empty
# authorized_keys exists — on a box reached purely by password (not the
# Oracle default, which is key-only from birth), flipping this blind would
# lock the operator out permanently. In that case we print what to do and
# exit 0 (setup must proceed) without touching sshd.

set -euo pipefail

DROPIN=/etc/ssh/sshd_config.d/60-neurox-harden.conf

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo ./harden_ssh.sh)." >&2
  exit 1
fi

has_key=0
for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do
  if [ -s "$f" ]; then
    has_key=1
    echo "    found SSH key(s): $f"
    break
  fi
done

if [ "$has_key" -ne 1 ]; then
  echo "    WARNING: no non-empty authorized_keys found — NOT disabling"
  echo "    password auth (that would lock you out). Add your key first"
  echo "    (ssh-copy-id), then re-run: sudo ./deploy/harden_ssh.sh"
  exit 0
fi

mkdir -p /etc/ssh/sshd_config.d
cat > "$DROPIN" <<'EOF'
# NEUROX deploy/harden_ssh.sh — key-only SSH
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
MaxAuthTries 4
EOF

if sshd -t 2>/dev/null || /usr/sbin/sshd -t; then
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
  echo "    password auth disabled ($DROPIN); existing sessions unaffected."
else
  rm -f "$DROPIN"
  echo "    sshd config validation failed — drop-in removed, sshd untouched." >&2
  exit 1
fi
