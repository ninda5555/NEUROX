---
name: neurox-deploy
description: Apply a NEUROX code update to the live server (pull + restart, or full re-setup). Use after a NEUROX change has been committed and pushed, when the user asks to deploy, update, or roll out a fix to the server — including when acting on behalf of the account owner via a Cowork/orchestrating session.
---

# Deploying a NEUROX update

Claude Code cannot reach the server directly (no network path — see
CLAUDE.md §12). This skill is the exact procedure to hand to whoever has
SSH access (the account owner, or an operator they've authorized).

## Step 1 — confirm the change is actually pushed

Before telling anyone to deploy, verify locally:

```bash
git log --oneline -3
git status --short
```

Both `main` and the working feature branch should be pushed and the tree
clean. Never tell someone to pull a change that isn't on the remote yet.

## Step 2 — pick the right update type

**Most changes — code, config, the dashboard's built assets:**

```bash
cd ~/NEUROX
git pull
sudo systemctl restart neurox-dashboard neurox-scheduler
```

**Only when the change touches `deploy/systemd/*.service`, `deploy/server_setup.sh`
itself, firewall rules, or SSH hardening** — a bare restart does NOT pick up
systemd unit file changes:

```bash
cd ~/NEUROX
git pull
sudo ./deploy/server_setup.sh
```

This is idempotent and safe to re-run; it ends with its own
`SELF-CHECK: PASS/FAIL` line.

If unsure which applies, default to the full `server_setup.sh` re-run — it's
safe either way, just slower.

## Step 3 — verify, don't assume

Immediately follow up with the `neurox-status` skill / runbook. A `git pull`
succeeding is not the same as the service coming back up healthy — confirm
it actually did.

## Non-negotiables while deploying (CLAUDE.md §17)

- Never skip the verify step in Step 3.
- Never add `--force` / skip hooks to push something that failed tests.
- Never touch `config.yaml` contents as part of a deploy — that file holds
  the Fyers secret and is the account owner's alone (see the Operator
  Briefing, §04, for the full boundary list).
