"""Secret log redaction (T8, §12): credentials and tokens are scrubbed from
every record — including exception text — before reaching a handler."""

from __future__ import annotations

import copy
import logging

from src.config import Config, DEFAULTS
from src.logsafe import SecretRedactor, install_redaction

SECRET = "sup3r-s3cret-key-value"
TOTP = "JBSWY3DPEHPK3PXP"
PIN = "4321"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJmeV9pZCI6IlhZMDEyMzQifQ.c2lnbmF0dXJlLXBhcnQtaGVyZQ"


def _cfg(tmp_path):
    data = copy.deepcopy(DEFAULTS)
    data["fyers"].update(secret_key=SECRET, totp_secret=TOTP, pin=PIN)
    return Config(data, root=tmp_path)


def _capture_logger(tmp_path, name="redact-test"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    records = []

    class Sink(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    h = Sink()
    h.setFormatter(logging.Formatter("%(message)s"))
    h.addFilter(SecretRedactor([SECRET, TOTP], [PIN]))
    logger.handlers = [h]
    return logger, records


def test_known_secrets_and_jwt_are_scrubbed(tmp_path):
    logger, records = _capture_logger(tmp_path)
    logger.info("exchanging %s with key %s", JWT, SECRET)
    logger.info(f"totp seed {TOTP} pin {PIN} qty 43210")
    assert SECRET not in records[0] and JWT not in records[0]
    assert "[REDACTED]" in records[0]
    assert TOTP not in records[1] and f"pin {PIN}" not in records[1]
    assert "43210" in records[1]          # digits merely containing the pin survive


def test_exception_text_is_scrubbed(tmp_path):
    logger, records = _capture_logger(tmp_path, "redact-exc")
    try:
        raise RuntimeError(f"login failed for key {SECRET}")
    except RuntimeError:
        logger.exception("auth step blew up")
    joined = "\n".join(records)
    assert SECRET not in joined and "[REDACTED]" in joined


def test_unset_secrets_are_noops_and_short_values_ignored(tmp_path):
    r = SecretRedactor(["", None, "abc"], [""])          # nothing usable
    assert r.scrub("plain message abc 123") == "plain message abc 123"


def test_install_is_idempotent(tmp_path):
    root = logging.getLogger()
    h = logging.NullHandler()
    root.addHandler(h)
    try:
        install_redaction(_cfg(tmp_path))
        install_redaction(_cfg(tmp_path))
        assert sum(isinstance(f, SecretRedactor) for f in h.filters) == 1
    finally:
        root.removeHandler(h)
