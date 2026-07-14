"""Guards for deploy/ (cloud VPS deployment, CLAUDE.md §2 deployment row).

Not a deployment smoke test (no real server here) — just makes sure the
artifacts exist, the systemd units point at real, importable module paths,
and never bind a public interface.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def test_deploy_artifacts_exist():
    for rel in ("server_setup.sh", "oneshot.sh", "firewall.sh",
               "systemd/neurox-dashboard.service",
               "systemd/neurox-scheduler.service"):
        assert (DEPLOY / rel).exists(), f"missing deploy/{rel}"


def _unit(name: str) -> str:
    return (DEPLOY / "systemd" / name).read_text()


@pytest.mark.parametrize("name,module", [
    ("neurox-dashboard.service", "src.api.app"),
    ("neurox-scheduler.service", "src.jobs.scheduler"),
])
def test_systemd_unit_references_importable_module(name, module):
    text = _unit(name)
    assert "ExecStart=" in text
    assert module in text, f"{name} ExecStart doesn't reference {module}"
    __import__(module)  # the module actually exists and imports cleanly


def test_dashboard_service_never_binds_publicly():
    exec_line = next(l for l in _unit("neurox-dashboard.service").splitlines()
                     if l.startswith("ExecStart="))
    assert "127.0.0.1" in exec_line
    assert "0.0.0.0" not in exec_line


def test_services_restart_and_run_as_unprivileged_user():
    for name in ("neurox-dashboard.service", "neurox-scheduler.service"):
        text = _unit(name)
        assert re.search(r"^Restart=always", text, re.M)
        assert re.search(r"^User=(?!root)\S+", text, re.M), f"{name} must not run as root"


def test_firewall_denies_dashboard_port_publicly():
    text = (DEPLOY / "firewall.sh").read_text()
    assert "deny 8000" in text
    assert "allow OpenSSH" in text or "allow ssh" in text.lower()


def test_never_recommends_tailscale_funnel():
    """`tailscale funnel` publishes the (no-auth) dashboard to the public
    internet — the opposite of `tailscale serve`. No deploy script may invoke
    it, and docs may only mention it inside an explicit prohibition."""
    for sh in (DEPLOY).glob("*.sh"):
        assert "funnel" not in sh.read_text().lower(), \
            f"{sh.name} mentions tailscale funnel — it must never appear in a script"
    # In docs, the *command* `tailscale funnel` may appear only on a
    # prohibition line; explanatory prose about what funnel does is fine.
    setup = (DEPLOY.parent / "SETUP.md").read_text().splitlines()
    for i, line in enumerate(setup):
        if "tailscale funnel" in line.lower():
            assert "never" in line.lower() or "not " in line.lower(), \
                f"SETUP.md:{i+1} shows `tailscale funnel` outside a prohibition: {line.strip()}"


def test_server_setup_locks_down_config_perms():
    text = (DEPLOY / "server_setup.sh").read_text()
    assert re.search(r"chmod 600 .*config\.yaml", text), \
        "server_setup.sh must chmod 600 config.yaml (holds the Fyers secret)"


SANDBOX_DIRECTIVES = ("NoNewPrivileges=true", "ProtectSystem=full",
                      "ProtectHome=read-only", "ReadWritePaths=",
                      "PrivateTmp=true", "CapabilityBoundingSet=",
                      "SystemCallFilter=@system-service", "UMask=0077")


@pytest.mark.parametrize("name,memory_max", [
    ("neurox-dashboard.service", "MemoryMax=1500M"),
    ("neurox-scheduler.service", "MemoryMax=4500M"),
])
def test_systemd_units_are_sandboxed(name, memory_max):
    text = _unit(name)
    for directive in SANDBOX_DIRECTIVES:
        assert directive in text, f"{name} missing sandbox directive {directive}"
    assert memory_max in text, f"{name} missing {memory_max}"


def test_server_setup_rewrites_readwritepaths_on_override():
    text = (DEPLOY / "server_setup.sh").read_text()
    assert re.search(r"sed .*ReadWritePaths", text), \
        "server_setup.sh must rewrite ReadWritePaths when NEUROX_APP_DIR is overridden"


def test_server_setup_is_idempotent_and_logs():
    text = (DEPLOY / "server_setup.sh").read_text()
    assert "set -euo pipefail" in text
    assert "LOG_FILE" in text and "tee -a" in text
    # every apt/useradd/venv/config.yaml/systemd step guards before acting,
    # rather than assuming a clean-slate box
    assert re.search(r"if\s*\[\s*!\s*-f\s*\"\$APP_DIR/config\.yaml\"", text)
    assert re.search(r"if\s*!\s*id -u", text)


def test_server_setup_has_arm_build_deps():
    text = (DEPLOY / "server_setup.sh").read_text()
    for pkg in ("cmake", "ninja-build", "libomp-dev", "libgomp1", "gfortran",
               "libopenblas-dev", "python3.11-dev"):
        assert pkg in text, f"missing aarch64 build dep: {pkg}"


def test_server_setup_self_checks_both_services():
    text = (DEPLOY / "server_setup.sh").read_text()
    assert "systemctl" in text and "is-active" in text
    assert "neurox-dashboard" in text and "neurox-scheduler" in text
    assert "curl" in text and "127.0.0.1:8000" in text
    assert "SELF-CHECK" in text


def test_oneshot_delegates_to_server_setup_without_duplicating_it():
    text = (DEPLOY / "oneshot.sh").read_text()
    assert "server_setup.sh" in text
    assert "set -euo pipefail" in text
    # oneshot's job is fetch-then-delegate, not re-implementing the install —
    # it should not itself touch systemd/apt install of the app's deps
    assert "systemctl enable" not in text
    assert "requirements.txt" not in text
