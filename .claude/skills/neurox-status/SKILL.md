---
name: neurox-status
description: Check whether NEUROX's live server is healthy — both services running, dashboard responding, latest backup present. Use when the user asks for a NEUROX status check, health check, or "is it working" — including when acting on behalf of the account owner via a Cowork/orchestrating session.
---

# NEUROX health check

This confirms the REAL, current state of the deployed server — never assume
or infer status from the codebase alone; Claude Code has no network path to
the server (see CLAUDE.md §12), so a human or an agent with terminal access
must run the commands and report the output back.

## Steps

1. Ask the operator (whoever has SSH access to the server) to run:

```bash
cd ~/NEUROX
git log --oneline -3
sudo systemctl status neurox-dashboard neurox-scheduler --no-pager
curl -sSf http://127.0.0.1:8000/api/status
ls -la data/backups/ 2>/dev/null
```

   For pipeline-freshness questions ("why no signals?"), also ask for:

```bash
journalctl -u neurox-scheduler --since -48h --no-pager | grep -E "heartbeat|intraday pass|skipped|failed" | tail -30
```

   A healthy box logs an hourly `heartbeat:` line — its dates (last 1d bar,
   universe, surveillance) are the freshness ground truth, and `intraday pass`
   lines appear every ~20 min during market hours. "skipped: no valid token"
   means the morning re-auth is missing — signals resume automatically on the
   next pass after re-auth.

2. Read the pasted-back output and report, in plain language, one line per check:
   - **Code version**: does the top commit match what's expected? (compare against `git log --oneline -3` in this repo if unsure)
   - **Services**: both `neurox-dashboard` and `neurox-scheduler` must say `active (running)`. Anything else (`failed`, `inactive`, restarting in a loop) is a real problem — pull the `journalctl -u <service> -n 50 --no-pager` output before diagnosing further.
   - **Dashboard**: the `curl` must return a JSON block starting with `{"now_ist"...`. A connection error, empty response, or HTML error page means the dashboard is down even if systemd thinks it's running.
   - **Backups**: at least one recent `app-YYYY-MM-DD.db.gz` file in `data/backups/`. Nightly job runs 21:30 IST — a gap of more than ~2 days is worth flagging (§16 in CLAUDE.md).

3. Give a one-paragraph plain-English summary: healthy / needs attention / broken, and exactly what (if anything) to do next. Never report "it's fine" without having actually seen the pasted output for this run.

## What this does NOT do

Does not check whether a model is trained and active, or whether the daily
Fyers login has been completed today — those need `/api/model` and
`/api/status`'s `token` field respectively, which the curl above already
includes. Read the full JSON if the user wants that detail too.
