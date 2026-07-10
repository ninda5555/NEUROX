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
    for rel in ("server_setup.sh", "firewall.sh",
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
